"""OpenCV-backed dual-hue red-marker frame scanner; the only OpenCV import for
guided marker suggestions.

Implements :class:`~soft_actuator_testing.application.marker_suggestion.RedMarkerFrameDetector`.
No other marker-suggestion module imports ``cv2`` directly, mirroring
``infrastructure/video_file_reader.py``'s isolation of OpenCV for manual video
geometry: this adapter is freely replaceable without touching the domain,
application, or UI layers. OpenCV is used here purely to analyze pixels; it
never becomes the authoritative record of a marker's position — that remains
an operator-confirmed (optionally suggestion-assisted) selection persisted by
``VideoGeometryWorkflow``.
"""

from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np

from soft_actuator_testing.application.marker_suggestion import (
    HsvRedThresholds,
    MarkerSuggestionCancelled,
    RedBlob,
    RedMarkerScan,
)
from soft_actuator_testing.application.services import CancellationToken
from soft_actuator_testing.domain.errors import ErrorCode, GeometryError
from soft_actuator_testing.domain.geometry import FrameSize, NormalizedRoi, PixelPoint


def _check_cancelled(cancellation: CancellationToken | None) -> None:
    if cancellation is not None and cancellation.is_cancelled():
        raise MarkerSuggestionCancelled("marker suggestion scan was cancelled")


class OpenCvRedMarkerFrameDetector:
    """Dual-hue HSV red-blob scanner restricted to an optional ROI.

    Frames are expected in RGB pixel order (matching
    ``OpenCvVideoFileReader.read_frame`` / ``VideoCanvas`` conventions
    elsewhere in this codebase), not OpenCV's native BGR order.
    """

    def scan(
        self,
        frame: Any,
        thresholds: HsvRedThresholds,
        roi: NormalizedRoi | None,
        *,
        cancellation: CancellationToken | None = None,
    ) -> RedMarkerScan:
        _check_cancelled(cancellation)
        if frame is None:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "a frame is required to scan for red markers", "frame")
        height, width = frame.shape[0], frame.shape[1]
        frame_size = FrameSize(int(width), int(height))

        left, top, right, bottom = 0, 0, width, height
        if roi is not None:
            left = max(0, int(math.floor(roi.left)))
            top = max(0, int(math.floor(roi.top)))
            right = min(width, int(math.ceil(roi.right)))
            bottom = min(height, int(math.ceil(roi.bottom)))

        full_mask = np.zeros((height, width), dtype=np.uint8)
        if right > left and bottom > top:
            region = frame[top:bottom, left:right]
            hsv = cv2.cvtColor(region, cv2.COLOR_RGB2HSV)
            _check_cancelled(cancellation)
            mask = self._dual_hue_mask(hsv, thresholds)
            mask = self._apply_morphology(mask, thresholds)
            full_mask[top:bottom, left:right] = mask

        _check_cancelled(cancellation)
        contours, _ = cv2.findContours(full_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        hsv_full = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV).astype(np.float64)
        blobs: list[RedBlob] = []
        for contour in contours:
            _check_cancelled(cancellation)
            area = cv2.contourArea(contour)
            if area <= 0:
                continue
            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            moments = cv2.moments(contour)
            if moments.get("m00", 0) == 0:
                continue
            cx = moments["m10"] / moments["m00"]
            cy = moments["m01"] / moments["m00"]
            bx, by, bw, bh = cv2.boundingRect(contour)
            contour_mask = np.zeros((height, width), dtype=np.uint8)
            cv2.drawContours(contour_mask, [contour], -1, 255, thickness=cv2.FILLED)
            matched = (contour_mask > 0) & (full_mask > 0)
            if not matched.any():
                continue
            saturation = hsv_full[..., 1][matched]
            value = hsv_full[..., 2][matched]
            redness = float(np.mean((saturation / 255.0) * (value / 255.0)))
            blobs.append(
                RedBlob(
                    centroid=PixelPoint(float(cx), float(cy)),
                    bounding_box=NormalizedRoi(float(bx), float(by), float(bx + bw), float(by + bh)),
                    area_pixels=float(area),
                    perimeter_pixels=float(perimeter),
                    redness_score=min(1.0, max(0.0, redness)),
                )
            )

        return RedMarkerScan(frame_size=frame_size, roi=roi, blobs=tuple(blobs), mask_preview=full_mask)

    @staticmethod
    def _dual_hue_mask(hsv: np.ndarray, thresholds: HsvRedThresholds) -> np.ndarray:
        lower1 = np.array([0, thresholds.saturation_min, thresholds.value_min], dtype=np.uint8)
        upper1 = np.array([thresholds.hue_low_max, thresholds.saturation_max, thresholds.value_max], dtype=np.uint8)
        lower2 = np.array([thresholds.hue_high_min, thresholds.saturation_min, thresholds.value_min], dtype=np.uint8)
        upper2 = np.array([179, thresholds.saturation_max, thresholds.value_max], dtype=np.uint8)
        mask1 = cv2.inRange(hsv, lower1, upper1)
        mask2 = cv2.inRange(hsv, lower2, upper2)
        return cv2.bitwise_or(mask1, mask2)

    @staticmethod
    def _apply_morphology(mask: np.ndarray, thresholds: HsvRedThresholds) -> np.ndarray:
        size = thresholds.morphology_kernel_size
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        if thresholds.morph_open_iterations:
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=thresholds.morph_open_iterations)
        if thresholds.morph_close_iterations:
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=thresholds.morph_close_iterations)
        return mask


__all__ = ["OpenCvRedMarkerFrameDetector"]
