"""Thread-marshalling Qt bridge for the camera panel presenter."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from soft_actuator_testing.application.camera_capture import (
    CameraPanelPresenter,
    CameraPanelSnapshot,
)


class CameraPresenterBridge(QObject):
    """Deliver Qt-free presenter publications on the receiving Qt thread."""

    snapshot_received = Signal(object)

    def __init__(
        self,
        presenter: CameraPanelPresenter,
        render: Callable[[CameraPanelSnapshot], None],
        *,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._presenter = presenter
        self.snapshot_received.connect(render)
        self._subscription = presenter.state.subscribe(self.snapshot_received.emit)
        self.destroyed.connect(self._subscription.dispose)
        self.snapshot_received.emit(presenter.state.snapshot)

    def dispose(self) -> None:
        self._subscription.dispose()


__all__ = ["CameraPresenterBridge"]
