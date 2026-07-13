"""Analysis workflow page.

Composes the Qt-free ``application.analysis_pipeline`` contracts into a real,
production-capable analysis review surface:

* Recorded-file mode runs :class:`AnalysisPipeline` off the GUI thread against
  a chosen video + geometry artifact, with visible frame preview, progress,
  cancellation, and an explicit authoritative/CANCELLED/TRUNCATED completion
  state (see ``docs/architecture/2026-07-13-analysis-review-ui.md``).
* Review/correction lets an operator correct or clear a row's marker point;
  :meth:`AnalysisPipeline.recompute` deterministically rebuilds the derived
  angle without ever mutating a prior result.
* Live Capture consumes an existing, externally-owned ``CameraPanelPresenter``
  (never a second camera) and scores frames with :func:`analyze_frame` through
  a ``ProvisionalAnalysisChannel`` — these results are always labeled
  provisional and are never exportable as authoritative.
* A finalized-video handoff group accepts a production run's finalized video
  path (or its explicit unavailable state) and, once accepted, feeds it into
  the same authoritative recorded-file pipeline above.

The pre-existing demo-mode widgets (``mode``/``choose_file_button``/
``source_label``/``analyze_button``/``progress``/``review_label``) are kept
verbatim for prototype/demo compatibility and are hidden (not removed) when
``production_mode=True``.
"""

from __future__ import annotations

from pathlib import Path
from time import monotonic
from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from soft_actuator_testing.application.analysis_pipeline import (
    AnalysisArtifactExporter,
    AnalysisCancellation,
    AnalysisCompletion,
    AnalysisPipeline,
    AnalysisRunResult,
    OperatorCorrection,
    ProvisionalAnalysisChannel,
    ProvisionalAnalysisUpdate,
    analyze_frame,
)
from soft_actuator_testing.application.camera_capture import (
    CameraPanelPresenter,
    CameraPanelSnapshot,
    PreviewFrame,
)
from soft_actuator_testing.application.marker_suggestion import HsvRedThresholds, RedMarkerFrameDetector
from soft_actuator_testing.application.presentation import (
    AnalysisMode,
    ApplicationSnapshot,
    ChooseAnalysisSource,
    RunAnalysis,
    SetAnalysisMode,
)
from soft_actuator_testing.application.services import ArtifactDocument, ArtifactStore
from soft_actuator_testing.application.video_geometry_workflow import ViewTransform, frame_to_widget_point
from soft_actuator_testing.domain.analysis import AnalysisFrameResult
from soft_actuator_testing.domain.artifacts import ArtifactType
from soft_actuator_testing.domain.errors import DomainError, ErrorCode, GeometryError
from soft_actuator_testing.domain.geometry import (
    FrameSize,
    NormalizedRoi,
    PixelPoint,
    PreviewGeometryTransform,
    PreviewTransformPolicy,
    VideoGeometry,
)
from soft_actuator_testing.ui.presenters.camera import CameraPresenterBridge
from soft_actuator_testing.ui.views.base import PageScenario, WorkflowPage
from soft_actuator_testing.ui.widgets import AccessibleButton, PlotCanvas, VideoCanvas
from soft_actuator_testing.ui.widgets.file_picker import FileFilter


def _geometry_from_document(document: ArtifactDocument) -> VideoGeometry:
    """Parse a geometry artifact's payload without requiring an open video.

    Mirrors ``VideoGeometryWorkflow.load_document``'s field parsing (see
    ``application/video_geometry_workflow.py``) but skips its open-video
    frame-size mismatch check, which does not apply when only an output
    artifact store — not a video handle — is available yet.
    """

    if document.metadata.identity.artifact_type is not ArtifactType.GEOMETRY:
        raise GeometryError(ErrorCode.GEOMETRY_INVALID, "document is not a geometry artifact", "artifact_type")
    payload = document.payload
    try:
        size = payload["frame_size"]
        frame_size = FrameSize(int(size["width"]), int(size["height"]))
        base = payload["base_point"]
        base_point = PixelPoint(float(base["x"]), float(base["y"]))
        tip_data = payload.get("initial_tip_point")
        tip_point = PixelPoint(float(tip_data["x"]), float(tip_data["y"])) if tip_data else None
        roi_data = payload["roi"]
        roi = NormalizedRoi(
            float(roi_data["left"]), float(roi_data["top"]), float(roi_data["right"]), float(roi_data["bottom"])
        )
    except (KeyError, TypeError, ValueError) as error:
        raise GeometryError(ErrorCode.GEOMETRY_INVALID, "geometry document is missing required fields", "payload") from error
    return VideoGeometry(frame_size, base_point, tip_point, roi)


def _rgb_frame_from_preview(preview: PreviewFrame) -> np.ndarray:
    return np.frombuffer(preview.rgb_bytes, dtype=np.uint8).reshape(preview.height, preview.width, 3)


def _detection_description(result: AnalysisFrameResult) -> str:
    detection = result.detection
    parts = [
        f"Frame {result.frame_index}",
        f"state={detection.state.value}",
        f"confidence={detection.confidence:.2f}",
    ]
    if result.actuator_angle_degrees is not None:
        parts.append(f"angle={result.actuator_angle_degrees:.2f}\u00b0")
    if detection.correction_applied:
        parts.append("corrected by operator")
    if detection.reasons:
        parts.append("; ".join(detection.reasons))
    return " \u2014 ".join(parts)


