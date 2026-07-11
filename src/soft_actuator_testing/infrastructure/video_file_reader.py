"""OpenCV-backed prerecorded-video reader; the only OpenCV import in this workflow.

Implements :class:`~soft_actuator_testing.application.video_geometry_workflow.VideoFrameSource`.
No other geometry/video module imports ``cv2`` directly, so this adapter is
freely replaceable (for example with an ffmpeg-backed reader) without
touching the domain, application, or UI layers.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from soft_actuator_testing.application.services import CancellationToken
from soft_actuator_testing.application.video_geometry_workflow import (
    OpenVideoFile,
    VideoMetadata,
    VideoProbeCancelled,
)
from soft_actuator_testing.domain.errors import ErrorCode, GeometryError
from soft_actuator_testing.domain.geometry import FrameSize


class _OpenCvVideoFile:
    """One opened ``cv2.VideoCapture`` handle plus its safely-probed metadata."""

    def __init__(self, capture: cv2.VideoCapture, metadata: VideoMetadata) -> None:
        self._capture = capture
        self.metadata = metadata
        self._closed = False

    def read_frame(self, frame_index: int) -> np.ndarray:
        if self._closed:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "video handle is already closed", "video")
        if not (0 <= frame_index < self.metadata.frame_count):
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "frame index is out of range",
                "frame_index",
                f"Use an index between 0 and {self.metadata.frame_count - 1}.",
            )
        if not self._capture.set(cv2.CAP_PROP_POS_FRAMES, float(frame_index)):
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "cannot seek to the requested frame", "frame_index")
        ok, frame = self._capture.read()
        if not ok or frame is None:
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "cannot read the requested frame",
                "frame_index",
                "The source video may be corrupt or use an unsupported codec.",
            )
        # OpenCV decodes BGR; VideoCanvas and this workflow expect RGB.
        return np.ascontiguousarray(frame[..., ::-1])

    def close(self) -> None:
        if not self._closed:
            self._capture.release()
            self._closed = True


class OpenCvVideoFileReader:
    """Safely probes and opens prerecorded video files with OpenCV."""

    def open(self, source: Path, *, cancellation: CancellationToken | None = None) -> OpenVideoFile:
        source = Path(source)
        if not source.is_file():
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "video source file does not exist",
                "source",
                "Choose an existing prerecorded video file.",
            )
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            capture.release()
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "cannot open video source; the file may be corrupt or use an unsupported codec",
                "source",
            )
        try:
            metadata = self._probe(capture, cancellation)
        except Exception:
            capture.release()
            raise
        return _OpenCvVideoFile(capture, metadata)

    def _probe(self, capture: cv2.VideoCapture, cancellation: CancellationToken | None) -> VideoMetadata:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps_raw = capture.get(cv2.CAP_PROP_FPS)
        fps = float(fps_raw) if fps_raw and fps_raw > 0 else 0.0
        reported_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        if width <= 0 or height <= 0:
            ok, probe_frame = capture.read()
            if not ok or probe_frame is None:
                raise GeometryError(
                    ErrorCode.GEOMETRY_INVALID,
                    "cannot read the first frame to determine video dimensions",
                    "source",
                    "The source video may be empty, corrupt, or use an unsupported codec.",
                )
            height, width = probe_frame.shape[:2]
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0.0)

        frame_count = reported_count if reported_count > 0 else self._count_frames_by_scanning(capture, cancellation)
        capture.set(cv2.CAP_PROP_POS_FRAMES, 0.0)
        if frame_count <= 0:
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "video contains no readable frames",
                "source",
                "Choose a video that contains at least one frame.",
            )
        return VideoMetadata(FrameSize(width, height), frame_count, fps)

    @staticmethod
    def _count_frames_by_scanning(capture: cv2.VideoCapture, cancellation: CancellationToken | None) -> int:
        """Fall back to manually scanning frames when a container under-reports its count.

        OpenCV's ``CAP_PROP_FRAME_COUNT`` is unreliable for some containers/codecs
        (a well-known limitation), so this counts frames directly when needed. The
        scan is cancellable so a caller can abort probing a very large file.
        """

        count = 0
        while True:
            if cancellation is not None and cancellation.is_cancelled():
                raise VideoProbeCancelled("video probing was cancelled before the frame count could be determined")
            ok, _ = capture.read()
            if not ok:
                break
            count += 1
        return count


__all__ = ["OpenCvVideoFileReader"]
