"""Hardware-free pytest-qt tests for the real analysis review UI.

See ``docs/architecture/analysis-review-ui.md`` for the full test plan this
file implements.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QRect, QTimer
from PySide6.QtGui import QImage, QPainter

from soft_actuator_testing.application.analysis_pipeline import (
    AnalysisArtifactExporter,
    AnalysisCompletion,
    AnalysisPipeline,
    ProvisionalAnalysisChannel,
    ProvisionalAnalysisUpdate,
)
from soft_actuator_testing.application.camera_capture import (
    CameraCaptureService,
    CameraDevice,
    CameraPanelPresenter,
    CaptureHealth,
    LatestFrameChannel,
    PreviewFrame,
)
from soft_actuator_testing.application.marker_suggestion import (
    FakeRedMarkerFrameDetector,
    MarkerSuggestionCancelled,
    RedBlob,
    RedMarkerScan,
)
from soft_actuator_testing.application.services import ArtifactDocument
from soft_actuator_testing.application.video_geometry_workflow import FakeVideoFrameSource
from soft_actuator_testing.domain.analysis import AnalysisFrameResult, DetectionState, MarkerDetectionResult
from soft_actuator_testing.domain.artifacts import ArtifactIdentity, ArtifactMetadata, ArtifactType
from soft_actuator_testing.domain.errors import GeometryError, ErrorCode
from soft_actuator_testing.domain.geometry import FrameSize, NormalizedRoi, PixelPoint, VideoGeometry
from soft_actuator_testing.infrastructure.artifact_store import ArtifactFileStore
from soft_actuator_testing.infrastructure.camera import FakeCameraDeviceSource
from soft_actuator_testing.ui.views.workflows.analysis import AnalysisPage
from soft_actuator_testing.ui.widgets.file_picker import FakeFilePicker

SOURCE = Path("analysis-review.avi")
SIZE = FrameSize(100, 80)
GEOMETRY = VideoGeometry(SIZE, PixelPoint(10, 40), PixelPoint(90, 40), NormalizedRoi(0, 0, 100, 80))


def _blob(x: float, y: float, *, area: float = 100.0) -> RedBlob:
    return RedBlob(PixelPoint(x, y), NormalizedRoi(x - 3, y - 3, x + 3, y + 3), area, 36.0, 1.0)


def _pipeline(
    *, fps: float = 20.0, blobs: tuple[tuple[RedBlob, ...], ...], video_path: Path = SOURCE
) -> tuple[AnalysisPipeline, FakeVideoFrameSource]:
    source = FakeVideoFrameSource()
    frames = tuple(np.zeros((80, 100, 3), dtype=np.uint8) for _ in blobs)
    source.register(video_path, frames, fps=fps)
    detector = FakeRedMarkerFrameDetector()
    for frame, frame_blobs in zip(frames, blobs, strict=True):
        detector.register(frame, RedMarkerScan(SIZE, GEOMETRY.actuator_roi, frame_blobs, None))
    return AnalysisPipeline(source, detector), source


def _manifest_artifact_id(status_text: str) -> str:
    """Extract the shared results/manifest artifact ID from an export status message."""
    marker = "Exported analysis results "
    assert status_text.startswith(marker), status_text
    return status_text[len(marker) :].split(" ", 1)[0]


def _geometry_document(artifact_id: str = "geom-1") -> ArtifactDocument:
    now = datetime.now(timezone.utc)
    payload = {
        "frame_size": {"width": SIZE.width, "height": SIZE.height},
        "base_point": {"x": 10.0, "y": 40.0},
        "initial_tip_point": {"x": 90.0, "y": 40.0},
        "roi": {"left": 0.0, "top": 0.0, "right": float(SIZE.width), "bottom": float(SIZE.height)},
    }
    return ArtifactDocument(
        ArtifactMetadata(ArtifactIdentity(ArtifactType.GEOMETRY, artifact_id), now, now, "test"),
        payload,
    )


def _prepared_page(qtbot, tmp_path: Path, *, pipeline: AnalysisPipeline, artifact_id: str = "geom-1", **kwargs) -> AnalysisPage:
    """Build a page with a chosen video/output/geometry already set up."""

    store = ArtifactFileStore(tmp_path)
    store.save(_geometry_document(artifact_id))
    (tmp_path / SOURCE).write_bytes(b"workspace-analysis-video")
    picker = FakeFilePicker(queued_results=[SOURCE, tmp_path])
    page = AnalysisPage(pipeline=pipeline, file_picker=picker, production_mode=True, **kwargs)
    qtbot.addWidget(page)
    page.choose_video()
    page.choose_output_location()
    page.geometry_artifact_id_input.setText(artifact_id)
    page.load_geometry_artifact()
    return page


class _BlockingDetector:
    """Blocks in ``scan`` until cancelled; mirrors marker_suggestion.py's test double."""

    def __init__(self) -> None:
        self.started = Event()

    def scan(self, frame, thresholds, roi, *, cancellation=None):
        del frame, thresholds, roi
        self.started.set()
        while cancellation is None or not cancellation.is_cancelled():
            Event().wait(0.005)
        raise MarkerSuggestionCancelled("blocked scan interrupted")


