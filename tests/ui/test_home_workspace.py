"""pytest-qt coverage for workspace picker and drag/drop command paths."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent

from soft_actuator_testing.application.workspace import WorkspaceController, WorkspaceMode
from soft_actuator_testing.infrastructure.artifact_store import ArtifactFileStore
from soft_actuator_testing.infrastructure.workspace import JsonWorkspaceSettings
from soft_actuator_testing.ui.views.home_workspace import HomeWorkspaceView
from soft_actuator_testing.ui.widgets.file_picker import FakeFilePicker


def _controller(tmp_path: Path) -> WorkspaceController:
    return WorkspaceController(
        JsonWorkspaceSettings(tmp_path / "preferences.json"),
        store_factory=ArtifactFileStore,
    )


def test_picker_create_open_cancel_and_recents_are_presenter_driven(qtbot, tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    picker = FakeFilePicker(queued_results=[storage, None])
    controller = _controller(tmp_path)
    view = HomeWorkspaceView(controller=controller, file_picker=picker)
    qtbot.addWidget(view)

    qtbot.mouseClick(view.select_storage_button, Qt.MouseButton.LeftButton)
    view.workspace_name.setText("qt-workspace")
    qtbot.mouseClick(view.new_workspace_button, Qt.MouseButton.LeftButton)
    assert controller.snapshot.mode is WorkspaceMode.WORKSPACE
    assert str(storage / "qt-workspace") == view.workspace_label.text()
    assert view.recent_workspaces.count() == 1
    view.open_recent_workspace(0)
    assert controller.snapshot.root == storage / "qt-workspace"
    qtbot.mouseClick(view.choose_workspace_button, Qt.MouseButton.LeftButton)
    assert len(picker.calls) == 2
    assert controller.snapshot.root == storage / "qt-workspace"


def test_drop_invalid_local_file_reports_validation_issue_without_native_dialog(qtbot, tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    view = HomeWorkspaceView(controller=controller, file_picker=FakeFilePicker())
    qtbot.addWidget(view)
    missing = tmp_path / "missing.json"
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(missing))])
    event = QDropEvent(QPointF(2, 2), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)

    view.dropEvent(event)

    assert controller.snapshot.mode is WorkspaceMode.INDIVIDUAL_FILES
    assert controller.snapshot.issues[0].location == missing
    assert "does not exist" in view.issue_label.text()
