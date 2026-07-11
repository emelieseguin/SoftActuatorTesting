"""Project-owned video/image canvas wrapper.

Wraps raw NumPy frames in an accessible Qt widget so no other module needs
to touch ``QImage``/``QPixmap`` directly. Frames are expected as
``HxWx3`` ``uint8`` NumPy arrays in RGB order (callers reading OpenCV's BGR
frames must convert with ``frame[..., ::-1]`` before calling :meth:`set_frame`,
keeping any OpenCV-specific color handling out of this widget).

Because dragging/annotating video with a mouse is not usable for
keyboard-only or screen-reader operators, this widget also exposes a
keyboard alternative for frame navigation (arrow keys / Home / End) and
keeps an always-current text accessible description of the visible frame,
independent of any mouse interaction.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QPaintEvent, QKeyEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

OverlayPainter = Callable[[QPainter, QRect], None]
Unsubscribe = Callable[[], None]

_STEP_KEYS = {
    Qt.Key.Key_Left: -1,
    Qt.Key.Key_Right: 1,
}
_JUMP_KEYS = {
    Qt.Key.Key_Home: "first",
    Qt.Key.Key_End: "last",
}
_LARGE_STEP_MULTIPLIER = 10


class VideoCanvas(QWidget):
    """Displays one video/image frame with overlay hooks and keyboard stepping.

    Signals:
        frame_step_requested(int): a relative frame delta requested via
            keyboard (Left/Right = ±1, Shift+Left/Right = ±10).
        jump_requested(str): ``"first"`` or ``"last"`` requested via
            Home/End, for callers that support jumping to clip boundaries.
    """

    frame_step_requested = Signal(int)
    jump_requested = Signal(str)

    def __init__(self, *, accessible_title: str = "Video preview", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frame: np.ndarray | None = None
        self._frame_index = 0
        self._frame_count = 0
        self._title = accessible_title
        self._overlays: list[OverlayPainter] = []
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(160, 90)
        self._update_accessible_description(None)

    def set_frame(
        self,
        frame: np.ndarray | None,
        *,
        frame_index: int = 0,
        frame_count: int = 0,
        description: str | None = None,
    ) -> None:
        """Set the visible frame plus the state used to build the a11y text."""

        if frame is not None and (frame.ndim != 3 or frame.shape[2] != 3):
            raise ValueError("frame must be an HxWx3 RGB array")
        self._frame = frame
        self._frame_index = frame_index
        self._frame_count = frame_count
        self._update_accessible_description(description)
        self.update()

    def register_overlay(self, overlay: OverlayPainter) -> Unsubscribe:
        """Register a paint hook invoked with (painter, widget_rect) after the frame.

        Returns an unsubscribe callable so callers (for example a geometry
        editor showing base/tip/ROI handles) can remove their overlay when a
        view is torn down.
        """

        self._overlays.append(overlay)

        def _unsubscribe() -> None:
            if overlay in self._overlays:
                self._overlays.remove(overlay)
                self.update()

        return _unsubscribe

    def clear_overlays(self) -> None:
        self._overlays.clear()
        self.update()

    def _update_accessible_description(self, description: str | None) -> None:
        if self._frame_count > 0:
            position = f"frame {self._frame_index + 1} of {self._frame_count}"
        elif self._frame is None:
            position = "no frame loaded"
        else:
            position = f"frame {self._frame_index + 1}"
        detail = f": {description}" if description else ""
        text = f"{self._title} ({position}){detail}"
        self.setAccessibleName(self._title)
        self.setAccessibleDescription(text)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override naming
        key = event.key()
        if key in _STEP_KEYS:
            delta = _STEP_KEYS[key]
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                delta *= _LARGE_STEP_MULTIPLIER
            self.frame_step_requested.emit(delta)
            event.accept()
            return
        if key in _JUMP_KEYS:
            self.jump_requested.emit(_JUMP_KEYS[key])
            event.accept()
            return
        super().keyPressEvent(event)

    def _to_qimage(self) -> QImage | None:
        if self._frame is None:
            return None
        frame = np.ascontiguousarray(self._frame)
        height, width, _ = frame.shape
        image = QImage(
            frame.data,
            width,
            height,
            frame.strides[0],
            QImage.Format.Format_RGB888,
        )
        # Copy so the QImage stays valid after this frame's NumPy buffer is
        # replaced/garbage collected by the next set_frame call.
        return image.copy()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override naming
        painter = QPainter(self)
        image = self._to_qimage()
        if image is not None:
            target = self.rect()
            scaled = image.scaled(
                target.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = target.x() + (target.width() - scaled.width()) // 2
            y = target.y() + (target.height() - scaled.height()) // 2
            painter.drawImage(x, y, scaled)
        for overlay in self._overlays:
            overlay(painter, self.rect())
        painter.end()