def _blocking_pipeline() -> tuple[AnalysisPipeline, _BlockingDetector]:
    source = FakeVideoFrameSource()
    frames = tuple(np.zeros((80, 100, 3), dtype=np.uint8) for _ in range(5))
    source.register(SOURCE, frames, fps=10.0)
    detector = _BlockingDetector()
    return AnalysisPipeline(source, detector), detector


# -- 1. input validation ------------------------------------------------------


def test_run_analysis_without_inputs_reports_specific_validation_message(qtbot, tmp_path: Path) -> None:
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page = AnalysisPage(pipeline=pipeline, production_mode=True)
    qtbot.addWidget(page)

    page.run_analysis()

    assert page._run_thread is None
    message = page.run_status_label.text()
    assert "recorded video" in message
    assert "geometry artifact" in message
    assert "output location" in message
    assert not page.run_button.isEnabled()


# -- 2. frame-zero display -----------------------------------------------------


def test_first_progress_signal_displays_frame_zero_not_skipped(qtbot, tmp_path: Path) -> None:
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),), (_blob(90, 40),)))
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline)

    observed = []
    render_progress = page._on_run_progress

    def capture_progress(result_row, frame, frame_count) -> None:
        observed.append(result_row)
        render_progress(result_row, frame, frame_count)

    page._on_run_progress = capture_progress  # type: ignore[method-assign]
    page.run_analysis()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)

    assert observed
    first_row = observed[0]
    assert first_row.frame_index == 0  # frame zero is measured, never skipped
    assert page.results_table.rowCount() == 2
    assert page.results_table.item(0, 0).text() == "0"


# -- 3. progress ---------------------------------------------------------------


def test_progress_bar_and_table_update_incrementally(qtbot, tmp_path: Path) -> None:
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),), (), (_blob(80, 40),)))
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline)

    page.run_analysis()
    qtbot.waitUntil(lambda: page.results_table.rowCount() == 3, timeout=2000)
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)

    assert page.run_progress.value() == 100
    assert page.results_table.item(1, 2).text() == DetectionState.MISSING.value
    assert "Completed" in page.run_status_label.text()
    assert "authoritative" in page.run_status_label.text()


# -- 4. cancellation -------------------------------------------------------------


def test_cancel_run_yields_explicit_non_authoritative_cancelled_result(qtbot, tmp_path: Path) -> None:
    pipeline, detector = _blocking_pipeline()
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline)

    page.run_analysis()
    qtbot.waitUntil(detector.started.is_set, timeout=2000)
    assert page.cancel_button.isEnabled()

    page.cancel_run()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)

    assert page._current_result is not None
    assert page._current_result.completion is AnalysisCompletion.CANCELLED
    assert not page._current_result.authoritative
    assert "Cancelled" in page.run_status_label.text()
    assert "not authoritative" in page.run_status_label.text()
    assert page.run_button.isEnabled()


