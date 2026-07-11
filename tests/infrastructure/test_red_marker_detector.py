"""Tests for the OpenCV-backed dual-hue red-marker frame scanner.

These exercise the *real* ``tests/fixtures/video/synthetic-marker-suggestions.avi``
fixture (192x128, 9 frames, 10fps; see
``tests/fixtures/video/generate_marker_suggestion_video.py``) so pixel-level
HSV/morphology/contour analysis is proven against actual OpenCV decoding, not
just fakes. Application-layer ranking/ambiguity/staleness policy is tested
separately in ``tests/application/test_marker_suggestion.py`` with a fake
detector.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from soft_actuator_testing.application.marker_suggestion import HsvRedThresholds, MarkerSuggestionCancelled
from soft_actuator_testing.domain.geometry import NormalizedRoi
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


def _read(frame_index: int):
    reader = OpenCvVideoFileReader()
    handle = reader.open(FIXTURE)
    try:
        return handle.read_frame(frame_index), handle.metadata
    finally:
        handle.close()


def test_frame_zero_processing_finds_the_single_baseline_marker() -> None:
    frame, metadata = _read(FRAME_ZERO_BASELINE)
    detector = OpenCvRedMarkerFrameDetector()
    scan = detector.scan(frame, HsvRedThresholds(), None)
    assert scan.frame_size == metadata.frame_size
    assert len(scan.blobs) == 1
    blob = scan.blobs[0]
    assert blob.centroid.x == pytest.approx(96, abs=2)
    assert blob.centroid.y == pytest.approx(64, abs=2)
    assert blob.redness_score > 0.8
    assert blob.circularity > 0.7


def test_dual_hue_wraparound_marker_is_detected() -> None:
    """A marker colored near the OpenCV hue wraparound boundary (~173) must
    still be found via the high-hue band, proving the dual-range mask (not
    just the low [0, hue_low_max] band) is actually exercised."""

    frame, _ = _read(FRAME_HUE_WRAP)
    detector = OpenCvRedMarkerFrameDetector()
    scan = detector.scan(frame, HsvRedThresholds(), None)
    assert len(scan.blobs) == 1
    assert scan.blobs[0].centroid.x == pytest.approx(96, abs=2)


def test_low_hue_band_alone_misses_the_wraparound_marker() -> None:
    """Demonstrates the dual-hue design is load-bearing: disabling the high
    band (by pushing ``hue_high_min`` past 179) must lose the wraparound
    marker that the default dual-band thresholds correctly detect."""

    frame, _ = _read(FRAME_HUE_WRAP)
    detector = OpenCvRedMarkerFrameDetector()
    single_band = HsvRedThresholds(hue_high_min=179)
    scan = detector.scan(frame, single_band, None)
    assert scan.blobs == ()


def test_non_red_decoy_is_never_matched_by_the_dual_hue_mask() -> None:
    frame, _ = _read(FRAME_DECOY)
    detector = OpenCvRedMarkerFrameDetector()
    scan = detector.scan(frame, HsvRedThresholds(), None)
    # Only the genuine red marker (~x=140) is present; the orange decoy
    # (~x=50) never matches either hue band.
    assert len(scan.blobs) == 1
    assert scan.blobs[0].centroid.x == pytest.approx(140, abs=2)


def test_no_marker_frame_returns_no_blobs() -> None:
    frame, _ = _read(FRAME_NO_MARKER)
    detector = OpenCvRedMarkerFrameDetector()
    scan = detector.scan(frame, HsvRedThresholds(), None)
    assert scan.blobs == ()
    assert scan.mask_preview.any() == False  # noqa: E712 - readable assertion on a mask array


def test_roi_restriction_excludes_the_outside_marker() -> None:
    frame, _ = _read(FRAME_ROI)
    detector = OpenCvRedMarkerFrameDetector()
    unrestricted = detector.scan(frame, HsvRedThresholds(), None)
    assert len(unrestricted.blobs) == 2

    roi = NormalizedRoi(20, 30, 110, 110)
    restricted = detector.scan(frame, HsvRedThresholds(), roi)
    assert len(restricted.blobs) == 1
    assert restricted.blobs[0].centroid.x == pytest.approx(60, abs=2)
    # The preview mask itself must be zeroed outside the ROI too.
    assert not restricted.mask_preview[0:30, :].any()
    assert not restricted.mask_preview[:, 110:].any()


def test_small_marker_below_default_min_area_is_still_a_raw_blob() -> None:
    """The detector performs no area/circularity filtering itself (that is a
    scoring-layer policy); the small frame-8 marker must still appear in the
    raw scan so :class:`MarkerSuggestionWorkflow` can apply/relax thresholds."""

    frame, _ = _read(FRAME_SMALL_MARKER)
    detector = OpenCvRedMarkerFrameDetector()
    scan = detector.scan(frame, HsvRedThresholds(), None)
    assert len(scan.blobs) == 1
    assert scan.blobs[0].area_pixels < HsvRedThresholds().min_area_pixels


def test_ambiguous_frame_yields_two_near_identical_blobs() -> None:
    frame, _ = _read(FRAME_AMBIGUOUS)
    detector = OpenCvRedMarkerFrameDetector()
    scan = detector.scan(frame, HsvRedThresholds(), None)
    assert len(scan.blobs) == 2
    areas = sorted(blob.area_pixels for blob in scan.blobs)
    assert areas[1] - areas[0] < 5.0


def test_temporal_continuity_frames_each_yield_two_blobs() -> None:
    for frame_index in (FRAME_TEMPORAL_A, FRAME_TEMPORAL_B):
        frame, _ = _read(frame_index)
        detector = OpenCvRedMarkerFrameDetector()
        scan = detector.scan(frame, HsvRedThresholds(), None)
        assert len(scan.blobs) == 2


def test_scan_rejects_a_missing_frame() -> None:
    detector = OpenCvRedMarkerFrameDetector()
    from soft_actuator_testing.domain.errors import GeometryError

    with pytest.raises(GeometryError):
        detector.scan(None, HsvRedThresholds(), None)


def test_scan_is_cancellable_before_any_processing() -> None:
    frame, _ = _read(FRAME_ZERO_BASELINE)
    detector = OpenCvRedMarkerFrameDetector()
    with pytest.raises(MarkerSuggestionCancelled):
        detector.scan(frame, HsvRedThresholds(), None, cancellation=_AlwaysCancelled())


def test_morphology_and_area_thresholds_are_reconfigurable_without_crashing() -> None:
    frame, _ = _read(FRAME_ZERO_BASELINE)
    detector = OpenCvRedMarkerFrameDetector()
    tight = HsvRedThresholds(morphology_kernel_size=5, morph_open_iterations=2, morph_close_iterations=2, min_area_pixels=1.0)
    scan = detector.scan(frame, tight, None)
    assert len(scan.blobs) == 1
