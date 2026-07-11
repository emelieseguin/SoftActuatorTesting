"""Generate the deterministic synthetic marker-suggestion regression fixture.

Produces ``synthetic-marker-suggestions.avi``: nine 192x128, 10 fps frames,
each isolating one guided red-marker-suggestion scenario described in
``docs/architecture/marker-suggestions.md``. Uses only numpy/OpenCV, matching
the existing ``generate_synthetic_video.py`` fixture's conventions. Contains
no real experiment data.

Regenerate from the repository root with:

```bash
uv run python tests/fixtures/video/generate_marker_suggestion_video.py
```
"""

from pathlib import Path

import cv2
import numpy as np

WIDTH = 192
HEIGHT = 128
FPS = 10.0

# RGB colors (converted to BGR only at ``cv2.circle`` time, since OpenCV
# drawing primitives expect BGR channel order for the frames this generator
# writes with ``cv2.VideoWriter``).
PURE_RED_RGB = (255, 0, 0)  # OpenCV HSV hue 0 -- the low red band.
HUE_WRAP_RED_RGB = (255, 0, 60)  # OpenCV HSV hue ~173 -- the high/wrap red band.
DECOY_ORANGE_RGB = (255, 140, 0)  # OpenCV HSV hue ~16 -- must never be treated as red.

# Frame-0 baseline marker: proves correct frame-zero processing.
BASELINE_CENTER = (96, 64)
BASELINE_RADIUS = 7

# Frame-1 dual-hue wraparound marker (same size/position, different red hue).
HUE_WRAP_CENTER = (96, 64)
HUE_WRAP_RADIUS = 7

# Frame-2 decoy + one genuine marker: proves decoys are rejected by hue alone.
DECOY_CENTER = (50, 64)
DECOY_RADIUS = 7
DECOY_MARKER_CENTER = (140, 64)
DECOY_MARKER_RADIUS = 7

# Frame-3 is intentionally left blank (no red pixels at all).

# Frame-4 ROI-restriction: one marker inside, one outside a documented ROI.
ROI_INSIDE_CENTER = (60, 64)
ROI_INSIDE_RADIUS = 6
ROI_OUTSIDE_CENTER = (160, 20)
ROI_OUTSIDE_RADIUS = 6

# Frame-5 ambiguity: two equidistant, equal-size/color markers from a shared
# base point, with no previous tip to break the tie.
AMBIGUOUS_BASE_POINT = (96, 64)
AMBIGUOUS_LEFT_CENTER = (60, 64)
AMBIGUOUS_RIGHT_CENTER = (132, 64)
AMBIGUOUS_RADIUS = 7

# Frames 6/7: temporal continuity across two adjacent frames. Both markers
# are equal size/color (so redness/size/circularity never break the tie);
# only a previous confirmed tip should resolve which one is "current".
TEMPORAL_A_LEFT_CENTER = (55, 64)
TEMPORAL_A_RIGHT_CENTER = (145, 64)
TEMPORAL_B_LEFT_CENTER = (60, 66)
TEMPORAL_B_RIGHT_CENTER = (150, 62)
TEMPORAL_RADIUS = 7

# Frame-8: a marker smaller than the default minimum-area threshold, to prove
# raising/lowering ``min_area_pixels`` changes what gets suggested.
SMALL_MARKER_CENTER = (96, 64)
SMALL_MARKER_RADIUS = 3


def _rgb_to_bgr(color: tuple[int, int, int]) -> tuple[int, int, int]:
    r, g, b = color
    return (b, g, r)


def _blank_frame() -> np.ndarray:
    return np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)


def _draw(frame: np.ndarray, center: tuple[int, int], radius: int, color_rgb: tuple[int, int, int]) -> None:
    cv2.circle(frame, center, radius, _rgb_to_bgr(color_rgb), -1)


def make_frames() -> list[np.ndarray]:
    frames: list[np.ndarray] = []

    baseline = _blank_frame()
    _draw(baseline, BASELINE_CENTER, BASELINE_RADIUS, PURE_RED_RGB)
    frames.append(baseline)

    hue_wrap = _blank_frame()
    _draw(hue_wrap, HUE_WRAP_CENTER, HUE_WRAP_RADIUS, HUE_WRAP_RED_RGB)
    frames.append(hue_wrap)

    decoy = _blank_frame()
    _draw(decoy, DECOY_CENTER, DECOY_RADIUS, DECOY_ORANGE_RGB)
    _draw(decoy, DECOY_MARKER_CENTER, DECOY_MARKER_RADIUS, PURE_RED_RGB)
    frames.append(decoy)

    frames.append(_blank_frame())  # frame 3: no marker at all.

    roi_frame = _blank_frame()
    _draw(roi_frame, ROI_INSIDE_CENTER, ROI_INSIDE_RADIUS, PURE_RED_RGB)
    _draw(roi_frame, ROI_OUTSIDE_CENTER, ROI_OUTSIDE_RADIUS, PURE_RED_RGB)
    frames.append(roi_frame)

    ambiguous = _blank_frame()
    _draw(ambiguous, AMBIGUOUS_LEFT_CENTER, AMBIGUOUS_RADIUS, PURE_RED_RGB)
    _draw(ambiguous, AMBIGUOUS_RIGHT_CENTER, AMBIGUOUS_RADIUS, PURE_RED_RGB)
    frames.append(ambiguous)

    temporal_a = _blank_frame()
    _draw(temporal_a, TEMPORAL_A_LEFT_CENTER, TEMPORAL_RADIUS, PURE_RED_RGB)
    _draw(temporal_a, TEMPORAL_A_RIGHT_CENTER, TEMPORAL_RADIUS, PURE_RED_RGB)
    frames.append(temporal_a)

    temporal_b = _blank_frame()
    _draw(temporal_b, TEMPORAL_B_LEFT_CENTER, TEMPORAL_RADIUS, PURE_RED_RGB)
    _draw(temporal_b, TEMPORAL_B_RIGHT_CENTER, TEMPORAL_RADIUS, PURE_RED_RGB)
    frames.append(temporal_b)

    small = _blank_frame()
    _draw(small, SMALL_MARKER_CENTER, SMALL_MARKER_RADIUS, PURE_RED_RGB)
    frames.append(small)

    return frames


def main() -> None:
    output_path = Path(__file__).with_name("synthetic-marker-suggestions.avi")
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        FPS,
        (WIDTH, HEIGHT),
    )
    if not writer.isOpened():
        raise RuntimeError("OpenCV MJPG video writer is unavailable")
    try:
        for frame in make_frames():
            writer.write(frame)
    finally:
        writer.release()


if __name__ == "__main__":
    main()
