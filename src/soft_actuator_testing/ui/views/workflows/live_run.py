"""Live run workflow page."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QVBoxLayout

from soft_actuator_testing.application.presentation import (
    ApplicationSnapshot,
    BeginRun,
    CompleteRun,
    ConfirmRunStarted,
    ConnectDevices,
    EvaluateReadiness,
    GlobalStop,
)
from soft_actuator_testing.domain.run_state import RunCompletion, RunState
from soft_actuator_testing.ui.views.base import PageScenario, WorkflowPage, preview_image, semantic_run
from soft_actuator_testing.ui.widgets import AccessibleButton, PlotCanvas, StatusIndicator, VideoCanvas


class LiveRunPage(WorkflowPage):
    def __init__(self, **kwargs) -> None:
        super().__init__("Live Run", **kwargs)
        controls = self.section("Run controls")
        controls_layout = QHBoxLayout(controls)
        self.enable_readiness_button = AccessibleButton("Evaluate readiness")
        self.enable_readiness_button.setObjectName("enable-demo-readiness")
        self.enable_readiness_button.clicked.connect(self.enable_readiness)
        self.start_button = AccessibleButton("Start run")
        self.start_button.setObjectName("start-demo-run")
        self.start_button.clicked.connect(self.start_run)
        self.stop_button = AccessibleButton("Complete run")
        self.stop_button.setObjectName("stop-demo-run")
        self.stop_button.clicked.connect(self.stop_run)
        controls_layout.addWidget(self.enable_readiness_button)
        controls_layout.addWidget(self.start_button)
        controls_layout.addWidget(self.stop_button)
        self.run_status = StatusIndicator("Run", parent=controls)
        self.run_status.setObjectName("live-run-status")
        controls_layout.addWidget(self.run_status)

        preview = self.section("Live preview and telemetry")
        preview_layout = QHBoxLayout(preview)
        self.live_video = VideoCanvas(accessible_title="Live preview", parent=preview)
        self.live_video.setObjectName("live-video")
        self.live_plot = PlotCanvas(
            title="Live pressure (kPa)",
            x_label="Time (s)",
            y_label="Pressure (kPa)",
            parent=preview,
        )
        self.live_plot.setObjectName("live-pressure-plot")
        preview_layout.addWidget(self.live_video)
        preview_layout.addWidget(self.live_plot)
        log_group = self.section("Run log")
        log_layout = QVBoxLayout(log_group)
        self.run_log = QPlainTextEdit(log_group)
        self.run_log.setObjectName("live-run-log")
        self.run_log.setReadOnly(True)
        self.run_log.setAccessibleName("Live run log")
        log_layout.addWidget(self.run_log)
        self.layout.addStretch(1)
        self._bind_presenter()

    def render_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        run = snapshot.run
        self.start_button.setEnabled(run.can_start)
        self.stop_button.setEnabled(run.lifecycle.state is RunState.RUNNING)
        self.run_status.set_state(semantic_run(snapshot))
        self.run_log.setPlainText(f"{run.status_text}\n{run.outcome_text}")
        if run.telemetry:
            self.live_plot.set_series(
                "pressure",
                [point.timestamp_seconds for point in run.telemetry],
                [point.pressure_kpa for point in run.telemetry],
            )
        if run.preview is not None:
            preview = run.preview
            self.live_video.set_frame(
                preview_image(preview),
                frame_index=preview.frame_index,
                frame_count=preview.frame_count,
                description=preview.description,
            )

    def enable_readiness(self) -> None:
        if not self.application_snapshot.devices.all_connected:
            self.dispatch(ConnectDevices())
        result = self.dispatch(EvaluateReadiness())
        self.set_scenario(PageScenario.READY if result.accepted else PageScenario.FAULT)

    def start_run(self) -> None:
        result = self.dispatch(BeginRun())
        if result.accepted:
            self.dispatch(ConfirmRunStarted())
            self.set_scenario(PageScenario.RUNNING)
        else:
            self.set_scenario(PageScenario.FAULT)

    def stop_run(self) -> None:
        result = self.dispatch(CompleteRun())
        self.set_scenario(PageScenario.COMPLETED if result.accepted else PageScenario.FAULT)

    def global_stop(self) -> None:
        result = self.dispatch(GlobalStop())
        if self.application_snapshot.run.lifecycle.completion is RunCompletion.ABORTED:
            self.set_scenario(PageScenario.FAULT)
        elif not result.accepted:
            self.set_scenario(PageScenario.FAULT)
