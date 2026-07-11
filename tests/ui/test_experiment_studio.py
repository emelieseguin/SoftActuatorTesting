"""Behavioral and headless rendering tests for the Experiment Studio shell."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

from soft_actuator_testing.domain.run_state import RunCompletion
from soft_actuator_testing.ui.shells.experiment_studio import create_experiment_studio_shell
from soft_actuator_testing.ui.views import PageScenario


def _studio(qtbot):
    window = create_experiment_studio_shell()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def test_stage_navigation_is_persistent_and_completed_stages_are_revisitable(qtbot) -> None:
    studio = _studio(qtbot)

    qtbot.mouseClick(studio._navigation_buttons["connections"], Qt.MouseButton.LeftButton)
    assert studio.current_key == "connections"
    qtbot.mouseClick(studio.primary_action, Qt.MouseButton.LeftButton)
    assert studio.current_key == "calibration"
    assert "connections" in studio.completed_stages

    qtbot.mouseClick(studio._navigation_buttons["connections"], Qt.MouseButton.LeftButton)
    assert studio.current_key == "connections"
    assert studio._navigation_buttons["connections"].isEnabled()


def test_progress_and_readiness_summary_follow_full_guided_flow(qtbot) -> None:
    studio = _studio(qtbot)
    studio.navigate_to("connections")

    for _stage in ("connections", "calibration", "geometry", "experiment", "live-run", "analysis"):
        qtbot.mouseClick(studio.primary_action, Qt.MouseButton.LeftButton)

    assert studio.completed_stages.issuperset(
        {"connections", "calibration", "geometry", "experiment", "live-run", "analysis"}
    )
    assert "6/6 stages complete" in studio.summary_label.text()
    assert "Ready" in studio.readiness_detail.text()
    assert studio.current_key == "analysis"


def test_global_stop_is_visible_accessible_and_keyboard_reachable(qtbot) -> None:
    studio = _studio(qtbot)
    studio.navigate_to("live-run")
    run_page = studio.pages["live-run"]
    qtbot.mouseClick(run_page.enable_readiness_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(run_page.start_button, Qt.MouseButton.LeftButton)

    assert studio.global_stop_button is not None
    assert studio.global_stop_button.isVisible()
    assert studio.stop_action.isEnabled()
    assert studio.stop_shortcut.key() == QKeySequence("Ctrl+Shift+S")
    assert studio.global_stop_button.accessibleName() == "Stop active run"

    studio.activateWindow()
    qtbot.wait(20)
    qtbot.keyClick(run_page, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert run_page.scenario is PageScenario.FAULT
    assert studio.presenter.state.snapshot.run.lifecycle.completion is RunCompletion.ABORTED
    assert "aborted" in run_page.run_log.toPlainText()


def test_accessibility_and_advanced_disclosure_are_explicit(qtbot) -> None:
    studio = _studio(qtbot)

    assert studio.accessibleName() == "Experiment Studio demo window"
    assert studio._navigation_buttons["experiment"].accessibleName() == "Navigate to Prepare stage"
    assert "Blocked !" in studio.readiness_detail.text()
    assert "Warning" in studio.readiness_status.accessibleName()
    assert not studio.advanced_details.isVisible()
    studio.activateWindow()
    studio.advanced_toggle.setFocus(Qt.FocusReason.ShortcutFocusReason)
    qtbot.wait(20)
    assert studio.advanced_toggle.hasFocus()
    qtbot.mouseClick(studio.advanced_toggle, Qt.MouseButton.LeftButton)
    assert studio.advanced_details.isVisible()
    assert studio.advanced_toggle.text() == "Hide advanced demo details"


def test_demo_scenario_switch_updates_shared_pages_deterministically(qtbot) -> None:
    studio = _studio(qtbot)
    studio.navigate_to("geometry")
    index = studio.scenario_switch.findData(PageScenario.FAULT)
    studio.scenario_switch.setCurrentIndex(index)

    assert studio.pages["geometry"].scenario is PageScenario.FAULT
    assert "Fault" in studio.pages["geometry"].scenario_message.text()
    assert all(page.scenario is PageScenario.FAULT for page in studio.pages.values())


def test_screenshot_grab_smoke_at_1280_by_720(qtbot, tmp_path) -> None:
    studio = _studio(qtbot)
    studio.navigate_to("experiment")
    studio.resize(1280, 720)
    qtbot.wait(50)

    screenshot = studio.grab()
    output = (
        tmp_path / "experiment-studio.png"
        if os.environ.get("UPDATE_UI_REFERENCE") != "1"
        else Path("docs/ui/prototypes/experiment-studio-reference.png")
    )
    assert screenshot.width() == 1280
    assert screenshot.height() == 720
    assert screenshot.devicePixelRatio() >= 1
    assert screenshot.save(str(output), "PNG")
    assert output.stat().st_size > 0
