"""Marker and per-frame analysis result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import atan2, degrees, isfinite
from numbers import Real

from .errors import DomainError, ErrorCode
from .geometry import PixelPoint


class DetectionState(str, Enum):
    DETECTED = "detected"
    MANUAL = "manual"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    HELD = "held"


@dataclass(frozen=True)
class MarkerCandidate:
    """A detector candidate before the chosen marker is resolved."""

    center: PixelPoint
    area_pixels: float
    score: float

    def __post_init__(self) -> None:
        if not _is_finite_number(self.area_pixels) or self.area_pixels <= 0:
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
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.state, DetectionState):
            raise DomainError(ErrorCode.VALIDATION, "detection state is invalid", "detection.state")
        if self.point is not None and not isinstance(self.point, PixelPoint):
            raise DomainError(ErrorCode.VALIDATION, "detection point must be a pixel point", "detection.point")
        if not isinstance(self.candidates, tuple) or not all(isinstance(item, MarkerCandidate) for item in self.candidates):
            raise DomainError(ErrorCode.VALIDATION, "detection candidates must be marker candidates", "detection.candidates")
        if not isinstance(self.correction_applied, bool):
            raise DomainError(ErrorCode.VALIDATION, "correction_applied must be a boolean", "detection.correction_applied")
        if not isinstance(self.reasons, tuple) or any(not isinstance(reason, str) or not reason.strip() for reason in self.reasons):
            raise DomainError(ErrorCode.VALIDATION, "detection reasons must be non-empty strings", "detection.reasons")
        _validate_confidence(self.confidence, "detection.confidence")
        if self.state is DetectionState.MISSING:
            if self.point is not None:
                raise DomainError(ErrorCode.VALIDATION, "missing detection must not include a point", "detection.point")
            if self.confidence != 0:
                raise DomainError(ErrorCode.VALIDATION, "missing detection confidence must be zero", "detection.confidence")
        elif self.state is DetectionState.AMBIGUOUS:
            if self.point is not None:
                raise DomainError(ErrorCode.VALIDATION, "ambiguous detection must not select a marker point", "detection.point")
            if len(self.candidates) < 2:
                raise DomainError(ErrorCode.VALIDATION, "ambiguous detection requires competing candidates", "detection.candidates")
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
        if not isinstance(self.frame_index, int) or isinstance(self.frame_index, bool) or self.frame_index < 0:
            raise DomainError(ErrorCode.VALIDATION, "frame_index cannot be negative", "frame_index")
        if not _is_finite_number(self.video_time_seconds) or self.video_time_seconds < 0:
            raise DomainError(ErrorCode.NON_FINITE_VALUE, "video time must be finite and non-negative", "video_time_seconds")
        if not isinstance(self.detection, MarkerDetectionResult):
            raise DomainError(ErrorCode.VALIDATION, "detection must be a marker detection result", "detection")
        if self.detection.point is None:
            if self.actuator_angle_degrees is not None:
                message = (
                    "a missing marker cannot have an actuator angle"
                    if self.detection.state is DetectionState.MISSING
                    else "an unresolved marker cannot have an actuator angle"
                )
                raise DomainError(
                    ErrorCode.VALIDATION,
                    message,
                    "actuator_angle_degrees",
                )
        elif self.actuator_angle_degrees is None or not _is_finite_number(self.actuator_angle_degrees):
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
    if not _is_finite_number(value) or not 0 <= value <= 1:
        raise DomainError(
            ErrorCode.VALIDATION,
            "confidence must be finite and in the range [0, 1]",
            field_path,
        )


def _is_finite_number(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and isfinite(value)
