"""Headless behavior tests for the shell-independent prototype workflow pages."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from soft_actuator_testing.application.workspace import CreateWorkspace, SetStorageRoot, WorkspaceController
from soft_actuator_testing.infrastructure.artifact_store import ArtifactFileStore
from soft_actuator_testing.infrastructure.workspace import JsonWorkspaceSettings
from soft_actuator_testing.ui.demo import build_demo_environment
from soft_actuator_testing.ui.views import (
    AnalysisPage,
    CalibrationPage,
    ConnectionsDiagnosticsPage,
    ExperimentSetupReadinessPage,
    HomeWorkspacePage,
    LiveRunPage,
    PAGE_REGISTRY,
    PageScenario,
    SettingsProfilesHelpPage,
    VideoGeometryMarkerSetupPage,
    WorkflowPage,
    page_for_key,
    page_navigation,
)
from soft_actuator_testing.ui.widgets.file_picker import FakeFilePicker


def test_registry_is_ordered_complete_and_shell_neutral() -> None:
    assert [item.key for item in page_navigation()] == [
        "home",
        "connections",
        "calibration",
        "geometry",
        "experiment",
        "live-run",
        "analysis",
        "settings",
    ]
    assert [item.order for item in PAGE_REGISTRY] == sorted(item.order for item in PAGE_REGISTRY)
    assert page_for_key("live-run").short_title == "Run"


@pytest.mark.parametrize("metadata", PAGE_REGISTRY, ids=lambda item: item.key)
def test_every_page_builds_accessibly_without_shell_navigation(qtbot, metadata) -> None:
    page = metadata.factory(build_demo_environment(), FakeFilePicker(), None)
    qtbot.addWidget(page)
    assert isinstance(page, WorkflowPage)
    assert page.accessibleName() == f"{metadata.title} page"
    assert page.findChild(type(page.scenario_message), "scenario-message") is not None
    # Navigation belongs to a consuming shell, never to a shared workflow page.
    assert page.findChild(type(page.scenario_message), "shell-navigation") is None


@pytest.mark.parametrize("metadata", PAGE_REGISTRY, ids=lambda item: item.key)
@pytest.mark.parametrize("scenario", list(PageScenario), ids=lambda item: item.value)
def test_every_page_renders_all_explicit_scenarios(qtbot, metadata, scenario) -> None:
    page = metadata.factory(build_demo_environment(), FakeFilePicker(), None)
    qtbot.addWidget(page)
    page.set_scenario(scenario)
    assert page.scenario is scenario
    assert scenario.value.title() in page.scenario_message.text()
    assert page.scenario_status.accessibleName().startswith("Page scenario status:")


def test_home_uses_injected_file_picker_through_workspace_presenter(qtbot, tmp_path: Path) -> None:
    controller = WorkspaceController(
        JsonWorkspaceSettings(tmp_path / "preferences.json"),
        store_factory=ArtifactFileStore,
    )
    controller.dispatch(SetStorageRoot(tmp_path))
    controller.dispatch(CreateWorkspace("workspace"))
    workspace = tmp_path / "workspace"
    picker = FakeFilePicker(queued_results=[workspace])
    page = HomeWorkspacePage(file_picker=picker, workspace_controller=controller)
    qtbot.addWidget(page)
    qtbot.mouseClick(page.choose_workspace_button, Qt.MouseButton.LeftButton)
    assert picker.calls[0].method == "get_existing_directory"
    assert str(workspace) == page.workspace_label.text()
    page.create_demo_workspace()
    assert page.scenario is PageScenario.EMPTY


def test_connections_and_diagnostics_use_fake_services(qtbot) -> None:
    page = ConnectionsDiagnosticsPage()
    qtbot.addWidget(page)
    qtbot.mouseClick(page.connect_button, Qt.MouseButton.LeftButton)
    assert page.environment.services.serial.is_connected is True
    assert page.environment.services.camera.is_open is True
    qtbot.mouseClick(page.diagnostic_button, Qt.MouseButton.LeftButton)
    assert page.environment.services.serial.sent_commands == ["DEMO:DIAGNOSTICS"]
    assert "No physical controller" in page.diagnostic_log.toPlainText()
    page.disconnect_devices()
    assert page.environment.services.serial.is_connected is False


def test_calibration_collects_deterministic_samples_and_fits(qtbot) -> None:
    page = CalibrationPage()
    qtbot.addWidget(page)
    assert page.fit_button.isEnabled() is False
    qtbot.mouseClick(page.collect_button, Qt.MouseButton.LeftButton)
    assert page.samples_table.rowCount() == 3
    qtbot.mouseClick(page.fit_button, Qt.MouseButton.LeftButton)
    assert "R²" in page.fit_summary.text()
    assert page.scenario is PageScenario.COMPLETED


def test_geometry_supports_manual_and_automatic_marker_setup(qtbot) -> None:
    page = VideoGeometryMarkerSetupPage()
    qtbot.addWidget(page)
    qtbot.mouseClick(page.manual_button, Qt.MouseButton.LeftButton)
    assert "Manual geometry" in page.geometry_summary.text()
    qtbot.mouseClick(page.auto_button, Qt.MouseButton.LeftButton)
    assert "Automatic marker: detected" in page.geometry_summary.text()


def test_experiment_readiness_is_visibly_gated(qtbot) -> None:
    page = ExperimentSetupReadinessPage()
    qtbot.addWidget(page)
    assert "Blocked" in page.readiness_detail.text()
    page.experiment_name.setText("")
    qtbot.mouseClick(page.check_readiness_button, Qt.MouseButton.LeftButton)
    assert page.scenario is PageScenario.FAULT
    page.experiment_name.setText("Demo validation")
    qtbot.mouseClick(page.check_readiness_button, Qt.MouseButton.LeftButton)
    assert "Ready:" in page.readiness_detail.text()
    assert page.record_video.isChecked()
    page.record_video.setChecked(False)
    qtbot.mouseClick(page.check_readiness_button, Qt.MouseButton.LeftButton)
    assert page.application_snapshot.readiness.record_video is False


def test_live_run_gates_start_then_updates_preview_plot_log_and_stop(qtbot) -> None:
    page = LiveRunPage()
    qtbot.addWidget(page)
    assert page.start_button.isEnabled() is False
    qtbot.mouseClick(page.enable_readiness_button, Qt.MouseButton.LeftButton)
    assert page.start_button.isEnabled() is True
    qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)
    assert page.scenario is PageScenario.RUNNING
    assert page.live_plot.series_names() == ("pressure",)
    assert "synthetic" in page.live_video.accessibleDescription()
    qtbot.mouseClick(page.stop_button, Qt.MouseButton.LeftButton)
    assert page.scenario is PageScenario.COMPLETED
    assert "completed cleanly" in page.run_log.toPlainText()


def test_analysis_uses_file_picker_and_supports_live_capture(qtbot) -> None:
    picker = FakeFilePicker(queued_results=[Path("/recordings/run-0042.mp4")])
    page = AnalysisPage(file_picker=picker)
    qtbot.addWidget(page)
    qtbot.mouseClick(page.choose_file_button, Qt.MouseButton.LeftButton)
    assert picker.calls[0].method == "get_open_file"
    assert "/recordings/run-0042.mp4" in page.source_label.text()
    page.mode.setCurrentText("Live Capture")
    assert page.choose_file_button.isEnabled() is False
    qtbot.mouseClick(page.analyze_button, Qt.MouseButton.LeftButton)
    assert page.progress.value() == 100
    assert "Reviewed 6 frames" in page.review_label.text()


def test_settings_apply_session_only_profile_and_help(qtbot) -> None:
    page = SettingsProfilesHelpPage()
    qtbot.addWidget(page)
    page.profile.setCurrentText("Researcher")
    page.compact_mode.setChecked(True)
    qtbot.mouseClick(page.save_settings_button, Qt.MouseButton.LeftButton)
    assert "Researcher profile applied" in page.settings_result.text()
    assert "deterministic fake services" in page.findChild(type(page.settings_result), "help-text").text()
