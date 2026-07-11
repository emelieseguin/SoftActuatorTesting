"""Analysis workflow page."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QProgressBar, QVBoxLayout

from soft_actuator_testing.application.presentation import (
    AnalysisMode,
    ApplicationSnapshot,
    ChooseAnalysisSource,
    RunAnalysis,
    SetAnalysisMode,
)
from soft_actuator_testing.ui.views.base import PageScenario, WorkflowPage
from soft_actuator_testing.ui.widgets import AccessibleButton
from soft_actuator_testing.ui.widgets.file_picker import FileFilter


class AnalysisPage(WorkflowPage):
    def __init__(self, **kwargs) -> None:
        super().__init__("Analysis", **kwargs)
        source = self.section("Analysis source")
        source_form = QFormLayout(source)
        self.mode = QComboBox(source)
        self.mode.setObjectName("analysis-mode")
        self.mode.setAccessibleName("Analysis mode")
        self.mode.addItems(["Recorded File", "Live Capture"])
        self.mode.currentTextChanged.connect(self._update_mode)
        self.source_label = QLabel(source)
        self.source_label.setObjectName("analysis-source")
        self.source_label.setAccessibleName("Analysis source")
        self.choose_file_button = AccessibleButton("Choose recorded file")
        self.choose_file_button.setObjectName("choose-recorded-file")
        self.choose_file_button.clicked.connect(self.choose_recorded_file)
        source_form.addRow("Mode", self.mode)
        source_form.addRow("Source", self.source_label)
        source_form.addRow(self.choose_file_button)
        progress_group = self.section("Progress and review")
        progress_layout = QVBoxLayout(progress_group)
        self.analyze_button = AccessibleButton("Run demo analysis")
        self.analyze_button.setObjectName("run-analysis")
        self.analyze_button.clicked.connect(self.run_analysis)
        self.progress = QProgressBar(progress_group)
        self.progress.setObjectName("analysis-progress")
        self.progress.setRange(0, 100)
        self.progress.setAccessibleName("Analysis progress")
        self.review_label = QLabel(progress_group)
        self.review_label.setObjectName("analysis-review")
        self.review_label.setAccessibleName("Analysis review")
        progress_layout.addWidget(self.analyze_button)
        progress_layout.addWidget(self.progress)
        progress_layout.addWidget(self.review_label)
        self.layout.addStretch(1)
        self._bind_presenter()

    def render_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        analysis = snapshot.analysis
        expected_mode = "Live Capture" if analysis.mode is AnalysisMode.LIVE_CAPTURE else "Recorded File"
        if self.mode.currentText() != expected_mode:
            self.mode.blockSignals(True)
            self.mode.setCurrentText(expected_mode)
            self.mode.blockSignals(False)
        self.choose_file_button.setEnabled(analysis.mode is AnalysisMode.RECORDED_FILE)
        self.source_label.setText(
            "Live deterministic camera capture"
            if analysis.mode is AnalysisMode.LIVE_CAPTURE
            else f"Recorded file: {analysis.source}"
        )
        self.progress.setValue(analysis.progress_percent)
        self.review_label.setText(analysis.review)

    def _update_mode(self, mode: str) -> None:
        self.dispatch(
            SetAnalysisMode(
                AnalysisMode.LIVE_CAPTURE if mode == "Live Capture" else AnalysisMode.RECORDED_FILE
            )
        )

    def choose_recorded_file(self) -> None:
        selected = self.file_picker.get_open_file(
            caption="Choose recorded demo video",
            filters=(FileFilter("Video files", ("*.mp4", "*.mkv")),),
        )
        if selected is not None:
            self.dispatch(ChooseAnalysisSource(selected))
            self.set_scenario(PageScenario.READY)

    def run_analysis(self) -> None:
        self.dispatch(RunAnalysis())
        self.set_scenario(PageScenario.COMPLETED)
