from __future__ import annotations

import pytest

from soft_actuator_testing.domain.errors import GeometryError
from soft_actuator_testing.domain.geometry import (
    FrameSize,
    NormalizedRoi,
    PixelPoint,
    PreviewGeometryTransform,
    PreviewTransformPolicy,
    VideoGeometry,
)


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
    with pytest.raises(GeometryError) as raised:
        PixelPoint(True, 0)  # type: ignore[arg-type]
    assert raised.value.field_path == "point"
    with pytest.raises(GeometryError):
        NormalizedRoi(0, 0, "1", 1)  # type: ignore[arg-type]


def test_full_resolution_geometry_scales_exactly_to_the_960_by_540_preview() -> None:
    source = FrameSize(3840, 2160)
    geometry = VideoGeometry(
        source,
        PixelPoint(384, 1080),
        PixelPoint(3456, 540),
        NormalizedRoi(192, 216, 3648, 1944),
    )

    preview = PreviewGeometryTransform.create(source, FrameSize(960, 540)).map_geometry(geometry)

    assert preview.frame_size == FrameSize(960, 540)
    assert preview.base_point == PixelPoint(96.0, 270.0)
    assert preview.initial_tip_point == PixelPoint(864.0, 135.0)
    assert preview.actuator_roi == NormalizedRoi(48.0, 54.0, 912.0, 486.0)


def test_aspect_mismatch_policies_are_explicit_and_only_reject_clipped_crop_geometry() -> None:
    source = FrameSize(400, 300)
    full_geometry = VideoGeometry(
        source,
        PixelPoint(200, 150),
        PixelPoint(300, 150),
        NormalizedRoi(0, 0, 400, 300),
    )
    preview = FrameSize(960, 540)

    stretched = PreviewGeometryTransform.create(source, preview).map_geometry(full_geometry)
    letterboxed = PreviewGeometryTransform.create(
        source, preview, policy=PreviewTransformPolicy.LETTERBOX
    ).map_geometry(full_geometry)
    assert stretched.base_point == PixelPoint(480.0, 270.0)
    assert stretched.actuator_roi == NormalizedRoi(0.0, 0.0, 960.0, 540.0)
    assert letterboxed.base_point == PixelPoint(480.0, 270.0)
    assert letterboxed.actuator_roi == NormalizedRoi(120.0, 0.0, 840.0, 540.0)

    cropped = PreviewGeometryTransform.create(source, preview, policy=PreviewTransformPolicy.CROP)
    with pytest.raises(GeometryError, match="crop preview excludes"):
        cropped.map_geometry(full_geometry)

    visible_geometry = VideoGeometry(
        source,
        PixelPoint(200, 150),
        PixelPoint(300, 150),
        NormalizedRoi(100, 100, 300, 200),
    )
    assert cropped.map_geometry(visible_geometry).actuator_roi == NormalizedRoi(240.0, 150.0, 720.0, 390.0)
