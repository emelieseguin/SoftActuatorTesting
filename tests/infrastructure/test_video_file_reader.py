"""Tests for the OpenCV-backed prerecorded-video reader adapter.

These exercise the *real* ``tests/fixtures/video/synthetic-red-marker.avi``
fixture (192x128, 3 frames, 10fps) so at least one part of the suite proves
the adapter genuinely reads a video file, while the application-layer tests
stay hardware/codec free via ``FakeVideoFrameSource``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from soft_actuator_testing.application.video_geometry_workflow import VideoProbeCancelled
from soft_actuator_testing.domain.errors import GeometryError
from soft_actuator_testing.infrastructure.video_file_reader import OpenCvVideoFileReader

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "video" / "synthetic-red-marker.avi"


class _AlwaysCancelled:
    def is_cancelled(self) -> bool:
        return True


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


def test_open_safely_probes_metadata_for_the_synthetic_fixture() -> None:
    reader = OpenCvVideoFileReader()
    handle = reader.open(FIXTURE)
    try:
        assert handle.metadata.frame_size.width == 192
        assert handle.metadata.frame_size.height == 128
        assert handle.metadata.frame_count == 3
        assert handle.metadata.fps == pytest.approx(10.0)
    finally:
        handle.close()


def test_open_rejects_a_missing_file() -> None:
    reader = OpenCvVideoFileReader()
    with pytest.raises(GeometryError, match="does not exist"):
        reader.open(Path("does-not-exist.avi"))


def test_open_rejects_a_non_video_file(tmp_path: Path) -> None:
    bogus = tmp_path / "not-a-video.avi"
    bogus.write_text("this is not a video container")
    reader = OpenCvVideoFileReader()
    with pytest.raises(GeometryError):
        reader.open(bogus)


def test_read_frame_zero_returns_rgb_pixels_matching_reported_dimensions() -> None:
    reader = OpenCvVideoFileReader()
    handle = reader.open(FIXTURE)
    try:
        frame = handle.read_frame(0)
        assert frame.shape == (128, 192, 3)
        assert frame.dtype == np.uint8
    finally:
        handle.close()


def test_read_frame_accepts_every_reported_frame_index() -> None:
    reader = OpenCvVideoFileReader()
    handle = reader.open(FIXTURE)
    try:
        for index in range(handle.metadata.frame_count):
            frame = handle.read_frame(index)
            assert frame.shape == (128, 192, 3)
    finally:
        handle.close()


def test_read_frame_rejects_an_out_of_range_index() -> None:
    reader = OpenCvVideoFileReader()
    handle = reader.open(FIXTURE)
    try:
        with pytest.raises(GeometryError, match="out of range"):
            handle.read_frame(handle.metadata.frame_count)
        with pytest.raises(GeometryError, match="out of range"):
            handle.read_frame(-1)
    finally:
        handle.close()


def test_read_frame_after_close_fails_closed() -> None:
    reader = OpenCvVideoFileReader()
    handle = reader.open(FIXTURE)
    handle.close()
    with pytest.raises(GeometryError, match="already closed"):
        handle.read_frame(0)
    # Closing twice must not raise (idempotent cleanup).
    handle.close()


def test_open_with_an_already_cancelled_token_never_opens_a_video_handle() -> None:
    reader = OpenCvVideoFileReader()
    with pytest.raises(VideoProbeCancelled):
        reader.open(FIXTURE, cancellation=_AlwaysCancelled())


def test_manual_frame_count_scan_is_cancellable() -> None:
    reader = OpenCvVideoFileReader()
    import cv2

    capture = cv2.VideoCapture(str(FIXTURE))
    assert capture.isOpened()
    try:
        with pytest.raises(VideoProbeCancelled):
            reader._count_frames_by_scanning(capture, _AlwaysCancelled())
    finally:
        capture.release()


def test_manual_frame_count_scan_counts_frames_when_not_cancelled() -> None:
    reader = OpenCvVideoFileReader()
    import cv2

    capture = cv2.VideoCapture(str(FIXTURE))
    assert capture.isOpened()
    try:
        count = reader._count_frames_by_scanning(capture, _NeverCancelled())
        assert count == 3
    finally:
        capture.release()
