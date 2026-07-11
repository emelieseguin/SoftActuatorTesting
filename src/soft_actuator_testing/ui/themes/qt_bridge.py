"""Applies Qt-free theme tokens to Qt objects without any QSS/stylesheet text.

Only this module (and widgets that call it) translate :mod:`tokens` data into
``QPalette``/``QFont``. No ``.qss`` files and no ``setStyleSheet`` string
styling are used anywhere in this project; every visual property is set
through typed Qt API calls driven by plain Python token data.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from .tokens import FontToken, RgbColor, Theme


def to_qcolor(color: RgbColor) -> QColor:
    return QColor(color.red, color.green, color.blue, color.alpha)


def to_qfont(token: FontToken) -> QFont:
    font = QFont(token.family, token.size_pt)
    font.setWeight(QFont.Weight(token.weight))
    font.setItalic(token.italic)
    return font


def build_palette(theme: Theme) -> QPalette:
    """Build a ``QPalette`` from theme color tokens (no stylesheet strings)."""

    colors = theme.colors
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, to_qcolor(colors.background))
    palette.setColor(QPalette.ColorRole.WindowText, to_qcolor(colors.text_primary))
    palette.setColor(QPalette.ColorRole.Base, to_qcolor(colors.surface))
    palette.setColor(QPalette.ColorRole.AlternateBase, to_qcolor(colors.surface_alt))
    palette.setColor(QPalette.ColorRole.Text, to_qcolor(colors.text_primary))
    palette.setColor(QPalette.ColorRole.PlaceholderText, to_qcolor(colors.text_secondary))
    palette.setColor(QPalette.ColorRole.Button, to_qcolor(colors.surface_alt))
    palette.setColor(QPalette.ColorRole.ButtonText, to_qcolor(colors.text_primary))
    palette.setColor(QPalette.ColorRole.Highlight, to_qcolor(colors.accent))
    palette.setColor(QPalette.ColorRole.HighlightedText, to_qcolor(colors.accent_text))
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        to_qcolor(colors.text_disabled),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        to_qcolor(colors.text_disabled),
    )
    return palette


def apply_theme(application: QApplication, theme: Theme) -> None:
    """Apply the palette and base font for ``theme`` to the whole application."""

    application.setPalette(build_palette(theme))
    application.setFont(to_qfont(theme.typography.body))


def apply_theme_to_widget(widget: QWidget, theme: Theme) -> None:
    """Apply a theme to a single widget (for widgets built before app palette)."""

    widget.setPalette(build_palette(theme))
    widget.setFont(to_qfont(theme.typography.body))
