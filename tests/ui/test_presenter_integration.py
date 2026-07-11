"""pytest-qt coverage for presenter-driven pages and selected Console."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid

from soft_actuator_testing.application.presentation import (
    BeginRun,
    ConfirmRunStarted,
    ConnectDevices,
    DisconnectDevices,
    EvaluateReadiness,
    GlobalStop,
    ReportRunFault,
    RequestRunStop,
    StateStore,
)
from soft_actuator_testing.domain.run_state import RunCompletion, RunState
from soft_actuator_testing.ui.demo import build_demo_controller, build_demo_environment
from soft_actuator_testing.ui.presenters import bind_view
from soft_actuator_testing.ui.shells.instrument_console import create_instrument_console_shell
from soft_actuator_testing.ui.views import ConnectionsDiagnosticsPage, LiveRunPage


def _console(qtbot):
    environment = build_demo_environment()
    controller = build_demo_controller(environment)
    console = create_instrument_console_shell(
        environment=environment,
        presenter=controller.session(),
    )
    qtbot.addWidget(console)
    console.show()
    qtbot.waitExposed(console)
    return controller, console


def test_shared_pages_render_the_same_connection_and_run_snapshot(qtbot) -> None:
    controller = build_demo_controller()
    connections = ConnectionsDiagnosticsPage(presenter=controller.session())
    run = LiveRunPage(presenter=controller.session())
    qtbot.addWidget(connections)
    qtbot.addWidget(run)

    qtbot.mouseClick(connections.connect_button, Qt.MouseButton.LeftButton)
    controller.dispatch(EvaluateReadiness())
    assert connections.controller_status.accessibleName().endswith("Success")
    assert run.start_button.isEnabled()

    qtbot.mouseClick(run.start_button, Qt.MouseButton.LeftButton)
    assert controller.snapshot.run.lifecycle.state is RunState.RUNNING
    assert run.stop_button.isEnabled()

    controller.dispatch(DisconnectDevices("Synthetic camera disconnect."))
    assert connections.controller_status.accessibleName().endswith("Neutral")
    assert not run.stop_button.isEnabled()
    assert "faulted" in run.run_log.toPlainText()


def test_console_readiness_guidance_and_progressive_diagnostics_are_snapshot_driven(qtbot) -> None:
    controller, console = _console(qtbot)
    assert "Connect the controller" in console.readiness_guidance.text()
    assert "Connect the controller" in console.next_action_value.text()
    assert not console.diagnostics_detail.isVisible()

    qtbot.mouseClick(console.diagnostics_toggle, Qt.MouseButton.LeftButton)
    assert console.diagnostics_detail.isVisible()
    assert "Controller: disconnected" in console.diagnostics_detail.text()

    controller.dispatch(ConnectDevices())
    assert console.readiness_guidance.text().startswith("Ready:")
    assert "Live Run" in console.next_action_value.text()


def test_console_fixed_run_fault_and_global_stop_chrome_follow_commands(qtbot) -> None:
    controller, console = _console(qtbot)
    controller.dispatch(ConnectDevices())
    controller.dispatch(EvaluateReadiness())
    controller.dispatch(BeginRun())
    assert console.stop_button.isEnabled()
    assert console.run_status.accessibleName().endswith("Info")

    controller.dispatch(ReportRunFault("Synthetic worker fault."))
    assert console.fault_status.accessibleName().endswith("Error")
    assert console.run_status.accessibleName().endswith("Error")
    assert not console.stop_button.isEnabled()
    assert console.stop_button.isVisible()


def test_console_global_stop_is_idempotent_while_starting_and_stopping(qtbot) -> None:
    controller, console = _console(qtbot)
    controller.dispatch(ConnectDevices())
    controller.dispatch(EvaluateReadiness())
    controller.dispatch(BeginRun())
    qtbot.mouseClick(console.stop_button, Qt.MouseButton.LeftButton)
    assert controller.snapshot.run.lifecycle.completion is RunCompletion.ABORTED
    console.trigger_global_stop()
    assert controller.snapshot.run.lifecycle.completion is RunCompletion.ABORTED

    second_controller, second_console = _console(qtbot)
    second_controller.dispatch(ConnectDevices())
    second_controller.dispatch(EvaluateReadiness())
    second_controller.dispatch(BeginRun())
    second_controller.dispatch(ConfirmRunStarted())
    second_controller.dispatch(RequestRunStop())
    assert second_controller.snapshot.run.lifecycle.state is RunState.STOPPING
    second_console.trigger_global_stop()
    assert second_controller.snapshot.run.lifecycle.completion is RunCompletion.ABORTED


def test_destroyed_view_subscription_drops_stale_callbacks(qtbot) -> None:
    class RenderingView(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.rendered: list[str] = []

        def render(self, value: str) -> None:
            self.rendered.append(value)

    store = StateStore("initial")
    view = RenderingView()
    view.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    qtbot.addWidget(view)
    subscription = bind_view(view, store, view.render)
    assert view.rendered == ["initial"]

    view.close()
    qtbot.waitUntil(lambda: not isValid(view))
    store.publish("stale")
    assert subscription.is_disposed
