"""Frame-bounded video geometry with normalized rectangular regions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from math import isfinite
from numbers import Real

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
        if not _is_finite_number(self.x) or not _is_finite_number(self.y):
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
        if not all(_is_finite_number(value) for value in (self.left, self.top, self.right, self.bottom)):
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


class PreviewTransformPolicy(str, Enum):
    """How a full-resolution frame was made into a preview frame."""

    STRETCH = "stretch"
    LETTERBOX = "letterbox"
    CROP = "crop"


@dataclass(frozen=True)
class PreviewGeometryTransform:
    """Exact, reproducible full-frame to preview-pixel mapping.

    The production capture proxy currently uses ``stretch`` because FFmpeg's
    ``scale=width:height`` is configured with both dimensions.  Letterbox and
    crop are explicit alternatives for callers that know their preview
    producer's policy.  A crop is rejected only when the geometry required for
    detection is outside the visible preview area.
    """

    source_frame_size: FrameSize
    preview_frame_size: FrameSize
    policy: PreviewTransformPolicy = PreviewTransformPolicy.STRETCH
    _scale_x: Fraction = Fraction(1)
    _scale_y: Fraction = Fraction(1)
    _offset_x: Fraction = Fraction(0)
    _offset_y: Fraction = Fraction(0)

    @classmethod
    def create(
        cls,
        source_frame_size: FrameSize,
        preview_frame_size: FrameSize,
        *,
        policy: PreviewTransformPolicy = PreviewTransformPolicy.STRETCH,
    ) -> PreviewGeometryTransform:
        if not isinstance(policy, PreviewTransformPolicy):
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "preview transform policy is invalid", "preview.policy")
        source_width, source_height = source_frame_size.width, source_frame_size.height
        preview_width, preview_height = preview_frame_size.width, preview_frame_size.height
        if policy is PreviewTransformPolicy.STRETCH:
            return cls(
                source_frame_size,
                preview_frame_size,
                policy,
                Fraction(preview_width, source_width),
                Fraction(preview_height, source_height),
            )
        uniform_scale = (
            min(Fraction(preview_width, source_width), Fraction(preview_height, source_height))
            if policy is PreviewTransformPolicy.LETTERBOX
            else max(Fraction(preview_width, source_width), Fraction(preview_height, source_height))
        )
        return cls(
            source_frame_size,
            preview_frame_size,
            policy,
            uniform_scale,
            uniform_scale,
            (Fraction(preview_width) - source_width * uniform_scale) / 2,
            (Fraction(preview_height) - source_height * uniform_scale) / 2,
        )

    def map_point(self, point: PixelPoint, field_path: str = "point") -> PixelPoint:
        point.validate_in(self.source_frame_size, field_path)
        mapped = PixelPoint(
            float(Fraction(point.x) * self._scale_x + self._offset_x),
            float(Fraction(point.y) * self._scale_y + self._offset_y),
        )
        mapped.validate_in(self.preview_frame_size, field_path)
        return mapped

    def map_roi(self, roi: NormalizedRoi, field_path: str = "roi") -> NormalizedRoi:
        roi.validate_in(self.source_frame_size)
        mapped = NormalizedRoi(
            float(Fraction(roi.left) * self._scale_x + self._offset_x),
            float(Fraction(roi.top) * self._scale_y + self._offset_y),
            float(Fraction(roi.right) * self._scale_x + self._offset_x),
            float(Fraction(roi.bottom) * self._scale_y + self._offset_y),
        )
        try:
            return mapped.validate_in(self.preview_frame_size)
        except GeometryError as error:
            if self.policy is PreviewTransformPolicy.CROP:
                raise GeometryError(
                    ErrorCode.GEOMETRY_INVALID,
                    "crop preview excludes required geometry",
                    field_path,
                    "Use a preview crop containing the base point and actuator ROI, or use stretch/letterbox.",
                ) from error
            raise

    def map_geometry(self, geometry: VideoGeometry) -> VideoGeometry:
        if geometry.frame_size != self.source_frame_size:
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "geometry frame size does not match the preview transform source",
                "geometry.frame_size",
            )
        return VideoGeometry(
            self.preview_frame_size,
            self.map_point(geometry.base_point, "base_point"),
            None
            if geometry.initial_tip_point is None
            else self.map_point(geometry.initial_tip_point, "initial_tip_point"),
            self.map_roi(geometry.actuator_roi, "actuator_roi"),
        )


def _is_finite_number(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and isfinite(value)
