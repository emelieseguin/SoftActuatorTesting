"""Light, guided Experiment Studio shell used only for ADR 0005 evaluation.

The shell owns navigation and cross-page guidance.  Workflow widgets remain in
``ui.views`` so this prototype and the Instrument Console exercise the same
deterministic pages and fake-service bundle.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from soft_actuator_testing.application.presentation import (
    ApplicationSnapshot,
    GlobalStop,
    PresenterSession,
)
from soft_actuator_testing.domain.run_state import RunCompletion, RunState
from soft_actuator_testing.ui.demo import DemoEnvironment, build_demo_controller, build_demo_environment
from soft_actuator_testing.ui.presenters import bind_view
from soft_actuator_testing.ui.themes import LIGHT_THEME, SemanticState
from soft_actuator_testing.ui.themes.qt_bridge import apply_theme_to_widget, to_qcolor, to_qfont
from soft_actuator_testing.ui.views import PAGE_REGISTRY, PageScenario, WorkflowPage, page_for_key
from soft_actuator_testing.ui.widgets import AccessibleButton, StatusIndicator
from soft_actuator_testing.ui.widgets.file_picker import FakeFilePicker, FilePicker

_STAGE_KEYS = ("connections", "calibration", "geometry", "experiment", "live-run", "analysis")
_NAVIGATION_KEYS = ("home", *_STAGE_KEYS, "settings")
_RUN_PREREQUISITES = frozenset(("connections", "calibration", "geometry", "experiment"))


class _Card(QFrame):
    """A palette-backed surface card; no stylesheet is needed for the shell."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, to_qcolor(LIGHT_THEME.colors.surface))
        palette.setColor(QPalette.ColorRole.WindowText, to_qcolor(LIGHT_THEME.colors.text_primary))
        self.setPalette(palette)


