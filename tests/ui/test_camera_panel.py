from __future__ import annotations

from pathlib import Path
from time import monotonic

from PySide6.QtCore import Qt

from soft_actuator_testing.application.camera_capture import (
    CameraCaptureService,
    CameraDevice,
    CameraPanelPresenter,
    CaptureHealth,
    CapturePhase,
    CaptureResult,
    LatestFrameChannel,
    PreviewFrame,
)
from soft_actuator_testing.infrastructure.camera import FakeCameraDeviceSource
from soft_actuator_testing.ui.widgets.camera_panel import CameraPanel


class PanelBackend:
    def __init__(self) -> None:
        self.frame_channel: LatestFrameChannel[PreviewFrame] = LatestFrameChannel()
        self.health = CaptureHealth()
        self.result = None
        self.close_timeouts = []

    def start(self, output_directory, device_identifier, *, readiness_timeout):
        del output_directory, device_identifier, readiness_timeout
        self.health = CaptureHealth(phase=CapturePhase.RECORDING, ready=True)

    def stop(self, reason="operator", *, timeout=None):
        del timeout
        self.health = CaptureHealth(phase=CapturePhase.COMPLETED)
        self.result = CaptureResult(
            reason,
            Path("video.mkv"),
            Path("video.partial.mkv"),
            True,
            True,
            self.health,
        )
        return self.result

    def close(self, *, timeout=None):
        self.close_timeouts.append(timeout)
        return self.stop("close")


def test_camera_panel_renders_presenter_status_and_preview(qtbot, tmp_path: Path) -> None:
    backend = PanelBackend()
    presenter = CameraPanelPresenter(
        FakeCameraDeviceSource([CameraDevice("fake-0", "Synthetic camera", "fake")]),
        CameraCaptureService(backend),
    )
    panel = CameraPanel(
        presenter,
        output_directory_provider=lambda: tmp_path / "run",
        poll_interval_ms=10,
    )
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitUntil(lambda: panel.device_selector.count() == 1)
    assert panel.start_button.isEnabled()

    qtbot.mouseClick(panel.start_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: presenter.state.snapshot.health.phase is CapturePhase.RECORDING)
    assert panel.stop_button.isEnabled()

    backend.frame_channel.publish(
        PreviewFrame(
            0,
            2,
            1,
            b"\xff\x00\x00\x00\xff\x00",
            monotonic(),
        )
    )
    qtbot.waitUntil(lambda: presenter.state.snapshot.preview is not None)
    assert "dropped" in panel.preview.accessibleDescription()

    qtbot.mouseClick(panel.stop_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: presenter.state.snapshot.health.phase is CapturePhase.COMPLETED)
    qtbot.waitUntil(lambda: panel.status.accessibleName().endswith("Success"))

    panel.close()
    assert backend.close_timeouts == [10.0]
