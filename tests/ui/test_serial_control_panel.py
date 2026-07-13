"""Qt coverage for the composable serial panel without physical hardware."""

from __future__ import annotations

from threading import Thread

from PySide6.QtCore import Qt

from soft_actuator_testing.application.serial_controller import SerialConnectionStatus, SerialController
from soft_actuator_testing.ui.views.connections import SerialControlPanel


def test_serial_panel_default_is_unconfigured_and_never_opens_ports(qtbot) -> None:
    panel = SerialControlPanel()
    qtbot.addWidget(panel)
    assert panel.controller.snapshot.status is SerialConnectionStatus.UNCONFIGURED
    assert not panel.disconnect_button.isEnabled()
    qtbot.mouseClick(panel.refresh_button, Qt.MouseButton.LeftButton)
    assert "no serial adapter" in panel.diagnostics.toPlainText().lower()


def test_serial_panel_renders_profile_uncertainty(qtbot) -> None:
    panel = SerialControlPanel(controller=SerialController())
    qtbot.addWidget(panel)
    assert panel.profile_note.text().startswith("Parser profile: unconfigured")
    assert not panel.start_button.isEnabled()
    assert "CMD:START is blocked" in panel.profile_note.text()


def test_serial_publications_from_worker_are_marshaled_to_gui_thread(qtbot) -> None:
    controller = SerialController()
    panel = SerialControlPanel(controller=controller)
    qtbot.addWidget(panel)

    worker = Thread(target=controller.refresh_ports)
    worker.start()
    worker.join(1)

    qtbot.waitUntil(
        lambda: "Port refresh skipped" in panel.diagnostics.toPlainText(),
        timeout=500,
    )