# -- 5. truncated result display -------------------------------------------------


def test_truncated_result_is_explicit_and_non_authoritative(qtbot, tmp_path: Path) -> None:
    frames = (np.zeros((80, 100, 3), dtype=np.uint8), np.zeros((80, 100, 3), dtype=np.uint8))

    class OverreportedHandle:
        metadata = SimpleNamespace(frame_size=SIZE, frame_count=3, fps=10.0)

        def read_frame(self, frame_index: int):
            if frame_index >= len(frames):
                raise GeometryError(ErrorCode.GEOMETRY_INVALID, "cannot read the requested frame", "frame_index")
            return frames[frame_index]

        def close(self) -> None:
            pass

    class OverreportedSource:
        def open(self, _source: Path, *, cancellation=None):
            del cancellation
            return OverreportedHandle()

    detector = FakeRedMarkerFrameDetector()
    for frame in frames:
        detector.register(frame, RedMarkerScan(SIZE, GEOMETRY.actuator_roi, (_blob(90, 40),), None))
    pipeline = AnalysisPipeline(OverreportedSource(), detector)
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline)

    page.run_analysis()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)

    assert page._current_result is not None
    assert page._current_result.completion is AnalysisCompletion.TRUNCATED
    assert not page._current_result.authoritative
    assert "Truncated" in page.run_status_label.text()
    assert page.results_table.rowCount() == 2


# -- 6. correction / recompute ----------------------------------------------------


def test_correction_and_clear_marker_recompute_without_mutating_prior_result(qtbot, tmp_path: Path) -> None:
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),), (),))
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline)

    page.run_analysis()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)
    original = page._current_result
    assert original is not None

    # Correct the missing row (index 1) to a manual point.
    page.results_table.selectRow(1)
    assert page.apply_correction_button.isEnabled()
    page.correction_x.setValue(50.0)
    page.correction_y.setValue(40.0)
    page.apply_correction()

    corrected = page._current_result
    assert corrected is not original
    assert original.results[1].detection.state is DetectionState.MISSING  # prior result untouched
    assert corrected.results[1].detection.state is DetectionState.MANUAL
    assert corrected.results[1].detection.correction_applied
    assert corrected.results[1].actuator_angle_degrees is not None
    assert page.results_table.item(1, 5).text() == "yes"

    # Clear the marker back to missing on row 0.
    page.results_table.selectRow(0)
    page.clear_marker_point()
    cleared = page._current_result
    assert cleared.results[0].detection.state is DetectionState.MISSING
    assert cleared.results[0].detection.point is None
    assert cleared.results[0].actuator_angle_degrees is None
    assert cleared.results[0].detection.correction_applied
    # Completion/authoritative flags are preserved by recompute.
    assert cleared.completion is original.completion
    assert cleared.authoritative == original.authoritative


# -- 7. export ---------------------------------------------------------------------


def test_export_refuses_non_authoritative_and_succeeds_when_authoritative(qtbot, tmp_path: Path) -> None:
    pipeline, detector = _blocking_pipeline()
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline)
    page.run_analysis()
    qtbot.waitUntil(detector.started.is_set, timeout=2000)
    page.cancel_run()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)

    page.export_results()
    assert "not authoritative" in page.export_status_label.text()

    good_pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page2 = _prepared_page(qtbot, tmp_path, pipeline=good_pipeline, artifact_id="geom-2")
    page2.run_analysis()
    qtbot.waitUntil(lambda: page2._run_thread is None, timeout=2000)
    assert page2._current_result.authoritative

    page2.export_results()
    assert "Exported analysis results" in page2.export_status_label.text()

    # A second export after this authoritative result creates a new artifact,
    # never overwriting/mutating the previous one.
    first_message = page2.export_status_label.text()
    page2.export_results()
    second_message = page2.export_status_label.text()
    assert first_message != second_message


