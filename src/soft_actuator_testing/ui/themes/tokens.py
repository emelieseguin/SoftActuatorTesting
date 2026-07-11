"""Qt-free theme tokens: color, spacing, typography, focus, and chart data.

Per ADR 0001, theme tokens are plain Python data (dataclasses/enums), not a
second markup or styling language. This module imports nothing from Qt so the
tokens are importable and unit-testable without a display; :mod:`soft_actuator_testing.ui.themes.qt_bridge`
is the only place that converts these tokens into Qt objects (``QPalette``,
``QFont``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ThemeMode(str, Enum):
    LIGHT = "light"
    DARK = "dark"


class SemanticState(str, Enum):
    """Non-color-only semantic states shared by controls and indicators."""

    NEUTRAL = "neutral"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class RgbColor:
    """A plain sRGB color; avoids depending on any Qt color type."""

    red: int
    green: int
    blue: int
    alpha: int = 255

    def __post_init__(self) -> None:
        for name, value in (
            ("red", self.red),
            ("green", self.green),
            ("blue", self.blue),
            ("alpha", self.alpha),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 255:
                raise ValueError(f"{name} must be an int in [0, 255], got {value!r}")

    def to_hex(self) -> str:
        return f"#{self.red:02x}{self.green:02x}{self.blue:02x}"


@dataclass(frozen=True)
class ColorPalette:
    """Semantic color roles for one theme mode."""

    background: RgbColor
    surface: RgbColor
    surface_alt: RgbColor
    border: RgbColor
    text_primary: RgbColor
    text_secondary: RgbColor
    text_disabled: RgbColor
    accent: RgbColor
    accent_text: RgbColor


@dataclass(frozen=True)
class StateStyle:
    """A semantic state's color plus a non-color glyph and label.

    Screens must not rely on color alone (per ADR 0005's non-color-only state
    rule); every state carries a short text glyph and label alongside color.
    """

    color: RgbColor
    glyph: str
    label: str


@dataclass(frozen=True)
class SpacingScale:
    """4px-grid spacing tokens used for margins, padding, and gaps."""

    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24
    xxl: int = 32


@dataclass(frozen=True)
class FontToken:
    family: str
    size_pt: int
    weight: int = 400
    italic: bool = False

    def __post_init__(self) -> None:
        if self.size_pt <= 0:
            raise ValueError("size_pt must be positive")
        if not 1 <= self.weight <= 1000:
            raise ValueError("weight must be a valid font weight in [1, 1000]")


@dataclass(frozen=True)
class TypographyScale:
    """Named font tokens; monospace is used for dense numeric readouts."""

    display: FontToken
    heading: FontToken
    body: FontToken
    caption: FontToken
    monospace: FontToken


@dataclass(frozen=True)
class FocusStyle:
    """A visible focus indicator independent of any single widget's palette."""

    ring_color: RgbColor
    ring_width_px: int = 2
    ring_offset_px: int = 2

    def __post_init__(self) -> None:
        if self.ring_width_px <= 0:
            raise ValueError("ring_width_px must be positive")
        if self.ring_offset_px < 0:
            raise ValueError("ring_offset_px cannot be negative")


@dataclass(frozen=True)
class ChartPalette:
    """Colors for the project-owned PyQtGraph plot wrapper."""

    background: RgbColor
    grid: RgbColor
    axis: RgbColor
    series: tuple[RgbColor, ...]

    def __post_init__(self) -> None:
        if not self.series:
            raise ValueError("series must contain at least one color")

    def series_color(self, index: int) -> RgbColor:
        return self.series[index % len(self.series)]


@dataclass(frozen=True)
class Theme:
    """A complete named theme: one mode's full token set."""

    name: str
    mode: ThemeMode
    colors: ColorPalette
    spacing: SpacingScale
    typography: TypographyScale
    focus: FocusStyle
    chart: ChartPalette
    states: dict[SemanticState, StateStyle] = field(default_factory=dict)

    def state_style(self, state: SemanticState) -> StateStyle:
        try:
            return self.states[state]
        except KeyError as error:
            raise KeyError(f"no state style registered for {state.value}") from error


def _typography(body_size: int = 10) -> TypographyScale:
    return TypographyScale(
        display=FontToken("Noto Sans", body_size + 12, weight=600),
        heading=FontToken("Noto Sans", body_size + 4, weight=600),
        body=FontToken("Noto Sans", body_size, weight=400),
        caption=FontToken("Noto Sans", body_size - 1, weight=400),
        monospace=FontToken("Noto Sans Mono", body_size, weight=400),
    )


