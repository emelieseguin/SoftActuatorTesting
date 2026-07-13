"""Hardware-free tests for the production cyclic-run coordinator."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Event, Thread
from time import sleep

import pytest

from soft_actuator_testing.application.camera_capture import (
    CameraMode,
    CaptureEvidence,
    CaptureHealth,
    CapturePhase,
    CaptureResult,
    LatestFrameStats,
    NegotiatedCaptureProfile,
    TARGET_4K60,
)
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


class _FakeFrameSubscription:
    def __init__(self, serial: "FakeSerial") -> None:
        self._serial = serial
        self.closed = False

    def drain(self) -> tuple[object, ...]:
        result = tuple(self._serial.frames)
        self._serial.frames.clear()
        return result

    def close(self) -> None:
        self.closed = True


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

    def subscribe_frames(self, *_args: object, **_kwargs: object) -> _FakeFrameSubscription:
        return _FakeFrameSubscription(self)

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


class EvidenceCamera(FakeCamera):
    """Fake camera exposing the complete public capture-evidence contract."""

    def __init__(
        self,
        *,
        clean: bool = True,
        readable: bool = True,
        promoted: bool = True,
        error: str = "",
        retain_partial: bool = True,
    ) -> None:
        evidence = CaptureEvidence(
            startup_proven=True,
            cooperative_shutdown=True,
            process_exit_code=0,
            drainers_stopped=True,
            verification_readable=readable,
            promoted=promoted,
            shutdown_escalated=False,
        )
        health = CaptureHealth(
            phase=CapturePhase.RECORDING,
            frame=240,
            fps=59.94,
            speed=1.01,
            output_time_us=4_000_000,
            output_bytes=12_345_678,
            duplicate_frames=2,
            dropped_frames=3,
            malformed_progress_lines=1,
            negotiated_profile=NegotiatedCaptureProfile(3840, 2160, 60.0, "nv12", "rawvideo"),
            encoder="h264_nvenc",
            preview=LatestFrameStats(produced=40, consumed=12, replaced_stale=28, maximum_age_seconds=0.1),
            warnings=("capture warning",),
            ready=True,
            evidence=evidence,
        )
        super().__init__()
        self.health = health
        self.clean = clean
        self.readable = readable
        self.promoted = promoted
        self.error = error
        self.retain_partial = retain_partial
        self.result: CaptureResult | None = None
        self.backend = "v4l2"
        self.input_mode = CameraMode(3840, 2160, 60.0, "nv12")
        self.ffmpeg_version = "ffmpeg version 7.1"
        self.ffmpeg_build = "test build"
        self.command: tuple[str, ...] = ()

    def stop_capture(self, reason: str) -> CaptureResult:
        self.stops += 1
        directory = self.started[-1][0]
        partial = directory / "video.partial.mkv"
        video = directory / "video.mkv"
        if self.clean and self.promoted:
            video.write_bytes(b"finalized-video")
            video_path: Path | None = video
        elif self.retain_partial:
            partial.write_bytes(b"partial-video")
            video_path = None
        else:
            video_path = None
        evidence = self.health.evidence
        terminal_health = CaptureHealth(
            **{
                **self.health.__dict__,
                "phase": CapturePhase.COMPLETED if self.clean else CapturePhase.FAULT,
                "ready": False,
                "clean": self.clean,
                "evidence": evidence,
            }
        )
        self.health = terminal_health
        self.result = CaptureResult(
            reason,
            video_path,
            partial,
            self.readable,
            self.clean,
            terminal_health,
            self.error,
            evidence,
        )
        return self.result


class StartupFailureCamera(EvidenceCamera):
    def start_capture(self, output_directory: Path, device_identifier: str, *, duration_seconds=None) -> None:
        self.started.append((output_directory, device_identifier))
        partial = output_directory / "video.partial.mkv"
        partial.write_bytes(b"startup-partial")
        evidence = CaptureEvidence(
            startup_proven=False,
            cooperative_shutdown=False,
            process_exit_code=1,
            drainers_stopped=True,
            verification_readable=False,
            promoted=False,
            shutdown_escalated=False,
        )
        self.health = CaptureHealth(
            **{
                **self.health.__dict__,
                "phase": CapturePhase.FAULT,
                "ready": False,
                "clean": False,
                "evidence": evidence,
            }
        )
        self.result = CaptureResult(
            "startup-failure",
            None,
            partial,
            False,
            False,
            self.health,
            "camera unavailable",
            evidence,
        )
        raise RuntimeError("camera unavailable")


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


def test_run_manifest_serializes_complete_capture_evidence_without_private_command_data(tmp_path: Path) -> None:
    camera = EvidenceCamera()
    controller, _, _ = _controller(tmp_path, camera=camera)
    controller.configure(_configuration(tmp_path))
    controller.start()
    assert camera.started
    camera.command = (
        "/opt/private/ffmpeg",
        "-i",
        "/home/operator/private-camera-input",
        "-password",
        "do-not-persist",
        "token=also-do-not-persist",
        str(camera.started[-1][0] / "video.partial.mkv"),
    )

    result = controller.complete()

    assert result.manifest_path is not None
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))["payload"]
    capture = payload["capture"]
    assert capture["status"] == "completed"
    assert capture["requested_target"] == {
        "width": 3840,
        "height": 2160,
        "fps": 60,
        "label": "3840x2160@60",
    }
    assert capture["selected_device"] == {
        "identifier": "fake-camera",
        "backend": "v4l2",
        "mode": {"width": 3840, "height": 2160, "fps": 60.0, "pixel_format": "nv12"},
    }
    assert capture["encoder"] == "h264_nvenc"
    assert capture["ffmpeg"]["version"] == "ffmpeg version 7.1"
    assert capture["ffmpeg"]["build"] == "test build"
    assert capture["ffmpeg"]["command"] == [
        "ffmpeg",
        "-i",
        "<external-path>",
        "-password",
        "<redacted>",
        "token=<redacted>",
        f"runs/{result.manifest_path.parent.name}/video.partial.mkv",
    ]
    assert "do-not-persist" not in json.dumps(capture)
    assert str(tmp_path) not in json.dumps(capture)
    assert capture["startup"] == {
        "proven": True,
        "proof_at": None,
        "components": {
            "negotiated_profile": True,
            "progress": True,
            "output_file": True,
            "preview": True,
        },
    }
    assert capture["negotiated"] == {
        "width": 3840,
        "height": 2160,
        "fps": 60.0,
        "pixel_format": "nv12",
        "codec": "rawvideo",
    }
    assert capture["progress"] == {
        "frames": 240,
        "fps": 59.94,
        "speed": 1.01,
        "output_time_us": 4_000_000,
        "file_size_bytes": 12_345_678,
        "duplicate_frames": 2,
        "dropped_frames": 3,
        "malformed_progress_lines": 1,
    }
    assert capture["preview"] == {
        "received_frames": 40,
        "consumed_frames": 12,
        "dropped_frames": 28,
        "latest_timestamp": None,
        "rate_fps": None,
        "profile": None,
        "maximum_age_seconds": 0.1,
    }
    assert capture["termination"] == {
        "controller_reason": "controller completion",
        "stop_reason": "controller completion",
        "failure": None,
        "clean": True,
        "cooperative_shutdown": True,
        "process_exit_code": 0,
        "drainers_stopped": True,
        "shutdown_escalated": False,
    }
    assert capture["verification"] == {
        "readable": True,
        "evidence_readable": True,
        "duration_seconds": None,
        "frame_count": None,
        "streams": None,
    }
    assert capture["paths"] == {
        "partial_path": None,
        "final_path": f"runs/{result.manifest_path.parent.name}/video.mkv",
    }
    assert capture["promotion"] == {"promoted": True, "outcome": "promoted"}
    assert payload["camera"]["finalized_video"] == capture["paths"]["final_path"]
    assert payload["output_files"] == [
        f"runs/{result.manifest_path.parent.name}/pressure.csv",
        f"runs/{result.manifest_path.parent.name}/video.mkv",
    ]


def test_unclean_capture_retains_partial_evidence_and_faults_requested_stop(tmp_path: Path) -> None:
    camera = EvidenceCamera(clean=False, readable=True, promoted=False, error="ffprobe rejected recording")
    controller, _, _ = _controller(tmp_path, camera=camera)
    controller.configure(_configuration(tmp_path))
    controller.start()

    result = controller.stop()

    assert result.completion is RunCompletion.FAULTED
    assert result.manifest_path is not None
    capture = json.loads(result.manifest_path.read_text(encoding="utf-8"))["payload"]["capture"]
    assert capture["status"] == "retained_partial"
    assert capture["termination"]["stop_reason"] == "operator stop"
    assert capture["termination"]["failure"] == "ffprobe rejected recording"
    assert capture["paths"]["final_path"] is None
    assert capture["paths"]["partial_path"] == f"runs/{result.manifest_path.parent.name}/video.partial.mkv"
    assert capture["promotion"] == {"promoted": False, "outcome": "retained_partial"}
    assert (result.manifest_path.parent / "video.partial.mkv").is_file()


def test_unclean_capture_without_a_partial_file_records_failed_not_retained(tmp_path: Path) -> None:
    camera = EvidenceCamera(
        clean=False,
        readable=False,
        promoted=False,
        error="FFmpeg produced no partial recording",
        retain_partial=False,
    )
    controller, _, _ = _controller(tmp_path, camera=camera)
    controller.configure(_configuration(tmp_path))
    controller.start()

    result = controller.stop()

    assert result.manifest_path is not None
    capture = json.loads(result.manifest_path.read_text(encoding="utf-8"))["payload"]["capture"]
    assert capture["status"] == "failed"
    assert capture["paths"]["partial_path"] is None
    assert capture["promotion"] == {"promoted": False, "outcome": "not_promoted"}


def test_capture_startup_failure_preserves_result_evidence_before_manifest_finalization(tmp_path: Path) -> None:
    camera = StartupFailureCamera()
    controller, _, _ = _controller(tmp_path, camera=camera)
    controller.configure(_configuration(tmp_path))

    with pytest.raises(RuntimeError, match="camera unavailable"):
        controller.start()

    result = controller.finalization_result
    assert result is not None and result.manifest_path is not None
    capture = json.loads(result.manifest_path.read_text(encoding="utf-8"))["payload"]["capture"]
    assert capture["status"] == "retained_partial"
    assert capture["startup"]["proven"] is False
    assert capture["termination"]["failure"] == "camera unavailable"
    assert capture["verification"]["readable"] is False
    assert capture["paths"]["partial_path"] == f"runs/{result.manifest_path.parent.name}/video.partial.mkv"
    assert capture["promotion"] == {"promoted": False, "outcome": "retained_partial"}


def test_disabled_capture_manifest_uses_explicit_unknown_evidence_values(tmp_path: Path) -> None:
    controller = RunController(
        serial=FakeSerial(),
        camera=None,
        storage=ArtifactFileStore(tmp_path),
    )
    controller.configure(_configuration(tmp_path, record_video=False, camera_device=""))
    controller.start()

    result = controller.global_stop()

    assert result.manifest_path is not None
    capture = json.loads(result.manifest_path.read_text(encoding="utf-8"))["payload"]["capture"]
    assert capture["status"] == "disabled"
    assert capture["selected_device"] == {"identifier": None, "backend": None, "mode": None}
    assert capture["encoder"] is None
    assert capture["ffmpeg"] == {"command": None, "version": None, "build": None}
    assert capture["startup"]["proven"] is None
    assert capture["negotiated"] is None
    assert capture["progress"]["frames"] is None
    assert capture["preview"]["latest_timestamp"] is None
    assert capture["verification"] == {
        "readable": None,
        "evidence_readable": None,
        "duration_seconds": None,
        "frame_count": None,
        "streams": None,
    }
    assert capture["paths"] == {"partial_path": None, "final_path": None}
    assert capture["promotion"] == {"promoted": None, "outcome": "not_started"}


def test_camera_unavailable_before_start_writes_a_manifest_with_no_invented_capture_paths(tmp_path: Path) -> None:
    class UnavailableCamera(FakeCamera):
        def start_capture(self, output_directory: Path, device_identifier: str, *, duration_seconds=None) -> None:
            raise RuntimeError("camera unavailable")

        def stop_capture(self, reason: str) -> CaptureResult:
            raise RuntimeError("camera did not start")

    controller, _, _ = _controller(tmp_path, camera=UnavailableCamera())
    controller.configure(_configuration(tmp_path))

    with pytest.raises(RuntimeError, match="camera unavailable"):
        controller.start()

    result = controller.finalization_result
    assert result is not None and result.manifest_path is not None
    capture = json.loads(result.manifest_path.read_text(encoding="utf-8"))["payload"]["capture"]
    assert capture["status"] == "unavailable"
    assert capture["paths"] == {"partial_path": None, "final_path": None}
    assert capture["termination"]["failure"] is None
    assert capture["verification"]["readable"] is None


def test_capture_with_missing_optional_contract_fields_persists_null_not_defaults(tmp_path: Path) -> None:
    controller, _, _ = _controller(tmp_path, camera=FakeCamera())
    controller.configure(_configuration(tmp_path))
    controller.start()

    result = controller.complete()

    assert result.manifest_path is not None
    capture = json.loads(result.manifest_path.read_text(encoding="utf-8"))["payload"]["capture"]
    assert capture["encoder"] is None
    assert capture["ffmpeg"] == {"command": None, "version": None, "build": None}
    assert capture["selected_device"]["backend"] is None
    assert capture["selected_device"]["mode"] is None
    assert capture["negotiated"] is None
    assert capture["verification"]["duration_seconds"] is None
    assert capture["verification"]["frame_count"] is None
    assert capture["verification"]["streams"] is None


@pytest.mark.parametrize(
    ("method", "expected_reason"),
    [("complete", "controller completion"), ("stop", "operator stop")],
)
def test_controller_terminal_reason_is_linked_to_capture_stop_evidence(
    tmp_path: Path,
    method: str,
    expected_reason: str,
) -> None:
    camera = EvidenceCamera()
    controller, _, _ = _controller(tmp_path, camera=camera)
    controller.configure(_configuration(tmp_path))
    controller.start()

    result = getattr(controller, method)()

    assert result.manifest_path is not None
    capture = json.loads(result.manifest_path.read_text(encoding="utf-8"))["payload"]["capture"]
    assert capture["termination"]["controller_reason"] == expected_reason
    assert capture["termination"]["stop_reason"] == expected_reason


def test_repeated_finalize_keeps_one_immutable_capture_manifest(tmp_path: Path) -> None:
    camera = EvidenceCamera()
    controller, _, _ = _controller(tmp_path, camera=camera)
    controller.configure(_configuration(tmp_path))
    controller.start()

    first = controller.stop()
    assert first.manifest_path is not None
    content = first.manifest_path.read_bytes()
    second = controller.stop()

    assert second.idempotent
    assert second.manifest_path == first.manifest_path
    assert second.manifest_path.read_bytes() == content
    assert camera.stops == 1


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


def test_stop_during_artifact_reservation_publishes_one_complete_terminal_manifest(tmp_path: Path) -> None:
    class BlockingStore(ArtifactFileStore):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.entered = Event()
            self.release = Event()

        def begin_run_artifacts(self, *, run_id: str | None = None, software_version: str | None = None):
            self.entered.set()
            assert self.release.wait(1)
            return super().begin_run_artifacts(run_id=run_id, software_version=software_version)

    store = BlockingStore(tmp_path)
    camera = EvidenceCamera()
    controller = RunController(serial=FakeSerial(), camera=camera, storage=store)  # type: ignore[arg-type]
    controller.configure(_configuration(tmp_path))
    worker = controller.start_async()
    assert store.entered.wait(1)

    initial = controller.global_stop()
    assert initial.manifest_path is None
    store.release.set()
    worker.join(1)

    result = controller.finalization_result
    assert result is not None and result.manifest_path is not None
    original = result.manifest_path.read_bytes()
    payload = json.loads(original)["payload"]
    assert payload["completion"] == "aborted"
    assert payload["experiment"]["name"] == "cyclic validation"
    assert payload["capture"]["status"] == "unavailable"
    assert controller.stop().manifest_path == result.manifest_path
    assert result.manifest_path.read_bytes() == original


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
