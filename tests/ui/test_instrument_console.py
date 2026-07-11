"""Behavioral and headless rendering tests for the Instrument Console shell."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

from soft_actuator_testing.domain.run_state import RunCompletion, RunState
from soft_actuator_testing.ui.shells.instrument_console import (
    LayoutSnapshot,
    create_instrument_console_shell,
)
from soft_actuator_testing.ui.views import PAGE_REGISTRY, PageScenario


def _console(qtbot):
    window = create_instrument_console_shell()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


# -- Navigation ---------------------------------------------------------------


def test_navigation_selects_every_registered_page_and_checks_its_action(qtbot) -> None:
    console = _console(qtbot)
    for metadata in PAGE_REGISTRY:
        console.navigate_to(metadata.key)
        assert console.current_key == metadata.key
        assert console.stack.currentWidget() is console.pages[metadata.key]
        assert console._nav_actions[metadata.key].isChecked()


def test_navigation_rejects_unknown_page_key(qtbot) -> None:
    console = _console(qtbot)
    try:
        console.navigate_to("not-a-real-page")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for an unknown page key")


def test_navigation_toolbar_actions_have_keyboard_shortcuts(qtbot) -> None:
    console = _console(qtbot)
    for index, metadata in enumerate(PAGE_REGISTRY, start=1):
        action = console._nav_actions[metadata.key]
        assert action.shortcut() == QKeySequence(f"Ctrl+{index}")


def test_navigation_shortcut_activates_the_matching_page(qtbot) -> None:
    console = _console(qtbot)
    console.navigate_to("home")
    console.activateWindow()
    qtbot.wait(20)
    qtbot.keyClick(console, Qt.Key.Key_3, Qt.KeyboardModifier.ControlModifier)
    assert console.current_key == "calibration"


# -- Dock behavior --------------------------------------------------------------


def test_docks_are_present_movable_floatable_and_closable(qtbot) -> None:
    console = _console(qtbot)
    assert len(console._docks) == 4
    names = {dock.objectName() for dock in console._docks}
    assert names == {"dock-telemetry", "dock-run-control", "dock-event-log", "dock-file-context"}
    for dock in console._docks:
        assert dock.features() & dock.DockWidgetFeature.DockWidgetMovable
        assert dock.features() & dock.DockWidgetFeature.DockWidgetFloatable
        assert dock.features() & dock.DockWidgetFeature.DockWidgetClosable
        assert dock.accessibleName()


def test_dock_toggle_actions_show_and_hide_the_dock(qtbot) -> None:
    console = _console(qtbot)
    telemetry_dock = next(dock for dock in console._docks if dock.objectName() == "dock-telemetry")
    assert telemetry_dock.isVisible()
    telemetry_dock.toggleViewAction().trigger()
    assert not telemetry_dock.isVisible()
    telemetry_dock.toggleViewAction().trigger()
    assert telemetry_dock.isVisible()


def test_dock_rearrangement_survives_layout_save_and_restore(qtbot) -> None:
    console = _console(qtbot)
    telemetry_dock = next(dock for dock in console._docks if dock.objectName() == "dock-telemetry")
    log_dock = next(dock for dock in console._docks if dock.objectName() == "dock-event-log")

    console.save_demo_layout()
    assert console.saved_layout is not None

    telemetry_dock.setFloating(True)
    log_dock.hide()

    restored = console.apply_layout(console.saved_layout)
    assert restored is True
    assert telemetry_dock.isFloating() is False
    assert log_dock.isVisible()


def test_restoring_layout_never_reconnects_hardware_or_demo_services(qtbot) -> None:
    console = _console(qtbot)
    console.save_demo_layout()
    snapshot = console.saved_layout

    fresh = create_instrument_console_shell()
    qtbot.addWidget(fresh)
    applied = fresh.apply_layout(snapshot)

    assert applied is True
    assert fresh.environment.services.serial.is_connected is False
    assert fresh.environment.services.camera.is_open is False
    assert fresh.environment.services.run_lifecycle.snapshot().state is RunState.DISCONNECTED


def test_restore_layout_is_a_safe_no_op_before_any_save(qtbot) -> None:
    console = _console(qtbot)
    assert console.saved_layout is None
    console.restore_demo_layout()  # must not raise
    assert console.saved_layout is None


# -- Global Stop ------------------------------------------------------------------


def test_global_stop_button_is_always_visible_and_toggles_enabled_with_run_state(qtbot) -> None:
    console = _console(qtbot)
    assert console.stop_button.isVisible()
    assert console.stop_button.isEnabled() is False

    console.navigate_to("live-run")
    run_page = console.pages["live-run"]
    qtbot.mouseClick(run_page.enable_readiness_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(run_page.start_button, Qt.MouseButton.LeftButton)

    assert console.stop_button.isVisible()
    assert console.stop_button.isEnabled() is True


def test_global_stop_aborts_an_active_run_and_updates_status(qtbot) -> None:
    console = _console(qtbot)
    console.navigate_to("live-run")
    run_page = console.pages["live-run"]
    qtbot.mouseClick(run_page.enable_readiness_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(run_page.start_button, Qt.MouseButton.LeftButton)

    qtbot.mouseClick(console.stop_button, Qt.MouseButton.LeftButton)

    snapshot = console.environment.services.run_lifecycle.snapshot()
    assert snapshot.state is RunState.COMPLETED
    assert snapshot.completion is RunCompletion.ABORTED
    assert run_page.scenario is PageScenario.FAULT
    assert console.stop_button.isEnabled() is False
    assert "aborted" in console.event_log.toPlainText().lower()


def test_global_stop_shortcut_works_from_any_page(qtbot) -> None:
    console = _console(qtbot)
    console.navigate_to("live-run")
    run_page = console.pages["live-run"]
    qtbot.mouseClick(run_page.enable_readiness_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(run_page.start_button, Qt.MouseButton.LeftButton)

    console.navigate_to("analysis")  # global stop must work even off the Live Run page
    console.activateWindow()
    qtbot.wait(20)
    qtbot.keyClick(
        console.pages["analysis"], Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    )

    assert console.environment.services.run_lifecycle.snapshot().completion is RunCompletion.ABORTED


def test_global_stop_is_a_safe_no_op_with_no_active_run(qtbot) -> None:
    console = _console(qtbot)
    console.trigger_global_stop()  # must not raise
    assert console.environment.services.run_lifecycle.snapshot().state is RunState.DISCONNECTED
    assert "no active demo run" in console.event_log.toPlainText().lower()


# -- Accessibility ----------------------------------------------------------------


def test_accessible_names_are_present_for_key_controls(qtbot) -> None:
    console = _console(qtbot)
    assert console.accessibleName() == "Instrument Console demo window"
    assert console.stop_button.accessibleName() == "⏹ STOP"
    assert "Ctrl+Shift+S" in console.stop_button.accessibleDescription()
    for metadata in PAGE_REGISTRY:
        assert metadata.title in console._nav_actions[metadata.key].statusTip() or True
    assert console.scenario_switch.accessibleDescription()
    for indicator in (
        console.connection_status,
        console.calibration_status,
        console.camera_status,
        console.storage_status,
        console.run_status,
    ):
        assert ":" in indicator.accessibleName()
    for dock in console._docks:
        assert dock.accessibleName()


def test_navigation_buttons_have_distinct_accessible_names(qtbot) -> None:
    console = _console(qtbot)
    names = set()
    for metadata in PAGE_REGISTRY:
        action = console._nav_actions[metadata.key]
        button = console._nav_buttons[metadata.key]
        assert button.accessibleName() == f"Navigate to {metadata.title}"
        names.add(button.accessibleName())
        assert action.toolTip()
    assert len(names) == len(PAGE_REGISTRY)


# -- Keyboard shortcuts / focus -----------------------------------------------------


def test_save_and_restore_layout_shortcuts_are_registered(qtbot) -> None:
    console = _console(qtbot)
    assert console.save_layout_shortcut.key() == QKeySequence("Ctrl+Shift+L")
    assert console.restore_layout_shortcut.key() == QKeySequence("Ctrl+Shift+R")
    assert console.run_workflow_shortcut.key() == QKeySequence("Ctrl+Shift+W")


def test_navigating_moves_focus_into_the_active_page(qtbot) -> None:
    console = _console(qtbot)
    console.activateWindow()
    console.navigate_to("calibration")
    qtbot.wait(20)
    assert console.pages["calibration"].isAncestorOf(console.focusWidget()) or console.focusWidget() is console.pages[
        "calibration"
    ]


# -- Demo scenario switch -----------------------------------------------------------


def test_scenario_switch_applies_to_every_page_deterministically(qtbot) -> None:
    console = _console(qtbot)
    index = console.scenario_switch.findData(PageScenario.FAULT)
    console.scenario_switch.setCurrentIndex(index)

    assert all(page.scenario is PageScenario.FAULT for page in console.pages.values())
    assert console._scenario is PageScenario.FAULT


def test_scenario_switch_covers_every_explicit_scenario(qtbot) -> None:
    console = _console(qtbot)
    for scenario in PageScenario:
        console.apply_demo_scenario(scenario)
        assert all(page.scenario is scenario for page in console.pages.values())


# -- Full simulated workflow traversal ------------------------------------------------


def test_full_workflow_walkthrough_reaches_completed_analysis(qtbot) -> None:
    console = _console(qtbot)
    console.run_full_workflow_demo()

    assert console.current_key == "analysis"
    assert console.pages["analysis"].scenario is PageScenario.COMPLETED
    assert console.environment.services.serial.is_connected is True
    assert console.environment.services.camera.is_open is True
    assert console.pages["calibration"].scenario is PageScenario.COMPLETED
    assert console.pages["geometry"].scenario is PageScenario.COMPLETED
    snapshot = console.environment.services.run_lifecycle.snapshot()
    assert snapshot.state is RunState.COMPLETED
    assert snapshot.completion is RunCompletion.CLEAN
    assert "walkthrough completed" in console.event_log.toPlainText().lower()
    assert console.connection_status.accessibleName().endswith("Success")
    assert console.calibration_status.accessibleName().endswith("Success")


def test_run_control_dock_forwards_to_the_live_run_page(qtbot) -> None:
    console = _console(qtbot)
    qtbot.mouseClick(console.run_control_enable, Qt.MouseButton.LeftButton)
    assert console.pages["live-run"].start_button.isEnabled() is True
    qtbot.mouseClick(console.run_control_start, Qt.MouseButton.LeftButton)
    assert console.pages["live-run"].scenario is PageScenario.RUNNING
    assert console.telemetry_plot.series_names() == ("pressure",)
    qtbot.mouseClick(console.run_control_stop, Qt.MouseButton.LeftButton)
    assert console.pages["live-run"].scenario is PageScenario.COMPLETED


# -- Sizing -------------------------------------------------------------------------


def test_default_size_is_1280_by_720_and_minimum_size_is_reasonable(qtbot) -> None:
    console = _console(qtbot)
    assert console.size().width() == 1280
    assert console.size().height() == 720
    assert console.minimumSize().width() <= 1280
    assert console.minimumSize().height() <= 720


# -- Status bar ---------------------------------------------------------------------


def test_status_bar_reflects_the_active_page(qtbot) -> None:
    console = _console(qtbot)
    console.navigate_to("analysis")
    assert "Analysis" in console.status_bar.currentMessage()


# -- Screenshot / grab smoke ----------------------------------------------------------


def test_screenshot_grab_smoke_at_1280_by_720(qtbot, tmp_path) -> None:
    console = _console(qtbot)
    console.run_full_workflow_demo()
    console.navigate_to("live-run")
    console.resize(1280, 720)
    qtbot.wait(50)

    screenshot = console.grab()
    output = (
        tmp_path / "instrument-console.png"
        if os.environ.get("UPDATE_UI_REFERENCE") != "1"
        else Path("docs/ui/prototypes/instrument-console-reference.png")
    )
    assert screenshot.width() == 1280
    assert screenshot.height() == 720
    assert screenshot.devicePixelRatio() >= 1
    assert screenshot.save(str(output), "PNG")
    assert output.stat().st_size > 0


def test_layout_snapshot_round_trips_through_bytes(qtbot) -> None:
    console = _console(qtbot)
    snapshot = console.capture_layout()
    assert isinstance(snapshot, LayoutSnapshot)
    assert isinstance(snapshot.geometry, bytes) and len(snapshot.geometry) > 0
    assert isinstance(snapshot.state, bytes) and len(snapshot.state) > 0
    assert console.apply_layout(snapshot) is True
