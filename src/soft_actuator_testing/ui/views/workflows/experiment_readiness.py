"""Experiment setup and readiness workflow page."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout

from soft_actuator_testing.application.presentation import (
    ApplicationSnapshot,
    ConfigureExperiment,
    ConnectDevices,
    EvaluateReadiness,
)
from soft_actuator_testing.ui.themes import SemanticState
from soft_actuator_testing.ui.views.base import PageScenario, WorkflowPage
from soft_actuator_testing.ui.widgets import AccessibleButton, StatusIndicator


class ExperimentSetupReadinessPage(WorkflowPage):
    def __init__(self, **kwargs) -> None:
        super().__init__("Experiment Setup / Readiness", **kwargs)
        setup = self.section("Experiment setup")
        form = QFormLayout(setup)
        self.experiment_name = QLineEdit(setup)
        self.experiment_name.setAccessibleName("Experiment name")
        self.cycles = QSpinBox(setup)
        self.cycles.setRange(1, 1000)
        self.cycles.setAccessibleName("Cycle count")
        self.record_video = QCheckBox("Record video", setup)
        self.record_video.setObjectName("record-video")
        self.record_video.setAccessibleName("Record cyclic run video")
        self.record_video.setChecked(True)
        form.addRow("Name", self.experiment_name)
        form.addRow("Cycles", self.cycles)
        form.addRow("", self.record_video)
        readiness = self.section("Readiness gate")
        readiness_layout = QVBoxLayout(readiness)
        self.readiness_status = StatusIndicator("Run readiness", parent=readiness)
        self.readiness_status.setObjectName("readiness-status")
        self.readiness_detail = QLabel(readiness)
        self.readiness_detail.setObjectName("readiness-detail")
        self.readiness_detail.setAccessibleName("Readiness details")
        self.check_readiness_button = AccessibleButton("Check readiness")
        self.check_readiness_button.setObjectName("check-readiness")
        self.check_readiness_button.clicked.connect(self.check_readiness)
        readiness_layout.addWidget(self.readiness_status)
        readiness_layout.addWidget(self.readiness_detail)
        readiness_layout.addWidget(self.check_readiness_button)
        self.layout.addStretch(1)
        self._bind_presenter()

    def render_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        readiness = snapshot.readiness
        if not self.experiment_name.hasFocus():
            self.experiment_name.setText(readiness.experiment_name)
        if not self.cycles.hasFocus():
            self.cycles.setValue(readiness.cycles)
        if not self.record_video.hasFocus():
            self.record_video.setChecked(readiness.record_video)
        self.readiness_status.set_state(
            SemanticState.SUCCESS if readiness.is_ready else SemanticState.WARNING
        )
        self.readiness_detail.setText(f"{readiness.guidance} Next: {readiness.next_action}")

    def check_readiness(self) -> None:
        self.dispatch(
            ConfigureExperiment(
                self.experiment_name.text(),
                self.cycles.value(),
                self.record_video.isChecked(),
            )
        )
        if not self.application_snapshot.devices.all_connected:
            self.dispatch(ConnectDevices())
        result = self.dispatch(EvaluateReadiness())
        self.set_scenario(PageScenario.READY if result.accepted else PageScenario.FAULT)
