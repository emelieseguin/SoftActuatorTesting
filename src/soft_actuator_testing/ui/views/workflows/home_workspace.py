"""Home workflow wrapper around the persistent workspace presenter view."""

from __future__ import annotations

from soft_actuator_testing.application.workspace import WorkspaceController
from soft_actuator_testing.ui.views.base import PageScenario, WorkflowPage
from soft_actuator_testing.ui.views.home_workspace import HomeWorkspaceView


class HomeWorkspacePage(WorkflowPage):
    def __init__(self, *, workspace_controller: WorkspaceController | None = None, **kwargs) -> None:
        super().__init__("Home / Workspace", **kwargs)
        self.workspace_view = HomeWorkspaceView(
            controller=workspace_controller,
            file_picker=self.file_picker,
            parent=self,
        )
        self.layout.addWidget(self.workspace_view)
        self.workspace_label = self.workspace_view.workspace_label
        self.workspace_summary = self.workspace_view.workspace_summary
        self.choose_workspace_button = self.workspace_view.choose_workspace_button
        self.new_workspace_button = self.workspace_view.new_workspace_button
        self.layout.addStretch(1)

    def choose_workspace(self) -> None:
        self.workspace_view.choose_workspace()
        self.set_scenario(PageScenario.READY)

    def create_demo_workspace(self) -> None:
        self.workspace_view.create_workspace()
        self.set_scenario(PageScenario.EMPTY)
