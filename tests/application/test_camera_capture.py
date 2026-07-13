from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest

from soft_actuator_testing.application.camera_capture import (
    CameraCaptureService,
    CameraDevice,
    CameraMode,
    CameraPanelPresenter,
    CaptureError,
    CaptureHealth,
    CapturePhase,
    CaptureResult,
    LatestFrameChannel,
    PreviewFrame,
)
from soft_actuator_testing.infrastructure.camera import FakeCameraDeviceSource


class FakeBackend:
    def __init__(self) -> None:
        self.frame_channel: LatestFrameChannel[PreviewFrame] = LatestFrameChannel()
        self.health = CaptureHealth()
        self.result: CaptureResult | None = None
        self.starts: list[tuple[Path, str]] = []
        self.stops: list[str] = []

    def start(
        self,
        output_directory: Path,
        device_identifier: str,
        *,
        readiness_timeout: float,
    ) -> None:
        del readiness_timeout
        self.starts.append((output_directory, device_identifier))
        self.health = CaptureHealth(phase=CapturePhase.RECORDING, ready=True)

    def stop(self, reason: str = "operator", *, timeout: float | None = None) -> CaptureResult:
        del timeout
        self.stops.append(reason)
        self.health = CaptureHealth(phase=CapturePhase.COMPLETED)
        if self.result is None:
            self.result = CaptureResult(
                completion_reason=reason,
                video_path=Path("video.mkv"),
                partial_path=Path("video.partial.mkv"),
                readable=True,
                clean=True,
                health=self.health,
            )
        return self.result

    def close(self, *, timeout: float | None = None) -> CaptureResult | None:
        del timeout
        return self.stop("close")


def _wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = monotonic() + timeout
    while not predicate() and monotonic() < deadline:
        sleep(0.002)
    assert predicate()


def test_latest_frame_channel_is_bounded_and_reports_slow_consumer() -> None:
    channel: LatestFrameChannel[int] = LatestFrameChannel()
    for value in range(100):
        channel.publish(value)
    assert channel.consume_latest() == 99
    assert channel.consume_latest() is None
    assert channel.stats.produced == 100
    assert channel.stats.replaced_stale == 99
    assert channel.stats.consumed == 1


def test_timed_capture_service_stops_after_duration() -> None:
    backend = FakeBackend()
    preflights: list[tuple[Path, float | None]] = []
    service = CameraCaptureService(
        backend,
        storage_preflight=lambda path, duration: preflights.append((path, duration)),
    )
    service.start_capture(
        Path("run"),
        "fake-camera",
        duration_seconds=0.01,
    )
    _wait_until(lambda: backend.stops == ["duration"])
    assert backend.starts == [(Path("run"), "fake-camera")]
    assert preflights == [(Path("run"), 0.01)]


def test_capture_service_rejects_duplicate_start_without_replacing_active_timer() -> None:
    backend = FakeBackend()
    service = CameraCaptureService(backend)
    service.start_capture(Path("run"), "fake-camera", duration_seconds=1)

    with pytest.raises(CaptureError, match="already active"):
        service.start_capture(Path("other"), "fake-camera")

    assert backend.starts == [(Path("run"), "fake-camera")]
    service.stop_capture()


def test_preflight_failure_does_not_leave_start_latched() -> None:
    backend = FakeBackend()
    attempts = 0

    def preflight(path: Path, duration: float | None) -> None:
        nonlocal attempts
        del path, duration
        attempts += 1
        if attempts == 1:
            raise OSError("storage unavailable")

    service = CameraCaptureService(backend, storage_preflight=preflight)
    with pytest.raises(OSError, match="storage unavailable"):
        service.start_capture(Path("run"), "fake-camera")

    service.start_capture(Path("run"), "fake-camera")
    assert backend.starts == [(Path("run"), "fake-camera")]
    service.stop_capture()


