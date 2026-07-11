"""Shared primitives for shell-independent workflow pages."""

from __future__ import annotations

from enum import Enum

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from soft_actuator_testing.application.presentation import (
    ApplicationSnapshot,
    ConnectionStatus,
    PresenterSession,
)
from soft_actuator_testing.domain.run_state import RunCompletion, RunState
from soft_actuator_testing.ui.demo import (
    DemoEnvironment,
    build_demo_environment,
    build_demo_presenter,
)
from soft_actuator_testing.ui.presenters import bind_view
from soft_actuator_testing.ui.themes import DARK_THEME, SemanticState, Theme
from soft_actuator_testing.ui.themes.qt_bridge import apply_theme_to_widget, to_qfont
from soft_actuator_testing.ui.widgets import AccessibleButton, PlotCanvas, StatusIndicator
from soft_actuator_testing.ui.widgets.file_picker import FakeFilePicker, FilePicker


class PageScenario(str, Enum):
    """Prototype-only visual fixture; never used for workflow decisions."""

    EMPTY = "empty"
    LOADING = "loading"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAULT = "fault"


_SCENARIO_PRESENTATION = {
    PageScenario.EMPTY: (SemanticState.NEUTRAL, "Empty — no demo item is selected."),
    PageScenario.LOADING: (SemanticState.INFO, "Loading — deterministic demo data is being prepared."),
    PageScenario.READY: (SemanticState.SUCCESS, "Ready — representative demo data is available."),
    PageScenario.RUNNING: (SemanticState.INFO, "Running — a deterministic demo action is active."),
    PageScenario.COMPLETED: (SemanticState.SUCCESS, "Completed — the deterministic demo action finished."),
    PageScenario.FAULT: (SemanticState.ERROR, "Fault — simulated issue; no hardware was contacted."),
}


def _resolve_presenter(
    presenter: PresenterSession | DemoEnvironment | None,
    environment: DemoEnvironment | None,
) -> tuple[PresenterSession, DemoEnvironment | None]:
    source = environment if environment is not None else presenter
    if isinstance(source, PresenterSession):
        return source, environment
    demo_environment = source if isinstance(source, DemoEnvironment) else build_demo_environment()
    return build_demo_presenter(demo_environment), demo_environment


def semantic_connection(status: ConnectionStatus) -> SemanticState:
    """Map presenter connection state to the common visual semantic state."""

    if status is ConnectionStatus.CONNECTED:
        return SemanticState.SUCCESS
    if status is ConnectionStatus.CONNECTING:
        return SemanticState.INFO
    if status is ConnectionStatus.FAULT:
        return SemanticState.ERROR
    return SemanticState.NEUTRAL


def semantic_run(snapshot: ApplicationSnapshot) -> SemanticState:
    """Map the run lifecycle from a snapshot to the common visual state."""

    lifecycle = snapshot.run.lifecycle
    if lifecycle.state is RunState.FAULT:
        return SemanticState.ERROR
    if lifecycle.state is RunState.COMPLETED:
        return (
            SemanticState.ERROR
            if lifecycle.completion in {RunCompletion.ABORTED, RunCompletion.FAULTED}
            else SemanticState.SUCCESS
        )
    if lifecycle.state is RunState.READY:
        return SemanticState.SUCCESS
    if lifecycle.state in {RunState.CONNECTING, RunState.STARTING, RunState.RUNNING, RunState.STOPPING}:
        return SemanticState.INFO
    return SemanticState.NEUTRAL


def preview_image(preview) -> np.ndarray:
    """Return the RGB image represented by a presenter preview value."""

    return np.frombuffer(preview.rgb_bytes, dtype=np.uint8).reshape(
        preview.height,
        preview.width,
        preview.channels,
    )


class WorkflowPage(QWidget):
    """Shared page base: render snapshots and dispatch typed commands only."""

    scenario_changed = Signal(object)

    def __init__(
        self,
        title: str,
        *,
        presenter: PresenterSession | DemoEnvironment | None = None,
        environment: DemoEnvironment | None = None,
        file_picker: FilePicker | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.presenter, self.environment = _resolve_presenter(presenter, environment)
        self.file_picker = file_picker or FakeFilePicker()
        self._scenario = PageScenario.READY
        self._state_subscription = None
        self.setAccessibleName(f"{title} page")
        self.setAccessibleDescription(f"{title} workflow content; shell navigation is outside this page.")

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 16, 16, 16)
        self.layout.setSpacing(12)
        self._heading = QLabel(title, self)
        self._heading.setObjectName("page-heading")
        self._heading.setAccessibleName(f"{title} heading")
        self.layout.addWidget(self._heading)

        self.scenario_status = StatusIndicator("Page scenario", parent=self)
        self.scenario_status.setObjectName("scenario-status")
        self.scenario_message = QLabel(self)
        self.scenario_message.setObjectName("scenario-message")
        self.scenario_message.setWordWrap(True)
        self.scenario_message.setAccessibleName("Prototype visual-state fixture")
        scenario_row = QHBoxLayout()
        scenario_row.addWidget(self.scenario_status)
        scenario_row.addWidget(self.scenario_message, 1)
        self.layout.addLayout(scenario_row)
        self.apply_theme(DARK_THEME)
        self.set_scenario(PageScenario.READY)

    @property
    def scenario(self) -> PageScenario:
        return self._scenario

    @property
    def application_snapshot(self) -> ApplicationSnapshot:
        return self.presenter.state.snapshot

    def set_scenario(self, scenario: PageScenario) -> None:
        """Apply an explicit prototype visual fixture without changing state."""

        self._scenario = PageScenario(scenario)
        semantic_state, message = _SCENARIO_PRESENTATION[self._scenario]
        self.scenario_status.set_state(semantic_state)
        self.scenario_message.setText(message)
        self.scenario_message.setAccessibleDescription(message)
        self.scenario_changed.emit(self._scenario)

    def _bind_presenter(self) -> None:
        self.apply_theme(DARK_THEME)
        self._state_subscription = bind_view(
            self,
            self.presenter.state,
            self.render_snapshot,
        )

    def render_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        del snapshot

    def dispatch(self, command):
        return self.presenter.commands.dispatch(command)

    def apply_theme(self, theme: Theme) -> None:
        apply_theme_to_widget(self, theme)
        self._heading.setFont(to_qfont(theme.typography.heading))
        for widget_type in (AccessibleButton, StatusIndicator, PlotCanvas):
            for widget in self.findChildren(widget_type):
                widget.apply_theme(theme)

    def section(self, title: str) -> QGroupBox:
        group = QGroupBox(title, self)
        group.setAccessibleName(title)
        self.layout.addWidget(group)
        return group
