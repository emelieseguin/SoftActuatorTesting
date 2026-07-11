from __future__ import annotations

import pytest

from soft_actuator_testing.domain.errors import GeometryError
from soft_actuator_testing.domain.geometry import FrameSize, NormalizedRoi, PixelPoint, VideoGeometry


def test_reverse_corner_selection_is_normalized_inside_frame() -> None:
    frame = FrameSize(192, 128)
    roi = NormalizedRoi.from_corners(PixelPoint(180, 115), PixelPoint(10, 15), frame)

    assert (roi.left, roi.top, roi.right, roi.bottom) == (10, 15, 180, 115)
    assert (roi.width, roi.height) == (170, 100)


@pytest.mark.parametrize(
    ("roi_args", "message"),
    [
        ((180, 110, 170, 95), "outside"),
        ((10, 10, -1, 5), "positive"),
    ],
)
def test_roi_rejects_out_of_bounds_or_empty_regions(
    roi_args: tuple[float, float, float, float], message: str
) -> None:
    with pytest.raises(GeometryError, match=message):
        NormalizedRoi.from_xywh(*roi_args, FrameSize(192, 128))


def test_geometry_rejects_points_outside_source_frame() -> None:
    frame = FrameSize(192, 128)
    roi = NormalizedRoi.from_xywh(10, 15, 170, 100, frame)
    with pytest.raises(GeometryError, match="outside frame"):
        VideoGeometry(frame, PixelPoint(192, 0), PixelPoint(140, 36), roi)


def test_geometry_allows_an_optional_initial_tip_but_requires_base_and_roi() -> None:
    frame = FrameSize(192, 128)
    roi = NormalizedRoi.from_xywh(10, 15, 170, 100, frame)
    geometry = VideoGeometry(frame, PixelPoint(20, 96), None, roi)

    assert geometry.initial_tip_point is None


def test_pixel_point_and_frame_size_require_valid_finite_dimensions() -> None:
    with pytest.raises(GeometryError, match="positive"):
        FrameSize(0, 128)
    with pytest.raises(GeometryError, match="integers"):
        FrameSize(192.0, 128)  # type: ignore[arg-type]
    with pytest.raises(GeometryError, match="finite"):
        PixelPoint(float("nan"), 0)