def test_export_button_enabled_state_truthfully_matches_export_readiness(qtbot, tmp_path: Path) -> None:
    """export_button must be disabled whenever export_results() would refuse.

    Mirrors the enable/disable-matches-readiness pattern already used by
    run_button/cancel_button/apply_correction_button/clear_marker_button on
    this same page (see docs/architecture/quality-ui-accessibility.md).
    """

    pipeline, detector = _blocking_pipeline()
    page = AnalysisPage(pipeline=pipeline, file_picker=FakeFilePicker(queued_results=[]), production_mode=True)
    qtbot.addWidget(page)
    assert not page.export_button.isEnabled()

    store = ArtifactFileStore(tmp_path)
    store.save(_geometry_document())
    page._video_path = SOURCE
    page.artifact_store = store
    page._geometry_artifact_id = "geom-1"
    page._geometry = GEOMETRY
    page._refresh_run_availability()

    page.run_recorded_analysis()
    qtbot.waitUntil(detector.started.is_set, timeout=2000)
    # A run in flight (non-authoritative/no result yet) must not enable export.
    assert not page.export_button.isEnabled()
    page.cancel_run()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)
    assert page._current_result is not None and not page._current_result.authoritative
    assert not page.export_button.isEnabled()

    good_pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page2 = _prepared_page(qtbot, tmp_path, pipeline=good_pipeline, artifact_id="geom-2")
    assert not page2.export_button.isEnabled()
    page2.run_analysis()
    qtbot.waitUntil(lambda: page2._run_thread is None, timeout=2000)
    assert page2._current_result.authoritative
    assert page2.export_button.isEnabled()

    # Losing the output location (never possible via the real UI once chosen,
    # but exercised directly here) must disable export again.
    page2.artifact_store = None
    page2._refresh_export_availability()
    assert not page2.export_button.isEnabled()


# -- 7b. export provenance is snapshotted at analysis time, never read live --------


def test_export_source_video_reflects_the_analyzed_run_not_a_later_video_selection(qtbot, tmp_path: Path) -> None:
    """Guards against a regression where export read the mutable, live video
    selection instead of the immutable ``AnalysisRunResult.source_video`` that
    was actually analyzed."""
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline)

    page.run_analysis()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)
    assert page._current_result.authoritative
    assert page._current_result.source_video == SOURCE

    # The operator picks a different video afterwards without re-running.
    other_video = Path("some-other-recording.avi")
    page.file_picker = FakeFilePicker(queued_results=[other_video])
    page.choose_video()
    assert page._video_path == other_video

    page.export_results()
    manifest_id = _manifest_artifact_id(page.export_status_label.text())
    manifest = page.artifact_store.load(ArtifactType.ANALYSIS_MANIFEST, manifest_id)
    assert manifest.payload["source_video"] == str(SOURCE)
    assert manifest.payload["source_video"] != str(other_video)


def test_export_geometry_artifact_id_reflects_the_analyzed_run_not_a_later_geometry_selection(
    qtbot, tmp_path: Path
) -> None:
    """Guards against a regression where export read the mutable, live
    geometry-artifact-ID selection instead of a snapshot captured when the
    analyzed run actually started."""
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline, artifact_id="geom-1")

    page.run_analysis()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)
    assert page._current_result.authoritative

    # The operator loads a different geometry artifact afterwards without re-running.
    page.artifact_store.save(_geometry_document("geom-2"))
    page.geometry_artifact_id_input.setText("geom-2")
    page.load_geometry_artifact()
    assert page._geometry_artifact_id == "geom-2"

    page.export_results()
    manifest_id = _manifest_artifact_id(page.export_status_label.text())
    manifest = page.artifact_store.load(ArtifactType.ANALYSIS_MANIFEST, manifest_id)
    assert manifest.payload["geometry_artifact_id"] == "geom-1"