def test_standalone_capture_reserves_unique_workspace_output_and_status(tmp_path: Path) -> None:
    backend = FakeBackend()
    service = CameraCaptureService(backend)

    first = service.reserve_standalone_capture(tmp_path)
    second = service.reserve_standalone_capture(tmp_path)

    assert first.output_directory != second.output_directory
    assert first.output_directory.parent == tmp_path / "runs"
    assert not (tmp_path / "runs" / "video.mkv").exists()
    assert json.loads(first.status_path.read_text())["state"] == "reserved"

    service.start_capture(first, "fake-camera")
    result = service.stop_capture("controller cyclic completion")

    status = json.loads(first.status_path.read_text())
    assert backend.starts == [(first.output_directory, "fake-camera")]
    assert result.clean
    assert status["state"] == "completed"
    assert status["completion_reason"] == "controller cyclic completion"
    assert status["video_path"] == "video.mkv"


def test_mode_probe_rejects_unsupported_camera_before_start() -> None:
    backend = FakeBackend()
    presenter = CameraPanelPresenter(
        FakeCameraDeviceSource(
            [
                CameraDevice(
                    "fake-0",
                    "Low resolution camera",
                    "fake",
                    modes=(CameraMode(1920, 1080, 30, "mjpeg"),),
                )
            ]
        ),
        CameraCaptureService(backend),
    )

    presenter.refresh_devices()

    snapshot = presenter.state.snapshot
    assert not snapshot.can_start
    assert "does not advertise 3840x2160@60" in snapshot.error
    assert backend.starts == []


def test_presenter_applies_a_probed_target_mode_before_start() -> None:
    class ModeBackend(FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.configured_modes: list[CameraMode] = []

        def configure_input_mode(self, mode: CameraMode) -> None:
            self.configured_modes.append(mode)

    mode = CameraMode(3840, 2160, 60, "mjpeg")
    backend = ModeBackend()
    presenter = CameraPanelPresenter(
        FakeCameraDeviceSource([CameraDevice("fake-0", "4K camera", "fake", (mode,))]),
        CameraCaptureService(backend),
    )
    presenter.refresh_devices()
    assert backend.configured_modes == [mode]

    presenter.start_capture(Path("run"), readiness_timeout=0.1)
    _wait_until(lambda: bool(backend.starts))

    assert backend.configured_modes == [mode]
    presenter.stop_capture()


def test_camera_panel_presenter_discovers_starts_polls_and_stops() -> None:
    backend = FakeBackend()
    service = CameraCaptureService(backend)
    devices = FakeCameraDeviceSource(
        [CameraDevice("fake-0", "Synthetic 4K60 camera", "fake")]
    )
    presenter = CameraPanelPresenter(devices, service)
    presenter.refresh_devices()
    assert presenter.state.snapshot.selected_device == "fake-0"
    assert presenter.state.snapshot.target_profile.label == "3840x2160@60"

    presenter.start_capture(Path("run"), readiness_timeout=0.1)
    _wait_until(lambda: bool(backend.starts))
    presenter.refresh_status()
    assert presenter.state.snapshot.health.phase is CapturePhase.RECORDING
    assert presenter.state.snapshot.can_stop

    backend.frame_channel.publish(
        PreviewFrame(
            index=3,
            width=2,
            height=1,
            rgb_bytes=b"\x00\x01\x02\x03\x04\x05",
            captured_monotonic=monotonic(),
        )
    )
    presenter.refresh_status()
    assert presenter.state.snapshot.preview is not None
    assert presenter.state.snapshot.preview.index == 3

    presenter.stop_capture()
    _wait_until(lambda: backend.stops == ["operator"])


def test_stop_cancels_a_capture_that_is_still_starting() -> None:
    class StartingBackend(FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.started = Event()
            self.stop_requested = Event()

        def start(self, output_directory, device_identifier, *, readiness_timeout):
            del output_directory, device_identifier, readiness_timeout
            self.health = CaptureHealth(phase=CapturePhase.STARTING)
            self.started.set()
            self.stop_requested.wait(1)

        def stop(self, reason="operator", *, timeout=None):
            self.stop_requested.set()
            return super().stop(reason, timeout=timeout)

    backend = StartingBackend()
    presenter = CameraPanelPresenter(
        FakeCameraDeviceSource([CameraDevice("fake-0", "Synthetic camera", "fake")]),
        CameraCaptureService(backend),
    )
    presenter.refresh_devices()
    presenter.start_capture(Path("run"), readiness_timeout=1)
    assert backend.started.wait(0.5)

    presenter.stop_capture()

    assert backend.stop_requested.wait(0.5)
    _wait_until(lambda: presenter.state.snapshot.health.phase is CapturePhase.COMPLETED)
