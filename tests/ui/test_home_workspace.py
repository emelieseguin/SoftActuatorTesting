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


def test_open_individual_files_button_imports_every_selected_file_in_order(qtbot, tmp_path: Path) -> None:
    """Mouse/keyboard multi-select must match drag/drop's multi-file behavior.

    Uses the plural ``get_open_files`` picker API (never a native dialog in
    tests) and confirms every selected path is imported, in the exact order
    the picker returned them -- the same deterministic, order-preserving
    behavior already exercised by ``dropEvent`` for multiple dragged files.
    """

    first = tmp_path / "missing-a.json"
    second = tmp_path / "missing-b.json"
    picker = FakeFilePicker(queued_multi_results=[(first, second)])
    controller = _controller(tmp_path)
    view = HomeWorkspaceView(controller=controller, file_picker=picker)
    qtbot.addWidget(view)

    qtbot.mouseClick(view.open_files_button, Qt.MouseButton.LeftButton)

    assert picker.calls[0].method == "get_open_files"
    assert controller.snapshot.mode is WorkspaceMode.INDIVIDUAL_FILES
    assert [issue.location for issue in controller.snapshot.issues] == [first, second]


def test_open_individual_files_button_is_a_safe_no_op_when_selection_is_cancelled(qtbot, tmp_path: Path) -> None:
    picker = FakeFilePicker(queued_multi_results=[()])
    controller = _controller(tmp_path)
    view = HomeWorkspaceView(controller=controller, file_picker=picker)
    qtbot.addWidget(view)

    qtbot.mouseClick(view.open_files_button, Qt.MouseButton.LeftButton)

    assert picker.calls[0].method == "get_open_files"
    assert controller.snapshot.mode is WorkspaceMode.NONE


def test_open_individual_files_button_is_keyboard_activatable(qtbot, tmp_path: Path) -> None:
    """The multi-file picker must be reachable and triggerable without a mouse."""

    first = tmp_path / "missing-a.json"
    picker = FakeFilePicker(queued_multi_results=[(first,)])
    controller = _controller(tmp_path)
    view = HomeWorkspaceView(controller=controller, file_picker=picker)
    qtbot.addWidget(view)
    view.show()
    qtbot.waitExposed(view)

    view.open_files_button.setFocus()
    qtbot.keyClick(view.open_files_button, Qt.Key.Key_Space)

    assert picker.calls[0].method == "get_open_files"
    assert controller.snapshot.mode is WorkspaceMode.INDIVIDUAL_FILES
    assert controller.snapshot.issues[0].location == first
