"""Calibration authoring service tests; all sample sources are hardware-free."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from soft_actuator_testing.application.calibration_workflow import (
    CalibrationCaptureCancelled,
    CalibrationCaptureTimeout,
    CalibrationMeasurement,
    CalibrationValidationStatus,
    CalibrationWorkflowService,
    CaptureCancellation,
    FakeCalibrationSampleSource,
    PressureRange,
    SerialCalibrationSampleSource,
)
from soft_actuator_testing.domain.artifacts import ArtifactType
from soft_actuator_testing.domain.calibration import CalibrationModelType
from soft_actuator_testing.domain.errors import CalibrationError
from soft_actuator_testing.infrastructure.artifact_store import ArtifactFileStore


NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)


def _service() -> CalibrationWorkflowService:
    return CalibrationWorkflowService(
        FakeCalibrationSampleSource(
            (
                CalibrationMeasurement(1, NOW, 0.5, "fake"),
                CalibrationMeasurement(2, NOW, 1.5, "fake"),
                CalibrationMeasurement(3, NOW, 2.5, "fake"),
            )
        ),
        pressure_range=PressureRange(0, 200),
    )


def _record_three(service: CalibrationWorkflowService) -> None:
    for pressure in (0, 50, 100):
        service.request_sample()
        service.record_sample(pressure)


def test_capture_requires_a_strictly_new_structured_sequence() -> None:
    class StaleSource:
        def current_sequence(self) -> int:
            return 4

        def request_after(self, sequence: int, **kwargs) -> CalibrationMeasurement:
            del kwargs
            return CalibrationMeasurement(sequence, NOW, 1.2)

    service = CalibrationWorkflowService(StaleSource())
    with pytest.raises(CalibrationError, match="stale"):
        service.request_sample()


@pytest.mark.parametrize("pressure", ["", "nan", "inf", -1, 201])
def test_known_pressure_is_finite_and_configurably_ranged(pressure: object) -> None:
    service = _service()
    service.request_sample()
    with pytest.raises(CalibrationError, match="known pressure"):
        service.record_sample(pressure)  # type: ignore[arg-type]


def test_record_remove_clear_and_undo_return_to_prior_draft() -> None:
    service = _service()
    _record_three(service)
    first = service.snapshot.samples[0].identifier
    service.remove_sample(first)
    assert len(service.snapshot.samples) == 2
    service.undo()
    assert len(service.snapshot.samples) == 3
    service.clear_samples()
    assert not service.snapshot.samples
    assert service.undo() is True
    assert len(service.snapshot.samples) == 3


def test_fit_exposes_residuals_validity_and_extrapolation_warning() -> None:
    service = _service()
    _record_three(service)
    fit = service.fit(CalibrationModelType.LINEAR)
    assert len(fit.residuals) == 3
    assert service.snapshot.validation_status is CalibrationValidationStatus.VALID
    value, warning = service.predict(3.0)
    assert value == pytest.approx(125.0)
    assert warning is not None and "extrapolated" in warning


def test_versioned_save_load_and_legacy_import_export(tmp_path: Path) -> None:
    service = _service()
    _record_three(service)
    service.fit(CalibrationModelType.LINEAR)
    service.set_notes("Gauge checked before capture.")
    store = ArtifactFileStore(tmp_path)

    document = service.save(store)
    assert store.load(ArtifactType.CALIBRATION, document.metadata.identity.artifact_id).payload["notes"].startswith("Gauge")

    loaded = CalibrationWorkflowService(FakeCalibrationSampleSource())
    loaded.load(store, document.metadata.identity.artifact_id)
    assert loaded.snapshot.validation_status is CalibrationValidationStatus.VALID

    legacy_path = tmp_path / "legacy.json"
    loaded.export_legacy(store, legacy_path)
    imported = CalibrationWorkflowService(FakeCalibrationSampleSource())
    imported.import_legacy(store, legacy_path)
    assert len(imported.snapshot.samples) == 3


def test_serial_controller_bridge_uses_decoded_voltage_field_not_raw_text() -> None:
    from soft_actuator_testing.infrastructure.serial_adapter import TelemetryFrame

    class Controller:
        def __init__(self) -> None:
            self.streaming: list[bool] = []
            self.polls = 0

        def set_legacy_calibration_streaming(self, enabled: bool) -> None:
            self.streaming.append(enabled)

        def poll(self):
            self.polls += 1
            if self.polls == 2:
                return (
                    TelemetryFrame(
                        raw_line="timestamp 123.0, status, 1.25",
                        received_at=NOW,
                        values={"volts": 1.25},
                    ),
                )
            return ()

    controller = Controller()
    source = SerialCalibrationSampleSource(controller, poll_interval_seconds=0.001)
    measurement = source.request_after(source.current_sequence())
    assert measurement is not None and measurement.volts == 1.25
    assert controller.streaming == [True, False]


def test_serial_capture_times_out_and_releases_calibration_streaming() -> None:
    class Controller:
        def __init__(self) -> None:
            self.streaming: list[bool] = []

        def set_legacy_calibration_streaming(self, enabled: bool) -> None:
            self.streaming.append(enabled)

        def poll(self):
            return ()

    time = [0.0]
    controller = Controller()
    source = SerialCalibrationSampleSource(
        controller,
        capture_timeout_seconds=0.1,
        poll_interval_seconds=0.05,
        clock=lambda: time[0],
        sleeper=lambda seconds: time.__setitem__(0, time[0] + seconds),
    )
    with pytest.raises(CalibrationCaptureTimeout, match="within 0.1 seconds"):
        source.request_after(source.current_sequence())
    assert controller.streaming == [True, False]


def test_serial_capture_cancellation_releases_calibration_streaming() -> None:
    class Controller:
        def __init__(self) -> None:
            self.streaming: list[bool] = []

        def set_legacy_calibration_streaming(self, enabled: bool) -> None:
            self.streaming.append(enabled)

        def poll(self):
            return ()

    cancellation = CaptureCancellation()
    controller = Controller()

    def cancel_after_one_poll(_: float) -> None:
        cancellation.cancel()

    source = SerialCalibrationSampleSource(
        controller,
        capture_timeout_seconds=10,
        poll_interval_seconds=0.001,
        sleeper=cancel_after_one_poll,
    )
    with pytest.raises(CalibrationCaptureCancelled, match="cancelled"):
        source.request_after(source.current_sequence(), cancellation=cancellation)
    assert controller.streaming == [True, False]
