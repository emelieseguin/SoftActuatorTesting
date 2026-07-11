"""pytest-qt coverage for the guided marker-suggestion widget; no real
video/OpenCV/hardware is touched — only fakes (`FakeVideoFrameSource`,
`FakeRedMarkerFrameDetector`, and a small blocking detector double for the
bounded-cancellation test).
"""

from __future__ import annotations

from pathlib import Path
from threading import Event

import numpy as np
from PySide6.QtCore import QTimer

from soft_actuator_testing.application.marker_suggestion import (
    FakeRedMarkerFrameDetector,
    MarkerSuggestionCancelled,
    MarkerSuggestionWorkflow,
    RedBlob,
    RedMarkerScan,
)
from soft_actuator_testing.application.video_geometry_workflow import FakeVideoFrameSource, VideoGeometryWorkflow
from soft_actuator_testing.domain.geometry import FrameSize, NormalizedRoi, PixelPoint
from soft_actuator_testing.ui.views.marker_suggestion import MarkerSuggestionView
from soft_actuator_testing.ui.views.video_geometry import VideoGeometryView

WIDTH, HEIGHT = 192, 128
FRAME_SIZE = FrameSize(WIDTH, HEIGHT)


def _frame(color: int = 0) -> np.ndarray:
    return np.full((HEIGHT, WIDTH, 3), color, dtype=np.uint8)


def _blob(cx: float, cy: float, *, area: float = 150.0, redness: float = 0.9) -> RedBlob:
    return RedBlob(
        centroid=PixelPoint(cx, cy),
        bounding_box=NormalizedRoi(cx - 7, cy - 7, cx + 7, cy + 7),
        area_pixels=area,
        perimeter_pixels=44.0,
        redness_score=redness,
    )


def _geometry_view_with_video(frames: tuple[np.ndarray, ...]) -> tuple[VideoGeometryView, VideoGeometryWorkflow]:
    source = FakeVideoFrameSource()
    path = Path("demo.avi")
    source.register(path, frames)
    workflow = VideoGeometryWorkflow(source)
    workflow.load_video(path)
    view = VideoGeometryView(workflow)
    return view, workflow


def _resolved_engine(frame: np.ndarray) -> tuple[MarkerSuggestionWorkflow, FakeRedMarkerFrameDetector]:
    detector = FakeRedMarkerFrameDetector()
    mask = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    detector.register(
        frame,
        RedMarkerScan(frame_size=FRAME_SIZE, roi=None, blobs=(_blob(96, 64),), mask_preview=mask),
    )
    return MarkerSuggestionWorkflow(detector), detector


def test_default_view_reports_no_detection_yet(qtbot) -> None:
    geometry_view, _ = _geometry_view_with_video((_frame(),))
    qtbot.addWidget(geometry_view)
    view = MarkerSuggestionView(geometry_view)
    qtbot.addWidget(view)
    assert view.status_label.text() == "No detection has been run yet."
    assert view.candidates_table.rowCount() == 0
    assert not view.accept_button.isEnabled()


def test_detect_without_a_loaded_video_reports_an_actionable_error(qtbot) -> None:
    geometry_view = VideoGeometryView(VideoGeometryWorkflow(FakeVideoFrameSource()))
    qtbot.addWidget(geometry_view)
    view = MarkerSuggestionView(geometry_view)
    qtbot.addWidget(view)
    view.detect()
    assert "Load a video" in view.status_label.text()


def test_detect_populates_ranked_candidates_and_mask_preview(qtbot) -> None:
    frame = _frame()
    geometry_view, _ = _geometry_view_with_video((frame,))
    qtbot.addWidget(geometry_view)
    engine, _ = _resolved_engine(frame)
    view = MarkerSuggestionView(geometry_view, engine=engine)
    qtbot.addWidget(view)

    view.detect()
    qtbot.waitUntil(lambda: view._detection_thread is None, timeout=2000)

    assert view.candidates_table.rowCount() == 1
    assert view.candidates_table.item(0, 0).text() == "1"
    assert "Resolved" in view.status_label.text()
    assert view.mask_preview._frame is not None


