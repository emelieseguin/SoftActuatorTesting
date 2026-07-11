"""Video geometry and marker setup workflow page."""

from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QLabel, QVBoxLayout

from soft_actuator_testing.application.presentation import (
    ApplicationSnapshot,
    DetectMarker,
    SetManualGeometry,
)
from soft_actuator_testing.application.services import ArtifactStore
from soft_actuator_testing.application.video_geometry_workflow import VideoGeometryWorkflow
from soft_actuator_testing.infrastructure.video_file_reader import OpenCvVideoFileReader
from soft_actuator_testing.ui.views.base import PageScenario, WorkflowPage, preview_image
from soft_actuator_testing.ui.views.marker_suggestion import MarkerSuggestionView
from soft_actuator_testing.ui.views.video_geometry import VideoGeometryView
from soft_actuator_testing.ui.widgets import AccessibleButton, VideoCanvas


class VideoGeometryMarkerSetupPage(WorkflowPage):
    def __init__(
        self,
        *,
        geometry_workflow: VideoGeometryWorkflow | None = None,
        artifact_store: ArtifactStore | None = None,
        **kwargs,
    ) -> None:
        super().__init__("Video Geometry / Marker Setup", **kwargs)
        canvas_group = self.section("Synthetic camera frame")
        canvas_layout = QVBoxLayout(canvas_group)
        self.video = VideoCanvas(accessible_title="Geometry setup preview", parent=canvas_group)
        self.video.setObjectName("geometry-video")
        canvas_layout.addWidget(self.video)
        setup_group = self.section("Geometry controls")
        form = QFormLayout(setup_group)
        self.geometry_summary = QLabel(setup_group)
        self.geometry_summary.setObjectName("geometry-summary")
        self.geometry_summary.setAccessibleName("Geometry summary")
        self.manual_button = AccessibleButton("Set manual geometry")
        self.manual_button.setObjectName("set-manual-geometry")
        self.manual_button.clicked.connect(self.set_manual_geometry)
        self.auto_button = AccessibleButton("Detect marker automatically")
        self.auto_button.setObjectName("detect-marker")
        self.auto_button.clicked.connect(self.detect_marker)
        form.addRow("Current geometry", self.geometry_summary)
        form.addRow(self.manual_button, self.auto_button)

        # Real, replaceable manual geometry authoring lives in its own Qt-free
        # workflow/widget pair, independent of the demo presenter above.
        self.geometry_view = VideoGeometryView(
            geometry_workflow or VideoGeometryWorkflow(OpenCvVideoFileReader()),
            file_picker=self.file_picker,
            artifact_store=artifact_store,
            parent=self,
        )
        self.layout.addWidget(self.geometry_view)

        # Guided red-marker suggestions complement (never replace) the manual
        # geometry editor above: it shares the same VideoGeometryWorkflow and
        # canvas, so an accepted candidate becomes an ordinary tip point that
        # can still be corrected manually.
        self.marker_suggestion_view = MarkerSuggestionView(self.geometry_view, parent=self)
        self.layout.addWidget(self.marker_suggestion_view)

        self.layout.addStretch(1)
        self._bind_presenter()

    def render_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        geometry = snapshot.geometry
        self.geometry_summary.setText(geometry.summary)
        if geometry.preview is not None:
            preview = geometry.preview
            self.video.set_frame(
                preview_image(preview),
                frame_index=preview.frame_index,
                frame_count=preview.frame_count,
                description=preview.description,
            )

    def set_manual_geometry(self) -> None:
        self.dispatch(SetManualGeometry())
        self.video.setAccessibleDescription(
            "Geometry setup preview; manual geometry selected by accessible controls."
        )
        self.set_scenario(PageScenario.READY)

    def detect_marker(self) -> None:
        self.dispatch(DetectMarker())
        self.set_scenario(PageScenario.COMPLETED)