def test_repeated_runs_each_export_their_own_video_and_geometry_provenance(qtbot, tmp_path: Path) -> None:
    """A second, later run with new video/geometry selections must export its
    own provenance, proving the snapshot is replaced (not stuck) across
    repeated runs."""
    other_video = Path("second-recording.avi")
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    second_pipeline, _ = _pipeline(blobs=((_blob(90, 40),),), video_path=other_video)

    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline, artifact_id="geom-1")
    page.run_analysis()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)
    page.export_results()
    first_manifest = page.artifact_store.load(
        ArtifactType.ANALYSIS_MANIFEST, _manifest_artifact_id(page.export_status_label.text())
    )
    assert first_manifest.payload["source_video"] == str(SOURCE)
    assert first_manifest.payload["geometry_artifact_id"] == "geom-1"

    page.artifact_store.save(_geometry_document("geom-2"))
    (tmp_path / other_video).write_bytes(b"second-workspace-analysis-video")
    page.file_picker = FakeFilePicker(queued_results=[other_video])
    page.choose_video()
    page.geometry_artifact_id_input.setText("geom-2")
    page.load_geometry_artifact()
    page._pipeline = second_pipeline

    page.run_analysis()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)
    assert page._current_result.authoritative

    page.export_results()
    second_manifest = page.artifact_store.load(
        ArtifactType.ANALYSIS_MANIFEST, _manifest_artifact_id(page.export_status_label.text())
    )
    assert second_manifest.payload["source_video"] == str(other_video)
    assert second_manifest.payload["geometry_artifact_id"] == "geom-2"


def test_failed_run_does_not_leak_its_pending_snapshot_onto_a_later_export(qtbot, tmp_path: Path) -> None:
    """A run that raises an unexpected error must never adopt its pending
    provenance snapshot; a later successful run must export its own,
    unaffected snapshot."""

    class _RaisingDetector:
        def scan(self, frame, thresholds, roi, *, cancellation=None):
            del frame, thresholds, roi, cancellation
            raise RuntimeError("simulated unexpected detector failure")

    failing_source = FakeVideoFrameSource()
    failing_source.register(SOURCE, (np.zeros((80, 100, 3), dtype=np.uint8),), fps=20.0)
    failing_pipeline = AnalysisPipeline(failing_source, _RaisingDetector())
    page = _prepared_page(qtbot, tmp_path, pipeline=failing_pipeline, artifact_id="geom-1")

    page.run_analysis()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)
    assert page._current_result is None
    assert "failed" in page.run_status_label.text().lower()
    assert page._pending_run_geometry_artifact_id is None

    # A later, unrelated successful run with a different geometry must export
    # its own provenance, not anything left over from the failed attempt.
    page.artifact_store.save(_geometry_document("geom-3"))
    page.geometry_artifact_id_input.setText("geom-3")
    page.load_geometry_artifact()
    good_pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page._pipeline = good_pipeline

    page.run_analysis()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)
    assert page._current_result.authoritative

    page.export_results()
    manifest = page.artifact_store.load(
        ArtifactType.ANALYSIS_MANIFEST, _manifest_artifact_id(page.export_status_label.text())
    )
    assert manifest.payload["geometry_artifact_id"] == "geom-3"


