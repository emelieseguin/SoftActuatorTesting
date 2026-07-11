"""End-to-end marker-suggestion regression tests wiring the real OpenCV video
reader, the real OpenCV red-marker detector, and the Qt-free scoring engine
together against the deterministic ``synthetic-marker-suggestions.avi``
fixture.

This is the top-level regression suite required for guided marker
suggestions: frame-zero processing, dual-red hue wraparound, decoys, no
marker, ROI exclusion, ambiguity, temporal continuity, threshold changes,
cancellation, and manual fallback/correction (via
``VideoGeometryWorkflow.accept_marker_suggestion``/``set_tip_point``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from soft_actuator_testing.application.marker_suggestion import (
    HsvRedThresholds,
    MarkerSuggestionCancelled,
    MarkerSuggestionState,
    MarkerSuggestionWorkflow,
)
from soft_actuator_testing.application.video_geometry_workflow import FakeVideoFrameSource, VideoGeometryWorkflow
from soft_actuator_testing.domain.geometry import NormalizedRoi, PixelPoint
from soft_actuator_testing.infrastructure.red_marker_detector import OpenCvRedMarkerFrameDetector
from soft_actuator_testing.infrastructure.video_file_reader import OpenCvVideoFileReader

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "video" / "synthetic-marker-suggestions.avi"

FRAME_ZERO_BASELINE = 0
FRAME_HUE_WRAP = 1
FRAME_DECOY = 2
FRAME_NO_MARKER = 3
FRAME_ROI = 4
FRAME_AMBIGUOUS = 5
FRAME_TEMPORAL_A = 6
FRAME_TEMPORAL_B = 7
FRAME_SMALL_MARKER = 8


class _AlwaysCancelled:
    def is_cancelled(self) -> bool:
        return True


@pytest.fixture()
def open_video():
    reader = OpenCvVideoFileReader()
    handle = reader.open(FIXTURE)
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture()
def engine() -> MarkerSuggestionWorkflow:
    return MarkerSuggestionWorkflow(OpenCvRedMarkerFrameDetector())


def test_frame_zero_processing_is_resolved(open_video, engine: MarkerSuggestionWorkflow) -> None:
    frame = open_video.read_frame(FRAME_ZERO_BASELINE)
    result = engine.suggest(frame, frame_index=FRAME_ZERO_BASELINE, frame_size=open_video.metadata.frame_size)
    assert result.state is MarkerSuggestionState.RESOLVED
    assert result.frame_index == FRAME_ZERO_BASELINE
    assert result.best_candidate is not None
    assert result.best_candidate.tip_point.x == pytest.approx(96, abs=2)


def test_dual_red_hue_wrap_is_resolved(open_video, engine: MarkerSuggestionWorkflow) -> None:
    frame = open_video.read_frame(FRAME_HUE_WRAP)
    result = engine.suggest(frame, frame_index=FRAME_HUE_WRAP, frame_size=open_video.metadata.frame_size)
    assert result.state is MarkerSuggestionState.RESOLVED
    assert result.best_candidate is not None


def test_decoy_is_excluded_from_ranked_candidates(open_video, engine: MarkerSuggestionWorkflow) -> None:
    frame = open_video.read_frame(FRAME_DECOY)
    result = engine.suggest(frame, frame_index=FRAME_DECOY, frame_size=open_video.metadata.frame_size)
    assert result.state is MarkerSuggestionState.RESOLVED
    assert len(result.candidates) == 1
    assert result.best_candidate.tip_point.x == pytest.approx(140, abs=2)


def test_no_marker_frame_is_explicit_no_detection(open_video, engine: MarkerSuggestionWorkflow) -> None:
    frame = open_video.read_frame(FRAME_NO_MARKER)
    result = engine.suggest(frame, frame_index=FRAME_NO_MARKER, frame_size=open_video.metadata.frame_size)
    assert result.state is MarkerSuggestionState.NO_DETECTION
    assert result.candidates == ()


def test_roi_restriction_only_suggests_the_inside_marker(open_video, engine: MarkerSuggestionWorkflow) -> None:
    frame = open_video.read_frame(FRAME_ROI)
    roi = NormalizedRoi(20, 30, 110, 110)
    result = engine.suggest(frame, frame_index=FRAME_ROI, frame_size=open_video.metadata.frame_size, roi=roi)
    assert result.state is MarkerSuggestionState.RESOLVED
    assert len(result.candidates) == 1
    assert result.best_candidate.tip_point.x == pytest.approx(60, abs=2)
    assert result.roi == roi


def test_ambiguous_symmetric_markers_require_operator_review(open_video, engine: MarkerSuggestionWorkflow) -> None:
    frame = open_video.read_frame(FRAME_AMBIGUOUS)
    result = engine.suggest(
        frame, frame_index=FRAME_AMBIGUOUS, frame_size=open_video.metadata.frame_size, base_point=PixelPoint(96, 64)
    )
    assert result.state is MarkerSuggestionState.AMBIGUOUS
    assert len(result.candidates) == 2


def test_temporal_continuity_resolves_across_adjacent_frames(open_video, engine: MarkerSuggestionWorkflow) -> None:
    frame_a = open_video.read_frame(FRAME_TEMPORAL_A)
    first = engine.suggest(frame_a, frame_index=FRAME_TEMPORAL_A, frame_size=open_video.metadata.frame_size)
    assert first.state is MarkerSuggestionState.AMBIGUOUS

    # The operator confirms the left-hand candidate for frame A...
    engine.note_confirmed_tip(first.candidates[0].tip_point if first.candidates[0].tip_point.x < 96 else first.candidates[1].tip_point)

    frame_b = open_video.read_frame(FRAME_TEMPORAL_B)
    second = engine.suggest(frame_b, frame_index=FRAME_TEMPORAL_B, frame_size=open_video.metadata.frame_size)
    assert second.state is MarkerSuggestionState.RESOLVED
    assert second.best_candidate is not None
    assert second.best_candidate.tip_point.x < 96  # continuity kept the left-hand candidate on top


def test_threshold_change_reveals_a_previously_excluded_small_marker(open_video, engine: MarkerSuggestionWorkflow) -> None:
    frame = open_video.read_frame(FRAME_SMALL_MARKER)
    default_result = engine.suggest(frame, frame_index=FRAME_SMALL_MARKER, frame_size=open_video.metadata.frame_size)
    assert default_result.state is MarkerSuggestionState.NO_DETECTION

    engine.set_thresholds(HsvRedThresholds(min_area_pixels=10.0))
    relaxed_result = engine.suggest(frame, frame_index=FRAME_SMALL_MARKER, frame_size=open_video.metadata.frame_size)
    assert relaxed_result.state is MarkerSuggestionState.RESOLVED


def test_cancellation_aborts_a_real_scan(open_video, engine: MarkerSuggestionWorkflow) -> None:
    frame = open_video.read_frame(FRAME_ZERO_BASELINE)
    with pytest.raises(MarkerSuggestionCancelled):
        engine.suggest(
            frame,
            frame_index=FRAME_ZERO_BASELINE,
            frame_size=open_video.metadata.frame_size,
            cancellation=_AlwaysCancelled(),
        )


def test_manual_fallback_and_correction_after_accepting_a_suggestion(open_video, engine: MarkerSuggestionWorkflow) -> None:
    """Accepting a candidate persists its provenance; a subsequent manual
    correction (never fabricated, never silently overwritten) must revert the
    provenance to ``"manual"`` while keeping every other selection intact."""

    source = FakeVideoFrameSource()
    path = Path("marker-suggestions-demo.avi")
    frame = open_video.read_frame(FRAME_ZERO_BASELINE)
    source.register(path, (frame,))
    workflow = VideoGeometryWorkflow(source)
    workflow.load_video(path)
    workflow.set_base_point(10, 64)
    workflow.set_roi_xywh(0, 0, 190, 126)

    result = engine.suggest(frame, frame_index=0, frame_size=open_video.metadata.frame_size, base_point=PixelPoint(10, 64))
    assert result.state is MarkerSuggestionState.RESOLVED
    candidate = result.best_candidate
    assert candidate is not None

    workflow.accept_marker_suggestion(
        candidate.tip_point,
        confidence=candidate.confidence,
        reasons=candidate.reasons,
        settings=result.thresholds.as_dict(),
    )
    snapshot = workflow.snapshot
    assert snapshot.tip_point == candidate.tip_point
    assert snapshot.tip_provenance == "marker_suggestion"
    assert snapshot.tip_selection_confidence == pytest.approx(candidate.confidence)

    # Never report the suggestion as authoritative once a human corrects it.
    workflow.set_tip_point(candidate.tip_point.x + 3, candidate.tip_point.y + 3)
    corrected = workflow.snapshot
    assert corrected.tip_provenance == "manual"
    assert corrected.tip_selection_confidence is None
    assert corrected.tip_selection_reasons == ()

    document = workflow.as_document()
    assert document.payload["tip_provenance"] == "manual"
    assert "tip_selection_confidence" not in document.payload
    assert "marker_suggestion_settings" not in document.payload