class ExperimentStudioWindow(QMainWindow):
    """Guided, revisitable stage shell over the shared deterministic pages."""

    def __init__(
        self,
        *,
        environment: DemoEnvironment | None = None,
        presenter: PresenterSession | None = None,
        file_picker: FilePicker | None = None,
        scenario: PageScenario = PageScenario.READY,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.environment = environment or build_demo_environment()
        self._demo_controller = None
        if presenter is None:
            self._demo_controller = build_demo_controller(self.environment)
            presenter = self._demo_controller.session()
        self.presenter = presenter
        self.file_picker = file_picker or FakeFilePicker()
        self._scenario = PageScenario(scenario)
        self._pages: dict[str, WorkflowPage] = {}
        self._navigation_buttons: dict[str, QToolButton] = {}

        self.setObjectName("experiment-studio")
        self.setWindowTitle("Soft Actuator Testing — Experiment Studio (Demo)")
        self.setAccessibleName("Experiment Studio demo window")
        self.setMinimumSize(960, 600)
        self.resize(1280, 720)
        apply_theme_to_widget(self, LIGHT_THEME)

        self._build_global_toolbar()
        self._build_content()
        self.set_scenario(self._scenario)
        self.navigate_to("home")
        self._state_subscription = bind_view(
            self,
            self.presenter.state,
            self.render_snapshot,
        )

    @property
    def current_key(self) -> str:
        return self._current_key

    @property
    def completed_stages(self) -> frozenset[str]:
        """Completed stage keys, exposed for evaluation tests and summaries."""

        return self.presenter.state.snapshot.completed_steps & frozenset(_STAGE_KEYS)

    @property
    def pages(self) -> dict[str, WorkflowPage]:
        """The one shared page instance per registry key."""

        return dict(self._pages)

    def _build_global_toolbar(self) -> None:
        toolbar = QToolBar("Global controls", self)
        toolbar.setObjectName("studio-global-controls")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setAccessibleName("Global controls")
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        title = QLabel("Experiment Studio", toolbar)
        title.setFont(to_qfont(LIGHT_THEME.typography.heading))
        title.setAccessibleName("Experiment Studio")
        toolbar.addWidget(title)
        toolbar.addSeparator()

        self.stop_action = QAction("Stop Run (Ctrl+Shift+S)", self)
        self.stop_action.setObjectName("global-stop-action")
        self.stop_action.setStatusTip("Stop the active simulated run (Ctrl+Shift+S)")
        self.stop_action.triggered.connect(self.stop_active_run)
        self.addAction(self.stop_action)
        toolbar.addAction(self.stop_action)
        self.stop_shortcut = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        self.stop_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.stop_shortcut.activated.connect(self.stop_active_run)
        self.global_stop_button = toolbar.widgetForAction(self.stop_action)
        if self.global_stop_button is not None:
            self.global_stop_button.setObjectName("global-stop-button")
            self.global_stop_button.setAccessibleName("Stop active run")
            self.global_stop_button.setAccessibleDescription(
                "Global Stop remains available in every stage. Shortcut: Ctrl+Shift+S."
            )

        toolbar.addSeparator()
        demo_label = QLabel("DEMO • deterministic services • no hardware", toolbar)
        demo_label.setObjectName("demo-mode-label")
        demo_label.setAccessibleName("Demo mode: deterministic services, no hardware")
        toolbar.addWidget(demo_label)

    def _build_content(self) -> None:
        root = QWidget(self)
        root.setObjectName("studio-root")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(16)

        root_layout.addWidget(self._build_sidebar())
        root_layout.addWidget(self._build_workspace(), 1)
        self.setCentralWidget(root)

    def _build_sidebar(self) -> QWidget:
        sidebar = _Card(self)
        sidebar.setObjectName("studio-sidebar")
        sidebar.setAccessibleName("Experiment stages")
        sidebar.setMinimumWidth(230)
        sidebar.setMaximumWidth(300)
        sidebar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        heading = QLabel("Experiment flow", sidebar)
        heading.setFont(to_qfont(LIGHT_THEME.typography.heading))
        heading.setAccessibleName("Experiment flow stages")
        layout.addWidget(heading)
        guidance = QLabel("Complete stages in order, or revisit any completed stage.", sidebar)
        guidance.setWordWrap(True)
        guidance.setAccessibleName("Stage navigation guidance")
        layout.addWidget(guidance)

        self._button_group = QButtonGroup(sidebar)
        self._button_group.setExclusive(True)
        for index, key in enumerate(_NAVIGATION_KEYS, start=1):
            metadata = page_for_key(key)
            button = QToolButton(sidebar)
            prefix = str(index) if key in _STAGE_KEYS else ""
            button.setText(f"{prefix + '. ' if prefix else ''}{metadata.short_title}")
            button.setObjectName(f"stage-{key}")
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setMinimumHeight(36)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setAccessibleName(f"Navigate to {metadata.short_title} stage")
            button.setAccessibleDescription(
                f"{metadata.description} Completed stages remain available for review."
            )
            button.clicked.connect(lambda _checked=False, page_key=key: self.navigate_to(page_key))
            self._button_group.addButton(button)
            self._navigation_buttons[key] = button
            layout.addWidget(button)

        layout.addStretch(1)
        summary = _Card(sidebar)
        summary.setObjectName("experiment-summary-card")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(12, 12, 12, 12)
        summary_layout.setSpacing(6)
        summary_heading = QLabel("Experiment summary", summary)
        summary_heading.setFont(to_qfont(LIGHT_THEME.typography.heading))
        summary_layout.addWidget(summary_heading)
        self.summary_label = QLabel(summary)
        self.summary_label.setObjectName("experiment-summary")
        self.summary_label.setWordWrap(True)
        self.summary_label.setAccessibleName("Experiment progress summary")
        summary_layout.addWidget(self.summary_label)
        self.readiness_status = StatusIndicator("Run readiness", parent=summary)
        self.readiness_status.setObjectName("studio-readiness-status")
        self.readiness_status.apply_theme(LIGHT_THEME)
        summary_layout.addWidget(self.readiness_status)
        self.readiness_detail = QLabel(summary)
        self.readiness_detail.setObjectName("studio-readiness-detail")
        self.readiness_detail.setWordWrap(True)
        self.readiness_detail.setAccessibleName("Run readiness details")
        summary_layout.addWidget(self.readiness_detail)
        layout.addWidget(summary)
        return sidebar

    def _build_workspace(self) -> QWidget:
        workspace = QWidget(self)
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        context = _Card(workspace)
        context.setObjectName("studio-context-card")
        context_layout = QVBoxLayout(context)
        context_layout.setContentsMargins(16, 12, 16, 12)
        context_layout.setSpacing(8)
        self.stage_title = QLabel(context)
        self.stage_title.setObjectName("studio-stage-title")
        self.stage_title.setFont(to_qfont(LIGHT_THEME.typography.display))
        self.stage_title.setAccessibleName("Current stage")
        context_layout.addWidget(self.stage_title)
        self.stage_description = QLabel(context)
        self.stage_description.setObjectName("studio-stage-description")
        self.stage_description.setWordWrap(True)
        self.stage_description.setAccessibleName("Current stage guidance")
        context_layout.addWidget(self.stage_description)

        action_row = QHBoxLayout()
        self.primary_action = AccessibleButton(
            "Complete stage and continue",
            accessible_description="Mark this guided demo stage complete and move to the next stage.",
        )
        self.primary_action.setObjectName("studio-primary-action")
        self.primary_action.clicked.connect(self.complete_current_stage)
        action_row.addWidget(self.primary_action)
        action_row.addStretch(1)
        self.scenario_switch = QComboBox(context)
        self.scenario_switch.setObjectName("studio-scenario-switch")
        self.scenario_switch.setAccessibleName("Demo-only scenario switch")
        self.scenario_switch.setAccessibleDescription(
            "Choose an explicit deterministic evaluation state for the active workflow page."
        )
        for item in PageScenario:
            self.scenario_switch.addItem(item.value.title(), item)
        self.scenario_switch.currentIndexChanged.connect(self._scenario_selected)
        action_row.addWidget(QLabel("Demo state:", context))
        action_row.addWidget(self.scenario_switch)
        context_layout.addLayout(action_row)

        self.advanced_toggle = QToolButton(context)
        self.advanced_toggle.setObjectName("studio-advanced-toggle")
        self.advanced_toggle.setText("Show advanced demo details")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.advanced_toggle.setAccessibleName("Show advanced demo details")
        self.advanced_toggle.setAccessibleDescription(
            "Progressive disclosure for deterministic service and scenario details."
        )
        self.advanced_toggle.toggled.connect(self._set_advanced_visible)
        context_layout.addWidget(self.advanced_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self.advanced_details = QLabel(
            "Advanced details: shared fake serial, camera, detector, lifecycle, analysis, and artifact "
            "services are held in one in-memory DemoEnvironment. No ports, cameras, files, or clocks are used.",
            context,
        )
        self.advanced_details.setObjectName("studio-advanced-details")
        self.advanced_details.setWordWrap(True)
        self.advanced_details.setAccessibleName("Advanced deterministic demo details")
        self.advanced_details.setVisible(False)
        context_layout.addWidget(self.advanced_details)
        layout.addWidget(context)

        self.run_cockpit = _Card(workspace)
        self.run_cockpit.setObjectName("studio-run-cockpit")
        cockpit_layout = QHBoxLayout(self.run_cockpit)
        cockpit_layout.setContentsMargins(16, 10, 16, 10)
        cockpit_heading = QLabel("RUN COCKPIT", self.run_cockpit)
        cockpit_heading.setFont(to_qfont(LIGHT_THEME.typography.heading))
        cockpit_heading.setAccessibleName("Run cockpit")
        cockpit_layout.addWidget(cockpit_heading)
        self.cockpit_status = StatusIndicator("Cockpit run state", parent=self.run_cockpit)
        self.cockpit_status.setObjectName("studio-cockpit-status")
        self.cockpit_status.apply_theme(LIGHT_THEME)
        cockpit_layout.addWidget(self.cockpit_status)
        cockpit_layout.addStretch(1)
        cockpit_note = QLabel("Global Stop: Ctrl+Shift+S", self.run_cockpit)
        cockpit_note.setAccessibleName("Global Stop shortcut Ctrl+Shift+S")
        cockpit_layout.addWidget(cockpit_note)
        self.run_cockpit.setVisible(False)
        layout.addWidget(self.run_cockpit)

        page_card = _Card(workspace)
        page_card.setObjectName("studio-page-card")
        page_layout = QVBoxLayout(page_card)
        page_layout.setContentsMargins(0, 0, 0, 0)
        self.page_stack = QStackedWidget(page_card)
        self.page_stack.setObjectName("studio-page-stack")
        self.page_stack.setAccessibleName("Workflow content")
        for metadata in PAGE_REGISTRY:
            page = metadata.factory(self.presenter, self.file_picker, self.page_stack)
            page.apply_theme(LIGHT_THEME)
            page.scenario_changed.connect(self._page_scenario_changed)
            self._pages[metadata.key] = page
            self.page_stack.addWidget(page)

        scroll = QScrollArea(page_card)
        scroll.setObjectName("studio-page-scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.page_stack)
        page_layout.addWidget(scroll)
        layout.addWidget(page_card, 1)
        return workspace

    def navigate_to(self, key: str) -> None:
        """Select a registered page without invalidating its prior completion."""

        if key not in self._pages:
            raise KeyError(f"unknown Experiment Studio page: {key}")
        self._current_key = key
        self.page_stack.setCurrentWidget(self._pages[key])
        self._navigation_buttons[key].setChecked(True)
        metadata = page_for_key(key)
        self.stage_title.setText(metadata.title)
        self.stage_description.setText(metadata.description)
        self.stage_description.setAccessibleDescription(metadata.description)
        is_run = key == "live-run"
        self.run_cockpit.setVisible(is_run)
        self._set_primary_action_text()
        self._refresh_context()

    def complete_current_stage(self) -> None:
        """Execute the stage command(s), then advance without local workflow state."""

        key = self._current_key
        if key not in _STAGE_KEYS:
            next_key = "connections" if key == "home" else "home"
            self.navigate_to(next_key)
            return
        current_page = self._pages[key]
        if key == "connections":
            current_page.connect_devices()
            current_page.request_diagnostics()
        elif key == "calibration":
            current_page.collect_samples()
            current_page.fit_calibration()
        elif key == "geometry":
            current_page.set_manual_geometry()
            current_page.detect_marker()
        elif key == "experiment":
            current_page.check_readiness()
        elif key == "live-run":
            current_page.enable_readiness()
            current_page.start_run()
            current_page.stop_run()
        elif key == "analysis":
            current_page.run_analysis()
        index = _STAGE_KEYS.index(key)
        self.navigate_to(_STAGE_KEYS[index + 1] if index + 1 < len(_STAGE_KEYS) else "analysis")

    def set_scenario(self, scenario: PageScenario) -> None:
        """Set the explicit demo state for every shared page deterministically."""

        self._scenario = PageScenario(scenario)
        for page in self._pages.values():
            page.set_scenario(self._scenario)
        index = self.scenario_switch.findData(self._scenario)
        if index >= 0 and index != self.scenario_switch.currentIndex():
            self.scenario_switch.setCurrentIndex(index)
        self._refresh_context()

    def stop_active_run(self) -> None:
        """Dispatch the same emergency-abort command as the selected Console."""

        result = self.presenter.commands.dispatch(GlobalStop())
        if self.presenter.state.snapshot.run.lifecycle.completion is RunCompletion.ABORTED:
            self._pages["live-run"].set_scenario(PageScenario.FAULT)
        self.statusBar().showMessage(result.message, 4000)

    def _scenario_selected(self, _index: int) -> None:
        scenario = self.scenario_switch.currentData()
        if scenario is not None and PageScenario(scenario) is not self._scenario:
            self.set_scenario(PageScenario(scenario))

    def _set_advanced_visible(self, visible: bool) -> None:
        self.advanced_details.setVisible(visible)
        self.advanced_toggle.setText(
            "Hide advanced demo details" if visible else "Show advanced demo details"
        )

    def _page_scenario_changed(self, _scenario: PageScenario) -> None:
        self._refresh_context()

    def _set_primary_action_text(self) -> None:
        if self._current_key not in _STAGE_KEYS:
            self.primary_action.setText(
                "Begin guided setup" if self._current_key == "home" else "Return to Home"
            )
            return
        index = _STAGE_KEYS.index(self._current_key)
        next_key = _STAGE_KEYS[index + 1] if index + 1 < len(_STAGE_KEYS) else "analysis"
        self.primary_action.setText(
            f"Complete {page_for_key(self._current_key).short_title} → "
            f"{page_for_key(next_key).short_title}"
        )

    def _refresh_context(self) -> None:
        snapshot = self.presenter.state.snapshot
        completed_steps = snapshot.completed_steps
        completed = [page_for_key(key).short_title for key in _STAGE_KEYS if key in completed_steps]
        complete_text = ", ".join(completed) if completed else "No stages completed"
        self.summary_label.setText(
            f"Demo: {snapshot.readiness.experiment_name}\n"
            f"{len(completed_steps & frozenset(_STAGE_KEYS))}/{len(_STAGE_KEYS)} stages complete\n"
            f"{complete_text}"
        )

        if snapshot.readiness.is_ready:
            self.readiness_status.set_state(SemanticState.SUCCESS)
            self.readiness_detail.setText(
                f"{snapshot.readiness.guidance} Next: {snapshot.readiness.next_action}"
            )
        else:
            self.readiness_status.set_state(SemanticState.WARNING)
            self.readiness_detail.setText(
                f"{snapshot.readiness.guidance} Next: {snapshot.readiness.next_action}"
            )

        self.stop_action.setEnabled(snapshot.run.can_global_stop)
        active = snapshot.run.lifecycle.state in {
            RunState.STARTING,
            RunState.RUNNING,
            RunState.STOPPING,
        }
        self.cockpit_status.set_state(SemanticState.INFO if active else SemanticState.NEUTRAL)

    def render_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        del snapshot
        self._refresh_context()


def create_experiment_studio_shell(
    *,
    environment: DemoEnvironment | None = None,
    presenter: PresenterSession | None = None,
    file_picker: FilePicker | None = None,
    scenario: PageScenario = PageScenario.READY,
    parent: QWidget | None = None,
) -> ExperimentStudioWindow:
    """Factory for evaluation launches; defaults are deterministic demo-only boundaries."""

    return ExperimentStudioWindow(
        environment=environment,
        presenter=presenter,
        file_picker=file_picker,
        scenario=scenario,
        parent=parent,
    )


ExperimentStudioShell = ExperimentStudioWindow
