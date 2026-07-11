"""Connections and diagnostics workflow page."""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QPlainTextEdit, QVBoxLayout

from soft_actuator_testing.application.presentation import (
    ApplicationSnapshot,
    ConnectDevices,
    DisconnectDevices,
    RequestDiagnostics,
)
from soft_actuator_testing.ui.views.base import PageScenario, WorkflowPage, semantic_connection
from soft_actuator_testing.ui.views.connections import SerialControlPanel
from soft_actuator_testing.ui.widgets import AccessibleButton, StatusIndicator


class ConnectionsDiagnosticsPage(WorkflowPage):
    def __init__(self, **kwargs) -> None:
        super().__init__("Connections / Diagnostics", **kwargs)
        connection = self.section("Demo devices")
        grid = QGridLayout(connection)
        self.controller_status = StatusIndicator("Controller", parent=connection)
        self.camera_status = StatusIndicator("Camera", parent=connection)
        self.controller_status.setObjectName("controller-status")
        self.camera_status.setObjectName("camera-status")
        self.connect_button = AccessibleButton("Connect demo devices")
        self.connect_button.setObjectName("connect-devices")
        self.connect_button.clicked.connect(self.connect_devices)
        self.disconnect_button = AccessibleButton("Disconnect demo devices")
        self.disconnect_button.setObjectName("disconnect-devices")
        self.disconnect_button.clicked.connect(self.disconnect_devices)
        grid.addWidget(self.controller_status, 0, 0)
        grid.addWidget(self.camera_status, 1, 0)
        grid.addWidget(self.connect_button, 0, 1)
        grid.addWidget(self.disconnect_button, 1, 1)

        self.serial_panel = SerialControlPanel(parent=self)
        self.layout.addWidget(self.serial_panel)

        diagnostics = self.section("Diagnostics")
        diag_layout = QVBoxLayout(diagnostics)
        self.diagnostic_button = AccessibleButton("Request demo telemetry")
        self.diagnostic_button.setObjectName("request-diagnostics")
        self.diagnostic_button.clicked.connect(self.request_diagnostics)
        self.diagnostic_log = QPlainTextEdit(diagnostics)
        self.diagnostic_log.setObjectName("diagnostic-log")
        self.diagnostic_log.setReadOnly(True)
        self.diagnostic_log.setAccessibleName("Diagnostic output")
        diag_layout.addWidget(self.diagnostic_button)
        diag_layout.addWidget(self.diagnostic_log)
        self.layout.addStretch(1)
        self._bind_presenter()

    def render_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        devices = snapshot.devices
        self.controller_status.set_state(semantic_connection(devices.controller))
        self.camera_status.set_state(semantic_connection(devices.camera))
        self.connect_button.setEnabled(not devices.all_connected)
        self.disconnect_button.setEnabled(devices.all_connected)
        self.diagnostic_log.setPlainText(devices.diagnostic_text)

    def connect_devices(self) -> None:
        self.dispatch(ConnectDevices())
        self.set_scenario(PageScenario.READY)

    def disconnect_devices(self) -> None:
        self.dispatch(DisconnectDevices())

    def request_diagnostics(self) -> None:
        self.dispatch(RequestDiagnostics())
        self.set_scenario(PageScenario.COMPLETED)