def test_cancelled_rerun_does_not_retroactively_relabel_the_prior_completed_export(
    qtbot, tmp_path: Path
) -> None:
    """Starting (and cancelling) a second run with new selections must not
    change the still-current, already-completed first result's provenance."""
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline, artifact_id="geom-1")
    page.run_analysis()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)
    completed_result = page._current_result
    assert completed_result.authoritative

    blocking_pipeline, detector = _blocking_pipeline()
    page._pipeline = blocking_pipeline
    page.artifact_store.save(_geometry_document("geom-4"))
    page.geometry_artifact_id_input.setText("geom-4")
    page.load_geometry_artifact()
    page.run_analysis()
    qtbot.waitUntil(detector.started.is_set, timeout=2000)
    page.cancel_run()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)

    # The cancelled run replaced _current_result with a non-authoritative one...
    assert page._current_result.completion is AnalysisCompletion.CANCELLED
    assert not page._current_result.authoritative
    # ...but the *previously exported-quality* completed result object is
    # untouched/immutable; its own provenance never changes after the fact.
    assert completed_result.source_video == SOURCE
    assert completed_result.authoritative


# -- 8. provisional vs authoritative labeling (live capture) ------------------------


class _StubBackend:
    def __init__(self) -> None:
        self.frame_channel: LatestFrameChannel[PreviewFrame] = LatestFrameChannel()
        self.health = CaptureHealth()


def _fake_camera_presenter() -> tuple[CameraPanelPresenter, _StubBackend]:
    backend = _StubBackend()
    presenter = CameraPanelPresenter(
        FakeCameraDeviceSource([CameraDevice("fake-0", "Synthetic camera", "fake")]),
        CameraCaptureService(backend),
    )
    return presenter, backend


class _AnyFrameDetector:
    """Returns the same registered scan regardless of frame identity.

    Live-capture frames are reconstructed from raw preview bytes (see
    ``_rgb_frame_from_preview``), so ``FakeRedMarkerFrameDetector``'s
    identity-keyed registration (``id(frame)``) cannot match them; this
    double sidesteps that by always returning one fixed scan.
    """

    def __init__(self, scan: RedMarkerScan) -> None:
        self._scan = scan
        self.last_roi: NormalizedRoi | None = None

    def scan(self, frame, thresholds, roi, *, cancellation=None):
        del frame, thresholds, cancellation
        self.last_roi = roi
        return self._scan


def test_live_capture_results_are_always_labeled_provisional_and_not_authoritative(qtbot, tmp_path: Path) -> None:
    presenter, backend = _fake_camera_presenter()
    detector = _AnyFrameDetector(RedMarkerScan(SIZE, GEOMETRY.actuator_roi, (_blob(90, 40),), None))
    frame = np.zeros((80, 100, 3), dtype=np.uint8)
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline, camera_presenter=presenter, live_detector=detector)

    backend.frame_channel.publish(PreviewFrame(0, SIZE.width, SIZE.height, frame.tobytes(), 0.0))
    presenter.refresh_status()
    qtbot.waitUntil(lambda: page._live_thread is not None or page._live_last_update is not None, timeout=2000)
    qtbot.waitUntil(lambda: page._live_thread is None, timeout=2000)
    page._poll_live_channel()

    text = page.live_overlay_label.text()
    assert "not authoritative" in text
    assert "Provisional" in text
    # A provisional live result must never reach the authoritative results table.
    assert page.results_table.rowCount() == 0


