"""Production-only Qt views bound directly to the Qt-free run coordinator."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QCheckBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout, QWidget

from soft_actuator_testing.application.camera_capture import CameraPanelPresenter
from soft_actuator_testing.application.run_controller import CyclicRunConfiguration, RunController
from soft_actuator_testing.application.serial_controller import SerialController
from soft_actuator_testing.domain.calibration import CalibrationFit
from soft_actuator_testing.domain.geometry import VideoGeometry
from soft_actuator_testing.ui.views.connections import SerialControlPanel
from soft_actuator_testing.ui.themes import SemanticState
from soft_actuator_testing.ui.widgets import AccessibleButton, PlotCanvas, StatusIndicator
from soft_actuator_testing.ui.widgets.camera_panel import CameraPanel


class ProductionConnectionsPage(QWidget):
    def __init__(
        self,
        serial: SerialController,
        camera: CameraPanelPresenter | None,
        workspace: callable,
        camera_auto_refresh: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Production device connections", self))
        self.serial_panel = SerialControlPanel(serial, self)
        layout.addWidget(self.serial_panel)
        def reserve_camera_output():
            root = workspace()
            return camera.reserve_standalone_capture(root) if root is not None else None

        self.camera_panel = (
            CameraPanel(
                camera,
                output_directory_provider=reserve_camera_output,
                output_available_provider=lambda: workspace() is not None,
                auto_refresh=camera_auto_refresh,
                parent=self,
            )
            if camera is not None
            else QLabel("Camera capture unavailable: install FFmpeg/FFprobe or turn recording off.", self)
        )
        layout.addWidget(self.camera_panel)


class ProductionReadinessPage(QWidget):
    def __init__(
        self,
        run: RunController,
        *,
        workspace: callable,
        calibration: callable,
        geometry: callable,
        selected_camera: callable,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._run, self._workspace, self._calibration, self._geometry, self._selected_camera = (
            run,
            workspace,
            calibration,
            geometry,
            selected_camera,
        )
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit("Cyclic pressure validation", self)
        self.cycles = QSpinBox(self); self.cycles.setRange(1, 100000); self.cycles.setValue(10)
        self.on_ms = QSpinBox(self); self.on_ms.setRange(1, 3_600_000); self.on_ms.setValue(6000)
        self.off_ms = QSpinBox(self); self.off_ms.setRange(1, 3_600_000); self.off_ms.setValue(5000)
        self.record = QCheckBox("Record video", self); self.record.setChecked(True)
        self.camera = QLabel(self)
        form.addRow("Experiment", self.name); form.addRow("Cycles", self.cycles)
        form.addRow("On (ms)", self.on_ms); form.addRow("Off (ms)", self.off_ms)
        form.addRow("", self.record); form.addRow("Selected camera", self.camera)
        layout.addLayout(form)
        self.check = AccessibleButton("Check readiness", parent=self); self.check.clicked.connect(self.configure)
        self.status = StatusIndicator("Production readiness", parent=self)
        self.detail = QLabel(self); self.detail.setWordWrap(True)
        layout.addWidget(self.check); layout.addWidget(self.status); layout.addWidget(self.detail)
        self.configure()

    def configure(self) -> None:
        device = self._selected_camera() if self.record.isChecked() else ""
        self.camera.setText(device or "No camera selected")
        readiness = self._run.configure(
            CyclicRunConfiguration(
                experiment_name=self.name.text(),
                cycles=self.cycles.value(),
                on_milliseconds=self.on_ms.value(),
                off_milliseconds=self.off_ms.value(),
                workspace=self._workspace(),
                camera_device=device,
                calibration=self._calibration(),
                geometry=self._geometry(),
                record_video=self.record.isChecked(),
            )
        )
        self.status.set_state(SemanticState.SUCCESS if readiness.ready else SemanticState.WARNING)
        self.detail.setText("Ready." if readiness.ready else "\n".join(readiness.failures))


class ProductionLiveRunPage(QWidget):
    def __init__(self, run: RunController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._run = run
        layout = QVBoxLayout(self)
        self.start = AccessibleButton("Start run", parent=self); self.start.clicked.connect(run.start_async)
        self.stop = AccessibleButton("Stop run", parent=self); self.stop.clicked.connect(run.stop)
        self.status = StatusIndicator("Run", parent=self)
        self.detail = QLabel(self); self.plot = PlotCanvas(title="Pressure", x_label="Time (s)", y_label="Pressure (kPa)")
        for widget in (self.start, self.stop, self.status, self.detail, self.plot): layout.addWidget(widget)
        self._timer = QTimer(self); self._timer.timeout.connect(self.refresh); self._timer.start(50); self.refresh()
        # As a page embedded in a shell's stack, this widget never receives
        # its own closeEvent when the owning window closes; the destroyed
        # fallback guarantees the poll timer stops for as long as this
        # object survives, regardless of how it is parented/closed.
        self.destroyed.connect(self._timer.stop)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        self._timer.stop()
        super().closeEvent(event)

    def refresh(self) -> None:
        snapshot = self._run.snapshot
        active = snapshot.lifecycle.state.value in {"starting", "running", "stopping"}
        self.start.setEnabled(snapshot.readiness.ready and not active)
        self.stop.setEnabled(active)
        self.detail.setText("\n".join(snapshot.diagnostic_text) or snapshot.lifecycle.state.value)
        if snapshot.telemetry:
            self.plot.set_series("pressure", [p.time_s for p in snapshot.telemetry], [p.pressure_kpa for p in snapshot.telemetry])
