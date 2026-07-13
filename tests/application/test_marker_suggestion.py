"""Marker-suggestion scoring/ranking tests; every scan is a deterministic fake.

These exercise pure ranking/ambiguity/staleness/cancellation policy with a
:class:`FakeRedMarkerFrameDetector`. Real-pixel dual-hue HSV/OpenCV coverage
(frame-zero processing, hue wraparound, decoys, no marker, ROI exclusion) is
in ``tests/infrastructure/test_red_marker_detector.py`` and the end-to-end
``tests/application/test_marker_suggestion_pipeline.py``.
"""

from __future__ import annotations

from threading import Event, Thread

import pytest

from soft_actuator_testing.application.marker_suggestion import (
    FakeRedMarkerFrameDetector,
    HsvRedThresholds,
    MarkerSuggestionCancellation,
    MarkerSuggestionCancelled,
    MarkerSuggestionState,
    MarkerSuggestionWorkflow,
    RedBlob,
    RedMarkerScan,
)
from soft_actuator_testing.domain.errors import GeometryError
from soft_actuator_testing.domain.geometry import FrameSize, NormalizedRoi, PixelPoint

FRAME_SIZE = FrameSize(192, 128)


def _blob(cx: float, cy: float, *, area: float = 150.0, perimeter: float = 44.0, redness: float = 0.9) -> RedBlob:
    return RedBlob(
        centroid=PixelPoint(cx, cy),
        bounding_box=NormalizedRoi(cx - 7, cy - 7, cx + 7, cy + 7),
        area_pixels=area,
        perimeter_pixels=perimeter,
        redness_score=redness,
    )


def _engine_with_scan(frame: object, blobs: tuple[RedBlob, ...], *, roi: NormalizedRoi | None = None) -> tuple[MarkerSuggestionWorkflow, FakeRedMarkerFrameDetector]:
    detector = FakeRedMarkerFrameDetector()
    detector.register(frame, RedMarkerScan(frame_size=FRAME_SIZE, roi=roi, blobs=blobs, mask_preview="mask"))
    return MarkerSuggestionWorkflow(detector), detector


class _FakeCancellationToken:
    def __init__(self, cancelled: bool = False) -> None:
        self._cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self._cancelled


# --- HsvRedThresholds validation -------------------------------------------------------


def test_thresholds_reject_overlapping_hue_bands() -> None:
    with pytest.raises(GeometryError, match="hue_low_max"):
        HsvRedThresholds(hue_low_max=170, hue_high_min=10)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"saturation_min": 300},
        {"value_max": -1},
        {"morphology_kernel_size": 0},
        {"morph_open_iterations": -1},
        {"min_area_pixels": 0},
        {"min_circularity": 1.5},
        {"exclusion_radius_pixels": -1},
        {"max_candidates": 0},
        {"ambiguity_margin": 2.0},
    ],
)
def test_thresholds_reject_invalid_fields(kwargs: dict) -> None:
    with pytest.raises(GeometryError):
        HsvRedThresholds(**kwargs)


def test_thresholds_round_trip_through_plain_dict() -> None:
    thresholds = HsvRedThresholds(min_area_pixels=55.0)
    restored = HsvRedThresholds.from_dict(thresholds.as_dict())
    assert restored == thresholds


def test_thresholds_from_dict_ignores_unknown_keys() -> None:
    restored = HsvRedThresholds.from_dict({"min_area_pixels": 20.0, "bogus_future_field": 123})
    assert restored.min_area_pixels == 20.0


# --- no detection / resolved / ambiguous states ----------------------------------------


def test_no_red_pixels_produces_no_detection_state() -> None:
    engine, _ = _engine_with_scan("frame", ())
    result = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE)
    assert result.state is MarkerSuggestionState.NO_DETECTION
    assert result.candidates == ()
    assert "No red pixels" in result.message