def test_no_detection_frame_reports_explicit_state_with_no_candidates(qtbot) -> None:
    frame = _frame()
    geometry_view, _ = _geometry_view_with_video((frame,))
    qtbot.addWidget(geometry_view)
    detector = FakeRedMarkerFrameDetector()
    detector.register(frame, RedMarkerScan(frame_size=FRAME_SIZE, roi=None, blobs=(), mask_preview=np.zeros((HEIGHT, WIDTH), dtype=np.uint8)))
    engine = MarkerSuggestionWorkflow(detector)
    view = MarkerSuggestionView(geometry_view, engine=engine)
    qtbot.addWidget(view)

    view.detect()
    qtbot.waitUntil(lambda: view._detection_thread is None, timeout=2000)

    assert view.candidates_table.rowCount() == 0
    assert "No detection" in view.status_label.text()
    assert not view.accept_button.isEnabled()


def test_ambiguous_frame_still_lists_both_candidates_for_operator_review(qtbot) -> None:
    frame = _frame()
    geometry_view, _ = _geometry_view_with_video((frame,))
    qtbot.addWidget(geometry_view)
    detector = FakeRedMarkerFrameDetector()
    detector.register(
        frame,
        RedMarkerScan(
            frame_size=FRAME_SIZE,
            roi=None,
            blobs=(_blob(60, 64), _blob(130, 64)),
            mask_preview=np.zeros((HEIGHT, WIDTH), dtype=np.uint8),
        ),
    )
    engine = MarkerSuggestionWorkflow(detector)
    view = MarkerSuggestionView(geometry_view, engine=engine)
    qtbot.addWidget(view)

    view.detect()
    qtbot.waitUntil(lambda: view._detection_thread is None, timeout=2000)

    assert view.candidates_table.rowCount() == 2
    assert "Ambiguous" in view.status_label.text()


def test_accepting_a_candidate_sets_the_tip_and_can_be_manually_corrected(qtbot) -> None:
    frame = _frame()
    geometry_view, workflow = _geometry_view_with_video((frame,))
    qtbot.addWidget(geometry_view)
    engine, _ = _resolved_engine(frame)
    view = MarkerSuggestionView(geometry_view, engine=engine)
    qtbot.addWidget(view)

    view.detect()
    qtbot.waitUntil(lambda: view._detection_thread is None, timeout=2000)
    view.candidates_table.selectRow(0)
    assert view.accept_button.isEnabled()
    view.accept_selected_candidate()

    snapshot = workflow.snapshot
    assert snapshot.tip_point == PixelPoint(96.0, 64.0)
    assert snapshot.tip_provenance == "marker_suggestion"
    assert snapshot.tip_selection_confidence is not None

    # Manual correction afterwards must revert provenance without being blocked.
    workflow.set_tip_point(50.0, 50.0)
    corrected = workflow.snapshot
    assert corrected.tip_provenance == "manual"
    assert corrected.tip_selection_confidence is None


def test_rerunning_detection_after_scrubbing_targets_the_new_frame(qtbot) -> None:
    frame_zero = _frame(0)
    frame_one = _frame(10)
    geometry_view, workflow = _geometry_view_with_video((frame_zero, frame_one))
    qtbot.addWidget(geometry_view)
    detector = FakeRedMarkerFrameDetector()
    detector.register(
        frame_zero,
        RedMarkerScan(frame_size=FRAME_SIZE, roi=None, blobs=(_blob(96, 64),), mask_preview=np.zeros((HEIGHT, WIDTH), dtype=np.uint8)),
    )
    detector.register(
        frame_one,
        RedMarkerScan(frame_size=FRAME_SIZE, roi=None, blobs=(_blob(40, 40),), mask_preview=np.zeros((HEIGHT, WIDTH), dtype=np.uint8)),
    )
    engine = MarkerSuggestionWorkflow(detector)
    view = MarkerSuggestionView(geometry_view, engine=engine)
    qtbot.addWidget(view)

    view.detect()
    qtbot.waitUntil(lambda: view._detection_thread is None, timeout=2000)
    assert view._last_result.frame_index == 0

    geometry_view.step_frame(1)
    assert workflow.snapshot.frame_index == 1
    view.check_staleness()
    assert "stale" in view.status_label.text()
    assert not view.accept_button.isEnabled()

    view.detect()
    qtbot.waitUntil(lambda: view._detection_thread is None, timeout=2000)
    assert view._last_result.frame_index == 1
    assert view.candidates_table.item(0, 2).text() == "(40.0, 40.0)"


