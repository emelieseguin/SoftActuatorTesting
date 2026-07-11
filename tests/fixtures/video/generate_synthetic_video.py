"""Generate the compact, synthetic legacy-video compatibility fixture."""

from pathlib import Path

import cv2
import numpy as np


WIDTH = 192
HEIGHT = 128
FPS = 10.0
MARKER_CENTERS = ((140, 36), (146, 32), (152, 28))


def make_frame(marker_center: tuple[int, int]) -> np.ndarray:
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    cv2.circle(frame, marker_center, 7, (0, 0, 255), -1)
    return frame


def main() -> None:
    output_path = Path(__file__).with_name("synthetic-red-marker.avi")
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        FPS,
        (WIDTH, HEIGHT),
    )
    if not writer.isOpened():
        raise RuntimeError("OpenCV MJPG video writer is unavailable")
    try:
        for marker_center in MARKER_CENTERS:
            writer.write(make_frame(marker_center))
    finally:
        writer.release()


if __name__ == "__main__":
    main()