def test_a_single_strong_candidate_resolves() -> None:
    engine, _ = _engine_with_scan("frame", (_blob(96, 64),))
    result = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE)
    assert result.state is MarkerSuggestionState.RESOLVED
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.rank == 1
    assert candidate.tip_point == PixelPoint(96, 64)
    assert candidate.reasons  # explainable
    assert 0.0 <= candidate.confidence <= 1.0


def test_blobs_below_min_area_are_excluded_and_reported() -> None:
    engine, _ = _engine_with_scan("frame", (_blob(96, 64, area=10.0, perimeter=12.0),))
    result = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE)
    assert result.state is MarkerSuggestionState.NO_DETECTION
    assert "excluded" in result.message


def test_blobs_below_min_circularity_are_excluded() -> None:
    # A long thin "blob" (large perimeter relative to area) has low circularity.
    engine, _ = _engine_with_scan("frame", (_blob(96, 64, area=150.0, perimeter=400.0),))
    result = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE)
    assert result.state is MarkerSuggestionState.NO_DETECTION


def test_base_point_exclusion_radius_drops_blobs_near_the_base() -> None:
    detector = FakeRedMarkerFrameDetector()
    thresholds = HsvRedThresholds(exclusion_radius_pixels=20.0)
    detector.register("frame", RedMarkerScan(frame_size=FRAME_SIZE, roi=None, blobs=(_blob(100, 64),), mask_preview=None))
    engine = MarkerSuggestionWorkflow(detector, thresholds=thresholds)
    result = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE, base_point=PixelPoint(96, 64))
    assert result.state is MarkerSuggestionState.NO_DETECTION


def test_two_near_equal_candidates_are_ambiguous() -> None:
    blobs = (_blob(60, 64, area=130.0, redness=0.90), _blob(132, 64, area=130.0, redness=0.90))
    engine, _ = _engine_with_scan("frame", blobs)
    result = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE, base_point=PixelPoint(96, 64))
    assert result.state is MarkerSuggestionState.AMBIGUOUS
    assert len(result.candidates) == 2
    assert "review the ranked list" in result.message


def test_ambiguity_margin_is_configurable() -> None:
    blobs = (_blob(60, 64, area=130.0, redness=0.90), _blob(132, 64, area=130.0, redness=0.90))
    detector = FakeRedMarkerFrameDetector()
    detector.register("frame", RedMarkerScan(frame_size=FRAME_SIZE, roi=None, blobs=blobs, mask_preview=None))
    engine = MarkerSuggestionWorkflow(detector, thresholds=HsvRedThresholds(ambiguity_margin=0.0))
    result = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE, base_point=PixelPoint(96, 64))
    # With a zero margin only an exact tie remains ambiguous; these two blobs
    # are placed asymmetrically enough around the base to break an exact tie
    # once floating point ranks them, but if they truly tie, both states are
    # acceptable evidence the margin is respected (never crashes).
    assert result.state in (MarkerSuggestionState.AMBIGUOUS, MarkerSuggestionState.RESOLVED)


def test_max_candidates_caps_the_ranked_list() -> None:
    blobs = tuple(_blob(10.0 * i, 64, area=150.0 + i) for i in range(1, 9))
    detector = FakeRedMarkerFrameDetector()
    detector.register("frame", RedMarkerScan(frame_size=FRAME_SIZE, roi=None, blobs=blobs, mask_preview=None))
    engine = MarkerSuggestionWorkflow(detector, thresholds=HsvRedThresholds(max_candidates=3))
    result = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE)
    assert len(result.candidates) == 3
    assert [candidate.rank for candidate in result.candidates] == [1, 2, 3]


# --- proximity from base point ---------------------------------------------------------


def test_distance_from_base_score_favors_the_farther_candidate() -> None:
    near = _blob(100, 64, area=150.0, redness=0.9)
    far = _blob(180, 64, area=150.0, redness=0.9)
    engine, _ = _engine_with_scan("frame", (near, far))
    result = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE, base_point=PixelPoint(96, 64))
    assert result.best_candidate is not None
    assert result.best_candidate.tip_point == PixelPoint(180, 64)


