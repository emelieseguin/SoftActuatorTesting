"""ADR 0005-selected Instrument Console over application presenter state.

Per ADR 0005 (`docs/architecture/0005-ui-shell-evaluation.md`), this shell owns
navigation, window chrome, the persistent status/Stop strip, and the
dockable telemetry/log/file-context/run-control panels an expert operator
uses; workflow content itself stays in ``ui.views`` so this shell and the
rejected Experiment Studio prototype exercise the same deterministic pages
and fake-service bundle (``ui.demo``). Selection does not make the current
demo state or Stop semantics production-ready. Nothing here touches real
hardware, a native dialog, or the filesystem — dock-layout save/restore is an
in-memory demo affordance only, and the "demo scenario switch" only ever calls
each shared page's existing ``set_scenario`` presentation hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QSize, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFormLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QStackedWidget,
    QStyle,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from soft_actuator_testing.application.presentation import (
    ApplicationSnapshot,
    BeginRun,
    CompleteRun,
    ConfirmRunStarted,
    ConnectDevices,
    ConnectionStatus,
    EvaluateReadiness,
    GlobalStop,
    PresenterSession,
    SelectWorkspace,
)
from soft_actuator_testing.application.run_controller import RunController
from soft_actuator_testing.domain.run_state import RunCompletion, RunSnapshot, RunState
from soft_actuator_testing.ui.demo import (
    DemoEnvironment,
    build_demo_controller,
    build_demo_environment,
)
from soft_actuator_testing.ui.presenters import bind_view
from soft_actuator_testing.ui.themes import DARK_THEME, SemanticState
from soft_actuator_testing.ui.themes.qt_bridge import apply_theme_to_widget, to_qcolor, to_qfont
from soft_actuator_testing.ui.views import PAGE_REGISTRY, PageScenario, WorkflowPage, page_for_key
from soft_actuator_testing.ui.widgets import AccessibleButton, PlotCanvas, StatusIndicator
from soft_actuator_testing.ui.widgets.file_picker import FakeFilePicker, FilePicker

#: Run states where an active run can/should be interrupted by Global Stop.
_ACTIVE_RUN_STATES = frozenset({RunState.STARTING, RunState.RUNNING, RunState.STOPPING})

#: Semantic-neutral standard icons paired with text labels (never color-only)
#: for the compact left navigation rail.
_NAV_ICON_BY_KEY = {
    "home": QStyle.StandardPixmap.SP_DirHomeIcon,
    "connections": QStyle.StandardPixmap.SP_DriveNetIcon,
    "calibration": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "geometry": QStyle.StandardPixmap.SP_FileDialogContentsView,
    "experiment": QStyle.StandardPixmap.SP_DialogApplyButton,
    "live-run": QStyle.StandardPixmap.SP_MediaPlay,
    "analysis": QStyle.StandardPixmap.SP_FileDialogInfoView,
    "settings": QStyle.StandardPixmap.SP_FileDialogListView,
}


def _run_state_label(snapshot: RunSnapshot) -> str:
    label = snapshot.state.value.replace("_", " ").title()
    if snapshot.completion is not None:
        label = f"{label} ({snapshot.completion.value})"
    return label


def _run_semantic_state(snapshot: RunSnapshot) -> SemanticState:
    """Map the run-state machine onto a non-color-only semantic state."""

    if snapshot.state is RunState.FAULT:
        return SemanticState.ERROR
    if snapshot.state is RunState.COMPLETED:
        if snapshot.completion in (RunCompletion.ABORTED, RunCompletion.FAULTED):
            return SemanticState.ERROR
        return SemanticState.SUCCESS
    if snapshot.state in (RunState.CONNECTING, RunState.STARTING, RunState.RUNNING, RunState.STOPPING):
        return SemanticState.INFO
    if snapshot.state is RunState.READY:
        return SemanticState.SUCCESS
    return SemanticState.NEUTRAL


@dataclass(frozen=True)
class LayoutSnapshot:
    """An in-memory dock/toolbar layout capture; never written to a real file."""

    geometry: bytes
    state: bytes


class InstrumentConsoleWindow(QMainWindow):
    """Dense, dockable expert shell over the shared deterministic pages.

    Owns: the persistent top connection/calibration/camera/storage/run
    status strip and always-visible global Stop, the compact left page
    navigation, the central shared-page workspace, and four dockable panels
    (telemetry, event log, file/context, run control) with save/restore
    layout support. Workflow content, scenario presentation, and demo
    services all come from ``ui.views``/``ui.demo`` unmodified.
    """

    def __init__(
        self,
        *,
        environment: DemoEnvironment | None = None,
        presenter: PresenterSession | None = None,
        file_picker: FilePicker | None = None,
        scenario: PageScenario = PageScenario.READY,
        production_run: RunController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.environment = environment or build_demo_environment()
        self._demo_controller = None
        if presenter is None:
            self._demo_controller = build_demo_controller(self.environment)
            presenter = self._demo_controller.session()
        self.presenter = presenter
        self.production_run = production_run
        self.file_picker = file_picker or FakeFilePicker()
        self._scenario = PageScenario(scenario)
        self._pages: dict[str, WorkflowPage] = {}
        self._nav_actions: dict[str, QAction] = {}
        self._nav_buttons: dict[str, QWidget] = {}
        self._current_key = "home"
        self._event_counter = 0
        self._saved_layout: LayoutSnapshot | None = None

        self.setObjectName("instrument-console")
        mode_label = "Production" if production_run is not None else "Demo"
        self.setWindowTitle(f"Soft Actuator Testing — Instrument Console ({mode_label})")
        self.setAccessibleName(f"Instrument Console {mode_label.casefold()} window")
        self.setMinimumSize(1024, 640)
        self.resize(1280, 720)
        apply_theme_to_widget(self, DARK_THEME)
        self.setDockNestingEnabled(True)

        self.status_bar = self.statusBar()
        self.status_bar.setObjectName("console-status-bar")
        self.status_bar.showMessage("Demo mode — deterministic services only; no hardware connected.")

        nav_toolbar = self._build_navigation_toolbar()
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, nav_toolbar)

        status_strip = self._build_status_strip()
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, status_strip)

        self._build_central_stack()

        telemetry_dock = self._build_telemetry_dock()
        run_control_dock = self._build_run_control_dock()
        log_dock = self._build_event_log_dock()
        context_dock = self._build_file_context_dock()
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, telemetry_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, run_control_dock)
        self.splitDockWidget(telemetry_dock, run_control_dock, Qt.Orientation.Vertical)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, log_dock)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, context_dock)
        self.tabifyDockWidget(log_dock, context_dock)
        log_dock.raise_()
        self._docks: tuple[QDockWidget, ...] = (telemetry_dock, run_control_dock, log_dock, context_dock)

        self._build_menus()

        self.apply_demo_scenario(self._scenario)
        self.navigate_to("home")
        self._state_subscription = bind_view(
            self,
            self.presenter.state,
            self.render_snapshot,
        )
        self.log_event("Instrument Console demo window ready; no hardware was contacted.")
        self._production_timer: QTimer | None = None
        if self.production_run is not None:
            # Worker-owned coordinator state is read by this GUI-thread timer;
            # no worker callback ever mutates a QWidget directly.
            self._production_timer = QTimer(self)
            self._production_timer.setInterval(50)
            self._production_timer.timeout.connect(self._refresh_production_run)
            self._production_timer.start()
            self._refresh_production_run()

    # -- Public read-only surface for evaluation/tests ------------------

    @property
    def current_key(self) -> str:
        return self._current_key

    @property
    def pages(self) -> dict[str, WorkflowPage]:
        """The one shared page instance per registry key."""

        return dict(self._pages)

    # -- Construction helpers --------------------------------------------

    def _build_navigation_toolbar(self) -> QToolBar:
        toolbar = QToolBar("Navigation", self)
        toolbar.setObjectName("console-navigation")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setAccessibleName("Page navigation")
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setOrientation(Qt.Orientation.Vertical)

        for index, metadata in enumerate(PAGE_REGISTRY, start=1):
            icon = self.style().standardIcon(_NAV_ICON_BY_KEY[metadata.key])
            action = QAction(icon, metadata.short_title, self)
            action.setObjectName(f"nav-{metadata.key}")
            action.setCheckable(True)
            action.setStatusTip(metadata.description)
            action.setToolTip(f"{metadata.title} (Ctrl+{index})")
            if index <= 9:
                action.setShortcut(QKeySequence(f"Ctrl+{index}"))
            action.triggered.connect(lambda _checked=False, key=metadata.key: self.navigate_to(key))
            toolbar.addAction(action)
            button = toolbar.widgetForAction(action)
            if button is not None:
                button.setObjectName(f"nav-button-{metadata.key}")
                button.setAccessibleName(f"Navigate to {metadata.title}")
                button.setAccessibleDescription(metadata.description)
                button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                self._nav_buttons[metadata.key] = button
            self._nav_actions[metadata.key] = action
        return toolbar

    def _build_status_strip(self) -> QToolBar:
        strip = QToolBar("Status", self)
        strip.setObjectName("console-status-strip")
        strip.setMovable(False)
        strip.setFloatable(False)
        strip.setAccessibleName("Persistent connection, calibration, camera, storage, and run status")

        title = QLabel("Instrument Console", strip)
        title.setFont(to_qfont(DARK_THEME.typography.heading))
        title.setAccessibleName("Instrument Console")
        strip.addWidget(title)
        strip.addSeparator()

        self.connection_status = StatusIndicator("Connection", parent=strip)
        self.connection_status.setObjectName("status-connection")
        self.calibration_status = StatusIndicator("Calibration", parent=strip)
        self.calibration_status.setObjectName("status-calibration")
        self.camera_status = StatusIndicator("Camera", parent=strip)
        self.camera_status.setObjectName("status-camera")
        self.storage_status = StatusIndicator("Storage", parent=strip)
        self.storage_status.setObjectName("status-storage")
        self.run_status = StatusIndicator("Run", parent=strip)
        self.run_status.setObjectName("status-run")
        self.fault_status = StatusIndicator("Fault", parent=strip)
        self.fault_status.setObjectName("status-fault")
        for indicator in (
            self.connection_status,
            self.calibration_status,
            self.camera_status,
            self.storage_status,
            self.run_status,
            self.fault_status,
        ):
            indicator.apply_theme(DARK_THEME)
            strip.addWidget(indicator)
        strip.addSeparator()

        strip.addWidget(QLabel("Demo state:", strip))
        self.scenario_switch = QComboBox(strip)
        self.scenario_switch.setObjectName("console-scenario-switch")
        self.scenario_switch.setAccessibleName("Demo-only scenario switch")
        self.scenario_switch.setAccessibleDescription(
            "Apply an explicit empty, loading, ready, running, completed, or fault "
            "evaluation state to every workflow page at once."
        )
        for scenario in PageScenario:
            self.scenario_switch.addItem(scenario.value.title(), scenario)
        self.scenario_switch.currentIndexChanged.connect(self._scenario_switch_changed)
        strip.addWidget(self.scenario_switch)
        strip.addSeparator()

        self.stop_button = AccessibleButton(
            "⏹ STOP",
            accessible_description="Immediately stop the active demo run. Shortcut: Ctrl+Shift+S.",
            variant="danger",
        )
        self.stop_button.setObjectName("global-stop-button")
        self.stop_button.apply_theme(DARK_THEME)
        self.stop_button.setFont(to_qfont(DARK_THEME.typography.heading))
        palette = self.stop_button.palette()
        palette.setColor(
            self.stop_button.foregroundRole(),
            to_qcolor(DARK_THEME.state_style(SemanticState.ERROR).color),
        )
        self.stop_button.setPalette(palette)
        self.stop_button.clicked.connect(self.trigger_global_stop)
        strip.addWidget(self.stop_button)

        self.stop_action = QAction("Global Stop (Ctrl+Shift+S)", self)
        self.stop_action.setObjectName("global-stop-action")
        self.stop_action.setStatusTip("Immediately stop the active demo run (Ctrl+Shift+S)")
        self.stop_action.triggered.connect(self.trigger_global_stop)
        self.addAction(self.stop_action)
        self.stop_shortcut = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        self.stop_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.stop_shortcut.activated.connect(self.trigger_global_stop)

        strip.addSeparator()
        demo_label = QLabel("DEMO • fake services only • no hardware", strip)
        demo_label.setObjectName("demo-mode-label")
        demo_label.setAccessibleName("Demo mode: deterministic services, no hardware")
        strip.addWidget(demo_label)
        return strip

    def _build_central_stack(self) -> None:
        self.stack = QStackedWidget(self)
        self.stack.setObjectName("console-workspace-stack")
        self.stack.setAccessibleName("Workspace")
        for metadata in PAGE_REGISTRY:
            page = metadata.factory(self.presenter, self.file_picker, self.stack)
            page.scenario_changed.connect(
                lambda scenario, key=metadata.key: self._on_page_scenario_changed(key, scenario)
            )
            self._pages[metadata.key] = page
            self.stack.addWidget(page)
        self.setCentralWidget(self.stack)

    def _new_dock(self, title: str, object_name: str) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(object_name)
        dock.setAccessibleName(title)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        return dock

    def _build_telemetry_dock(self) -> QDockWidget:
        dock = self._new_dock("Telemetry", "dock-telemetry")
        self.telemetry_plot = PlotCanvas(
            title="Demo pressure telemetry (kPa)", x_label="Time (s)", y_label="Pressure (kPa)", parent=dock
        )
        self.telemetry_plot.setObjectName("telemetry-plot")
        self.telemetry_plot.apply_theme(DARK_THEME)
        dock.setWidget(self.telemetry_plot)
        self._refresh_telemetry()
        return dock

    def _build_event_log_dock(self) -> QDockWidget:
        dock = self._new_dock("Event Log", "dock-event-log")
        self.event_log = QPlainTextEdit(dock)
        self.event_log.setObjectName("console-event-log")
        self.event_log.setReadOnly(True)
        self.event_log.setAccessibleName("Console event log")
        dock.setWidget(self.event_log)
        return dock

    def _build_file_context_dock(self) -> QDockWidget:
        dock = self._new_dock("File / Context", "dock-file-context")
        content = QWidget(dock)
        form = QFormLayout(content)
        self.context_workspace_value = QLabel(content)
        self.context_workspace_value.setObjectName("context-workspace")
        self.context_workspace_value.setWordWrap(True)
        self.context_workspace_value.setAccessibleName("Workspace context")
        self.context_calibration_value = QLabel(content)
        self.context_calibration_value.setObjectName("context-calibration")
        self.context_calibration_value.setWordWrap(True)
        self.context_calibration_value.setAccessibleName("Calibration context")
        self.context_geometry_value = QLabel(content)
        self.context_geometry_value.setObjectName("context-geometry")
        self.context_geometry_value.setWordWrap(True)
        self.context_geometry_value.setAccessibleName("Geometry context")
        self.context_analysis_value = QLabel(content)
        self.context_analysis_value.setObjectName("context-analysis")
        self.context_analysis_value.setWordWrap(True)
        self.context_analysis_value.setAccessibleName("Analysis context")
        self.context_run_value = QLabel(content)
        self.context_run_value.setObjectName("context-run")
        self.context_run_value.setWordWrap(True)
        self.context_run_value.setAccessibleName("Run context")
        form.addRow("Workspace", self.context_workspace_value)
        form.addRow("Calibration", self.context_calibration_value)
        form.addRow("Geometry", self.context_geometry_value)
        form.addRow("Analysis source", self.context_analysis_value)
        form.addRow("Run state", self.context_run_value)

        self.readiness_guidance = QLabel(content)
        self.readiness_guidance.setObjectName("console-readiness-guidance")
        self.readiness_guidance.setWordWrap(True)
        self.readiness_guidance.setAccessibleName("Plain-language readiness guidance")
        form.addRow("Readiness", self.readiness_guidance)
        self.next_action_value = QLabel(content)
        self.next_action_value.setObjectName("console-next-action")
        self.next_action_value.setWordWrap(True)
        self.next_action_value.setAccessibleName("Recommended next action")
        form.addRow("Next action", self.next_action_value)
        self.diagnostics_toggle = AccessibleButton("Show readiness diagnostics")
        self.diagnostics_toggle.setObjectName("console-diagnostics-toggle")
        self.diagnostics_toggle.setCheckable(True)
        self.diagnostics_toggle.toggled.connect(self._set_diagnostics_visible)
        form.addRow(self.diagnostics_toggle)
        self.diagnostics_detail = QLabel(content)
        self.diagnostics_detail.setObjectName("console-readiness-diagnostics")
        self.diagnostics_detail.setWordWrap(True)
        self.diagnostics_detail.setAccessibleName("Readiness diagnostics")
        self.diagnostics_detail.setVisible(False)
        form.addRow("Diagnostics", self.diagnostics_detail)
        dock.setWidget(content)
        return dock

    def _build_run_control_dock(self) -> QDockWidget:
        dock = self._new_dock("Run Control", "dock-run-control")
        content = QWidget(dock)
        layout = QVBoxLayout(content)
        self.run_control_status = StatusIndicator("Run control", parent=content)
        self.run_control_status.setObjectName("run-control-status")
        self.run_control_status.apply_theme(DARK_THEME)
        layout.addWidget(self.run_control_status)

        self.run_control_enable = AccessibleButton("Enable readiness")
        self.run_control_enable.setObjectName("run-control-enable")
        self.run_control_enable.clicked.connect(self._run_control_enable_readiness)
        self.run_control_start = AccessibleButton("Start run")
        self.run_control_start.setObjectName("run-control-start")
        self.run_control_start.clicked.connect(self._run_control_start)
        self.run_control_stop = AccessibleButton("Stop run")
        self.run_control_stop.setObjectName("run-control-stop")
        self.run_control_stop.clicked.connect(self._run_control_stop)
        for button in (self.run_control_enable, self.run_control_start, self.run_control_stop):
            button.apply_theme(DARK_THEME)
            layout.addWidget(button)

        note = QLabel(
            "Quick access to the Live Run page's demo actions; the Live Run page reflects the same state.",
            content,
        )
        note.setWordWrap(True)
        note.setAccessibleName("Run control note")
        layout.addWidget(note)
        layout.addStretch(1)
        dock.setWidget(content)
        return dock

    def _build_menus(self) -> None:
        view_menu = self.menuBar().addMenu("&View")
        view_menu.setObjectName("menu-view")
        for key in ("home", "connections", "calibration", "geometry", "experiment", "live-run", "analysis", "settings"):
            view_menu.addAction(self._nav_actions[key])
        view_menu.addSeparator()
        for dock in self._docks:
            view_menu.addAction(dock.toggleViewAction())
        view_menu.addSeparator()

        self.save_layout_action = QAction("Save demo layout (Ctrl+Shift+L)", self)
        self.save_layout_action.setObjectName("save-layout-action")
        self.save_layout_action.setStatusTip("Save the current dock/toolbar arrangement in memory only")
        self.save_layout_action.triggered.connect(self.save_demo_layout)
        self.addAction(self.save_layout_action)
        self.save_layout_shortcut = QShortcut(QKeySequence("Ctrl+Shift+L"), self)
        self.save_layout_shortcut.activated.connect(self.save_demo_layout)
        view_menu.addAction(self.save_layout_action)

        self.restore_layout_action = QAction("Restore demo layout (Ctrl+Shift+R)", self)
        self.restore_layout_action.setObjectName("restore-layout-action")
        self.restore_layout_action.setStatusTip("Restore the last saved in-memory dock/toolbar arrangement")
        self.restore_layout_action.triggered.connect(self.restore_demo_layout)
        self.addAction(self.restore_layout_action)
        self.restore_layout_shortcut = QShortcut(QKeySequence("Ctrl+Shift+R"), self)
        self.restore_layout_shortcut.activated.connect(self.restore_demo_layout)
        view_menu.addAction(self.restore_layout_action)

        demo_menu = self.menuBar().addMenu("&Demo")
        demo_menu.setObjectName("menu-demo")
        demo_menu.addAction(self.stop_action)
        demo_menu.addSeparator()
        scenario_menu = demo_menu.addMenu("Apply demo state to every page")
        scenario_menu.setObjectName("menu-demo-scenario")
        for scenario in PageScenario:
            action = QAction(scenario.value.title(), self)
            action.triggered.connect(lambda _checked=False, applied=scenario: self.apply_demo_scenario(applied))
            scenario_menu.addAction(action)
        demo_menu.addSeparator()

        self.run_workflow_action = QAction("Run full simulated workflow (Ctrl+Shift+W)", self)
        self.run_workflow_action.setObjectName("run-workflow-action")
        self.run_workflow_action.setStatusTip(
            "Walk workspace \u2192 connections \u2192 calibration \u2192 geometry \u2192 "
            "experiment \u2192 live run \u2192 analysis using deterministic demo services"
        )
        self.run_workflow_action.triggered.connect(self.run_full_workflow_demo)
        self.addAction(self.run_workflow_action)
        self.run_workflow_shortcut = QShortcut(QKeySequence("Ctrl+Shift+W"), self)
        self.run_workflow_shortcut.activated.connect(self.run_full_workflow_demo)
        demo_menu.addAction(self.run_workflow_action)

    # -- Navigation -------------------------------------------------------

    def navigate_to(self, key: str) -> None:
        """Select a registered page; navigation never reconnects hardware."""

        if key not in self._pages:
            raise KeyError(f"unknown Instrument Console page: {key}")
        self._current_key = key
        self.stack.setCurrentWidget(self._pages[key])
        action = self._nav_actions.get(key)
        if action is not None and not action.isChecked():
            action.setChecked(True)
        metadata = page_for_key(key)
        self.status_bar.showMessage(f"{metadata.title} — {metadata.description}")
        self._pages[key].setFocus(Qt.FocusReason.OtherFocusReason)
        self._refresh_file_context()

    # -- Demo scenario switch ---------------------------------------------

    def apply_demo_scenario(self, scenario: PageScenario) -> None:
        """Force an explicit empty/loading/ready/running/completed/fault state.

        This only calls each shared page's existing ``set_scenario`` hook; it
        never mutates a fake service directly, matching how the workflow
        pages already treat scenario as an explicit presentation flag.
        """

        scenario = PageScenario(scenario)
        self._scenario = scenario
        for page in self._pages.values():
            page.set_scenario(scenario)
        index = self.scenario_switch.findData(scenario)
        if index >= 0 and index != self.scenario_switch.currentIndex():
            self.scenario_switch.blockSignals(True)
            self.scenario_switch.setCurrentIndex(index)
            self.scenario_switch.blockSignals(False)
        self.status_bar.showMessage(f"Demo scenario '{scenario.value}' applied to every page.", 4000)

    def _scenario_switch_changed(self, _index: int) -> None:
        data = self.scenario_switch.currentData()
        if data is not None and PageScenario(data) is not self._scenario:
            self.apply_demo_scenario(PageScenario(data))

    def _on_page_scenario_changed(self, key: str, scenario: PageScenario) -> None:
        """Record prototype fixture changes without treating them as state."""

        self.log_event(f"{page_for_key(key).short_title}: {scenario.value} state.")

    # -- Status strip / dock refresh --------------------------------------

    def _refresh_status_strip(self) -> None:
        self.render_snapshot(self.presenter.state.snapshot)

    def render_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        """Render every shell projection from one authoritative snapshot."""

        self.connection_status.set_state(
            SemanticState.SUCCESS
            if snapshot.devices.controller is ConnectionStatus.CONNECTED
            else SemanticState.NEUTRAL
        )
        self.camera_status.set_state(
            SemanticState.SUCCESS
            if snapshot.devices.camera is ConnectionStatus.CONNECTED
            else SemanticState.NEUTRAL
        )
        self.calibration_status.set_state(
            SemanticState.SUCCESS if snapshot.calibration.is_ready else SemanticState.NEUTRAL
        )
        self.storage_status.set_state(
            SemanticState.SUCCESS if snapshot.workspace.is_selected else SemanticState.NEUTRAL
        )
        self.fault_status.set_state(
            SemanticState.ERROR if snapshot.faults else SemanticState.NEUTRAL
        )

        run_semantic = _run_semantic_state(snapshot.run.lifecycle)
        self.run_status.set_state(run_semantic)
        self.run_control_status.set_state(run_semantic)
        self._refresh_stop_button(snapshot.run.lifecycle)
        self.run_control_start.setEnabled(snapshot.run.can_start)
        self.run_control_stop.setEnabled(snapshot.run.lifecycle.state is RunState.RUNNING)
        self._refresh_telemetry(snapshot)
        self._refresh_file_context(snapshot)

    def _refresh_stop_button(self, snapshot: RunSnapshot) -> None:
        active = snapshot.state in _ACTIVE_RUN_STATES
        self.stop_button.setEnabled(active)
        self.stop_action.setEnabled(active)
        description = (
            "Stop the active demo run immediately. Shortcut: Ctrl+Shift+S."
            if active
            else "No active demo run; Global Stop is idle. Shortcut: Ctrl+Shift+S."
        )
        self.stop_button.setAccessibleDescription(description)

    def _refresh_telemetry(self, snapshot: ApplicationSnapshot | None = None) -> None:
        current = snapshot or self.presenter.state.snapshot
        if current.run.telemetry:
            self.telemetry_plot.set_series(
                "pressure",
                [sample.timestamp_seconds for sample in current.run.telemetry],
                [sample.pressure_kpa for sample in current.run.telemetry],
            )

    def _refresh_file_context(self, snapshot: ApplicationSnapshot | None = None) -> None:
        current = snapshot or self.presenter.state.snapshot
        self.context_workspace_value.setText(str(current.workspace.path or "No workspace"))
        self.context_calibration_value.setText(current.calibration.fit_summary)
        self.context_geometry_value.setText(current.geometry.summary)
        self.context_analysis_value.setText(str(current.analysis.source or "No source"))
        self.context_run_value.setText(current.run.status_text)
        self.readiness_guidance.setText(current.readiness.guidance)
        self.next_action_value.setText(current.readiness.next_action)
        self.diagnostics_detail.setText("\n".join(current.readiness.diagnostics) or "No diagnostics.")

    def _set_diagnostics_visible(self, visible: bool) -> None:
        self.diagnostics_detail.setVisible(visible)
        self.diagnostics_toggle.setText(
            "Hide readiness diagnostics" if visible else "Show readiness diagnostics"
        )

    def log_event(self, message: str) -> None:
        self._event_counter += 1
        self.event_log.appendPlainText(f"[{self._event_counter:04d}] {message}")

    # -- Run control dock actions ------------------------------------------

    def _run_control_enable_readiness(self) -> None:
        if not self.presenter.state.snapshot.devices.all_connected:
            self.presenter.commands.dispatch(ConnectDevices())
        self.presenter.commands.dispatch(EvaluateReadiness())

    def _run_control_start(self) -> None:
        if self.production_run is not None:
            try:
                self.production_run.start_async()
                self.log_event("Production run start requested.")
            except Exception as error:
                self.log_event(f"Production run start failed: {error}")
            return
        result = self.presenter.commands.dispatch(BeginRun())
        if result.accepted:
            self.presenter.commands.dispatch(ConfirmRunStarted())
            self._pages["live-run"].set_scenario(PageScenario.RUNNING)

    def _run_control_stop(self) -> None:
        if self.production_run is not None:
            self.production_run.stop()
            self.log_event("Production run stop requested.")
            return
        result = self.presenter.commands.dispatch(CompleteRun())
        if result.accepted:
            self._pages["live-run"].set_scenario(PageScenario.COMPLETED)

    # -- Global Stop --------------------------------------------------------

    def trigger_global_stop(self) -> None:
        """Dispatch the one idempotent application emergency-abort command."""

        if self.production_run is not None:
            result = self.production_run.global_stop()
            self.log_event(f"Production Global STOP: {result.completion.value}")
            return
        result = self.presenter.commands.dispatch(GlobalStop())
        lifecycle = self.presenter.state.snapshot.run.lifecycle
        if lifecycle.completion is RunCompletion.ABORTED:
            self._pages["live-run"].set_scenario(PageScenario.FAULT)
        self.log_event(f"Global STOP: {result.message}")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.production_run is not None:
            self.production_run.close()
        super().closeEvent(event)

    def _refresh_production_run(self) -> None:
        """Render production coordinator snapshots only on the Qt event loop."""

        assert self.production_run is not None
        snapshot = self.production_run.snapshot
        semantic = _run_semantic_state(snapshot.lifecycle)
        self.run_status.set_state(semantic)
        self.run_control_status.set_state(semantic)
        active = snapshot.lifecycle.state in _ACTIVE_RUN_STATES
        self.stop_button.setEnabled(active)
        self.stop_action.setEnabled(active)
        self.run_control_start.setEnabled(snapshot.readiness.ready and not active)
        self.run_control_stop.setEnabled(active)
        if snapshot.telemetry:
            self.telemetry_plot.set_series(
                "pressure",
                [point.time_s for point in snapshot.telemetry],
                [point.pressure_kpa for point in snapshot.telemetry],
            )

    # -- Layout save/restore (demo-only, in-memory) ------------------------

    def capture_layout(self) -> LayoutSnapshot:
        return LayoutSnapshot(geometry=bytes(self.saveGeometry().data()), state=bytes(self.saveState().data()))

    def apply_layout(self, snapshot: LayoutSnapshot) -> bool:
        """Restore a captured layout. Never touches hardware/services."""

        geometry_ok = self.restoreGeometry(QByteArray(snapshot.geometry))
        state_ok = self.restoreState(QByteArray(snapshot.state))
        return bool(geometry_ok) and bool(state_ok)

    def save_demo_layout(self) -> None:
        self._saved_layout = self.capture_layout()
        self.log_event("Demo dock layout saved in memory (evaluation only; no file was written).")
        self.status_bar.showMessage("Layout saved (demo/in-memory only).", 4000)

    def restore_demo_layout(self) -> None:
        if self._saved_layout is None:
            self.log_event("Restore layout requested; no saved demo layout exists yet.")
            self.status_bar.showMessage("No saved demo layout to restore.", 4000)
            return
        self.apply_layout(self._saved_layout)
        self.log_event("Demo dock layout restored from the in-memory snapshot.")
        self.status_bar.showMessage("Layout restored (demo/in-memory only).", 4000)

    @property
    def saved_layout(self) -> LayoutSnapshot | None:
        return self._saved_layout

    # -- Full simulated workflow walkthrough --------------------------------

    def run_full_workflow_demo(self) -> None:
        """Walk workspace \u2192 connections \u2192 ... \u2192 analysis deterministically."""

        home = self._pages["home"]
        connections = self._pages["connections"]
        calibration = self._pages["calibration"]
        geometry = self._pages["geometry"]
        experiment = self._pages["experiment"]
        live_run = self._pages["live-run"]
        analysis = self._pages["analysis"]

        self.navigate_to("home")
        home.create_demo_workspace()
        self.presenter.commands.dispatch(SelectWorkspace(Path("/demo/full-workflow-workspace")))

        self.navigate_to("connections")
        connections.connect_devices()
        connections.request_diagnostics()

        self.navigate_to("calibration")
        calibration.collect_samples()
        calibration.fit_calibration()

        self.navigate_to("geometry")
        geometry.set_manual_geometry()
        geometry.detect_marker()

        self.navigate_to("experiment")
        experiment.check_readiness()

        self.navigate_to("live-run")
        live_run.enable_readiness()
        live_run.start_run()
        live_run.stop_run()

        self.navigate_to("analysis")
        analysis.run_analysis()

        self.log_event("Full simulated workflow walkthrough completed: workspace \u2192 analysis.")


def create_instrument_console_shell(
    *,
    environment: DemoEnvironment | None = None,
    presenter: PresenterSession | None = None,
    file_picker: FilePicker | None = None,
    scenario: PageScenario = PageScenario.READY,
    production_run: RunController | None = None,
    parent: QWidget | None = None,
) -> InstrumentConsoleWindow:
    """Build the selected normal shell with deterministic demo-only boundaries."""

    return InstrumentConsoleWindow(
        environment=environment,
        presenter=presenter,
        file_picker=file_picker,
        scenario=scenario,
        production_run=production_run,
        parent=parent,
    )


InstrumentConsoleShell = InstrumentConsoleWindow