def test_downscaled_4k_live_preview_uses_scaled_geometry_candidates_and_overlay(qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    import soft_actuator_testing.ui.views.workflows.analysis as analysis_view

    full_size = FrameSize(3840, 2160)
    preview_size = FrameSize(960, 540)
    full_geometry = VideoGeometry(
        full_size,
        PixelPoint(384, 1080),
        PixelPoint(3456, 540),
        NormalizedRoi(192, 216, 3648, 1944),
    )
    preview_roi = NormalizedRoi(48, 54, 912, 486)
    presenter, backend = _fake_camera_presenter()
    detector = _AnyFrameDetector(
        RedMarkerScan(
            preview_size,
            preview_roi,
            (RedBlob(PixelPoint(864, 135), NormalizedRoi(861, 132, 867, 138), 100.0, 36.0, 1.0),),
            None,
        )
    )
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page = AnalysisPage(pipeline=pipeline, production_mode=True, camera_presenter=presenter, live_detector=detector)
    qtbot.addWidget(page)
    page._geometry = full_geometry

    preview_frame = np.zeros((preview_size.height, preview_size.width, 3), dtype=np.uint8)
    backend.frame_channel.publish(
        PreviewFrame(7, preview_size.width, preview_size.height, preview_frame.tobytes(), 0.0)
    )
    presenter.refresh_status()
    qtbot.waitUntil(lambda: page._live_thread is None and page._live_channel.stats.published == 1, timeout=2000)
    page._poll_live_channel()

    update = page._live_last_update
    assert update is not None
    assert update.preview_geometry is not None
    assert update.preview_geometry.base_point == PixelPoint(96.0, 270.0)
    assert update.preview_geometry.initial_tip_point == PixelPoint(864.0, 135.0)
    assert update.preview_geometry.actuator_roi == preview_roi
    assert detector.last_roi == preview_roi
    assert update.result.detection.point == PixelPoint(864, 135)
    assert update.result.detection.candidates[0].center == PixelPoint(864, 135)
    assert "preview-derived" in page.live_overlay_label.text()
    assert "not authoritative" in page.live_overlay_label.text()

    observed: dict[str, FrameSize] = {}

    def capture_overlay_point(point, frame_size, visible, widget_width, widget_height):
        del point, visible, widget_width, widget_height
        observed["frame_size"] = frame_size
        return 10.0, 10.0

    monkeypatch.setattr(analysis_view, "frame_to_widget_point", capture_overlay_point)
    image = QImage(960, 540, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    page._paint_live_overlay(painter, QRect(0, 0, 960, 540))
    painter.end()
    assert observed["frame_size"] == preview_size


def test_live_preview_can_paint_before_any_provisional_result_arrives(qtbot) -> None:
    presenter, _ = _fake_camera_presenter()
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page = AnalysisPage(pipeline=pipeline, production_mode=True, camera_presenter=presenter)
    qtbot.addWidget(page)
    assert page._live_last_update is None

    frame = np.zeros((80, 100, 3), dtype=np.uint8)
    page.live_preview.resize(200, 160)
    page.live_preview.set_frame(frame, frame_index=0, description="Live camera preview (provisional; not authoritative)")
    page.live_preview.show()
    page.live_preview.repaint()
    qtbot.wait(10)

    image = QImage(100, 80, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    page._paint_live_overlay(painter, QRect(0, 0, 100, 80))
    painter.end()
    assert page._live_last_update is None


def test_live_capture_without_a_camera_presenter_shows_explicit_unavailable_state(qtbot, tmp_path: Path) -> None:
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline, camera_presenter=None)
    assert "requires a shared camera preview" in page.live_capture_status_label.text()


# -- 9. finalized-video handoff / unavailable state ------------------------------------


def test_finalized_video_handoff_enables_use_as_source(qtbot, tmp_path: Path) -> None:
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page = AnalysisPage(pipeline=pipeline, production_mode=True)
    qtbot.addWidget(page)

    finalized = tmp_path / "finalized.mkv"
    page.receive_finalization(SimpleNamespace(video_path=finalized))
    assert page.finalized_video == finalized
    assert "ready for authoritative analysis" in page.status.text()
    assert page.use_finalized_button.isEnabled()

    page._use_finalized_video()
    assert page.video_path_label.text() == str(finalized)


def test_finalized_video_unavailable_state_is_explicit(qtbot, tmp_path: Path) -> None:
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page = AnalysisPage(pipeline=pipeline, production_mode=True)
    qtbot.addWidget(page)

    page.receive_finalization(SimpleNamespace(video_path=None))
    assert page.finalized_video is None
    assert "No finalized video" in page.source.text()
    assert not page.use_finalized_button.isEnabled()


# -- 10. stale/dropped live updates -----------------------------------------------------


def test_stale_live_updates_are_dropped_and_counted(qtbot, tmp_path: Path) -> None:
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page = AnalysisPage(pipeline=pipeline, production_mode=True)
    qtbot.addWidget(page)

    detection_a = MarkerDetectionResult(DetectionState.DETECTED, PixelPoint(90, 40), 0.9)
    detection_b = MarkerDetectionResult(DetectionState.DETECTED, PixelPoint(80, 40), 0.9)
    row_a = AnalysisFrameResult.from_detection(0, 0.0, GEOMETRY.base_point, detection_a)
    row_b = AnalysisFrameResult.from_detection(1, 0.05, GEOMETRY.base_point, detection_b)

    page._live_channel.publish(ProvisionalAnalysisUpdate(row_a))
    page._live_channel.publish(ProvisionalAnalysisUpdate(row_b))  # drops row_a as stale
    page._poll_live_channel()

    assert page._live_channel.stats.dropped_stale == 1
    assert "dropped-stale=1" in page.live_overlay_label.text()
    assert page._live_last_update.result is row_b


# -- 11. GUI responsiveness --------------------------------------------------------------


def test_gui_remains_responsive_while_analysis_runs_in_background(qtbot, tmp_path: Path) -> None:
    pipeline, detector = _blocking_pipeline()
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline)

    responsive = Event()
    QTimer.singleShot(0, responsive.set)

    page.run_analysis()
    qtbot.waitUntil(detector.started.is_set, timeout=2000)
    qtbot.waitUntil(responsive.is_set, timeout=1000)
    assert not page.run_button.isEnabled()

    page.cancel_run()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)


