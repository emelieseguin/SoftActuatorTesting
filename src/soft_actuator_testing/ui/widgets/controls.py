"""Accessible, theme-aware reusable controls.

Every control here sets an explicit accessible name/description (so screen
readers announce useful text, not a blank/generic one), enforces a minimum
44x28px hit target, and draws its own focus ring in Python (no ``:focus``
QSS selector) so keyboard focus is always visible regardless of platform
Qt style.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from soft_actuator_testing.ui.themes.qt_bridge import to_qcolor, to_qfont
from soft_actuator_testing.ui.themes.tokens import Theme

#: Minimum interactive target size (px) so controls stay usable/clickable.
MIN_TARGET_WIDTH = 88
MIN_TARGET_HEIGHT = 28


class FocusRingMixin:
    """Paints a visible focus ring driven by theme tokens after the base paint."""

    _theme: Theme | None = None

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()  # type: ignore[attr-defined]

    def _paint_focus_ring(self) -> None:
        widget = self  # type: ignore[assignment]
        if not widget.hasFocus() or self._theme is None:  # type: ignore[attr-defined]
            return
        focus = self._theme.focus
        painter = QPainter(widget)  # type: ignore[arg-type]
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(to_qcolor(focus.ring_color))
        pen.setWidth(focus.ring_width_px)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        offset = focus.ring_offset_px
        rect = QRectF(widget.rect()).adjusted(offset, offset, -offset, -offset)  # type: ignore[attr-defined]
        painter.drawRoundedRect(rect, 4, 4)
        painter.end()


class AccessibleButton(FocusRingMixin, QPushButton):
    """A themed push button with a guaranteed accessible name and focus ring."""

    def __init__(
        self,
        text: str,
        *,
        accessible_description: str | None = None,
        variant: str = "primary",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.variant = variant
        self.setAccessibleName(text)
        if accessible_description:
            self.setAccessibleDescription(accessible_description)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(MIN_TARGET_WIDTH, MIN_TARGET_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override naming
        super().setText(text)
        # Keep the accessible name in sync so screen readers never see stale text.
        self.setAccessibleName(text)

    def apply_theme(self, theme: Theme) -> None:
        super().apply_theme(theme)
        self.setFont(to_qfont(theme.typography.body))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override naming
        super().paintEvent(event)
        self._paint_focus_ring()