class _AnalysisRunThread(QThread):
    """Run one bounded, cancellable finalized-video analysis off the GUI thread."""

    progress = Signal(object, object, int)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, pipeline: AnalysisPipeline, source_video: Path, geometry: VideoGeometry) -> None:
        super().__init__()
        self._pipeline = pipeline
        self._source_video = source_video
        self._geometry = geometry
        self._cancellation = AnalysisCancellation()

    def cancel(self) -> None:
        self._cancellation.cancel()

    def run(self) -> None:  # noqa: D102 - Qt override
        try:
            result = self._pipeline.analyze(
                self._source_video,
                self._geometry,
                cancellation=self._cancellation,
                on_progress=self._emit_progress,
            )
        except Exception as error:  # noqa: BLE001 - surfaced via a Qt signal, not raised across threads
            self.failed.emit(str(error))
            return
        self.succeeded.emit(result)

    def _emit_progress(self, result_row: AnalysisFrameResult, frame: Any, frame_count: int) -> None:
        self.progress.emit(result_row, frame, frame_count)


class _LiveAnalysisThread(QThread):
    """Score exactly one live-capture frame off the GUI thread; never authoritative."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        detector: RedMarkerFrameDetector,
        frame: np.ndarray,
        *,
        frame_index: int,
        video_time_seconds: float,
        geometry: VideoGeometry,
        thresholds: HsvRedThresholds | None,
    ) -> None:
        super().__init__()
        self._detector = detector
        self._frame = frame
        self._frame_index = frame_index
        self._video_time_seconds = video_time_seconds
        self._geometry = geometry
        self._thresholds = thresholds
        self._cancellation = AnalysisCancellation()

    def cancel(self) -> None:
        self._cancellation.cancel()

    def run(self) -> None:  # noqa: D102 - Qt override
        try:
            result = analyze_frame(
                self._detector,
                self._frame,
                frame_index=self._frame_index,
                video_time_seconds=self._video_time_seconds,
                geometry=self._geometry,
                thresholds=self._thresholds,
                cancellation=self._cancellation,
            )
        except Exception as error:  # noqa: BLE001 - surfaced via a Qt signal, not raised across threads
            self.failed.emit(str(error))
            return
        self.succeeded.emit(ProvisionalAnalysisUpdate(result, preview_geometry=self._geometry))


class AnalysisPage(WorkflowPage):
    def __init__(
        self,
        *,
        pipeline: AnalysisPipeline | None = None,
        artifact_store: ArtifactStore | None = None,
        camera_presenter: CameraPanelPresenter | None = None,
        live_detector: RedMarkerFrameDetector | None = None,
        live_thresholds: HsvRedThresholds | None = None,
        software_version: str | None = None,
        camera_poll_interval_ms: int = 200,
        live_display_interval_ms: int = 200,
        workspace_output_only: bool = False,
        **kwargs,
    ) -> None:
        super().__init__("Analysis", **kwargs)
        self._pipeline = pipeline or self._default_pipeline()
        self.artifact_store: ArtifactStore | None = None
        self._artifact_exporter: AnalysisArtifactExporter | None = None
        self._workspace_output_only = workspace_output_only
        self._live_detector = live_detector or self._default_detector()
        self._live_thresholds = live_thresholds
        self._software_version = software_version

        self._video_path: Path | None = None
        self._geometry: VideoGeometry | None = None
        self._geometry_artifact_id: str | None = None
        self._run_thread: _AnalysisRunThread | None = None
        self._current_result: AnalysisRunResult | None = None
        # Provenance snapshots are captured when a run starts and transferred
        # onto ``_current_result``'s companion snapshot only when that run's
        # result is actually adopted, so later changes to the live video or
        # geometry selections never retroactively relabel a completed run's
        # exported provenance (see docs/architecture/analysis-review-ui.md).
        self._pending_run_geometry_artifact_id: str | None = None
        self._current_result_geometry_artifact_id: str | None = None
        self._run_history: list[AnalysisRunResult] = []

        self._camera_presenter = camera_presenter
        self._camera_bridge: CameraPresenterBridge | None = None
        self._camera_poll_timer: QTimer | None = None
        self._live_display_timer: QTimer | None = None
        self._live_thread: _LiveAnalysisThread | None = None
        self._live_channel = ProvisionalAnalysisChannel()
        self._live_last_update: ProvisionalAnalysisUpdate | None = None
        self._live_started_at = monotonic()
        self._live_overlay_unsubscribe = None
        self._close_cleanup_complete = False

        self._build_demo_section()
        self._build_recorded_file_section()
        self._build_review_correction_section()
        self._build_live_capture_section(camera_poll_interval_ms, live_display_interval_ms)
        self._build_handoff_section()

        self.layout.addStretch(1)
        self._bind_presenter()
        if self.production_mode:
            self._demo_source_group.hide()
            self._demo_progress_group.hide()
        self.destroyed.connect(lambda: self._cancel_for_shutdown())
        self.set_artifact_store(artifact_store)
        self._refresh_run_availability()
        self._refresh_export_availability()

    # -- construction helpers -------------------------------------------------

    @staticmethod
    def _default_pipeline() -> AnalysisPipeline:
        from soft_actuator_testing.infrastructure.red_marker_detector import OpenCvRedMarkerFrameDetector
        from soft_actuator_testing.infrastructure.video_file_reader import OpenCvVideoFileReader

        return AnalysisPipeline(OpenCvVideoFileReader(), OpenCvRedMarkerFrameDetector())

    @staticmethod
    def _default_detector() -> RedMarkerFrameDetector:
        from soft_actuator_testing.infrastructure.red_marker_detector import OpenCvRedMarkerFrameDetector

        return OpenCvRedMarkerFrameDetector()

    def _build_demo_section(self) -> None:
        source = self.section("Analysis source (demo)")
        self._demo_source_group = source
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

        progress_group = self.section("Progress and review (demo)")
        self._demo_progress_group = progress_group
        progress_layout = QVBoxLayout(progress_group)
        self.analyze_button = AccessibleButton("Run demo analysis")
        self.analyze_button.setObjectName("run-analysis")
        self.analyze_button.clicked.connect(self.run_demo_analysis)
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

    def _build_recorded_file_section(self) -> None:
        self.recorded_group = self.section("Recorded-file analysis (finalized video)")
        form = QFormLayout(self.recorded_group)

        self.choose_video_button = AccessibleButton("Choose video")
        self.choose_video_button.setObjectName("choose-analysis-video")
        self.choose_video_button.clicked.connect(self.choose_video)
        self.video_path_label = QLabel("No video chosen.", self.recorded_group)
        self.video_path_label.setObjectName("analysis-video-path")
        self.video_path_label.setAccessibleName("Chosen analysis video")
        self.video_path_label.setWordWrap(True)
        video_row = QHBoxLayout()
        video_row.addWidget(self.choose_video_button)
        video_row.addWidget(self.video_path_label, 1)
        form.addRow("Video", video_row)

        self.choose_output_button = AccessibleButton("Choose output location")
        self.choose_output_button.setObjectName("choose-analysis-output")
        self.choose_output_button.clicked.connect(self.choose_output_location)
        self.output_location_label = QLabel("No output location chosen.", self.recorded_group)
        self.output_location_label.setObjectName("analysis-output-location")
        self.output_location_label.setAccessibleName("Analysis output location")
        self.output_location_label.setWordWrap(True)
        output_row = QHBoxLayout()
        output_row.addWidget(self.choose_output_button)
        output_row.addWidget(self.output_location_label, 1)
        form.addRow("Output", output_row)
        if self._workspace_output_only:
            self.choose_output_button.setEnabled(False)
            self.output_location_label.setText("Workspace output is unavailable until a workspace is opened.")

        self.geometry_artifact_id_input = QLineEdit(self.recorded_group)
        self.geometry_artifact_id_input.setObjectName("analysis-geometry-artifact-id")
        self.geometry_artifact_id_input.setAccessibleName("Geometry artifact ID")
        self.geometry_artifact_id_input.setPlaceholderText("geometry artifact ID")
        self.load_geometry_button = AccessibleButton("Load geometry")
        self.load_geometry_button.setObjectName("load-analysis-geometry")
        self.load_geometry_button.clicked.connect(self.load_geometry_artifact)
        geometry_row = QHBoxLayout()
        geometry_row.addWidget(self.geometry_artifact_id_input, 1)
        geometry_row.addWidget(self.load_geometry_button)
        form.addRow("Geometry", geometry_row)
        self.geometry_status_label = QLabel("No geometry loaded.", self.recorded_group)
        self.geometry_status_label.setObjectName("analysis-geometry-status")
        self.geometry_status_label.setAccessibleName("Geometry load status")
        self.geometry_status_label.setWordWrap(True)
        form.addRow("Geometry status", self.geometry_status_label)

        self.run_button = AccessibleButton("Run analysis")
        self.run_button.setObjectName("run-recorded-analysis")
        self.run_button.clicked.connect(self.run_recorded_analysis)
        self.cancel_button = AccessibleButton("Cancel analysis")
        self.cancel_button.setObjectName("cancel-recorded-analysis")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_run)
        run_row = QHBoxLayout()
        run_row.addWidget(self.run_button)
        run_row.addWidget(self.cancel_button)
        form.addRow(run_row)

        self.run_progress = QProgressBar(self.recorded_group)
        self.run_progress.setObjectName("recorded-analysis-progress")
        self.run_progress.setRange(0, 100)
        self.run_progress.setAccessibleName("Recorded-file analysis progress")
        form.addRow("Progress", self.run_progress)

        self.run_status_label = QLabel("Choose a video, output location, and geometry to begin.", self.recorded_group)
        self.run_status_label.setObjectName("recorded-analysis-status")
        self.run_status_label.setAccessibleName("Recorded-file analysis status")
        self.run_status_label.setWordWrap(True)
        form.addRow("Status", self.run_status_label)

        self.frame_preview = VideoCanvas(accessible_title="Recorded-file analysis frame preview", parent=self.recorded_group)
        form.addRow(self.frame_preview)
        self.detection_label = QLabel("No frame processed yet.", self.recorded_group)
        self.detection_label.setObjectName("analysis-detection")
        self.detection_label.setAccessibleName("Current frame detection state")
        self.detection_label.setWordWrap(True)
        form.addRow("Detection", self.detection_label)

        self.angle_plot = PlotCanvas(title="Actuator angle over time", x_label="Time (s)", y_label="Angle (\u00b0)")
        form.addRow(self.angle_plot)

    def _build_review_correction_section(self) -> None:
        group = self.section("Review and correction")
        layout = QVBoxLayout(group)
        self.results_table = QTableWidget(0, 7, group)
        self.results_table.setHorizontalHeaderLabels(
            ["Frame", "Time (s)", "State", "Confidence", "Angle (\u00b0)", "Corrected", "Reasons"]
        )
        self.results_table.setAccessibleName("Analysis results")
        self.results_table.setSortingEnabled(False)
        self.results_table.itemSelectionChanged.connect(self._on_row_selected)
        layout.addWidget(self.results_table)

        correction_form = QFormLayout()
        self.correction_x = QDoubleSpinBox(group)
        self.correction_x.setRange(0.0, 100000.0)
        self.correction_x.setDecimals(2)
        self.correction_x.setAccessibleName("Correction marker X")
        self.correction_y = QDoubleSpinBox(group)
        self.correction_y.setRange(0.0, 100000.0)
        self.correction_y.setDecimals(2)
        self.correction_y.setAccessibleName("Correction marker Y")
        correction_row = QHBoxLayout()
        correction_row.addWidget(self.correction_x)
        correction_row.addWidget(self.correction_y)
        correction_form.addRow("Marker (x, y)", correction_row)
        self.apply_correction_button = AccessibleButton("Apply correction")
        self.apply_correction_button.setObjectName("apply-analysis-correction")
        self.apply_correction_button.setEnabled(False)
        self.apply_correction_button.clicked.connect(self.apply_correction)
        self.clear_marker_button = AccessibleButton("Clear marker point")
        self.clear_marker_button.setObjectName("clear-analysis-marker")
        self.clear_marker_button.setEnabled(False)
        self.clear_marker_button.clicked.connect(self.clear_marker_point)
        correction_buttons = QHBoxLayout()
        correction_buttons.addWidget(self.apply_correction_button)
        correction_buttons.addWidget(self.clear_marker_button)
        correction_form.addRow(correction_buttons)
        layout.addLayout(correction_form)
        self.correction_status_label = QLabel("Select a results row to correct or clear its marker point.", group)
        self.correction_status_label.setObjectName("analysis-correction-status")
        self.correction_status_label.setAccessibleName("Correction status")
        self.correction_status_label.setWordWrap(True)
        layout.addWidget(self.correction_status_label)

        export_row = QHBoxLayout()
        self.export_button = AccessibleButton("Export results")
        self.export_button.setObjectName("export-analysis-results")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_results)
        export_row.addWidget(self.export_button)
        layout.addLayout(export_row)
        self.export_status_label = QLabel("No results exported yet.", group)
        self.export_status_label.setObjectName("analysis-export-status")
        self.export_status_label.setAccessibleName("Export status")
        self.export_status_label.setWordWrap(True)
        layout.addWidget(self.export_status_label)

    def _build_live_capture_section(self, camera_poll_interval_ms: int, live_display_interval_ms: int) -> None:
        self.live_group = self.section("Live capture (provisional preview)")
        layout = QVBoxLayout(self.live_group)
        self.live_preview = VideoCanvas(accessible_title="Live capture preview", parent=self.live_group)
        layout.addWidget(self.live_preview, 1)
        self.live_capture_status_label = QLabel("Live capture is not connected.", self.live_group)
        self.live_capture_status_label.setObjectName("live-capture-status")
        self.live_capture_status_label.setAccessibleName("Live capture health")
        self.live_capture_status_label.setWordWrap(True)
        layout.addWidget(self.live_capture_status_label)
        self.live_overlay_label = QLabel("Provisional analysis idle.", self.live_group)
        self.live_overlay_label.setObjectName("live-analysis-overlay")
        self.live_overlay_label.setAccessibleName("Provisional live analysis result")
        self.live_overlay_label.setWordWrap(True)
        layout.addWidget(self.live_overlay_label)

        if self._camera_presenter is None:
            self.live_capture_status_label.setText(
                "Live capture requires a shared camera preview; none was supplied to this page."
            )
            return
        self._live_overlay_unsubscribe = self.live_preview.register_overlay(self._paint_live_overlay)
        self._camera_bridge = CameraPresenterBridge(self._camera_presenter, self._on_camera_snapshot, parent=self)
        self._camera_poll_timer = QTimer(self)
        self._camera_poll_timer.setInterval(camera_poll_interval_ms)
        self._camera_poll_timer.timeout.connect(self._camera_presenter.refresh_status)
        self._camera_poll_timer.start()
        self._live_display_timer = QTimer(self)
        self._live_display_timer.setInterval(live_display_interval_ms)
        self._live_display_timer.timeout.connect(self._poll_live_channel)
        self._live_display_timer.start()

    def _build_handoff_section(self) -> None:
        group = self.section("Finalized recording handoff")
        layout = QFormLayout(group)
        self.source = QLabel("No finalized recording is available for analysis.", group)
        self.source.setObjectName("analysis-handoff-source")
        self.source.setAccessibleName("Finalized recording source")
        self.source.setWordWrap(True)
        self.status = QLabel(
            "Waiting for a finalized recording from a production run.",
            group,
        )
        self.status.setObjectName("analysis-handoff-status")
        self.status.setAccessibleName("Finalized recording handoff status")
        self.status.setWordWrap(True)
        self.finalized_video: Path | None = None
        self.use_finalized_button = AccessibleButton("Use as recorded-file source")
        self.use_finalized_button.setObjectName("use-finalized-video")
        self.use_finalized_button.setEnabled(False)
        self.use_finalized_button.clicked.connect(self._use_finalized_video)
        layout.addRow("Finalized recording", self.source)
        layout.addRow("Handoff", self.status)
        layout.addRow(self.use_finalized_button)

    # -- demo-mode (preserved prototype behavior) -----------------------------

    def render_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        analysis = snapshot.analysis
        expected_mode = "Live Capture" if analysis.mode is AnalysisMode.LIVE_CAPTURE else "Recorded File"
        if self.mode.currentText() != expected_mode:
            self.mode.blockSignals(True)
            self.mode.setCurrentText(expected_mode)
            self.mode.blockSignals(False)
        self._apply_mode_visibility(expected_mode)
        self.choose_file_button.setEnabled(analysis.mode is AnalysisMode.RECORDED_FILE)
        self.source_label.setText(
            "Live deterministic camera capture"
            if analysis.mode is AnalysisMode.LIVE_CAPTURE
            else f"Recorded file: {analysis.source}"
        )
        self.progress.setValue(analysis.progress_percent)
        self.review_label.setText(analysis.review)

    def _apply_mode_visibility(self, mode_text: str) -> None:
        live = mode_text == "Live Capture"
        self.recorded_group.setVisible(not live)
        self.live_group.setVisible(live)

    def _update_mode(self, mode: str) -> None:
        if not self.production_mode:
            self.dispatch(
                SetAnalysisMode(AnalysisMode.LIVE_CAPTURE if mode == "Live Capture" else AnalysisMode.RECORDED_FILE)
            )
        self._apply_mode_visibility(mode)

    def choose_recorded_file(self) -> None:
        if self.production_mode:
            return
        selected = self.file_picker.get_open_file(
            caption="Choose recorded demo video",
            filters=(FileFilter("Video files", ("*.mp4", "*.mkv")),),
        )
        if selected is not None:
            self.dispatch(ChooseAnalysisSource(selected))
            self.set_scenario(PageScenario.READY)

    def run_demo_analysis(self) -> None:
        if self.production_mode:
            return
        self.dispatch(RunAnalysis())
        self.set_scenario(PageScenario.COMPLETED)

    # -- recorded-file analysis ------------------------------------------------

    def choose_video(self) -> None:
        selected = self.file_picker.get_open_file(
            caption="Choose recorded video for analysis",
            filters=(FileFilter("Video files", ("*.mp4", "*.mkv", "*.avi")),),
        )
        if selected is None:
            return
        self._video_path = Path(selected)
        self.video_path_label.setText(str(self._video_path))
        self._refresh_run_availability()

    def choose_output_location(self) -> None:
        if self._workspace_output_only:
            self.run_status_label.setText("Analysis output is bound to the active workspace.")
            return
        selected = self.file_picker.get_existing_directory(caption="Choose analysis output workspace")
        if selected is None:
            return
        from soft_actuator_testing.infrastructure.artifact_store import ArtifactFileStore

        self.set_artifact_store(ArtifactFileStore(Path(selected)))
        self.output_location_label.setText(str(selected))

    def set_artifact_store(self, store: ArtifactStore | None) -> None:
        """Bind the current workspace's sole analysis output capability.

        Production composition uses this rather than the optional file-picker
        output path, so recorded analysis, calibration, geometry, and runs all
        persist through the same workspace-rooted store.
        """

        self.artifact_store = store
        self._artifact_exporter = (
            AnalysisArtifactExporter(store, software_version=self._software_version) if store is not None else None
        )
        if self._workspace_output_only:
            if store is None:
                self.output_location_label.setText("Workspace output is unavailable until a workspace is opened.")
            else:
                root = getattr(store, "root", None)
                self.output_location_label.setText(f"Workspace output: {root or 'active workspace'}")
            self.choose_output_button.setEnabled(False)
        self._refresh_run_availability()
        self._refresh_export_availability()

    def load_geometry_artifact(self) -> None:
        if self.artifact_store is None:
            self.geometry_status_label.setText("Choose an output location before loading a geometry artifact.")
            return
        artifact_id = self.geometry_artifact_id_input.text().strip()
        if not artifact_id:
            self.geometry_status_label.setText("Enter a geometry artifact ID to load.")
            return
        try:
            document = self.artifact_store.load(ArtifactType.GEOMETRY, artifact_id)
            geometry = _geometry_from_document(document)
        except DomainError as error:
            self.geometry_status_label.setText(str(error))
            return
        self._geometry = geometry
        self._geometry_artifact_id = artifact_id
        self.geometry_status_label.setText(
            f"Geometry loaded: {geometry.frame_size.width}x{geometry.frame_size.height}, "
            f"base ({geometry.base_point.x:.1f}, {geometry.base_point.y:.1f})."
        )
        self._update_correction_ranges()
        self._refresh_run_availability()

    def _validate_recorded_inputs(self) -> str | None:
        missing = []
        if self._video_path is None:
            missing.append("a recorded video")
        if self._geometry is None:
            missing.append("a loaded geometry artifact")
        if self.artifact_store is None:
            missing.append("an output location")
        if missing:
            return "Choose " + ", ".join(missing) + " before running analysis."
        return None

    def _refresh_run_availability(self) -> None:
        error = self._validate_recorded_inputs()
        self.run_button.setEnabled(error is None and self._run_thread is None)
        if error is not None and self._run_thread is None:
            self.run_status_label.setText(error)

    def _refresh_export_availability(self) -> None:
        """Keep the export button's enabled state truthful to export readiness.

        Mirrors the enable/disable-matches-readiness pattern already used by
        ``run_button``/``cancel_button``/``apply_correction_button``/
        ``clear_marker_button`` in this same page, so an operator cannot
        attempt (and only then be refused) an export that cannot possibly
        succeed.
        """

        self.export_button.setEnabled(
            self._current_result is not None
            and self._current_result.authoritative
            and self.artifact_store is not None
        )

    def run_analysis(self) -> None:
        """Public entry point used by shells/demos and by production callers.

        In demo (non-production) mode this preserves the original prototype
        behavior expected by ``instrument_console.py``/``experiment_studio.py``
        guided-walkthrough tests. In production mode it starts the real,
        pipeline-backed recorded-file analysis run.
        """
        if not self.production_mode:
            self.run_demo_analysis()
            return
        self.run_recorded_analysis()

    def run_recorded_analysis(self) -> None:
        if self._run_thread is not None:
            self.run_status_label.setText("An analysis run is already active; wait for it or cancel it.")
            return
        error = self._validate_recorded_inputs()
        if error is not None:
            self.run_status_label.setText(error)
            return
        assert self._video_path is not None and self._geometry is not None
        self.results_table.setRowCount(0)
        self.angle_plot.clear_series()
        self._current_result = None
        self._refresh_export_availability()
        self.run_progress.setValue(0)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.run_status_label.setText("Running finalized-video analysis\u2026")
        # Snapshot the geometry artifact ID used for *this* run now, before
        # the operator can change the live selection while the run is in
        # flight; it is only adopted as the current result's provenance once
        # this run's result is actually returned (see _on_run_succeeded).
        self._pending_run_geometry_artifact_id = self._geometry_artifact_id
        thread = _AnalysisRunThread(self._pipeline, self._video_path, self._geometry)
        self._run_thread = thread
        thread.progress.connect(self._on_run_progress)
        thread.succeeded.connect(self._on_run_succeeded)
        thread.failed.connect(self._on_run_failed)
        thread.finished.connect(self._on_run_finished)
        thread.start()
        self.set_scenario(PageScenario.RUNNING)

    def cancel_run(self) -> None:
        if self._run_thread is None:
            return
        self._run_thread.cancel()
        self.cancel_button.setEnabled(False)
        self.run_status_label.setText("Cancelling analysis\u2026")

    def _on_run_progress(self, result_row: AnalysisFrameResult, frame: Any, frame_count: int) -> None:
        self._append_result_row(result_row)
        self.frame_preview.set_frame(
            frame,
            frame_index=result_row.frame_index,
            frame_count=frame_count,
            description=_detection_description(result_row),
        )
        self.detection_label.setText(_detection_description(result_row))
        percent = int(round(100 * (result_row.frame_index + 1) / frame_count)) if frame_count else 0
        self.run_progress.setValue(percent)
        self.run_status_label.setText(f"Running \u2014 processed frame {result_row.frame_index + 1} of {frame_count}.")

    def _on_run_succeeded(self, result: AnalysisRunResult) -> None:
        self._current_result = result
        self._refresh_export_availability()
        # Adopt this run's geometry-artifact-id snapshot alongside its
        # result so export always reflects what was actually analyzed, even
        # if the live selection has since changed (repeated runs, cancelled
        # or truncated runs included).
        self._current_result_geometry_artifact_id = self._pending_run_geometry_artifact_id
        self._run_history.append(result)
        self._render_results_table(result)
        self._refresh_angle_plot(result)
        if result.completion is AnalysisCompletion.COMPLETED:
            self.run_progress.setValue(100)
        status_by_completion = {
            AnalysisCompletion.COMPLETED: "Completed \u2014 authoritative analysis is ready to export.",
            AnalysisCompletion.CANCELLED: "Cancelled \u2014 this partial result is not authoritative and cannot be exported.",
            AnalysisCompletion.TRUNCATED: "Truncated \u2014 this partial result is not authoritative and cannot be exported.",
        }
        status = status_by_completion[result.completion]
        if result.detail:
            status = f"{status} ({result.detail})"
        self.run_status_label.setText(status)
        self.set_scenario(PageScenario.COMPLETED if result.completion is AnalysisCompletion.COMPLETED else PageScenario.FAULT)

    def _on_run_failed(self, message: str) -> None:
        self.run_status_label.setText(f"Analysis failed: {message}")
        self.set_scenario(PageScenario.FAULT)

    def _on_run_finished(self) -> None:
        thread = self._run_thread
        self._run_thread = None
        # A failed run never adopted the pending snapshot (_on_run_succeeded
        # was not called), so discard it rather than let it linger and be
        # mistakenly adopted by a later, unrelated run.
        self._pending_run_geometry_artifact_id = None
        self.cancel_button.setEnabled(False)
        self._refresh_run_availability()
        if thread is not None:
            thread.deleteLater()

    def _append_result_row(self, result: AnalysisFrameResult) -> None:
        detection = result.detection
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)
        angle_text = "" if result.actuator_angle_degrees is None else f"{result.actuator_angle_degrees:.2f}"
        values = (
            str(result.frame_index),
            f"{result.video_time_seconds:.3f}",
            detection.state.value,
            f"{detection.confidence:.2f}",
            angle_text,
            "yes" if detection.correction_applied else "no",
            "; ".join(detection.reasons),
        )
        for column, value in enumerate(values):
            self.results_table.setItem(row, column, QTableWidgetItem(value))

    def _render_results_table(self, result: AnalysisRunResult) -> None:
        self.results_table.setRowCount(0)
        for row_result in result.results:
            self._append_result_row(row_result)

    def _refresh_angle_plot(self, result: AnalysisRunResult) -> None:
        times = [row.video_time_seconds for row in result.results if row.actuator_angle_degrees is not None]
        angles = [row.actuator_angle_degrees for row in result.results if row.actuator_angle_degrees is not None]
        self.angle_plot.set_series("angle", times, angles)

    # -- review / correction ---------------------------------------------------

    def _update_correction_ranges(self) -> None:
        if self._geometry is None:
            return
        self.correction_x.setRange(0.0, float(self._geometry.frame_size.width))
        self.correction_y.setRange(0.0, float(self._geometry.frame_size.height))

    def _selected_row(self) -> int:
        return self.results_table.currentRow()

    def _on_row_selected(self) -> None:
        row = self._selected_row()
        if self._current_result is None or row < 0 or row >= len(self._current_result.results):
            self.apply_correction_button.setEnabled(False)
            self.clear_marker_button.setEnabled(False)
            return
        result = self._current_result.results[row]
        if result.detection.point is not None:
            self.correction_x.setValue(result.detection.point.x)
            self.correction_y.setValue(result.detection.point.y)
        self.apply_correction_button.setEnabled(True)
        self.clear_marker_button.setEnabled(True)
        self.detection_label.setText(_detection_description(result))

    def apply_correction(self) -> None:
        row = self._selected_row()
        if self._current_result is None or row < 0 or row >= len(self._current_result.results):
            self.correction_status_label.setText("Select a results row before applying a correction.")
            return
        point = PixelPoint(self.correction_x.value(), self.correction_y.value())
        self._apply_correction(row, point, "operator correction")

    def clear_marker_point(self) -> None:
        row = self._selected_row()
        if self._current_result is None or row < 0 or row >= len(self._current_result.results):
            self.correction_status_label.setText("Select a results row before clearing its marker.")
            return
        self._apply_correction(row, None, "operator cleared marker")

    def _apply_correction(self, row: int, point: PixelPoint | None, reason: str) -> None:
        assert self._current_result is not None
        try:
            correction = OperatorCorrection(row, point, reason)
            updated = AnalysisPipeline.recompute(self._current_result, (correction,))
        except DomainError as error:
            self.correction_status_label.setText(str(error))
            return
        self._current_result = updated
        self._refresh_export_availability()
        self._render_results_table(updated)
        self._refresh_angle_plot(updated)
        self.results_table.selectRow(row)
        self.correction_status_label.setText(f"Applied correction to frame {row}; corrected rows are marked in the table.")

    def export_results(self) -> None:
        if self._current_result is None:
            self.export_status_label.setText("Run an analysis before exporting results.")
            return
        if not self._current_result.authoritative:
            self.export_status_label.setText(
                "Only a completed, authoritative analysis can be exported; this result is "
                f"{self._current_result.completion.value} and not authoritative."
            )
            return
        if self.artifact_store is None:
            self.export_status_label.setText("Choose an output location before exporting.")
            return
        exporter = self._artifact_exporter
        if exporter is None:
            # Defensive support for callers that predate ``set_artifact_store``
            # and set the public capability directly.
            exporter = AnalysisArtifactExporter(self.artifact_store, software_version=self._software_version)
        try:
            results_document, manifest_document = exporter.export(
                self._current_result,
                source_video=str(self._current_result.source_video),
                geometry_artifact_id=self._current_result_geometry_artifact_id or "",
            )
        except DomainError as error:
            self.export_status_label.setText(str(error))
            return
        self.export_status_label.setText(
            f"Exported analysis results {results_document.metadata.identity.artifact_id} "
            f"and manifest {manifest_document.metadata.identity.artifact_id}."
        )

    # -- live capture ------------------------------------------------------------

    def _on_camera_snapshot(self, snapshot: CameraPanelSnapshot) -> None:
        health = snapshot.health
        text = snapshot.status_text
        if snapshot.error:
            text = f"{text} {snapshot.error}"
        text = f"{text} (dropped {health.dropped_frames}, duplicate {health.duplicate_frames})"
        self.live_capture_status_label.setText(text)
        self.live_capture_status_label.setAccessibleDescription(text)
        if snapshot.preview is None:
            return
        frame = _rgb_frame_from_preview(snapshot.preview)
        self.live_preview.set_frame(
            frame,
            frame_index=snapshot.preview.index,
            description="Live camera preview (provisional; not authoritative)",
        )
        self._maybe_start_live_analysis(frame, snapshot.preview.index)

    def _maybe_start_live_analysis(self, frame: np.ndarray, frame_index: int) -> None:
        if self._live_thread is not None:
            return
        geometry = self._geometry
        if geometry is None:
            self.live_overlay_label.setText(
                "Provisional analysis idle: load a geometry artifact in Recorded-file analysis first."
            )
            return
        preview_size = FrameSize(int(frame.shape[1]), int(frame.shape[0]))
        try:
            # The production FFmpeg proxy uses ``scale=width:height`` and
            # therefore stretches to its declared preview dimensions.  Keep
            # that policy explicit so preview-derived coordinates match pixels.
            preview_geometry = PreviewGeometryTransform.create(
                geometry.frame_size,
                preview_size,
                policy=PreviewTransformPolicy.STRETCH,
            ).map_geometry(geometry)
        except GeometryError as error:
            self.live_overlay_label.setText(
                f"Provisional analysis idle: preview geometry is incompatible ({error})."
            )
            return
        video_time_seconds = monotonic() - self._live_started_at
        thread = _LiveAnalysisThread(
            self._live_detector,
            frame,
            frame_index=frame_index,
            video_time_seconds=video_time_seconds,
            geometry=preview_geometry,
            thresholds=self._live_thresholds,
        )
        self._live_thread = thread
        thread.succeeded.connect(self._on_live_succeeded)
        thread.failed.connect(self._on_live_failed)
        thread.finished.connect(self._on_live_finished)
        thread.start()

    def _on_live_succeeded(self, update: ProvisionalAnalysisUpdate) -> None:
        self._live_channel.publish(update)

    def _on_live_failed(self, message: str) -> None:
        self.live_overlay_label.setText(f"Provisional analysis error: {message}")

    def _on_live_finished(self) -> None:
        thread = self._live_thread
        self._live_thread = None
        if thread is not None:
            thread.deleteLater()

    def _poll_live_channel(self) -> None:
        update = self._live_channel.consume_latest()
        if update is None:
            return
        self._live_last_update = update
        stats = self._live_channel.stats
        detection = update.result.detection
        parts = [
            "Provisional (live) \u2014 not authoritative",
            "preview-derived",
            f"frame {update.result.frame_index}",
            f"state={detection.state.value}",
            f"confidence={detection.confidence:.2f}",
        ]
        if update.result.actuator_angle_degrees is not None:
            parts.append(f"angle={update.result.actuator_angle_degrees:.2f}\u00b0")
        if detection.reasons:
            parts.append("; ".join(detection.reasons))
        parts.append(f"published={stats.published}, consumed={stats.consumed}, dropped-stale={stats.dropped_stale}")
        text = " \u2014 ".join(parts)
        self.live_overlay_label.setText(text)
        self.live_overlay_label.setAccessibleDescription(text)
        self.live_preview.update()

    def _paint_live_overlay(self, painter: QPainter, rect: QRect) -> None:
        update = self._live_last_update
        if update is None:
            return
        geometry = update.preview_geometry or self._geometry
        if geometry is None:
            return
        point = update.result.detection.point
        if point is None:
            return
        visible = ViewTransform().visible_rect(geometry.frame_size)
        x, y = frame_to_widget_point(point, geometry.frame_size, visible, rect.width(), rect.height())
        painter.setPen(QPen(QColor("#2ecc71"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(x, y), 6, 6)

    # -- finalized-video handoff --------------------------------------------------

    def receive_finalization(self, result: Any) -> None:
        self.finalized_video = result.video_path
        if result.video_path is None:
            self.source.setText("No finalized video is available from this run.")
            self.status.setText(
                "Analysis unavailable: recording was disabled, unavailable, or did not finalize a video."
            )
            self.use_finalized_button.setEnabled(False)
            return
        self.source.setText(str(result.video_path))
        self.status.setText("Finalized recording is ready for authoritative analysis handoff.")
        self.use_finalized_button.setEnabled(True)

    def _use_finalized_video(self) -> None:
        if self.finalized_video is None:
            return
        self._video_path = self.finalized_video
        self.video_path_label.setText(str(self._video_path))
        self.run_status_label.setText(
            "Using the finalized recording as the recorded-file analysis source. "
            "Provisional live results are never presented or exported as authoritative."
        )
        self._refresh_run_availability()

    # -- bounded shutdown -------------------------------------------------------

    def _cancel_run_for_shutdown(self) -> None:
        thread = self._run_thread
        if thread is None:
            return
        thread.cancel()
        thread.wait(5000)
        self._run_thread = None

    def _cancel_live_for_shutdown(self) -> None:
        thread = self._live_thread
        if thread is None:
            return
        thread.cancel()
        thread.wait(2000)
        self._live_thread = None

    def _cancel_for_shutdown(self) -> None:
        self._cancel_run_for_shutdown()
        self._cancel_live_for_shutdown()

    def _close_cleanup(self) -> None:
        if self._close_cleanup_complete:
            return
        self._close_cleanup_complete = True
        self._cancel_for_shutdown()
        if self._camera_poll_timer is not None:
            self._camera_poll_timer.stop()
        if self._live_display_timer is not None:
            self._live_display_timer.stop()
        if self._camera_bridge is not None:
            self._camera_bridge.dispose()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override naming
        self._close_cleanup()
        super().closeEvent(event)
