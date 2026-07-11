"""Hardware-free tests for the production cyclic-run coordinator."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from time import sleep

import pytest

from soft_actuator_testing.application.camera_capture import CaptureHealth, CaptureResult, TARGET_4K60
from soft_actuator_testing.application.run_controller import CyclicRunConfiguration, RunController
from soft_actuator_testing.domain.calibration import (
    CalibrationFit,
    CalibrationModel,
    CalibrationModelType,
    FitAdequacy,
)
from soft_actuator_testing.domain.artifacts import ArtifactType
from soft_actuator_testing.domain.geometry import FrameSize, NormalizedRoi, PixelPoint, VideoGeometry
from soft_actuator_testing.domain.run_state import RunCompletion, RunState
from soft_actuator_testing.infrastructure.artifact_store import ArtifactFileStore
from soft_actuator_testing.infrastructure.serial_adapter import CommandReceipt, CommandState, ErrorFrame, RunMarkerFrame, TelemetryFrame


@dataclass(frozen=True)
class _Status:
    value: str = "connected"


@dataclass(frozen=True)
class _SerialSnapshot:
    status: _Status = _Status()


@dataclass(frozen=True)
class _Profile:
    name: str = "legacy-field-3-unconfirmed"


class FakeSerial:
    snapshot = _SerialSnapshot()
    profile = _Profile()

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.frames: list[object] = []
        self.disconnects = 0
        self.fail_stop = False

    def _receipt(self, command: str) -> CommandReceipt:
        return CommandReceipt(command, command, datetime.now(timezone.utc), CommandState.SENT)

    def set_legacy_parameters(self, *, cycles: int, on_milliseconds: int, off_milliseconds: int):
        self.commands.extend(
            [
                f"CMD:SET CYCLES {cycles}",
                f"CMD:SET ON {on_milliseconds}",
                f"CMD:SET OFF {off_milliseconds}",
            ]
        )
        return tuple(self._receipt(command) for command in self.commands[-3:])

    def send_command(self, command: str, **_kwargs: object):
        self.commands.append(command)
        return self._receipt(command)

    def start_legacy_run(self):
        self.commands.append("CMD:START")
        return self._receipt("CMD:START")

    def stop_legacy_run(self):
        self.commands.append("CMD:STOP")
        if self.fail_stop:
            raise RuntimeError("injected stop failure")
        return self._receipt("CMD:STOP")

    def poll(self, maximum: int | None = None) -> tuple[object, ...]:
        result = tuple(self.frames)
        self.frames.clear()
        return result

    def disconnect(self) -> None:
        self.disconnects += 1


class FakeCamera:
    def __init__(self, *, ready: bool = True, stop_error: bool = False) -> None:
        self.health = CaptureHealth(ready=ready)
        self.ready = ready
        self.stop_error = stop_error
        self.started: list[tuple[Path, str]] = []
        self.stops = 0

    def start_capture(self, output_directory: Path, device_identifier: str, *, duration_seconds=None) -> None:
        self.started.append((output_directory, device_identifier))
        if not self.ready:
            raise RuntimeError("camera startup proof failed")

    def stop_capture(self, reason: str) -> CaptureResult:
        self.stops += 1
        if self.stop_error:
            raise RuntimeError("camera cannot stop")
        video = self.started[-1][0] / "video.mkv"
        video.write_bytes(b"fake-video")
        return CaptureResult(reason, video, video, True, True, self.health)


def _fit() -> CalibrationFit:
    return CalibrationFit(
        CalibrationModel(CalibrationModelType.LINEAR, (10.0, 1.0)),
        FitAdequacy(2, 1.0, 0.0, True),
    )


def _geometry() -> VideoGeometry:
    return VideoGeometry(
        FrameSize(16, 16),
        PixelPoint(1, 1),
        PixelPoint(2, 2),
        NormalizedRoi(0, 0, 16, 16),
    )


def _configuration(tmp_path: Path, **changes: object) -> CyclicRunConfiguration:
    values = {
        "experiment_name": "cyclic validation",
        "cycles": 3,
        "on_milliseconds": 6000,
        "off_milliseconds": 5000,
        "workspace": tmp_path,
        "camera_device": "fake-camera",
        "calibration": _fit(),
        "geometry": _geometry(),
        "camera_profile": TARGET_4K60,
        "estimated_storage_bytes": 0,
    }
    values.update(changes)
    return CyclicRunConfiguration(**values)


def _controller(tmp_path: Path, serial: FakeSerial | None = None, camera: FakeCamera | None = None) -> tuple[RunController, FakeSerial, FakeCamera]:
    serial = serial or FakeSerial()
    camera = camera or FakeCamera()
    controller = RunController(
        serial=serial,
        camera=camera,  # type: ignore[arg-type]
        storage=ArtifactFileStore(tmp_path),
        ui_telemetry_capacity=2,
    )
    return controller, serial, camera


def test_camera_proof_precedes_exact_legacy_start_order_and_recording_defaults_on(tmp_path: Path) -> None:
    controller, serial, camera = _controller(tmp_path)
    controller.configure(_configuration(tmp_path))

    controller.start()

    assert controller.snapshot.recording_enabled
    assert camera.started
    assert serial.commands == [
        "CMD:SET CYCLES 3",
        "CMD:SET ON 6000",
        "CMD:SET OFF 5000",
        "CMD:START",
    ]
    assert controller.snapshot.lifecycle.state is RunState.RUNNING


def test_start_async_uses_worker_and_exposes_immutable_running_snapshot(tmp_path: Path) -> None:
    controller, _, _ = _controller(tmp_path)
    controller.configure(_configuration(tmp_path))

    worker = controller.start_async()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert controller.snapshot.lifecycle.state is RunState.RUNNING
    controller.stop()


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"experiment_name": ""}, "Experiment name"),
        ({"cycles": 0}, "Cycles"),
        ({"on_milliseconds": 0}, "On timing"),
        ({"calibration": None}, "calibration"),
        ({"geometry": None}, "geometry"),
        ({"camera_device": ""}, "camera selection"),
    ],
)
def test_readiness_rejects_invalid_run_inputs(tmp_path: Path, changes: dict[str, object], expected: str) -> None:
    controller, _, _ = _controller(tmp_path)
    readiness = controller.configure(_configuration(tmp_path, **changes))
    assert not readiness.ready
    assert expected.casefold() in " ".join(readiness.failures).casefold()


def test_disconnected_or_unconfigured_serial_and_camera_proof_block_start(tmp_path: Path) -> None:
    serial = FakeSerial()
    serial.snapshot = _SerialSnapshot(_Status("disconnected"))  # type: ignore[assignment]
    controller, _, _ = _controller(tmp_path, serial=serial)
    assert not controller.configure(_configuration(tmp_path)).ready

    failing, serial, _ = _controller(tmp_path / "camera", camera=FakeCamera(ready=False))
    failing.configure(_configuration(tmp_path / "camera"))
    with pytest.raises(RuntimeError, match="camera"):
        failing.start()
    assert "CMD:START" not in serial.commands


def test_all_telemetry_is_durable_while_ui_projection_is_decimated(tmp_path: Path) -> None:
    controller, _, _ = _controller(tmp_path)
    controller.configure(_configuration(tmp_path))
    controller.start()
    for index in range(10):
        controller.record_telemetry(time_s=index / 10, volts=1.0 + index)

    result = controller.complete()
    assert result.clean
    assert len(controller.snapshot.telemetry) == 2
    with (result.manifest_path.parent / "pressure.csv").open(newline="") as handle:  # type: ignore[union-attr]
        rows = list(csv.DictReader(handle))
    assert len(rows) == 10
    assert rows[0]["pressure_kPa"] == "11.0"


def test_decoded_frames_record_missing_calibration_as_explicit_raw_only_value(tmp_path: Path) -> None:
    controller, _, _ = _controller(tmp_path)
    controller.configure(_configuration(tmp_path))
    controller.start()
    # The public telemetry path remains explicit even if a calibration becomes
    # unavailable after readiness; the CSV blank is never a formatting failure.
    controller.mark_calibration_unavailable()
    controller.ingest_frames(
        (
            TelemetryFrame(
                "0.2,x,2.5",
                datetime.now(timezone.utc),
                {"timestamp_seconds": 0.2, "volts": 2.5},
            ),
        )
    )
    result = controller.stop()
    with (result.manifest_path.parent / "pressure.csv").open(newline="") as handle:  # type: ignore[union-attr]
        row = next(csv.DictReader(handle))
    assert row["pressure_kPa"] == ""
    assert "raw-only" in " ".join(controller.snapshot.diagnostic_text)


@pytest.mark.parametrize(
    "method",
    ["stop", "global_stop", "controller_timeout", "controller_fault", "camera_fault", "close"],
)
def test_every_terminal_path_uses_one_idempotent_finalizer(tmp_path: Path, method: str) -> None:
    controller, serial, camera = _controller(tmp_path)
    controller.configure(_configuration(tmp_path))
    controller.start()

    call = getattr(controller, method)
    result = call("fault") if method in {"controller_fault", "camera_fault"} else call()
    duplicate = controller.stop()

    assert result is not None
    assert serial.commands.count("CMD:STOP") == 1
    assert camera.stops == 1
    assert duplicate.idempotent
    assert result.manifest_path is not None and result.manifest_path.is_file()


def test_cleanup_attempts_every_step_and_preserves_partial_artifacts_on_failures(tmp_path: Path) -> None:
    serial = FakeSerial()
    serial.fail_stop = True
    controller, _, camera = _controller(tmp_path, serial=serial, camera=FakeCamera(stop_error=True))
    controller.configure(_configuration(tmp_path))
    controller.start()
    controller.record_telemetry(time_s=0.0, volts=1.0)

    result = controller.controller_fault("device fault")

    assert result.completion is RunCompletion.FAULTED
    assert any("CMD:STOP" in error for error in result.errors)
    assert any("camera cleanup" in error for error in result.errors)
    assert result.manifest_path is not None
    assert (result.manifest_path.parent / "pressure.csv").is_file()


def test_end_marker_completes_cleanly_without_redundant_stop(tmp_path: Path) -> None:
    controller, serial, _ = _controller(tmp_path)
    controller.configure(_configuration(tmp_path))
    controller.start()
    controller.ingest_frames((RunMarkerFrame("--- end run ---", datetime.now(timezone.utc), False),))

    assert controller.snapshot.lifecycle.completion is RunCompletion.CLEAN
    assert "CMD:STOP" not in serial.commands


def test_serial_error_frame_and_unsuccessful_receipt_fault_the_run(tmp_path: Path) -> None:
    controller, serial, _ = _controller(tmp_path)
    controller.configure(_configuration(tmp_path))
    controller.start()
    controller.ingest_frames((ErrorFrame("", datetime.now(timezone.utc), "lost", "read"),))
    assert controller.snapshot.lifecycle.completion is RunCompletion.FAULTED

    class FailedSerial(FakeSerial):
        def send_command(self, command: str, **_kwargs: object):
            self.commands.append(command)
            if command != "CMD:START":
                return self._receipt(command)
            return CommandReceipt("CMD:START", "1", datetime.now(timezone.utc), CommandState.WRITE_FAILED)

    failed, serial, _ = _controller(tmp_path / "failed", serial=FailedSerial())
    failed.configure(_configuration(tmp_path / "failed"))
    with pytest.raises(RuntimeError, match="not sent successfully"):
        failed.start()
    assert serial.commands.count("CMD:START") == 1


def test_record_video_off_skips_camera_readiness_and_zero_row_pressure_is_loadable(tmp_path: Path) -> None:
    controller = RunController(
        serial=FakeSerial(),
        camera=None,
        storage=ArtifactFileStore(tmp_path),
    )
    controller.configure(_configuration(tmp_path, record_video=False, camera_device=""))
    controller.start()
    result = controller.global_stop()
    assert result.manifest_path is not None
    store = ArtifactFileStore(tmp_path)
    loaded = store.load(ArtifactType.PRESSURE_DATA, result.manifest_path.parent.name)
    assert loaded.payload["rows"] == []


def test_reusing_same_configuration_resets_finalizer_and_manifest_snapshots(tmp_path: Path) -> None:
    controller, _, _ = _controller(tmp_path)
    configuration = _configuration(tmp_path)
    controller.configure(configuration)
    controller.start()
    first = controller.global_stop()
    controller.configure(configuration)
    controller.start()
    second = controller.stop()

    assert first.manifest_path != second.manifest_path
    import json

    manifest = json.loads(second.manifest_path.read_text())
    payload = manifest["payload"]
    assert payload["calibration_model_snapshot"]["coefficients"] == [10.0, 1.0]
    assert payload["geometry_model_snapshot"]["frame_size"] == {"height": 16, "width": 16}


def test_restarting_without_reconfiguration_resets_finalizer_generation(tmp_path: Path) -> None:
    controller, serial, camera = _controller(tmp_path)
    controller.configure(_configuration(tmp_path))
    controller.start()
    first = controller.stop()

    controller.start()
    second = controller.stop()

    assert first.manifest_path != second.manifest_path
    assert serial.commands.count("CMD:START") == 2
    assert serial.commands.count("CMD:STOP") == 2
    assert camera.stops == 2


def test_global_stop_winning_during_camera_start_cannot_send_start_or_leak_camera(tmp_path: Path) -> None:
    class BlockingCamera(FakeCamera):
        def __init__(self) -> None:
            super().__init__()
            self.entered = Event()
            self.release = Event()

        def start_capture(self, output_directory: Path, device_identifier: str, *, duration_seconds=None) -> None:
            self.started.append((output_directory, device_identifier))
            self.entered.set()
            assert self.release.wait(1)

    camera = BlockingCamera()
    controller, serial, _ = _controller(tmp_path, camera=camera)
    controller.configure(_configuration(tmp_path))
    worker = controller.start_async()
    assert camera.entered.wait(1)
    controller.global_stop()
    camera.release.set()
    worker.join(1)

    assert "CMD:START" not in serial.commands
    assert camera.stops == 1


def test_global_stop_waits_for_in_flight_start_write_then_sends_stop(tmp_path: Path) -> None:
    class BlockingStartSerial(FakeSerial):
        def __init__(self) -> None:
            super().__init__()
            self.start_write_entered = Event()
            self.release_start_write = Event()

        def send_command(self, command: str, **kwargs: object):
            if command == "CMD:START":
                self.start_write_entered.set()
                assert self.release_start_write.wait(1)
            return super().send_command(command, **kwargs)

    serial = BlockingStartSerial()
    controller, _, camera = _controller(tmp_path, serial=serial)
    controller.configure(_configuration(tmp_path))
    start_worker = controller.start_async()
    assert serial.start_write_entered.wait(1)
    stop_worker = Thread(target=controller.global_stop)
    stop_worker.start()

    serial.release_start_write.set()
    start_worker.join(1)
    stop_worker.join(1)

    assert serial.commands[-2:] == ["CMD:START", "CMD:STOP"]
    assert controller.snapshot.lifecycle.completion is RunCompletion.ABORTED
    assert camera.stops == 1


def test_watchdog_faults_run_after_expected_duration_plus_grace(tmp_path: Path) -> None:
    controller, _, _ = _controller(tmp_path)
    controller.configure(
        _configuration(
            tmp_path,
            cycles=1,
            on_milliseconds=1,
            off_milliseconds=1,
            timeout_grace_seconds=0.01,
        )
    )
    controller.start()
    for _ in range(30):
        if controller.snapshot.lifecycle.completion is RunCompletion.FAULTED:
            break
        sleep(0.01)
    assert controller.snapshot.lifecycle.completion is RunCompletion.FAULTED
