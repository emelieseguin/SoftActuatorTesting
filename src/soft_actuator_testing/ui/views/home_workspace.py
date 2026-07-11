"""Presenter-backed Qt view for the persistent workspace lifecycle."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from soft_actuator_testing.application.workspace import (
    CloseWorkspace,
    CreateWorkspace,
    OpenIndividualFiles,
    OpenWorkspace,
    SaveWorkspace,
    SetStorageRoot,
    WorkspaceController,
    WorkspaceSnapshot,
)
from soft_actuator_testing.infrastructure.artifact_store import ArtifactFileStore
from soft_actuator_testing.infrastructure.workspace import JsonWorkspaceSettings
from soft_actuator_testing.ui.widgets import AccessibleButton
from soft_actuator_testing.ui.widgets.file_picker import FileFilter, FilePicker, QtFilePicker


class HomeWorkspaceView(QWidget):
    """Render workspace snapshots and dispatch workspace commands only."""

    def __init__(
        self,
        *,
        controller: WorkspaceController | None = None,
        file_picker: FilePicker | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller or WorkspaceController(
            JsonWorkspaceSettings(),
            store_factory=ArtifactFileStore,
        )
        self.file_picker = file_picker or QtFilePicker(self)
        self.setAcceptDrops(True)
        self.setAccessibleName("Home / Workspace")
        self.setAccessibleDescription("Create, open, save, or close a portable workspace.")

        layout = QVBoxLayout(self)
        current = QWidget(self)
        form = QFormLayout(current)
        self.workspace_label = QLabel(current)
        self.workspace_label.setObjectName("workspace-path")
        self.workspace_label.setAccessibleName("Current workspace path")
        form.addRow("Workspace", self.workspace_label)
        self.workspace_summary = QLabel(current)
        self.workspace_summary.setObjectName("workspace-summary")
        self.workspace_summary.setWordWrap(True)
        self.workspace_summary.setAccessibleName("Loaded workspace artifact summary")
        form.addRow("Contents", self.workspace_summary)
        self.issue_label = QLabel(current)
        self.issue_label.setObjectName("workspace-issues")
        self.issue_label.setWordWrap(True)
        self.issue_label.setAccessibleName("Workspace validation issues")
        form.addRow("Issues", self.issue_label)
        self.storage_label = QLabel(current)
        self.storage_label.setObjectName("workspace-storage-root")
        self.storage_label.setAccessibleName("Writable workspace storage root")
        form.addRow("Storage root", self.storage_label)

        self.workspace_name = QLineEdit("soft-actuator-workspace", current)
        self.workspace_name.setObjectName("workspace-name")
        self.workspace_name.setAccessibleName("New workspace name")
        form.addRow("New name", self.workspace_name)
        self.recent_workspaces = QComboBox(current)
        self.recent_workspaces.setObjectName("recent-workspaces")
        self.recent_workspaces.setAccessibleName("Recent workspaces")
        self.recent_workspaces.activated.connect(self.open_recent_workspace)
        form.addRow("Recent", self.recent_workspaces)

        storage_actions = QHBoxLayout()
        self.select_storage_button = AccessibleButton("Choose storage root")
        self.select_storage_button.setObjectName("choose-storage-root")
        self.select_storage_button.clicked.connect(self.choose_storage_root)
        self.choose_workspace_button = AccessibleButton("Open workspace")
        self.choose_workspace_button.setObjectName("choose-workspace")
        self.choose_workspace_button.clicked.connect(self.choose_workspace)
        self.new_workspace_button = AccessibleButton("Create workspace")
        self.new_workspace_button.setObjectName("create-workspace")
        self.new_workspace_button.clicked.connect(self.create_workspace)
        storage_actions.addWidget(self.select_storage_button)
        storage_actions.addWidget(self.choose_workspace_button)
        storage_actions.addWidget(self.new_workspace_button)
        form.addRow("Workspace", storage_actions)

        file_actions = QHBoxLayout()
        self.open_files_button = AccessibleButton("Open individual files")
        self.open_files_button.setObjectName("open-individual-files")
        self.open_files_button.clicked.connect(self.choose_individual_file)
        self.save_workspace_button = AccessibleButton("Save workspace")
        self.save_workspace_button.setObjectName("save-workspace")
        self.save_workspace_button.clicked.connect(lambda: self.controller.dispatch(SaveWorkspace()))
        self.close_workspace_button = AccessibleButton("Close workspace")
        self.close_workspace_button.setObjectName("close-workspace")
        self.close_workspace_button.clicked.connect(lambda: self.controller.dispatch(CloseWorkspace()))
        file_actions.addWidget(self.open_files_button)
        file_actions.addWidget(self.save_workspace_button)
        file_actions.addWidget(self.close_workspace_button)
        form.addRow("Files", file_actions)
        layout.addWidget(current)
        layout.addStretch(1)

        self._unsubscribe = self.controller.state.subscribe(self.render_snapshot, emit_current=True)
        self.destroyed.connect(self._unsubscribe)

    def render_snapshot(self, snapshot: WorkspaceSnapshot) -> None:
        self.workspace_label.setText(str(snapshot.root) if snapshot.root else snapshot.mode.value.replace("-", " ").title())
        if not snapshot.root and snapshot.mode.value == "none":
            self.workspace_label.setText("No workspace selected")
        self.workspace_summary.setText(
            snapshot.status
            if not snapshot.artifacts
            else f"{snapshot.status} Loaded: " + "; ".join(
                f"{item.kind} ({item.status})" for item in snapshot.artifacts
            )
        )
        self.issue_label.setText(
            "No validation issues."
            if not snapshot.issues
            else "\n".join(
                f"{issue.location or 'Workspace'}: {issue.message}" for issue in snapshot.issues
            )
        )
        self.storage_label.setText(str(snapshot.storage_root) if snapshot.storage_root else "Choose a writable folder.")
        self.recent_workspaces.blockSignals(True)
        self.recent_workspaces.clear()
        for path in snapshot.recent_workspaces:
            self.recent_workspaces.addItem(str(path), path)
        self.recent_workspaces.blockSignals(False)
        self.save_workspace_button.setEnabled(snapshot.can_save)

    def choose_storage_root(self) -> None:
        selected = self.file_picker.get_existing_directory(
            caption="Choose writable workspace storage root",
            directory=self.controller.snapshot.storage_root,
        )
        if selected is not None:
            self.controller.dispatch(SetStorageRoot(selected))

    def choose_workspace(self) -> None:
        selected = self.file_picker.get_existing_directory(
            caption="Open workspace",
            directory=self.controller.snapshot.storage_root,
        )
        if selected is not None:
            self.controller.dispatch(OpenWorkspace(selected))

    def create_workspace(self) -> None:
        self.controller.dispatch(CreateWorkspace(self.workspace_name.text()))

    def open_recent_workspace(self, index: int) -> None:
        path = self.recent_workspaces.itemData(index)
        if isinstance(path, Path):
            self.controller.dispatch(OpenWorkspace(path))

    def choose_individual_file(self) -> None:
        selected = self.file_picker.get_open_file(
            caption="Open individual artifact file",
            directory=self.controller.snapshot.root or self.controller.snapshot.storage_root,
            filters=(FileFilter("Versioned artifact", ("*.json",)),),
        )
        if selected is not None:
            self.controller.dispatch(OpenIndividualFiles((selected,)))

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt callback spelling
        if self._local_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt callback spelling
        paths = self._local_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        if len(paths) == 1 and paths[0].is_dir():
            self.controller.dispatch(OpenWorkspace(paths[0]))
        else:
            self.controller.dispatch(OpenIndividualFiles(tuple(paths)))
        event.acceptProposedAction()

    @staticmethod
    def _local_paths(mime_data: QMimeData) -> list[Path]:
        if not mime_data.hasUrls():
            return []
        return [Path(url.toLocalFile()) for url in mime_data.urls() if url.isLocalFile() and url.toLocalFile()]


__all__ = ["HomeWorkspaceView"]
