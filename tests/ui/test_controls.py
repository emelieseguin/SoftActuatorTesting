"""Tests for accessible reusable controls: names, focus policy, target size."""

from __future__ import annotations

from PySide6.QtCore import Qt

from soft_actuator_testing.ui.themes.tokens import DARK_THEME
from soft_actuator_testing.ui.widgets.controls import (
    MIN_TARGET_HEIGHT,
    MIN_TARGET_WIDTH,
    AccessibleButton,
)


def test_accessible_button_has_accessible_name_matching_text(qtbot) -> None:
    button = AccessibleButton("Start Run")
    qtbot.addWidget(button)
    assert button.accessibleName() == "Start Run"
    assert button.text() == "Start Run"


def test_accessible_button_accepts_explicit_description(qtbot) -> None:
    button = AccessibleButton("Stop", accessible_description="Immediately stop the active run")
    qtbot.addWidget(button)
    assert button.accessibleDescription() == "Immediately stop the active run"


def test_accessible_button_keeps_accessible_name_in_sync_with_set_text(qtbot) -> None:
    button = AccessibleButton("Connect")
    qtbot.addWidget(button)
    button.setText("Disconnect")
    assert button.accessibleName() == "Disconnect"


def test_accessible_button_enforces_minimum_target_size(qtbot) -> None:
    button = AccessibleButton("Go")
    qtbot.addWidget(button)
    assert button.minimumSize().width() >= MIN_TARGET_WIDTH
    assert button.minimumSize().height() >= MIN_TARGET_HEIGHT


def test_accessible_button_is_keyboard_focusable_and_paints_focus_ring(qtbot) -> None:
    button = AccessibleButton("Save")
    button.apply_theme(DARK_THEME)
    qtbot.addWidget(button)
    button.show()
    assert button.focusPolicy() == Qt.FocusPolicy.StrongFocus

    button.setFocus()
    qtbot.waitUntil(lambda: button.hasFocus())
    # Painting must not raise with focus active (the focus ring is drawn here).
    button.repaint()
    assert button.hasFocus()
