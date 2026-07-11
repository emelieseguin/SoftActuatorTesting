"""Reusable presenter-backed camera controls and live preview."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSignalBlocker, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from soft_actuator_testing.application.camera_capture import (
    CameraPanelPresenter,
    CameraPanelSnapshot,
    CapturePhase,
)
from soft_actuator_testing.ui.presenters.camera import CameraPresenterBridge
from soft_actuator_testing.ui.themes import DARK_THEME, SemanticState, Theme
from soft_actuator_testing.ui.themes.qt_bridge import apply_theme_to_widget

from .controls import AccessibleButton
from .status import StatusIndicator
from .video_canvas import VideoCanvas

OutputDirectoryProvider = Callable[[], Path]


class CameraPanel(QWidget):
    """Camera discovery/start/stop/preview surface for later page composition."""

    def __init__(
        self,
        presenter: CameraPanelPresenter,
        *,
        output_directory_provider: OutputDirectoryProvider | None = None,
        poll_interval_ms: int = 100,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.presenter = presenter
        self._output_directory_provider = output_directory_provider
        self.setAccessibleName("Camera control and preview")

        layout = QVBoxLayout(self)
        controls = QGridLayout()
        self.device_selector = QComboBox(self)
        self.device_selector.setAccessibleName("Camera device")
        self.device_selector.currentIndexChanged.connect(self._device_selected)
        self.refresh_button = AccessibleButton("Refresh cameras", parent=self)
        self.refresh_button.clicked.connect(self.presenter.refresh_devices)
        self.duration = QSpinBox(self)
        self.duration.setRange(0, 86_400)
        self.duration.setSpecialValueText("Until stopped")
        self.duration.setSuffix(" s")
        self.duration.setAccessibleName("Capture duration")
        self.start_button = AccessibleButton("Start capture", parent=self)
        self.start_button.clicked.connect(self._start)
        self.stop_button = AccessibleButton("Stop capture", parent=self)
        self.stop_button.clicked.connect(lambda: self.presenter.stop_capture("operator"))
        controls.addWidget(QLabel("Camera", self), 0, 0)
        controls.addWidget(self.device_selector, 0, 1)
        controls.addWidget(self.refresh_button, 0, 2)
        controls.addWidget(QLabel("Duration", self), 1, 0)
        controls.addWidget(self.duration, 1, 1)
        controls.addWidget(self.start_button, 1, 2)
        controls.addWidget(self.stop_button, 1, 3)
        layout.addLayout(controls)

        self.status = StatusIndicator("Camera capture", parent=self)
        self.health_text = QLabel(self)
        self.health_text.setWordWrap(True)
        self.health_text.setAccessibleName("Camera capture health")
        layout.addWidget(self.status)
        layout.addWidget(self.health_text)
        self.preview = VideoCanvas(accessible_title="Live camera preview", parent=self)
        layout.addWidget(self.preview, 1)

        self._bridge = CameraPresenterBridge(presenter, self.render_snapshot, parent=self)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(poll_interval_ms)
        self._poll_timer.timeout.connect(self.presenter.refresh_status)
        self._poll_timer.start()
        self.apply_theme(DARK_THEME)
        self.presenter.refresh_devices()

    def render_snapshot(self, snapshot: CameraPanelSnapshot) -> None:
        selected = snapshot.selected_device
        with QSignalBlocker(self.device_selector):
            self.device_selector.clear()
            for device in snapshot.devices:
                self.device_selector.addItem(device.name, device.identifier)
            selected_index = self.device_selector.findData(selected)
            if selected_index >= 0:
                self.device_selector.setCurrentIndex(selected_index)

        self.start_button.setEnabled(
            snapshot.can_start and self._output_directory_provider is not None
        )
        self.stop_button.setEnabled(snapshot.can_stop)
        phase_state = {
            CapturePhase.IDLE: SemanticState.NEUTRAL,
            CapturePhase.STARTING: SemanticState.INFO,
            CapturePhase.READY: SemanticState.SUCCESS,
            CapturePhase.RECORDING: SemanticState.INFO,
            CapturePhase.STOPPING: SemanticState.INFO,
            CapturePhase.COMPLETED: SemanticState.SUCCESS,
            CapturePhase.FAULT: SemanticState.ERROR,
        }[snapshot.health.phase]
        self.status.set_state(phase_state)
        detail = snapshot.status_text
        if snapshot.error:
            detail = f"{detail} {snapshot.error}"
        self.health_text.setText(detail)
        self.health_text.setAccessibleDescription(detail)
        if snapshot.preview is not None:
            frame = np.frombuffer(snapshot.preview.rgb_bytes, dtype=np.uint8).reshape(
                snapshot.preview.height,
                snapshot.preview.width,
                3,
            )
            self.preview.set_frame(
                frame,
                frame_index=snapshot.preview.index,
                description=(
                    f"{snapshot.target_profile.label}; "
                    f"{snapshot.health.dropped_frames} dropped frames reported"
                ),
            )

    def apply_theme(self, theme: Theme) -> None:
        apply_theme_to_widget(self, theme)
        for control in (self.refresh_button, self.start_button, self.stop_button, self.status):
            control.apply_theme(theme)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        self._poll_timer.stop()
        if not self.presenter.close(timeout=10.0):
            self._poll_timer.start()
            event.ignore()
            return
        self._bridge.dispose()
        super().closeEvent(event)

    def _device_selected(self, index: int) -> None:
        identifier = self.device_selector.itemData(index)
        if identifier:
            self.presenter.select_device(str(identifier))

    def _start(self) -> None:
        if self._output_directory_provider is None:
            return
        duration = self.duration.value()
        self.presenter.start_capture(
            self._output_directory_provider(),
            duration_seconds=float(duration) if duration else None,
        )


__all__ = ["CameraPanel", "OutputDirectoryProvider"]
