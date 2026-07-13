"""Composable serial connection panel; it never owns a serial transport."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget

from soft_actuator_testing.application.serial_controller import (
    SerialConnectionSnapshot,
    SerialConnectionStatus,
    SerialController,
)
from soft_actuator_testing.infrastructure.serial_adapter import SerialConnectionConfig
from soft_actuator_testing.ui.themes.tokens import SemanticState
from soft_actuator_testing.ui.widgets import AccessibleButton, StatusIndicator


class SerialControlPanel(QWidget):
    """Render serial connection/polling state from an injected controller."""

    snapshot_received = Signal(object)

    def __init__(self, controller: SerialController | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("serial-control-panel")
        self.setAccessibleName("Serial controller connection and diagnostics")
        self.controller = controller or SerialController()
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.port = QComboBox(self)
        self.port.setObjectName("serial-port")
        self.port.setEditable(True)
        self.baudrate = QSpinBox(self)
        self.baudrate.setObjectName("serial-baudrate")
        self.baudrate.setRange(1, 10_000_000)
        self.baudrate.setValue(115200)
        self.timeout = QSpinBox(self)
        self.timeout.setObjectName("serial-timeout-ms")
        self.timeout.setRange(1, 60_000)
        self.timeout.setValue(500)
        self.status = StatusIndicator("Serial controller", parent=self)
        self.status.setObjectName("serial-status")
        form.addRow("Port", self.port)
        form.addRow("Baud", self.baudrate)
        form.addRow("Read timeout (ms)", self.timeout)
        form.addRow("Status", self.status)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.refresh_button = AccessibleButton("Refresh ports", parent=self)
        self.refresh_button.setObjectName("serial-refresh")
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.connect_button = AccessibleButton("Connect serial", parent=self)
        self.connect_button.setObjectName("serial-connect")
        self.connect_button.clicked.connect(self.connect)
        self.disconnect_button = AccessibleButton("Disconnect serial", parent=self)
        self.disconnect_button.setObjectName("serial-disconnect")
        self.disconnect_button.clicked.connect(self.disconnect)
        for button in (self.refresh_button, self.connect_button, self.disconnect_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        legacy = QHBoxLayout()
        self.start_button = AccessibleButton("Run start requires readiness", parent=self)
        self.start_button.setToolTip("CMD:START is blocked here. Use Live Run after readiness and camera proof.")
        self.stop_button = AccessibleButton("Send legacy stop", parent=self)
        self.calibration_on_button = AccessibleButton("Enable legacy calibration", parent=self)
        self.calibration_off_button = AccessibleButton("Disable legacy calibration", parent=self)
        for button, callback in (
            (self.stop_button, self.controller.stop_legacy_run),
            (self.calibration_on_button, lambda: self.controller.set_legacy_calibration_streaming(True)),
            (self.calibration_off_button, lambda: self.controller.set_legacy_calibration_streaming(False)),
        ):
            button.clicked.connect(callback)
            legacy.addWidget(button)
        self.start_button.setEnabled(False)
        legacy.addWidget(self.start_button)
        layout.addLayout(legacy)
        self.profile_note = QLabel(self)
        self.profile_note.setObjectName("serial-profile-note")
        self.diagnostics = QPlainTextEdit(self)
        self.diagnostics.setObjectName("serial-diagnostic-log")
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setAccessibleName("Serial diagnostic output")
        layout.addWidget(self.profile_note)
        layout.addWidget(self.diagnostics)
        self.snapshot_received.connect(self.render_snapshot)
        self._unsubscribe = self.controller.subscribe(self.snapshot_received.emit)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self.controller.poll)
        self._poll_timer.start()
        self.destroyed.connect(self._shutdown)

    def refresh_ports(self) -> None:
        ports = self.controller.refresh_ports()
        current = self.port.currentText()
        self.port.clear()
        self.port.addItems([item.device for item in ports])
        if current:
            self.port.setCurrentText(current)

    def connect(self) -> None:
        self.controller.connect(
            SerialConnectionConfig(
                port=self.port.currentText(),
                baudrate=self.baudrate.value(),
                timeout_seconds=self.timeout.value() / 1000,
            )
        )

    def disconnect(self) -> None:
        self.controller.disconnect()

    def render_snapshot(self, snapshot: SerialConnectionSnapshot) -> None:
        if snapshot.status is SerialConnectionStatus.CONNECTED:
            state = SemanticState.SUCCESS
        elif snapshot.status is SerialConnectionStatus.FAULT:
            state = SemanticState.ERROR
        elif snapshot.status is SerialConnectionStatus.UNCONFIGURED:
            state = SemanticState.NEUTRAL
        else:
            state = SemanticState.NEUTRAL
        self.status.set_state(state)
        self.connect_button.setEnabled(snapshot.status is not SerialConnectionStatus.CONNECTED)
        self.disconnect_button.setEnabled(snapshot.status is SerialConnectionStatus.CONNECTED)
        commands_enabled = snapshot.status is SerialConnectionStatus.CONNECTED
        for button in (self.stop_button, self.calibration_on_button, self.calibration_off_button):
            button.setEnabled(commands_enabled)
        self.start_button.setEnabled(False)
        unconfirmed = " Field mapping is unconfirmed and configurable." if snapshot.profile_is_unconfirmed else ""
        self.profile_note.setText(
            f"Parser profile: {snapshot.profile_name}.{unconfirmed} "
            "CMD:START is blocked here; use Live Run after readiness and camera proof."
        )
        self.diagnostics.setPlainText(snapshot.diagnostic_text)

    def _shutdown(self, *_: object) -> None:
        self._poll_timer.stop()
        self._unsubscribe()
        self.controller.close()
