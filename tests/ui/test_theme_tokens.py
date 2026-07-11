"""Tests for Qt-free theme tokens: palettes, spacing, typography, focus, charts."""

from __future__ import annotations

import pytest

from soft_actuator_testing.ui.themes.tokens import (
    DARK_THEME,
    LIGHT_THEME,
    RgbColor,
    SemanticState,
    ThemeMode,
    build_dark_theme,
    build_light_theme,
    theme_for_mode,
)


def test_light_and_dark_themes_have_distinct_backgrounds() -> None:
    assert LIGHT_THEME.mode is ThemeMode.LIGHT
    assert DARK_THEME.mode is ThemeMode.DARK
    assert LIGHT_THEME.colors.background != DARK_THEME.colors.background
    assert LIGHT_THEME.colors.text_primary != DARK_THEME.colors.text_primary


@pytest.mark.parametrize("theme", [LIGHT_THEME, DARK_THEME])
def test_theme_exposes_spacing_typography_focus_and_chart_tokens(theme) -> None:
    # Spacing is a strictly increasing scale used for margins/padding/gaps.
    spacing = theme.spacing
    assert spacing.xs < spacing.sm < spacing.md < spacing.lg < spacing.xl < spacing.xxl

    # Typography provides every named font used across dense/spacious shells.
    typography = theme.typography
    for token in (typography.display, typography.heading, typography.body, typography.caption, typography.monospace):
        assert token.size_pt > 0

    # A visible focus ring is always defined, independent of any one widget.
    assert theme.focus.ring_width_px > 0

    # The chart palette has enough distinct series colors for multi-curve plots.
    assert len(theme.chart.series) >= 2
    assert len({color.to_hex() for color in theme.chart.series}) == len(theme.chart.series)


@pytest.mark.parametrize("theme", [LIGHT_THEME, DARK_THEME])
def test_every_semantic_state_has_a_non_color_glyph_and_label(theme) -> None:
    """States must be distinguishable without relying on color alone."""

    for state in SemanticState:
        style = theme.state_style(state)
        assert style.glyph.strip(), f"{state} must have a non-empty glyph"
        assert style.label.strip(), f"{state} must have a non-empty label"

    glyphs = {theme.state_style(state).glyph for state in SemanticState}
    assert len(glyphs) == len(list(SemanticState)), "every state should have a distinct glyph"


def test_theme_for_mode_returns_matching_singleton() -> None:
    assert theme_for_mode(ThemeMode.LIGHT) is LIGHT_THEME
    assert theme_for_mode(ThemeMode.DARK) is DARK_THEME
    assert build_light_theme() == LIGHT_THEME
    assert build_dark_theme() == DARK_THEME


def test_rgb_color_validates_range_and_renders_hex() -> None:
    color = RgbColor(255, 0, 16)
    assert color.to_hex() == "#ff0010"
    with pytest.raises(ValueError):
        RgbColor(256, 0, 0)
    with pytest.raises(ValueError):
        RgbColor(-1, 0, 0)
