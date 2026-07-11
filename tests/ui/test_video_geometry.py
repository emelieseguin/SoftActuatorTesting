"""Hardware-free Qt coverage for the manual video geometry editor.

Uses ``FakeVideoFrameSource``/``FakeFilePicker`` exclusively; no real files,
codecs, or native dialogs are touched.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from soft_actuator_testing.application.video_geometry_workflow import FakeVideoFrameSource, VideoGeometryWorkflow
from soft_actuator_testing.infrastructure.artifact_store import ArtifactFileStore
from soft_actuator_testing.ui.views.video_geometry import VideoGeometryView
from soft_actuator_testing.ui.widgets.file_picker import FakeFilePicker

WIDTH, HEIGHT = 192, 128


def _frame(color: int) -> np.ndarray:
    return np.full((HEIGHT, WIDTH, 3), color, dtype=np.uint8)


def _workflow_with_video() -> tuple[VideoGeometryWorkflow, FakeVideoFrameSource]:
    source = FakeVideoFrameSource()
    path = Path("demo.avi")
    source.register(path, (_frame(0), _frame(10), _frame(20)))
    workflow = VideoGeometryWorkflow(source)
    workflow.load_video(path)
    return workflow, source


def test_default_view_shows_no_video_loaded(qtbot) -> None:
    view = VideoGeometryView(VideoGeometryWorkflow(FakeVideoFrameSource()))
    qtbot.addWidget(view)
    assert view.video_summary.text() == "No video loaded."
    assert not view.save_button.isEnabled()


def test_choosing_a_video_through_the_file_picker_loads_it(qtbot) -> None:
    source = FakeVideoFrameSource()
    path = Path("chosen.avi")
    source.register(path, (_frame(5),))
    workflow = VideoGeometryWorkflow(source)
    picker = FakeFilePicker(queued_results=[path])
    view = VideoGeometryView(workflow, file_picker=picker)
    qtbot.addWidget(view)

    view.choose_video()

    assert "chosen.avi" in view.video_summary.text()
    assert workflow.metadata is not None


def test_choose_video_shows_error_without_fabricating_state_when_cancelled(qtbot) -> None:
    workflow = VideoGeometryWorkflow(FakeVideoFrameSource())
    picker = FakeFilePicker(queued_results=[None])
    view = VideoGeometryView(workflow, file_picker=picker)
    qtbot.addWidget(view)

    view.choose_video()

    assert workflow.metadata is None
    assert view.video_summary.text() == "No video loaded."


def test_frame_scrub_buttons_step_and_jump(qtbot) -> None:
    workflow, _ = _workflow_with_video()
    view = VideoGeometryView(workflow)
    qtbot.addWidget(view)

    view.next_frame_button.click()
    assert workflow.snapshot.frame_index == 1

    view.last_frame_button.click()
    assert workflow.snapshot.frame_index == 2

    view.prev_frame_button.click()
    assert workflow.snapshot.frame_index == 1

    view.first_frame_button.click()
    assert workflow.snapshot.frame_index == 0


def test_representative_frame_button_records_current_frame(qtbot) -> None:
    workflow, _ = _workflow_with_video()
    view = VideoGeometryView(workflow)
    qtbot.addWidget(view)

    view.next_frame_button.click()
    view.representative_button.click()

    assert workflow.snapshot.representative_frame_index == 1


def test_zoom_pan_fit_reset_buttons_change_the_view(qtbot) -> None:
    workflow, _ = _workflow_with_video()
    view = VideoGeometryView(workflow)
    qtbot.addWidget(view)

    view.zoom_in_button.click()
    assert workflow.snapshot.view.zoom > 1.0

    view.pan_right_button.click()
    assert workflow.snapshot.view.center_x > 0.5

    view.fit_view_button.click()
    assert workflow.snapshot.view.zoom == 1.0
    assert workflow.snapshot.view.center_x == 0.5

    view.zoom_in_button.click()
    view.reset_view_button.click()
    assert workflow.snapshot.view.zoom == 1.0


def test_numeric_base_tip_roi_fields_apply_on_editing_finished(qtbot) -> None:
    workflow, _ = _workflow_with_video()
    view = VideoGeometryView(workflow)
    qtbot.addWidget(view)

    view.base_x.setValue(20)
    view.base_y.setValue(30)
    view.base_x.editingFinished.emit()

    assert workflow.snapshot.base_point.x == 20
    assert workflow.snapshot.base_point.y == 30

    view.tip_x.setValue(100)
    view.tip_y.setValue(90)
    view.tip_y.editingFinished.emit()
    assert workflow.snapshot.tip_point.x == 100
    assert workflow.snapshot.tip_point.y == 90

    view.roi_x.setValue(10)
    view.roi_y.setValue(10)
    view.roi_w.setValue(50)
    view.roi_h.setValue(40)
    view.roi_h.editingFinished.emit()
    roi = workflow.snapshot.roi
    assert (roi.left, roi.top, roi.width, roi.height) == (10, 10, 50, 40)


def test_invalid_numeric_field_shows_error_without_state_change(qtbot) -> None:
    workflow, _ = _workflow_with_video()
    view = VideoGeometryView(workflow)
    qtbot.addWidget(view)

    view.base_x.setValue(WIDTH + 500)
    view.base_x.editingFinished.emit()

    assert workflow.snapshot.base_point is None
    assert "outside frame bounds" in view.status_label.text()


def test_keyboard_nudge_buttons_move_the_selected_points(qtbot) -> None:
    workflow, _ = _workflow_with_video()
    view = VideoGeometryView(workflow)
    qtbot.addWidget(view)

    workflow.set_base_point(50, 50)
    workflow.set_tip_point(60, 60)
    workflow.set_roi_xywh(10, 10, 20, 20)
    view._render()

    view.base_nudge_right.click()
    assert workflow.snapshot.base_point.x == 51

    view.tip_nudge_down.click()
    assert workflow.snapshot.tip_point.y == 61

    view.roi_nudge_left.click()
    assert workflow.snapshot.roi.left == 9


def test_clear_tip_button(qtbot) -> None:
    workflow, _ = _workflow_with_video()
    view = VideoGeometryView(workflow)
    qtbot.addWidget(view)
    workflow.set_tip_point(60, 60)
    view._render()

    view.clear_tip_button.click()

    assert workflow.snapshot.tip_point is None


def test_undo_redo_reset_buttons(qtbot) -> None:
    workflow, _ = _workflow_with_video()
    view = VideoGeometryView(workflow)
    qtbot.addWidget(view)

    view.base_x.setValue(20)
    view.base_y.setValue(20)
    view.base_y.editingFinished.emit()
    assert workflow.snapshot.base_point is not None

    view.undo_button.click()
    assert workflow.snapshot.base_point is None

    view.redo_button.click()
    assert workflow.snapshot.base_point is not None

    view.reset_button.click()
    assert workflow.snapshot.base_point is None


def test_overlay_visibility_checkbox_toggles_workflow_state(qtbot) -> None:
    workflow, _ = _workflow_with_video()
    view = VideoGeometryView(workflow)
    qtbot.addWidget(view)
    assert workflow.snapshot.overlay_visible is True

    view.overlay_checkbox.setChecked(False)

    assert workflow.snapshot.overlay_visible is False


def test_mouse_click_with_base_tool_sets_base_point(qtbot) -> None:
    workflow, _ = _workflow_with_video()
    view = VideoGeometryView(workflow)
    qtbot.addWidget(view)
    view.canvas.resize(WIDTH, HEIGHT)
    view.tool_selector.setCurrentText("Place base point")

    position = QPointF(40, 30)
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        position,
        position,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.eventFilter(view.canvas, release)

    assert workflow.snapshot.base_point is not None


def test_mouse_drag_with_roi_tool_normalizes_reversed_drag(qtbot) -> None:
    workflow, _ = _workflow_with_video()
    view = VideoGeometryView(workflow)
    qtbot.addWidget(view)
    view.canvas.resize(WIDTH, HEIGHT)
    view.tool_selector.setCurrentText("Draw ROI")

    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(100, 80),
        QPointF(100, 80),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.eventFilter(view.canvas, press)
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(20, 15),
        QPointF(20, 15),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.eventFilter(view.canvas, release)

    roi = workflow.snapshot.roi
    assert roi is not None
    assert roi.left < roi.right
    assert roi.top < roi.bottom


def test_save_versioned_requires_all_three_selections(qtbot, tmp_path) -> None:
    workflow, _ = _workflow_with_video()
    store = ArtifactFileStore(tmp_path)
    view = VideoGeometryView(workflow, artifact_store=store)
    qtbot.addWidget(view)

    assert not view.save_button.isEnabled()
    view.save_versioned()

    assert "required before saving" in view.status_label.text()
    assert list(tmp_path.rglob("*.json")) == []


def test_save_and_load_versioned_round_trip(qtbot, tmp_path) -> None:
    workflow, _ = _workflow_with_video()
    store = ArtifactFileStore(tmp_path)
    view = VideoGeometryView(workflow, artifact_store=store)
    qtbot.addWidget(view)
    workflow.set_base_point(20, 96)
    workflow.set_tip_point(140, 36)
    workflow.set_roi_xywh(10, 15, 170, 100)
    view._render()

    view.save_button.click()
    saved_id = view.artifact_id_field.text()
    assert saved_id

    other_workflow, other_source = _workflow_with_video()
    other_view = VideoGeometryView(other_workflow, artifact_store=store)
    qtbot.addWidget(other_view)
    other_view.artifact_id_field.setText(saved_id)

    other_view.load_button_versioned.click()

    assert other_workflow.snapshot.base_point.x == 20
    assert other_workflow.snapshot.base_point.y == 96


def test_save_without_a_configured_store_shows_error(qtbot) -> None:
    workflow, _ = _workflow_with_video()
    workflow.set_base_point(1, 1)
    workflow.set_tip_point(2, 2)
    workflow.set_roi_xywh(0, 0, 10, 10)
    view = VideoGeometryView(workflow)
    qtbot.addWidget(view)

    view.save_button.click()

    assert "No ArtifactFileStore is configured" in view.status_label.text()


def test_import_and_export_legacy_round_trip(qtbot, tmp_path) -> None:
    workflow, _ = _workflow_with_video()
    store = ArtifactFileStore(tmp_path)
    view = VideoGeometryView(workflow, artifact_store=store)
    qtbot.addWidget(view)
    workflow.set_base_point(20, 96)
    workflow.set_tip_point(140, 36)
    workflow.set_roi_xywh(10, 15, 170, 100)
    view._render()

    destination = tmp_path / "exported_config.json"
    picker = FakeFilePicker(queued_results=[destination])
    view.file_picker = picker
    view.export_legacy()
    assert destination.exists()

    reimport_workflow, _ = _workflow_with_video()
    reimport_view = VideoGeometryView(reimport_workflow, artifact_store=store, file_picker=FakeFilePicker(queued_results=[destination]))
    qtbot.addWidget(reimport_view)

    reimport_view.import_legacy()

    assert reimport_workflow.snapshot.base_point.x == 20
    assert reimport_workflow.snapshot.base_point.y == 96


def test_import_legacy_rejects_reverse_roi_fixture_without_state_change(qtbot, tmp_path) -> None:
    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "geometry"
    workflow, _ = _workflow_with_video()
    store = ArtifactFileStore(tmp_path)
    picker = FakeFilePicker(queued_results=[fixtures / "reverse-order-roi_config.json"])
    view = VideoGeometryView(workflow, artifact_store=store, file_picker=picker)
    qtbot.addWidget(view)

    view.import_legacy()

    assert workflow.snapshot.base_point is None
    assert "actuator_roi" in view.status_label.text()


def test_close_video_releases_state_and_updates_the_view(qtbot) -> None:
    workflow, _ = _workflow_with_video()
    view = VideoGeometryView(workflow)
    qtbot.addWidget(view)

    view.close_button.click()

    assert workflow.metadata is None
    assert view.video_summary.text() == "No video loaded."


def test_shutdown_hook_releases_the_open_video_handle(qtbot) -> None:
    workflow, _ = _workflow_with_video()
    handle = workflow._open_video  # noqa: SLF001 - verifying cleanup on teardown
    view = VideoGeometryView(workflow)
    qtbot.addWidget(view)

    view._shutdown()

    assert handle.closed is True