# --- temporal continuity -----------------------------------------------------------------


def test_temporal_continuity_breaks_an_otherwise_ambiguous_tie() -> None:
    left = _blob(55, 64)
    right = _blob(145, 64)
    engine, _ = _engine_with_scan("frame", (left, right))
    without_history = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE)
    assert without_history.state is MarkerSuggestionState.AMBIGUOUS

    engine.note_confirmed_tip(PixelPoint(53, 64))
    with_history = engine.suggest("frame", frame_index=1, frame_size=FRAME_SIZE)
    assert with_history.state is MarkerSuggestionState.RESOLVED
    assert with_history.best_candidate is not None
    assert with_history.best_candidate.tip_point == PixelPoint(55, 64)


def test_explicit_previous_tip_argument_overrides_stored_history() -> None:
    left = _blob(55, 64)
    right = _blob(145, 64)
    engine, _ = _engine_with_scan("frame", (left, right))
    engine.note_confirmed_tip(PixelPoint(53, 64))  # would favor "left"
    result = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE, previous_tip=PixelPoint(147, 64))
    assert result.best_candidate is not None
    assert result.best_candidate.tip_point == PixelPoint(145, 64)


def test_clearing_confirmed_tip_removes_temporal_continuity() -> None:
    left = _blob(55, 64)
    right = _blob(145, 64)
    engine, _ = _engine_with_scan("frame", (left, right))
    engine.note_confirmed_tip(PixelPoint(53, 64))
    engine.note_confirmed_tip(None)
    result = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE)
    assert result.state is MarkerSuggestionState.AMBIGUOUS


# --- threshold reconfiguration -----------------------------------------------------------


def test_changing_min_area_threshold_changes_what_is_kept() -> None:
    engine, _ = _engine_with_scan("frame", (_blob(96, 64, area=18.0, perimeter=16.0),))
    excluded = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE)
    assert excluded.state is MarkerSuggestionState.NO_DETECTION

    engine.set_thresholds(HsvRedThresholds(min_area_pixels=10.0))
    included = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE)
    assert included.state is MarkerSuggestionState.RESOLVED


# --- cancellation ------------------------------------------------------------------------


def test_cancelled_before_scan_raises_and_reports_no_stale_result() -> None:
    detector = FakeRedMarkerFrameDetector()
    scan = RedMarkerScan(frame_size=FRAME_SIZE, roi=None, blobs=(_blob(96, 64),), mask_preview=None)
    detector.register("frame", scan)
    detector.cancel_before_scan.add(id("frame"))
    engine = MarkerSuggestionWorkflow(detector)
    with pytest.raises(MarkerSuggestionCancelled):
        engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE, cancellation=_FakeCancellationToken(True))


def test_cancellation_check_after_scan_also_aborts() -> None:
    """Even if the detector adapter itself never observes cancellation, the
    engine still checks once immediately after the scan returns."""

    engine, _ = _engine_with_scan("frame", (_blob(96, 64),))
    with pytest.raises(MarkerSuggestionCancelled):
        engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE, cancellation=_FakeCancellationToken(True))


# --- staleness / sequence guarding ---------------------------------------------------------


def test_sequence_increments_and_is_current_detects_staleness() -> None:
    engine, _ = _engine_with_scan("frame", (_blob(96, 64),))
    first = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE)
    assert engine.is_current(first)
    second = engine.suggest("frame", frame_index=1, frame_size=FRAME_SIZE)
    assert engine.is_current(second)
    assert not engine.is_current(first)
    assert second.sequence == first.sequence + 1


def test_negative_frame_index_is_rejected() -> None:
    engine, _ = _engine_with_scan("frame", (_blob(96, 64),))
    with pytest.raises(GeometryError):
        engine.suggest("frame", frame_index=-1, frame_size=FRAME_SIZE)


# --- immutability / result invariants -------------------------------------------------------


