"""Qt-free calibration authoring service with structured, fresh capture."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from pathlib import Path
from threading import Event, Lock
from time import monotonic, sleep
from typing import Protocol, runtime_checkable
from uuid import uuid4

from soft_actuator_testing.application.serial_controller import SerialController
from soft_actuator_testing.application.services import ArtifactDocument, ArtifactStore, CancellationToken
from soft_actuator_testing.domain.artifacts import ArtifactIdentity, ArtifactMetadata, ArtifactType
from soft_actuator_testing.domain.calibration import (
    CalibrationFit,
    CalibrationModelType,
    CalibrationSample,
    FitQualityPolicy,
    VoltageDomain,
    fit_calibration,
)
from soft_actuator_testing.domain.errors import CalibrationError, ErrorCode
from soft_actuator_testing.infrastructure.serial_adapter import ErrorFrame, TelemetryFrame


@dataclass(frozen=True)
class CalibrationMeasurement:
    """One explicitly decoded voltage measurement from the serial service seam."""

    sequence: int
    timestamp: datetime
    volts: float
    source: str = "serial"

    def __post_init__(self) -> None:
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0:
            raise CalibrationError(ErrorCode.CALIBRATION_INVALID, "sequence must be a non-negative integer", "sequence")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise CalibrationError(ErrorCode.CALIBRATION_INVALID, "timestamp must include a timezone", "timestamp")
        if not isfinite(self.volts):
            raise CalibrationError(ErrorCode.NON_FINITE_VALUE, "voltage must be finite", "volts")


@runtime_checkable
class CalibrationSampleSource(Protocol):
    """Capture boundary; the source must return only a sample newer than baseline."""

    def current_sequence(self) -> int: ...

    def request_after(
        self,
        sequence: int,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationToken | None = None,
    ) -> CalibrationMeasurement | None: ...


class CalibrationCaptureTimeout(CalibrationError):
    """A fresh controller measurement did not arrive before the configured deadline."""

    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(
            ErrorCode.CALIBRATION_INVALID,
            f"no fresh voltage sample arrived within {timeout_seconds:g} seconds",
            "capture.timeout",
            "Check controller streaming, then retry the capture.",
        )


class CalibrationCaptureCancelled(CalibrationError):
    """The operator cancelled a pending calibration capture."""

    def __init__(self) -> None:
        super().__init__(
            ErrorCode.CALIBRATION_INVALID,
            "calibration capture was cancelled",
            "capture",
            "Request another fresh sample when the controller is ready.",
        )


class CalibrationCaptureFault(CalibrationError):
    """The structured serial adapter reported an error while capturing."""

    def __init__(self, message: str) -> None:
        super().__init__(
            ErrorCode.CALIBRATION_INVALID,
            f"calibration capture failed: {message}",
            "capture",
            "Check the serial connection and controller diagnostics, then retry.",
        )


class CaptureCancellation:
    """Thread-safe cancellation token suitable for a bounded capture worker."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class SerialCalibrationSampleSource:
    """Bridge the real controller's parsed frames into bounded calibration capture.

    ``SerialAdapter`` owns the serial reader thread and parser. This source only
    polls its already-decoded controller frames, so it never reads raw text or
    blocks in ``readline``.  It drains queued telemetry before setting its
    baseline, then accepts only a frame observed after ``CAL_ON``.
    """

    requires_background_capture = True

    def __init__(
        self,
        controller: SerialController,
        *,
        capture_timeout_seconds: float = 2.0,
        poll_interval_seconds: float = 0.02,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if capture_timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("capture timeout and polling interval must be positive")
        self._controller = controller
        self._capture_timeout_seconds = capture_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._sequence = 0

    def current_sequence(self) -> int:
        self._consume_frames()
        return self._sequence

    def request_after(
        self,
        sequence: int,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationToken | None = None,
    ) -> CalibrationMeasurement | None:
        if sequence != self._sequence:
            raise CalibrationError(
                ErrorCode.CALIBRATION_INVALID,
                "capture baseline is no longer current",
                "sequence",
                "Request a new sample after the previous capture completes.",
            )
        timeout = self._capture_timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout <= 0:
            raise ValueError("capture timeout must be positive")
        self._controller.set_legacy_calibration_streaming(True)
        deadline = self._clock() + timeout
        try:
            while True:
                if cancellation is not None and cancellation.is_cancelled():
                    raise CalibrationCaptureCancelled()
                measurement = self._consume_frames(after=sequence)
                if measurement is not None:
                    return measurement
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise CalibrationCaptureTimeout(timeout)
                self._sleeper(min(self._poll_interval_seconds, remaining))
        finally:
            # CAL_OFF is issued for success, timeout, cancellation, and faults.
            self._controller.set_legacy_calibration_streaming(False)

    def _consume_frames(self, *, after: int | None = None) -> CalibrationMeasurement | None:
        measurement: CalibrationMeasurement | None = None
        for frame in self._controller.poll():
            if isinstance(frame, ErrorFrame) and frame.source in {"read", "write", "shutdown"}:
                raise CalibrationCaptureFault(frame.message)
            if not isinstance(frame, TelemetryFrame):
                continue
            volts = frame.values.get("volts")
            if volts is None or not isfinite(volts):
                continue
            self._sequence += 1
            if after is not None and self._sequence > after:
                measurement = CalibrationMeasurement(
                    self._sequence,
                    frame.received_at,
                    float(volts),
                    "serial_controller",
                )
        return measurement


class FakeCalibrationSampleSource:
    """Deterministic hardware-free source used by default tests and demos."""

    def __init__(self, measurements: Iterable[CalibrationMeasurement] = ()) -> None:
        self._measurements = list(measurements)
        self._sequence = 0
        self.requests: list[int] = []

    @classmethod
    def demo(cls) -> FakeCalibrationSampleSource:
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return cls(
            CalibrationMeasurement(index, timestamp, volts, "fake")
            for index, volts in enumerate((0.5, 1.5, 2.5), start=1)
        )

    def current_sequence(self) -> int:
        return self._sequence

    def request_after(
        self,
        sequence: int,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationToken | None = None,
    ) -> CalibrationMeasurement | None:
        del timeout_seconds
        if cancellation is not None and cancellation.is_cancelled():
            raise CalibrationCaptureCancelled()
        self.requests.append(sequence)
        for measurement in self._measurements:
            if measurement.sequence > sequence:
                self._sequence = measurement.sequence
                return measurement
        return None


@dataclass(frozen=True)
class PressureRange:
    minimum_kpa: float = 0.0
    maximum_kpa: float = 1_000.0

    def __post_init__(self) -> None:
        if not isfinite(self.minimum_kpa) or not isfinite(self.maximum_kpa) or self.minimum_kpa > self.maximum_kpa:
            raise CalibrationError(ErrorCode.CALIBRATION_INVALID, "pressure range must be finite and ordered", "pressure_range")

    def validate(self, value: float | str) -> float:
        try:
            pressure = float(value)
        except (TypeError, ValueError) as error:
            raise CalibrationError(ErrorCode.CALIBRATION_INVALID, "known pressure must be a finite number", "known_pressure_kpa") from error
        if not isfinite(pressure):
            raise CalibrationError(ErrorCode.NON_FINITE_VALUE, "known pressure must be finite", "known_pressure_kpa")
        if not self.minimum_kpa <= pressure <= self.maximum_kpa:
            raise CalibrationError(
                ErrorCode.CALIBRATION_INVALID,
                f"known pressure must be between {self.minimum_kpa:g} and {self.maximum_kpa:g} kPa",
                "known_pressure_kpa",
            )
        return pressure


@dataclass(frozen=True)
class ManagedCalibrationSample:
    identifier: str
    sample: CalibrationSample
    captured_at: datetime | None = None
    source_sequence: int | None = None
    provenance: str = "manual"


class CalibrationValidationStatus(str, Enum):
    DRAFT = "draft"
    VALID = "valid"


@dataclass(frozen=True)
class CalibrationWorkflowSnapshot:
    samples: tuple[ManagedCalibrationSample, ...]
    pending_measurement: CalibrationMeasurement | None
    fit: CalibrationFit | None
    validation_status: CalibrationValidationStatus
    warnings: tuple[str, ...] = ()
    notes: str = ""
    artifact_id: str | None = None
    message: str = "Draft calibration: capture a fresh measurement and record a known pressure."


@dataclass(frozen=True)
class _State:
    samples: tuple[ManagedCalibrationSample, ...]
    pending_measurement: CalibrationMeasurement | None
    fit: CalibrationFit | None
    notes: str
    artifact_id: str | None


class CalibrationWorkflowService:
    """Author calibration without Qt, hardware parsing, or filesystem access."""

    def __init__(
        self,
        sample_source: CalibrationSampleSource,
        *,
        pressure_range: PressureRange = PressureRange(),
        quality_policy: FitQualityPolicy | None = None,
    ) -> None:
        self._source = sample_source
        self._pressure_range = pressure_range
        self._quality_policy = quality_policy or FitQualityPolicy()
        self._state = _State((), None, None, "", None)
        self._history: list[_State] = []
        self._message = "Draft calibration: capture a fresh measurement and record a known pressure."
        self._capture_lock = Lock()

    @property
    def capture_requires_background_worker(self) -> bool:
        """Whether this source can wait for hardware and must not run in Qt's thread."""

        return bool(getattr(self._source, "requires_background_capture", False))

    @property
    def snapshot(self) -> CalibrationWorkflowSnapshot:
        fit = self._state.fit
        warnings: list[str] = []
        status = CalibrationValidationStatus.DRAFT
        if fit is not None:
            if fit.adequacy.is_adequate:
                status = CalibrationValidationStatus.VALID
            else:
                warnings.append(fit.adequacy.reason or "Fit does not meet the configured quality policy.")
            domain = fit.model.input_domain
            if domain:
                warnings.append(f"Validated voltage domain: {domain.minimum_volts:g}–{domain.maximum_volts:g} V; outside values extrapolate.")
        return CalibrationWorkflowSnapshot(
            self._state.samples,
            self._state.pending_measurement,
            fit,
            status,
            tuple(warnings),
            self._state.notes,
            self._state.artifact_id,
            self._message,
        )

    def capture_sample(
        self,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationToken | None = None,
    ) -> tuple[int, CalibrationMeasurement]:
        """Wait for a fresh source measurement without mutating workflow state."""

        if not self._capture_lock.acquire(blocking=False):
            raise CalibrationError(
                ErrorCode.CALIBRATION_INVALID,
                "a calibration capture is already active",
                "capture",
                "Wait for the active request to finish or cancel it.",
            )
        try:
            baseline = self._source.current_sequence()
            measurement = self._source.request_after(
                baseline,
                timeout_seconds=timeout_seconds,
                cancellation=cancellation,
            )
            if measurement is None:
                raise CalibrationCaptureTimeout(timeout_seconds or 0)
            return baseline, measurement
        finally:
            self._capture_lock.release()

    def accept_capture(self, baseline: int, measurement: CalibrationMeasurement) -> CalibrationMeasurement:
        """Commit a completed fresh capture on the caller's workflow thread."""

        if measurement.sequence <= baseline:
            raise CalibrationError(
                ErrorCode.CALIBRATION_INVALID,
                "sample is stale because its sequence did not advance",
                "capture.sequence",
                "Wait for a new controller measurement before recording.",
            )
        self._push_history()
        self._state = replace(self._state, pending_measurement=measurement, fit=None, artifact_id=None)
        self._message = f"Fresh sample #{measurement.sequence} captured: {measurement.volts:.6g} V."
        return measurement

    def request_sample(
        self,
        *,
        timeout_seconds: float | None = None,
        cancellation: CancellationToken | None = None,
    ) -> CalibrationMeasurement:
        baseline, measurement = self.capture_sample(
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
        )
        return self.accept_capture(baseline, measurement)

    def record_sample(self, known_pressure_kpa: float | str) -> ManagedCalibrationSample:
        pressure = self._pressure_range.validate(known_pressure_kpa)
        pending = self._state.pending_measurement
        if pending is None:
            raise CalibrationError(
                ErrorCode.CALIBRATION_INVALID,
                "recording requires a requested fresh sample",
                "capture",
                "Request a sample first; a prior voltage is never reused.",
            )
        item = ManagedCalibrationSample(
            uuid4().hex,
            CalibrationSample(pressure, pending.volts),
            pending.timestamp,
            pending.sequence,
            pending.source,
        )
        self._push_history()
        self._state = replace(
            self._state,
            samples=(*self._state.samples, item),
            pending_measurement=None,
            fit=None,
            artifact_id=None,
        )
        self._message = f"Recorded {pressure:g} kPa at {pending.volts:.6g} V."
        return item

    def replace_samples(self, samples: Iterable[CalibrationSample], *, provenance: str = "presenter_snapshot") -> None:
        self._push_history()
        self._state = _State(
            tuple(ManagedCalibrationSample(uuid4().hex, item, provenance=provenance) for item in samples),
            None,
            None,
            self._state.notes,
            None,
        )
        self._message = "Samples loaded as a draft; fit them before use."

    def edit_sample(self, identifier: str, known_pressure_kpa: float | str, measured_voltage: float | str) -> None:
        pressure = self._pressure_range.validate(known_pressure_kpa)
        try:
            voltage = float(measured_voltage)
        except (TypeError, ValueError) as error:
            raise CalibrationError(ErrorCode.CALIBRATION_INVALID, "measured voltage must be finite", "measured_voltage") from error
        if not isfinite(voltage):
            raise CalibrationError(ErrorCode.NON_FINITE_VALUE, "measured voltage must be finite", "measured_voltage")
        index = self._index(identifier)
        self._push_history()
        original = self._state.samples[index]
        samples = list(self._state.samples)
        samples[index] = replace(original, sample=CalibrationSample(pressure, voltage))
        self._state = replace(self._state, samples=tuple(samples), fit=None, artifact_id=None)
        self._message = "Sample edited; calibration is a draft until refit."

    def remove_sample(self, identifier: str) -> None:
        index = self._index(identifier)
        self._push_history()
        samples = list(self._state.samples)
        samples.pop(index)
        self._state = replace(self._state, samples=tuple(samples), fit=None, artifact_id=None)
        self._message = "Sample removed; calibration is a draft until refit."

    def clear_samples(self) -> None:
        self._push_history()
        self._state = _State((), None, None, self._state.notes, None)
        self._message = "All samples cleared. Undo restores the prior draft."

    def undo(self) -> bool:
        if not self._history:
            self._message = "Nothing to undo."
            return False
        self._state = self._history.pop()
        self._message = "Restored the previous calibration state."
        return True

    def set_notes(self, notes: str) -> None:
        self._push_history()
        self._state = replace(self._state, notes=notes.strip(), artifact_id=None)
        self._message = "Notes updated; calibration is a draft until saved."

    def fit(self, model_type: CalibrationModelType) -> CalibrationFit:
        fit = fit_calibration(
            [item.sample for item in self._state.samples],
            model_type,
            quality_policy=self._quality_policy,
        )
        self._push_history()
        self._state = replace(self._state, fit=fit, artifact_id=None)
        self._message = (
            "Calibration is valid and ready to save."
            if fit.adequacy.is_adequate
            else f"Fit is a draft: {fit.adequacy.reason}"
        )
        return fit

    def predict(self, volts: float | str) -> tuple[float, str | None]:
        if self._state.fit is None:
            raise CalibrationError(ErrorCode.CALIBRATION_INVALID, "fit a calibration before predicting pressure", "fit")
        try:
            voltage = float(volts)
        except (TypeError, ValueError) as error:
            raise CalibrationError(ErrorCode.CALIBRATION_INVALID, "voltage must be finite", "volts") from error
        if not isfinite(voltage):
            raise CalibrationError(ErrorCode.NON_FINITE_VALUE, "voltage must be finite", "volts")
        domain = self._state.fit.model.input_domain
        warning = None
        if domain and not domain.contains(voltage):
            warning = f"{voltage:g} V is outside {domain.minimum_volts:g}–{domain.maximum_volts:g} V; result is extrapolated."
        return self._state.fit.model.apply(voltage, require_in_domain=False), warning

    def save(self, store: ArtifactStore, *, software_version: str | None = None) -> ArtifactDocument:
        document = self.as_document(software_version=software_version)
        store.save(document)
        self._state = replace(self._state, artifact_id=document.metadata.identity.artifact_id)
        self._message = f"Saved versioned calibration {document.metadata.identity.artifact_id}."
        return document

    def load(self, store: ArtifactStore, artifact_id: str) -> ArtifactDocument:
        document = store.load(ArtifactType.CALIBRATION, artifact_id)
        self.load_document(document)
        return document

    def import_legacy(self, store: ArtifactStore, source: Path) -> ArtifactDocument:
        document = store.import_legacy(source, ArtifactType.CALIBRATION)
        self.load_document(document)
        self._message = f"Imported legacy calibration {source.name}; review and save a versioned copy."
        return document

    def export_legacy(self, store: ArtifactStore, destination: Path) -> None:
        store.export_legacy(self.as_document(), destination)
        self._message = f"Exported legacy-compatible calibration to {destination.name}."

    def as_document(self, *, software_version: str | None = None) -> ArtifactDocument:
        fit = self._state.fit
        if fit is None:
            raise CalibrationError(
                ErrorCode.CALIBRATION_INVALID,
                "a fitted calibration is required before saving or exporting",
                "fit",
            )
        identity = ArtifactIdentity.new(ArtifactType.CALIBRATION)
        metadata = ArtifactMetadata.now(identity, software_version)
        model = fit.model
        payload = {
            "model": {"type": model.model_type.value, "coeffs": list(model.coefficients)},
            "samples": [[item.sample.known_pressure_kpa, item.sample.measured_voltage] for item in self._state.samples],
            "units": {"voltage": "V", "pressure": "kPa"},
            "validation_status": self.snapshot.validation_status.value,
            "input_domain": {
                "minimum_volts": model.input_domain.minimum_volts if model.input_domain else None,
                "maximum_volts": model.input_domain.maximum_volts if model.input_domain else None,
            },
            "fit_quality": {
                "r_squared": fit.adequacy.r_squared,
                "rmse_kpa": fit.adequacy.rmse_kpa,
                "max_abs_residual_kpa": fit.adequacy.max_abs_residual_kpa,
                "condition_number": fit.adequacy.condition_number,
                "adequate": fit.adequacy.is_adequate,
                "reason": fit.adequacy.reason,
            },
            "residuals": [
                {
                    "sample_index": residual.sample_index,
                    "voltage": residual.voltage,
                    "observed_pressure_kpa": residual.observed_pressure_kpa,
                    "predicted_pressure_kpa": residual.predicted_pressure_kpa,
                    "residual_kpa": residual.residual_kpa,
                }
                for residual in fit.residuals
            ],
            "sample_provenance": [
                {
                    "id": item.identifier,
                    "captured_at": item.captured_at.isoformat() if item.captured_at else None,
                    "source_sequence": item.source_sequence,
                    "source": item.provenance,
                }
                for item in self._state.samples
            ],
            "notes": self._state.notes,
            "created_by": "calibration_workflow",
        }
        return ArtifactDocument(metadata, payload)

    def load_document(self, document: ArtifactDocument) -> None:
        if document.metadata.identity.artifact_type is not ArtifactType.CALIBRATION:
            raise CalibrationError(ErrorCode.CALIBRATION_INVALID, "document is not a calibration artifact", "artifact_type")
        payload = document.payload
        try:
            model_data = payload["model"]
            model_type = CalibrationModelType(model_data["type"])
            pairs = payload["samples"]
        except (KeyError, TypeError, ValueError) as error:
            raise CalibrationError(ErrorCode.CALIBRATION_INVALID, "calibration document lacks model or samples", "payload") from error
        samples = tuple(CalibrationSample(pair[0], pair[1]) for pair in pairs)
        fit = fit_calibration(samples, model_type, quality_policy=self._quality_policy)
        self._push_history()
        self._state = _State(
            tuple(ManagedCalibrationSample(uuid4().hex, sample, provenance="artifact_load") for sample in samples),
            None,
            fit,
            str(payload.get("notes", "")),
            document.metadata.identity.artifact_id,
        )
        self._message = f"Loaded calibration {document.metadata.identity.artifact_id}."

    def _index(self, identifier: str) -> int:
        for index, sample in enumerate(self._state.samples):
            if sample.identifier == identifier:
                return index
        raise CalibrationError(ErrorCode.CALIBRATION_INVALID, "sample no longer exists", "sample_id")

    def _push_history(self) -> None:
        self._history.append(self._state)