# -- 12. repeated runs ---------------------------------------------------------------------


def test_repeated_runs_reset_table_and_plot_cleanly(qtbot, tmp_path: Path) -> None:
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),), (_blob(90, 40),)))
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline)

    page.run_analysis()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)
    assert page.results_table.rowCount() == 2

    page.results_table.selectRow(0)
    page.clear_marker_point()
    assert page.results_table.item(0, 5).text() == "yes"

    page.run_analysis()
    qtbot.waitUntil(lambda: page._run_thread is None, timeout=2000)

    # A fresh run rebuilds the table from scratch; no corrected/leaked rows remain.
    assert page.results_table.rowCount() == 2
    assert page.results_table.item(0, 5).text() == "no"
    assert page._run_thread is None


# -- 13. bounded close cleanup -----------------------------------------------------------------


def test_close_bounds_and_cleans_up_an_active_run_thread(qtbot, tmp_path: Path) -> None:
    pipeline, detector = _blocking_pipeline()
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline)

    page.run_analysis()
    qtbot.waitUntil(detector.started.is_set, timeout=2000)

    page.close()  # must bound-cancel and join instead of orphaning the thread
    assert page._run_thread is None


def test_close_bounds_and_cleans_up_an_active_live_thread(qtbot, tmp_path: Path) -> None:
    presenter, backend = _fake_camera_presenter()

    class _BlockingLiveDetector:
        def __init__(self) -> None:
            self.started = Event()

        def scan(self, frame, thresholds, roi, *, cancellation=None):
            del frame, thresholds, roi
            self.started.set()
            while cancellation is None or not cancellation.is_cancelled():
                Event().wait(0.005)
            raise MarkerSuggestionCancelled("blocked live scan interrupted")

    live_detector = _BlockingLiveDetector()
    pipeline, _ = _pipeline(blobs=((_blob(90, 40),),))
    page = _prepared_page(qtbot, tmp_path, pipeline=pipeline, camera_presenter=presenter, live_detector=live_detector)

    frame = np.zeros((80, 100, 3), dtype=np.uint8)
    backend.frame_channel.publish(PreviewFrame(0, SIZE.width, SIZE.height, frame.tobytes(), 0.0))
    presenter.refresh_status()
    qtbot.waitUntil(live_detector.started.is_set, timeout=2000)

    page.close()
    assert page._live_thread is None
