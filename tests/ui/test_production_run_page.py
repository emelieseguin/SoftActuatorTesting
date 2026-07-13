"""Hardware-free pytest-qt regressions for ``ProductionLiveRunPage``.

See ``docs/architecture/quality-ui-accessibility.md`` for the audit finding
this file regresses: unlike every other page/widget in the codebase that
owns a ``QTimer``/``QThread``, ``ProductionLiveRunPage`` previously had no
``closeEvent``/``destroyed`` cleanup for its 50ms refresh timer.
"""

from __future__ import annotations

import json
from pathlib import Path

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
from soft_actuator_testing.application.run_controller import RunController
from soft_actuator_testing.application.serial_controller import SerialController
from soft_actuator_testing.infrastructure.camera import FakeCameraDeviceSource
from soft_actuator_testing.ui.views.production_run import (
    ProductionConnectionsPage,
    ProductionLiveRunPage,
)


class _StandaloneCameraBackend:
    def __init__(self) -> None:
        self.frame_channel: LatestFrameChannel[PreviewFrame] = LatestFrameChannel()
        self.health = CaptureHealth()
        self.result: CaptureResult | None = None
        self.starts: list[tuple[Path, str]] = []

    def start(self, output_directory: Path, device_identifier: str, *, readiness_timeout: float) -> None:
        del readiness_timeout
        self.starts.append((output_directory, device_identifier))
        self.health = CaptureHealth(phase=CapturePhase.RECORDING, ready=True)

    def stop(self, reason: str = "operator", *, timeout: float | None = None) -> CaptureResult:
        del timeout
        self.health = CaptureHealth(phase=CapturePhase.COMPLETED)
        directory = self.starts[-1][0]
        self.result = CaptureResult(
            reason,
            directory / "video.mkv",
            directory / "video.partial.mkv",
            True,
            True,
            self.health,
        )
        return self.result

    def close(self, *, timeout: float | None = None) -> CaptureResult | None:
        return self.stop("close", timeout=timeout) if self.starts else None


def test_close_stops_the_refresh_timer(qtbot) -> None:
    run = RunController()
    page = ProductionLiveRunPage(run)
    qtbot.addWidget(page)

    assert page._timer.isActive()

    page.close()

    assert not page._timer.isActive()


def test_destroyed_fallback_stops_the_refresh_timer_when_embedded(qtbot) -> None:
    """A page embedded in a shell's stack never receives its own closeEvent.

    The ``destroyed`` fallback guarantees the timer is stopped once the page
    object itself is actually destroyed (for example via a parent's deletion
    cascade), even if nothing ever calls ``.close()`` on it directly. This is
    observed by capturing the timer's active state from a second
    ``destroyed`` slot connected *after* the page's own fallback, so it
    always runs after the fallback has had a chance to stop the timer, while
    both objects are still alive (synchronous Qt signal delivery).
    """

    from PySide6.QtWidgets import QVBoxLayout, QWidget

    run = RunController()
    parent = QWidget()
    QVBoxLayout(parent)
    page = ProductionLiveRunPage(run, parent=parent)
    parent.layout().addWidget(page)
    qtbot.addWidget(parent)

    timer = page._timer
    assert timer.isActive()

    observed: list[bool] = []
    page.destroyed.connect(lambda: observed.append(timer.isActive()))

    parent.deleteLater()
    qtbot.wait(50)

    assert observed == [False]


def test_connections_camera_reserves_unique_workspace_capture_directories(qtbot, tmp_path: Path) -> None:
    backend = _StandaloneCameraBackend()
    presenter = CameraPanelPresenter(
        FakeCameraDeviceSource([CameraDevice("fake-0", "Synthetic camera", "fake")]),
        CameraCaptureService(backend),
    )
    page = ProductionConnectionsPage(
        SerialController(),
        presenter,
        workspace=lambda: tmp_path,
        camera_auto_refresh=False,
    )
    qtbot.addWidget(page)
    presenter.refresh_devices()

    page.camera_panel._start()
    qtbot.waitUntil(lambda: len(backend.starts) == 1)
    first = backend.starts[0][0]
    page.camera_panel.presenter.stop_capture()
    qtbot.waitUntil(lambda: backend.result is not None)

    page.camera_panel._start()
    qtbot.waitUntil(lambda: len(backend.starts) == 2)
    second = backend.starts[1][0]

    assert first != second
    assert first.parent == second.parent == tmp_path / "runs"
    assert not (tmp_path / "runs" / "video.mkv").exists()
    status = json.loads(first.joinpath("capture-status.json").read_text())
    assert status["state"] == "completed"
    assert status["video_path"] == "video.mkv"
