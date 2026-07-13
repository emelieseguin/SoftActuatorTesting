"""Real synthetic-video coverage for finalized analysis."""

from __future__ import annotations

from pathlib import Path

import pytest

from soft_actuator_testing.application.analysis_pipeline import AnalysisPipeline
from soft_actuator_testing.application.marker_suggestion import HsvRedThresholds
from soft_actuator_testing.domain.analysis import DetectionState
from soft_actuator_testing.domain.geometry import FrameSize, NormalizedRoi, PixelPoint, VideoGeometry
from soft_actuator_testing.infrastructure.red_marker_detector import OpenCvRedMarkerFrameDetector
from soft_actuator_testing.infrastructure.video_file_reader import OpenCvVideoFileReader


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "video" / "synthetic-marker-suggestions.avi"
GEOMETRY = VideoGeometry(
    FrameSize(192, 128),
    PixelPoint(10, 64),
    PixelPoint(96, 64),
    NormalizedRoi(0, 0, 192, 128),
)


def test_finalized_synthetic_video_includes_frame_zero_measured_times_and_missing_marker() -> None:
    analysis = AnalysisPipeline(
        OpenCvVideoFileReader(),
        OpenCvRedMarkerFrameDetector(),
        thresholds=HsvRedThresholds(exclusion_radius_pixels=0),
    ).analyze(FIXTURE, GEOMETRY)

    assert analysis.authoritative
    assert len(analysis.results) == 9
    assert analysis.results[0].frame_index == 0
    assert analysis.results[0].video_time_seconds == pytest.approx(0.0)
    assert analysis.results[1].video_time_seconds == pytest.approx(0.1)
    missing = analysis.results[3]
    assert missing.detection.state is DetectionState.MISSING
    assert missing.detection.point is None
    assert missing.actuator_angle_degrees is None