def build_light_theme() -> Theme:
    colors = ColorPalette(
        background=RgbColor(0xF7, 0xF8, 0xFA),
        surface=RgbColor(0xFF, 0xFF, 0xFF),
        surface_alt=RgbColor(0xED, 0xEF, 0xF2),
        border=RgbColor(0xC7, 0xCC, 0xD1),
        text_primary=RgbColor(0x1A, 0x1D, 0x21),
        text_secondary=RgbColor(0x4B, 0x50, 0x58),
        text_disabled=RgbColor(0x9A, 0x9F, 0xA6),
        accent=RgbColor(0x1F, 0x6F, 0xEB),
        accent_text=RgbColor(0xFF, 0xFF, 0xFF),
    )
    return Theme(
        name="Experiment Studio Light",
        mode=ThemeMode.LIGHT,
        colors=colors,
        spacing=SpacingScale(),
        typography=_typography(),
        focus=FocusStyle(ring_color=RgbColor(0x1F, 0x6F, 0xEB)),
        chart=ChartPalette(
            background=colors.surface,
            grid=RgbColor(0xD8, 0xDC, 0xE1),
            axis=colors.text_secondary,
            series=(
                RgbColor(0x1F, 0x6F, 0xEB),
                RgbColor(0xC2, 0x41, 0x0C),
                RgbColor(0x14, 0x84, 0x4C),
                RgbColor(0x7C, 0x3A, 0xED),
            ),
        ),
        states={
            SemanticState.NEUTRAL: StateStyle(colors.text_secondary, "•", "Neutral"),
            SemanticState.INFO: StateStyle(RgbColor(0x1F, 0x6F, 0xEB), "i", "Info"),
            SemanticState.SUCCESS: StateStyle(RgbColor(0x14, 0x84, 0x4C), "✓", "Success"),
            SemanticState.WARNING: StateStyle(RgbColor(0xB0, 0x7B, 0x00), "!", "Warning"),
            SemanticState.ERROR: StateStyle(RgbColor(0xC2, 0x18, 0x1E), "✕", "Error"),
        },
    )


def build_dark_theme() -> Theme:
    colors = ColorPalette(
        background=RgbColor(0x14, 0x16, 0x19),
        surface=RgbColor(0x1D, 0x20, 0x24),
        surface_alt=RgbColor(0x27, 0x2B, 0x30),
        border=RgbColor(0x3A, 0x3F, 0x45),
        text_primary=RgbColor(0xF0, 0xF2, 0xF5),
        text_secondary=RgbColor(0xAE, 0xB4, 0xBC),
        text_disabled=RgbColor(0x6B, 0x71, 0x79),
        accent=RgbColor(0x5B, 0x9D, 0xF9),
        accent_text=RgbColor(0x0A, 0x14, 0x24),
    )
    return Theme(
        name="Instrument Console Dark",
        mode=ThemeMode.DARK,
        colors=colors,
        spacing=SpacingScale(xs=4, sm=6, md=10, lg=14, xl=20, xxl=28),
        typography=_typography(body_size=9),
        focus=FocusStyle(ring_color=RgbColor(0x5B, 0x9D, 0xF9)),
        chart=ChartPalette(
            background=colors.surface,
            grid=RgbColor(0x33, 0x38, 0x3E),
            axis=colors.text_secondary,
            series=(
                RgbColor(0x5B, 0x9D, 0xF9),
                RgbColor(0xF2, 0x8B, 0x4E),
                RgbColor(0x5D, 0xD3, 0x9E),
                RgbColor(0xC0, 0x9C, 0xF5),
            ),
        ),
        states={
            SemanticState.NEUTRAL: StateStyle(colors.text_secondary, "•", "Neutral"),
            SemanticState.INFO: StateStyle(RgbColor(0x5B, 0x9D, 0xF9), "i", "Info"),
            SemanticState.SUCCESS: StateStyle(RgbColor(0x5D, 0xD3, 0x9E), "✓", "Success"),
            SemanticState.WARNING: StateStyle(RgbColor(0xF2, 0xC1, 0x4E), "!", "Warning"),
            SemanticState.ERROR: StateStyle(RgbColor(0xF2, 0x6D, 0x6D), "✕", "Error"),
        },
    )


LIGHT_THEME: Theme = build_light_theme()
DARK_THEME: Theme = build_dark_theme()

THEMES_BY_MODE: dict[ThemeMode, Theme] = {
    ThemeMode.LIGHT: LIGHT_THEME,
    ThemeMode.DARK: DARK_THEME,
}


def theme_for_mode(mode: ThemeMode) -> Theme:
    return THEMES_BY_MODE[mode]
