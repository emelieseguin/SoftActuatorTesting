"""An accessible, non-color-only semantic status indicator."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from soft_actuator_testing.ui.themes.qt_bridge import to_qcolor, to_qfont
from soft_actuator_testing.ui.themes.tokens import SemanticState, Theme

_DOT_DIAMETER = 10


class _StateDot(QWidget):
    """A small filled circle; paired with a text glyph so state is never color-only."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = None
        self.setFixedSize(QSize(_DOT_DIAMETER, _DOT_DIAMETER))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_color(self, color) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override naming
        if self._color is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(self.rect())
        painter.end()


class StatusIndicator(QWidget):
    """Shows a semantic state as color + glyph + label text (never color-only).

    The accessible name always reads the human label (for example
    ``"Connection status: Ready"``), so the state is available to assistive
    technology even though the visual is a colored dot and glyph.
    """

    def __init__(
        self,
        title: str,
        *,
        state: SemanticState = SemanticState.NEUTRAL,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._state = state
        self._theme: Theme | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._dot = _StateDot(self)
        self._glyph_label = QLabel(self)
        self._text_label = QLabel(self)
        layout.addWidget(self._dot)
        layout.addWidget(self._glyph_label)
        layout.addWidget(self._text_label)
        layout.addStretch(1)

        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    @property
    def state(self) -> SemanticState:
        return self._state

    def set_state(self, state: SemanticState) -> None:
        self._state = state
        self._render()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        for label in (self._glyph_label, self._text_label):
            label.setFont(to_qfont(theme.typography.body))
        self._render()

    def _render(self) -> None:
        if self._theme is None:
            return
        style = self._theme.state_style(self._state)
        self._dot.set_color(to_qcolor(style.color))
        self._glyph_label.setText(style.glyph)
        self._text_label.setText(f"{self._title}: {style.label}")
        accessible_name = f"{self._title} status: {style.label}"
        self.setAccessibleName(accessible_name)
        self.setAccessibleDescription(accessible_name)
