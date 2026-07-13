from __future__ import annotations

import pytest

from soft_actuator_testing.domain.analysis import (
    AnalysisFrameResult,
    DetectionState,
    MarkerCandidate,
    MarkerDetectionResult,
    actuator_angle_degrees,
)
from soft_actuator_testing.domain.errors import DomainError
from soft_actuator_testing.domain.geometry import PixelPoint


def test_analysis_result_preserves_detected_manual_held_and_missing_provenance() -> None:
    base = PixelPoint(10, 10)
    detected = MarkerDetectionResult(
        DetectionState.DETECTED,
        PixelPoint(20, 10),
        0.9,
        (MarkerCandidate(PixelPoint(20, 10), 12, 0.9),),
    )
    result = AnalysisFrameResult.from_detection(4, 0.4, base, detected)
    assert result.actuator_angle_degrees == pytest.approx(0)

    assert MarkerDetectionResult(DetectionState.MANUAL, PixelPoint(10, 20), 1.0).state is DetectionState.MANUAL
    assert MarkerDetectionResult.held(PixelPoint(10, 20), 0.4).state is DetectionState.HELD
    missing = MarkerDetectionResult.missing()
    assert AnalysisFrameResult(5, 0.5, missing, None).detection.state is DetectionState.MISSING


def test_detection_and_analysis_reject_inconsistent_or_nonfinite_results() -> None:
    with pytest.raises(DomainError, match="requires a marker point"):
        MarkerDetectionResult(DetectionState.DETECTED, None, 0.5)
    with pytest.raises(DomainError, match="confidence"):
        MarkerDetectionResult(DetectionState.MANUAL, PixelPoint(1, 1), 1.1)
    with pytest.raises(DomainError, match="missing marker"):
        AnalysisFrameResult(1, 0.1, MarkerDetectionResult.missing(), 4.0)
    with pytest.raises(DomainError, match="must differ"):
        actuator_angle_degrees(PixelPoint(1, 1), PixelPoint(1, 1))


def test_analysis_contracts_reject_wrong_types_and_boolean_frame_indexes() -> None:
    with pytest.raises(DomainError) as state:
        MarkerDetectionResult("detected", PixelPoint(1, 1), 0.5)  # type: ignore[arg-type]
    assert state.value.field_path == "detection.state"

    detection = MarkerDetectionResult(DetectionState.DETECTED, PixelPoint(2, 1), 0.5)
    with pytest.raises(DomainError) as frame:
        AnalysisFrameResult(True, 0.0, detection, 0.0)  # type: ignore[arg-type]
    assert frame.value.field_path == "frame_index"
