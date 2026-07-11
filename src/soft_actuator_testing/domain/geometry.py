"""Frame-bounded video geometry with normalized rectangular regions."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .errors import ErrorCode, GeometryError


@dataclass(frozen=True)
class FrameSize:
    width: int
    height: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.width, int)
            or isinstance(self.width, bool)
            or not isinstance(self.height, int)
            or isinstance(self.height, bool)
            or self.width <= 0
            or self.height <= 0
        ):
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "frame dimensions must be positive integers",
                "frame_size",
            )


@dataclass(frozen=True)
class PixelPoint:
    x: float
    y: float

    def __post_init__(self) -> None:
        if not isfinite(self.x) or not isfinite(self.y):
            raise GeometryError(ErrorCode.NON_FINITE_VALUE, "point coordinates must be finite", "point")

    def validate_in(self, frame_size: FrameSize, field_path: str = "point") -> None:
        if not (0 <= self.x < frame_size.width and 0 <= self.y < frame_size.height):
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "point is outside frame bounds",
                field_path,
                f"Use x in [0, {frame_size.width}) and y in [0, {frame_size.height}).",
            )


@dataclass(frozen=True)
class NormalizedRoi:
    """A non-empty pixel ROI with exclusive right/bottom edges."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        if not all(isfinite(value) for value in (self.left, self.top, self.right, self.bottom)):
            raise GeometryError(ErrorCode.NON_FINITE_VALUE, "ROI bounds must be finite", "roi")
        if self.left >= self.right or self.top >= self.bottom:
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "ROI must have positive width and height",
                "roi",
            )

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @classmethod
    def from_corners(cls, first: PixelPoint, second: PixelPoint, frame_size: FrameSize) -> NormalizedRoi:
        first.validate_in(frame_size, "roi.first_corner")
        second.validate_in(frame_size, "roi.second_corner")
        return cls(
            min(first.x, second.x),
            min(first.y, second.y),
            max(first.x, second.x),
            max(first.y, second.y),
        ).validate_in(frame_size)

    @classmethod
    def from_xywh(cls, x: float, y: float, width: float, height: float, frame_size: FrameSize) -> NormalizedRoi:
        return cls(x, y, x + width, y + height).validate_in(frame_size)

    def validate_in(self, frame_size: FrameSize) -> NormalizedRoi:
        if not (
            0 <= self.left < self.right <= frame_size.width
            and 0 <= self.top < self.bottom <= frame_size.height
        ):
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "ROI is outside frame bounds",
                "roi",
                "Normalize the corners and select a region entirely inside the source frame.",
            )
        return self


@dataclass(frozen=True)
class VideoGeometry:
    """Validated geometry used by analysis for a single source-video size."""

    frame_size: FrameSize
    base_point: PixelPoint
    initial_tip_point: PixelPoint | None
    actuator_roi: NormalizedRoi

    def __post_init__(self) -> None:
        self.base_point.validate_in(self.frame_size, "base_point")
        if self.initial_tip_point is not None:
            self.initial_tip_point.validate_in(self.frame_size, "initial_tip_point")
        self.actuator_roi.validate_in(self.frame_size)
