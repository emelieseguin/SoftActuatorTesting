"""Tests for the non-color-only semantic status indicator."""

from __future__ import annotations

from soft_actuator_testing.ui.themes.tokens import DARK_THEME, SemanticState
from soft_actuator_testing.ui.widgets.status import StatusIndicator


def test_status_indicator_accessible_name_includes_title_and_state_label(qtbot) -> None:
    indicator = StatusIndicator("Connection")
    indicator.apply_theme(DARK_THEME)
    qtbot.addWidget(indicator)
    indicator.set_state(SemanticState.SUCCESS)
    assert indicator.accessibleName() == "Connection status: Success"
    assert indicator.accessibleDescription() == "Connection status: Success"


def test_status_indicator_glyph_changes_with_state_not_only_color(qtbot) -> None:
    indicator = StatusIndicator("Run")
    indicator.apply_theme(DARK_THEME)
    qtbot.addWidget(indicator)

    seen_glyphs = set()
    for state in SemanticState:
        indicator.set_state(state)
        seen_glyphs.add(indicator._glyph_label.text())
        assert indicator._text_label.text() == f"Run: {DARK_THEME.state_style(state).label}"

    # Every semantic state renders a distinct glyph alongside color.
    assert len(seen_glyphs) == len(list(SemanticState))


def test_status_indicator_is_not_keyboard_focusable_by_default(qtbot) -> None:
    from PySide6.QtCore import Qt

    indicator = StatusIndicator("Camera")
    qtbot.addWidget(indicator)
    assert indicator.focusPolicy() == Qt.FocusPolicy.NoFocus
