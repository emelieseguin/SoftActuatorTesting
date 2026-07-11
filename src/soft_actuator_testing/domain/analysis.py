"""Marker and per-frame analysis result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import atan2, degrees, isfinite

from .errors import DomainError, ErrorCode
from .geometry import PixelPoint


class DetectionState(str, Enum):
    DETECTED = "detected"
    MANUAL = "manual"
    MISSING = "missing"
    HELD = "held"


@dataclass(frozen=True)
class MarkerCandidate:
    """A detector candidate before the chosen marker is resolved."""

    center: PixelPoint
    area_pixels: float
    score: float

    def __post_init__(self) -> None:
        if not isfinite(self.area_pixels) or self.area_pixels <= 0:
            raise DomainError(ErrorCode.VALIDATION, "candidate area must be finite and positive", "candidate.area_pixels")
        _validate_confidence(self.score, "candidate.score")


@dataclass(frozen=True)
class MarkerDetectionResult:
    """A selected marker point and explicit provenance/quality state."""

    state: DetectionState
    point: PixelPoint | None
    confidence: float
    candidates: tuple[MarkerCandidate, ...] = ()
    correction_applied: bool = False

    def __post_init__(self) -> None:
        _validate_confidence(self.confidence, "detection.confidence")
        if self.state is DetectionState.MISSING:
            if self.point is not None:
                raise DomainError(ErrorCode.VALIDATION, "missing detection must not include a point", "detection.point")
            if self.confidence != 0:
                raise DomainError(ErrorCode.VALIDATION, "missing detection confidence must be zero", "detection.confidence")
        elif self.point is None:
            raise DomainError(
                ErrorCode.VALIDATION,
                f"{self.state.value} detection requires a marker point",
                "detection.point",
            )

    @classmethod
    def missing(cls, candidates: tuple[MarkerCandidate, ...] = ()) -> MarkerDetectionResult:
        return cls(DetectionState.MISSING, None, 0.0, candidates)

    @classmethod
    def held(cls, point: PixelPoint, confidence: float) -> MarkerDetectionResult:
        return cls(DetectionState.HELD, point, confidence)


@dataclass(frozen=True)
class AnalysisFrameResult:
    """The versioned analysis row representation before CSV serialization."""

    frame_index: int
    video_time_seconds: float
    detection: MarkerDetectionResult
    actuator_angle_degrees: float | None

    def __post_init__(self) -> None:
        if self.frame_index < 0:
            raise DomainError(ErrorCode.VALIDATION, "frame_index cannot be negative", "frame_index")
        if not isfinite(self.video_time_seconds) or self.video_time_seconds < 0:
            raise DomainError(ErrorCode.NON_FINITE_VALUE, "video time must be finite and non-negative", "video_time_seconds")
        if self.detection.state is DetectionState.MISSING:
            if self.actuator_angle_degrees is not None:
                raise DomainError(
                    ErrorCode.VALIDATION,
                    "a missing marker cannot have an actuator angle",
                    "actuator_angle_degrees",
                )
        elif self.actuator_angle_degrees is None or not isfinite(self.actuator_angle_degrees):
            raise DomainError(
                ErrorCode.NON_FINITE_VALUE,
                "a resolved marker requires a finite actuator angle",
                "actuator_angle_degrees",
            )

    @classmethod
    def from_detection(
        cls,
        frame_index: int,
        video_time_seconds: float,
        base_point: PixelPoint,
        detection: MarkerDetectionResult,
    ) -> AnalysisFrameResult:
        angle = None if detection.point is None else actuator_angle_degrees(base_point, detection.point)
        return cls(frame_index, video_time_seconds, detection, angle)


def actuator_angle_degrees(base_point: PixelPoint, tip_point: PixelPoint) -> float:
    """Return the signed image-coordinate angle from base to tip in degrees."""

    dx = tip_point.x - base_point.x
    dy = tip_point.y - base_point.y
    if dx == 0 and dy == 0:
        raise DomainError(ErrorCode.VALIDATION, "base and tip points must differ", "tip_point")
    return degrees(atan2(dy, dx))


def _validate_confidence(value: float, field_path: str) -> None:
    if not isfinite(value) or not 0 <= value <= 1:
        raise DomainError(
            ErrorCode.VALIDATION,
            "confidence must be finite and in the range [0, 1]",
            field_path,
        )
