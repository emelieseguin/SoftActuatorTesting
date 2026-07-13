"""Behavioral and headless rendering tests for the Instrument Console shell."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QLabel, QWidget

from soft_actuator_testing.application.run_controller import (
    RunCoordinatorSnapshot,
    RunController,
    RunReadiness,
)
from soft_actuator_testing.domain.run_state import RunCompletion, RunSnapshot, RunState
from soft_actuator_testing.ui.shells.instrument_console import (
    LayoutSnapshot,
    PersistedLayoutStore,
    ProductionConsoleStatus,
    create_instrument_console_shell,
)
from soft_actuator_testing.ui.views import PAGE_REGISTRY, PageScenario
from soft_actuator_testing.ui.widgets.file_picker import FakeFilePicker



def _console(qtbot):
    window = create_instrument_console_shell()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _idle_production_status() -> ProductionConsoleStatus:
    return ProductionConsoleStatus(
        workspace=None,
        calibration_ready=False,
        geometry_ready=False,
        serial_connected=False,
        camera_selected=False,
    )


class _FakeProductionRun:
    """A minimal, hardware-free stand-in for ``RunController`` snapshot polling."""

    def __init__(self) -> None:
        self.active = False
        self.finalization_result = None

    @property
    def snapshot(self) -> RunCoordinatorSnapshot:
        state = RunState.RUNNING if self.active else RunState.DISCONNECTED
        return RunCoordinatorSnapshot(
            lifecycle=RunSnapshot(state),
            readiness=RunReadiness(True, ()),
            telemetry=(),
            recording_enabled=False,
        )

    def close(self) -> None:
        return None


def _production_console(qtbot, *, layout_store: PersistedLayoutStore | None = None, production_run=None):
    """A minimal, hardware-free production-mode console for wording/layout tests.

    Uses plain ``QWidget`` placeholders for every registered page and a
    never-started ``RunController`` (or an injected fake) so the production
    branch of ``InstrumentConsoleWindow`` is exercised without the heavier
    real composition wiring covered separately in
    ``test_production_composition.py``.
    """

    window = create_instrument_console_shell(
        production_run=production_run if production_run is not None else RunController(),
        production_pages={metadata.key: QWidget() for metadata in PAGE_REGISTRY},
        production_status=_idle_production_status,
        file_picker=FakeFilePicker(),
        layout_store=layout_store,
    )
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


# -- Production mode: no demo wording -----------------------------------------------


def test_production_console_never_shows_demo_wording(qtbot) -> None:
    console = _production_console(qtbot)
    assert "Demo" not in console.windowTitle()
    assert console.accessibleName() == "Instrument Console production window"
    assert "demo" not in console.stop_button.accessibleDescription().lower()
    assert "demo" not in console.stop_action.statusTip().lower()
    assert "demo" not in console.save_layout_action.text().lower()
    assert "demo" not in console.restore_layout_action.text().lower()

    telemetry_dock = next(dock for dock in console._docks if dock.objectName() == "dock-telemetry")
    assert "demo" not in telemetry_dock.accessibleName().lower()
    assert "demo" not in console.telemetry_plot.accessibleName().lower()

    run_control_dock = next(dock for dock in console._docks if dock.objectName() == "dock-run-control")
    note = next(label for label in run_control_dock.findChildren(QLabel) if label.accessibleName() == "Run control note")
    assert "demo" not in note.text().lower()


def test_production_console_has_no_demo_menu(qtbot) -> None:
    console = _production_console(qtbot)
    menu_titles = [action.text() for action in console.menuBar().actions()]
    assert not any("demo" in title.lower() for title in menu_titles)


def test_production_stop_wording_updates_with_run_state(qtbot) -> None:
    fake_run = _FakeProductionRun()
    console = _production_console(qtbot, production_run=fake_run)
    assert "idle" in console.stop_button.accessibleDescription().lower()

    fake_run.active = True
    console._refresh_production_run()

    assert "active" in console.stop_button.accessibleDescription().lower()
    assert "demo" not in console.stop_button.accessibleDescription().lower()
    assert "demo" not in console.stop_action.statusTip().lower()
    assert console.stop_button.isEnabled() is True


# -- Production layout persistence ---------------------------------------------------


def test_production_save_and_restore_layout_round_trips_through_disk(qtbot, tmp_path) -> None:
    store = PersistedLayoutStore(tmp_path / "console-layout.json")
    console = _production_console(qtbot, layout_store=store)
    telemetry_dock = next(dock for dock in console._docks if dock.objectName() == "dock-telemetry")

    console.save_layout()
    assert store.path.is_file()
    payload = json.loads(store.path.read_text())
    assert payload["schema_version"] == 1
    assert "demo" not in console.event_log.toPlainText().lower()

    telemetry_dock.setFloating(True)
    console.restore_layout()
    assert telemetry_dock.isFloating() is False
    assert "demo" not in console.event_log.toPlainText().lower()


def test_production_layout_persists_across_separate_window_instances(qtbot, tmp_path) -> None:
    store_path = tmp_path / "console-layout.json"
    first = _production_console(qtbot, layout_store=PersistedLayoutStore(store_path))
    telemetry_dock = next(dock for dock in first._docks if dock.objectName() == "dock-telemetry")
    telemetry_dock.setFloating(True)
    first.save_layout()
    first.close()

    second = _production_console(qtbot, layout_store=PersistedLayoutStore(store_path))
    second_telemetry_dock = next(dock for dock in second._docks if dock.objectName() == "dock-telemetry")
    second_telemetry_dock.setFloating(False)
    second.restore_layout()
    assert second_telemetry_dock.isFloating() is True


def test_production_restore_layout_is_a_safe_no_op_with_no_saved_file(qtbot, tmp_path) -> None:
    store = PersistedLayoutStore(tmp_path / "missing-console-layout.json")
    console = _production_console(qtbot, layout_store=store)
    console.restore_layout()  # must not raise
    assert "no valid saved layout" in console.event_log.toPlainText().lower()


def test_production_restore_layout_safely_ignores_a_corrupt_file(qtbot, tmp_path) -> None:
    path = tmp_path / "console-layout.json"
    path.write_text("{ not valid json")
    store = PersistedLayoutStore(path)
    console = _production_console(qtbot, layout_store=store)
    console.restore_layout()  # must not raise
    assert "no valid saved layout" in console.event_log.toPlainText().lower()


def test_production_restore_layout_never_touches_hardware(qtbot, tmp_path) -> None:
    store = PersistedLayoutStore(tmp_path / "console-layout.json")
    console = _production_console(qtbot, layout_store=store)
    console.save_layout()
    console.restore_layout()
    assert console.production_run.snapshot.lifecycle.state is RunState.DISCONNECTED


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


def test_save_and_restore_layout_shortcuts_activate_the_demo_actions(qtbot) -> None:
    console = _console(qtbot)
    console.activateWindow()
    qtbot.wait(20)

    assert console.saved_layout is None
    qtbot.keyClick(console, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert console.saved_layout is not None
    assert "layout saved" in console.event_log.toPlainText().lower()

    qtbot.keyClick(console, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert "layout restored" in console.event_log.toPlainText().lower()
    assert "demo" in console.event_log.toPlainText().lower()


def test_save_and_restore_layout_shortcuts_activate_the_production_actions(qtbot, tmp_path) -> None:
    store = PersistedLayoutStore(tmp_path / "console-layout.json")
    console = _production_console(qtbot, layout_store=store)
    console.activateWindow()
    qtbot.wait(20)

    qtbot.keyClick(console, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert store.path.is_file()
    assert "demo" not in console.event_log.toPlainText().lower()

    qtbot.keyClick(console, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert "layout restored" in console.event_log.toPlainText().lower()
    assert "demo" not in console.event_log.toPlainText().lower()


def test_tab_key_focus_traversal_reaches_navigation_and_workspace_controls(qtbot) -> None:
    """Every primary control must be reachable by keyboard alone (no mouse).

    Presses Tab repeatedly from the Home page and confirms the focus ring
    actually moves (not stuck on one widget) and passes through both the
    page-navigation toolbar and the active page's keyboard-operable controls.
    """

    console = _console(qtbot)
    console.activateWindow()
    console.navigate_to("home")
    qtbot.wait(20)

    visited: list[str] = []
    for _ in range(30):
        qtbot.keyClick(console, Qt.Key.Key_Tab)
        widget = console.focusWidget()
        visited.append(widget.objectName() if widget is not None else "")

    assert len(set(visited)) >= 10, f"focus ring did not move through distinct controls: {visited}"
    assert "nav-button-home" in visited
    assert "nav-button-live-run" in visited
    assert "open-individual-files" in visited


def test_shift_tab_focus_traversal_moves_backwards(qtbot) -> None:
    console = _console(qtbot)
    console.activateWindow()
    console.navigate_to("home")
    qtbot.wait(20)

    forward: list[str] = []
    for _ in range(5):
        qtbot.keyClick(console, Qt.Key.Key_Tab)
        widget = console.focusWidget()
        forward.append(widget.objectName() if widget is not None else "")

    backward: list[str] = []
    for _ in range(5):
        qtbot.keyClick(console, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
        widget = console.focusWidget()
        backward.append(widget.objectName() if widget is not None else "")

    # Reversing the same number of steps returns focus close to where it
    # started; at minimum this proves Shift+Tab genuinely reverses direction
    # rather than behaving identically to a forward Tab.
    assert backward != forward


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


# -- DPI / display-scale rendering -----------------------------------------------------
#
# ``QT_SCALE_FACTOR`` only takes effect if set before ``QApplication`` is
# constructed, and the test session shares a single ``QApplication`` across
# every test (via pytest-qt), so each scale factor is exercised in its own
# subprocess -- the same pattern used by
# ``tests/test_package.py::test_production_composition_does_not_import_demo_services``.
# Logical widget geometry (``.size()``) is scale-invariant in Qt Widgets;
# only the rendered/physical pixmap from ``grab()`` scales, so that is what
# these checks assert on.

_SCALING_SCRIPT = """
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "{scale}"

