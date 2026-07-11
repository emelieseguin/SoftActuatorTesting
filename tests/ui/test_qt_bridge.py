"""Tests for translating Qt-free theme tokens into QPalette/QFont (no QSS)."""

from __future__ import annotations

from PySide6.QtGui import QPalette

from soft_actuator_testing.ui.themes.qt_bridge import apply_theme, build_palette, to_qcolor, to_qfont
from soft_actuator_testing.ui.themes.tokens import DARK_THEME, LIGHT_THEME


def test_to_qcolor_matches_token_channels() -> None:
    color = LIGHT_THEME.colors.accent
    qcolor = to_qcolor(color)
    assert (qcolor.red(), qcolor.green(), qcolor.blue(), qcolor.alpha()) == (
        color.red,
        color.green,
        color.blue,
        color.alpha,
    )


def test_to_qfont_matches_typography_token() -> None:
    token = DARK_THEME.typography.heading
    font = to_qfont(token)
    assert font.family() == token.family
    assert font.pointSize() == token.size_pt
    assert font.italic() == token.italic


def test_build_palette_uses_theme_color_roles_not_stylesheets() -> None:
    palette = build_palette(DARK_THEME)
    assert isinstance(palette, QPalette)
    assert palette.color(QPalette.ColorRole.Window) == to_qcolor(DARK_THEME.colors.background)
    assert palette.color(QPalette.ColorRole.Highlight) == to_qcolor(DARK_THEME.colors.accent)
    assert (
        palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        == to_qcolor(DARK_THEME.colors.text_disabled)
    )


def test_apply_theme_sets_application_palette_and_font(qtbot, qapp) -> None:
    apply_theme(qapp, LIGHT_THEME)
    assert qapp.palette().color(QPalette.ColorRole.Window) == to_qcolor(LIGHT_THEME.colors.background)
    assert qapp.font().family() == LIGHT_THEME.typography.body.family

    apply_theme(qapp, DARK_THEME)
    assert qapp.palette().color(QPalette.ColorRole.Window) == to_qcolor(DARK_THEME.colors.background)
