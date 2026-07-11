"""Tests for the video/image canvas wrapper: keyboard alternatives and overlays."""

from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from soft_actuator_testing.ui.widgets.video_canvas import VideoCanvas


def _frame(width: int = 16, height: int = 12, fill: int = 128) -> np.ndarray:
    return np.full((height, width, 3), fill, dtype=np.uint8)


def test_no_frame_has_accessible_description(qtbot) -> None:
    canvas = VideoCanvas()
    qtbot.addWidget(canvas)
    assert "no frame loaded" in canvas.accessibleDescription()


def test_set_frame_updates_accessible_description_with_position_and_detail(qtbot) -> None:
    canvas = VideoCanvas(accessible_title="Camera preview")
    qtbot.addWidget(canvas)
    canvas.set_frame(_frame(), frame_index=2, frame_count=10, description="marker detected")
    description = canvas.accessibleDescription()
    assert "frame 3 of 10" in description
    assert "marker detected" in description
    assert canvas.accessibleName() == "Camera preview"


def test_set_frame_rejects_non_rgb_arrays(qtbot) -> None:
    canvas = VideoCanvas()
    qtbot.addWidget(canvas)
    with pytest.raises(ValueError):
        canvas.set_frame(np.zeros((4, 4), dtype=np.uint8))


def test_keyboard_left_right_emit_frame_step_requested(qtbot) -> None:
    canvas = VideoCanvas()
    qtbot.addWidget(canvas)
    canvas.show()
    canvas.setFocus()
    qtbot.waitUntil(lambda: canvas.hasFocus())

    with qtbot.waitSignal(canvas.frame_step_requested, timeout=1000) as blocker:
        QTest.keyClick(canvas, Qt.Key.Key_Right)
    assert blocker.args == [1]

    with qtbot.waitSignal(canvas.frame_step_requested, timeout=1000) as blocker:
        QTest.keyClick(canvas, Qt.Key.Key_Left)
    assert blocker.args == [-1]


def test_shift_arrow_steps_by_ten(qtbot) -> None:
    canvas = VideoCanvas()
    qtbot.addWidget(canvas)
    canvas.show()
    canvas.setFocus()
    qtbot.waitUntil(lambda: canvas.hasFocus())

    with qtbot.waitSignal(canvas.frame_step_requested, timeout=1000) as blocker:
        QTest.keyClick(canvas, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert blocker.args == [10]


def test_home_and_end_emit_jump_requested(qtbot) -> None:
    canvas = VideoCanvas()
    qtbot.addWidget(canvas)
    canvas.show()
    canvas.setFocus()
    qtbot.waitUntil(lambda: canvas.hasFocus())

    with qtbot.waitSignal(canvas.jump_requested, timeout=1000) as blocker:
        QTest.keyClick(canvas, Qt.Key.Key_Home)
    assert blocker.args == ["first"]

    with qtbot.waitSignal(canvas.jump_requested, timeout=1000) as blocker:
        QTest.keyClick(canvas, Qt.Key.Key_End)
    assert blocker.args == ["last"]


def test_register_overlay_is_invoked_on_paint_and_can_be_unsubscribed(qtbot) -> None:
    canvas = VideoCanvas()
    qtbot.addWidget(canvas)
    canvas.set_frame(_frame())
    canvas.resize(64, 48)
    canvas.show()

    calls = []
    unsubscribe = canvas.register_overlay(lambda painter, rect: calls.append(rect))
    canvas.repaint()
    qtbot.waitUntil(lambda: len(calls) >= 1)

    unsubscribe()
    calls.clear()
    canvas.repaint()
    qtbot.wait(50)
    assert calls == []