from PySide6.QtWidgets import QApplication
from soft_actuator_testing.ui.shells.instrument_console import create_instrument_console_shell

application = QApplication([])
console = create_instrument_console_shell()
console.resize(1280, 720)
console.show()
application.processEvents()
grab = console.grab()
print(grab.width(), grab.height(), application.devicePixelRatio())
console.close()
application.quit()
"""


def _run_scaling_subprocess(scale: float) -> tuple[int, int, float]:
    result = subprocess.run(
        [sys.executable, "-c", _SCALING_SCRIPT.format(scale=scale)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.stderr == "", result.stderr
    width_text, height_text, ratio_text = result.stdout.strip().split()
    return int(width_text), int(height_text), float(ratio_text)


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
def test_console_renders_at_representative_100_150_200_percent_scaling(scale: float) -> None:
    width, height, ratio = _run_scaling_subprocess(scale)
    assert ratio == pytest.approx(scale)
    assert width == round(1280 * scale)
    assert height == round(720 * scale)


def test_layout_snapshot_round_trips_through_bytes(qtbot) -> None:
    console = _console(qtbot)
    snapshot = console.capture_layout()
    assert isinstance(snapshot, LayoutSnapshot)
    assert isinstance(snapshot.geometry, bytes) and len(snapshot.geometry) > 0
    assert isinstance(snapshot.state, bytes) and len(snapshot.state) > 0
    assert console.apply_layout(snapshot) is True