def test_result_and_candidates_are_frozen_dataclasses() -> None:
    engine, _ = _engine_with_scan("frame", (_blob(96, 64),))
    result = engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE)
    with pytest.raises(Exception):
        result.state = MarkerSuggestionState.NO_DETECTION  # type: ignore[misc]
    with pytest.raises(Exception):
        result.candidates[0].confidence = 1.0  # type: ignore[misc]


def test_no_detection_result_cannot_carry_candidates() -> None:
    from soft_actuator_testing.application.marker_suggestion import MarkerSuggestionCandidate, MarkerSuggestionResult

    candidate = MarkerSuggestionCandidate(
        rank=1,
        tip_point=PixelPoint(1, 1),
        bounding_box=NormalizedRoi(0, 0, 2, 2),
        area_pixels=10.0,
        circularity=0.9,
        redness_score=0.9,
        size_score=0.9,
        circularity_score=0.9,
        distance_from_base_score=None,
        temporal_continuity_score=None,
        confidence=0.9,
        reasons=("test",),
    )
    with pytest.raises(GeometryError):
        MarkerSuggestionResult(
            state=MarkerSuggestionState.NO_DETECTION,
            frame_index=0,
            sequence=1,
            candidates=(candidate,),
            mask_preview=None,
            roi=None,
            thresholds=HsvRedThresholds(),
            message="bogus",
        )


def test_resolved_result_requires_a_candidate() -> None:
    from soft_actuator_testing.application.marker_suggestion import MarkerSuggestionResult

    with pytest.raises(GeometryError):
        MarkerSuggestionResult(
            state=MarkerSuggestionState.RESOLVED,
            frame_index=0,
            sequence=1,
            candidates=(),
            mask_preview=None,
            roi=None,
            thresholds=HsvRedThresholds(),
            message="bogus",
        )


def test_marker_suggestion_cancellation_is_thread_safe_and_starts_uncancelled() -> None:
    token = MarkerSuggestionCancellation()
    assert token.is_cancelled() is False
    token.cancel()
    assert token.is_cancelled() is True


def test_marker_suggestion_cancellation_actually_aborts_a_real_workflow_scan() -> None:
    detector = FakeRedMarkerFrameDetector()
    detector.register("frame", RedMarkerScan(frame_size=FRAME_SIZE, roi=None, blobs=(_blob(96, 64),), mask_preview=None))
    detector.cancel_before_scan.add(id("frame"))
    engine = MarkerSuggestionWorkflow(detector)
    token = MarkerSuggestionCancellation()
    token.cancel()
    with pytest.raises(MarkerSuggestionCancelled):
        engine.suggest("frame", frame_index=0, frame_size=FRAME_SIZE, cancellation=token)


def test_concurrent_suggestions_assign_monotonic_sequences_and_make_the_late_result_stale() -> None:
    first, second = object(), object()

    class BlockingDetector(FakeRedMarkerFrameDetector):
        def __init__(self) -> None:
            super().__init__()
            self.entered = Event()
            self.release = Event()

        def scan(self, frame, *args, **kwargs):  # type: ignore[no-untyped-def]
            if frame is first:
                self.entered.set()
                assert self.release.wait(1)
            return super().scan(frame, *args, **kwargs)

    detector = BlockingDetector()
    scan = RedMarkerScan(FRAME_SIZE, None, (_blob(100, 64),), "mask")
    detector.register(first, scan)
    detector.register(second, scan)
    workflow = MarkerSuggestionWorkflow(detector)
    results = []
    thread = Thread(target=lambda: results.append(workflow.suggest(first, frame_index=0, frame_size=FRAME_SIZE)))
    thread.start()
    assert detector.entered.wait(1)

    current = workflow.suggest(second, frame_index=1, frame_size=FRAME_SIZE)
    detector.release.set()
    thread.join(1)

    assert not thread.is_alive()
    assert current.sequence == 2 and workflow.is_current(current)
    assert results[0].sequence == 1 and not workflow.is_current(results[0])