def test_threshold_reconfiguration_is_applied_to_the_engine(qtbot) -> None:
    geometry_view, _ = _geometry_view_with_video((_frame(),))
    qtbot.addWidget(geometry_view)
    view = MarkerSuggestionView(geometry_view)
    qtbot.addWidget(view)

    view.min_area_pixels.setValue(5.0)
    view.min_circularity.setValue(0.1)
    view.apply_thresholds()

    assert view.engine.thresholds.min_area_pixels == 5.0
    assert view.engine.thresholds.min_circularity == 0.1
    assert "applied" in view.status_label.text()


def test_invalid_threshold_combination_is_rejected_without_crashing(qtbot) -> None:
    geometry_view, _ = _geometry_view_with_video((_frame(),))
    qtbot.addWidget(geometry_view)
    view = MarkerSuggestionView(geometry_view)
    qtbot.addWidget(view)
    previous = view.engine.thresholds

    view.hue_low_max.setValue(170)
    view.hue_high_min.setValue(10)
    view.apply_thresholds()

    assert view.engine.thresholds is previous  # rejected; nothing was fabricated/applied
    assert "hue_low_max" in view.status_label.text() or "hue" in view.status_label.text().lower()


def test_detection_runs_off_the_gui_thread_and_supports_bounded_cancellation(qtbot) -> None:
    class _BlockingDetector:
        def __init__(self) -> None:
            self.started = Event()
            self.cancelled = Event()

        def scan(self, frame, thresholds, roi, *, cancellation=None):
            del frame, thresholds, roi
            self.started.set()
            while cancellation is None or not cancellation.is_cancelled():
                Event().wait(0.005)
            self.cancelled.set()
            raise MarkerSuggestionCancelled("cancelled by test")

    detector = _BlockingDetector()
    engine = MarkerSuggestionWorkflow(detector)
    geometry_view, _ = _geometry_view_with_video((_frame(),))
    qtbot.addWidget(geometry_view)
    view = MarkerSuggestionView(geometry_view, engine=engine)
    qtbot.addWidget(view)

    responsive = Event()
    QTimer.singleShot(0, responsive.set)

    view.detect()
    view.detect()  # duplicate request while one is active must be rejected
    qtbot.waitUntil(detector.started.is_set, timeout=1000)
    qtbot.waitUntil(responsive.is_set, timeout=1000)
    assert not view.detect_button.isEnabled()
    assert view.cancel_button.isEnabled()

    view.cancel_detection()
    qtbot.waitUntil(detector.cancelled.is_set, timeout=1000)
    qtbot.waitUntil(lambda: view._detection_thread is None, timeout=1000)
    assert view.detect_button.isEnabled()
    assert "cancel" in view.status_label.text().lower()


def test_widget_close_bounds_and_cleans_up_an_active_detection_thread(qtbot) -> None:
    class _BlockingDetector:
        def __init__(self) -> None:
            self.started = Event()

        def scan(self, frame, thresholds, roi, *, cancellation=None):
            del frame, thresholds, roi
            self.started.set()
            while cancellation is None or not cancellation.is_cancelled():
                Event().wait(0.005)
            raise MarkerSuggestionCancelled("cancelled by shutdown")

    detector = _BlockingDetector()
    engine = MarkerSuggestionWorkflow(detector)
    geometry_view, _ = _geometry_view_with_video((_frame(),))
    qtbot.addWidget(geometry_view)
    view = MarkerSuggestionView(geometry_view, engine=engine)
    qtbot.addWidget(view)

    view.detect()
    qtbot.waitUntil(detector.started.is_set, timeout=1000)

    view.close()  # must bound-cancel and join instead of orphaning the thread
    assert view._detection_thread is None
