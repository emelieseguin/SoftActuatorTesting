"""Manual video geometry workflow tests; every video source is hardware-free."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from soft_actuator_testing.application.video_geometry_workflow import (
    FakeVideoFrameSource,
    ViewTransform,
    VideoGeometryWorkflow,
    VideoProbeCancelled,
    frame_to_widget_point,
    widget_point_to_frame,
)
from soft_actuator_testing.domain.artifacts import ArtifactType
from soft_actuator_testing.domain.errors import DomainError, GeometryError
from soft_actuator_testing.domain.geometry import FrameSize, NormalizedRoi, PixelPoint
from soft_actuator_testing.infrastructure.artifact_store import ArtifactFileStore
from soft_actuator_testing.infrastructure.legacy_import import LegacyArtifactImporter

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
WIDTH, HEIGHT = 192, 128


def _frame(fill: int) -> np.ndarray:
    return np.full((HEIGHT, WIDTH, 3), fill, dtype=np.uint8)


class _FakeCancellationToken:
    def __init__(self, cancelled: bool = False) -> None:
        self._cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self._cancelled


def _workflow_with_video(path: Path = Path("synthetic.avi"), frame_count: int = 3) -> tuple[VideoGeometryWorkflow, FakeVideoFrameSource]:
    source = FakeVideoFrameSource()
    source.register(path, tuple(_frame(fill) for fill in range(frame_count)))
    workflow = VideoGeometryWorkflow(source)
    workflow.load_video(path)
    return workflow, source


# --- video load / metadata / frame zero -------------------------------------


def test_load_video_probes_metadata_and_exposes_frame_zero() -> None:
    workflow, source = _workflow_with_video()
    metadata = workflow.metadata

    assert metadata.frame_size == FrameSize(WIDTH, HEIGHT)
    assert metadata.frame_count == 3
    frame_zero = workflow.current_frame()

    assert frame_zero.shape == (HEIGHT, WIDTH, 3)
    assert source.opened_paths == [Path("synthetic.avi")]
    assert workflow.snapshot.frame_index == 0
    assert workflow.snapshot.representative_frame_index == 0


def test_loading_a_new_video_resets_the_prior_draft_geometry_without_fabricating_one() -> None:
    workflow, source = _workflow_with_video()
    workflow.set_base_point(10, 10)
    assert workflow.snapshot.base_point is not None

    source.register(Path("second.avi"), (_frame(1), _frame(2)))
    workflow.load_video(Path("second.avi"))

    snapshot = workflow.snapshot
    assert snapshot.base_point is None
    assert snapshot.tip_point is None
    assert snapshot.roi is None
    assert not snapshot.can_undo
    assert not snapshot.can_redo


def test_safe_metadata_probing_rejects_missing_registration_without_state_change() -> None:
    workflow, _ = _workflow_with_video()
    workflow.set_base_point(10, 10)
    before = workflow.snapshot

    with pytest.raises(GeometryError, match="no fake frames are registered"):
        workflow.load_video(Path("missing.avi"))

    assert workflow.snapshot == before


def test_operating_without_a_loaded_video_fails_closed() -> None:
    workflow = VideoGeometryWorkflow(FakeVideoFrameSource())
    with pytest.raises(GeometryError, match="no video is loaded"):
        workflow.current_frame()
    with pytest.raises(GeometryError, match="frame dimensions are not known"):
        workflow.set_base_point(0, 0)


# --- cancellation / error cleanup -------------------------------------------


def test_video_probe_cancellation_raises_and_leaves_no_video_loaded() -> None:
    source = FakeVideoFrameSource()
    path = Path("large.avi")
    source.register(path, (_frame(0),))
    source.cancel_probe_for.add(path)
    workflow = VideoGeometryWorkflow(source)

    with pytest.raises(VideoProbeCancelled):
        workflow.load_video(path, cancellation=_FakeCancellationToken(cancelled=True))

    assert workflow.metadata is None
    assert workflow.snapshot.video_path is None


def test_close_video_releases_the_underlying_handle() -> None:
    workflow, source = _workflow_with_video()
    handle = workflow._open_video  # noqa: SLF001 - verifying adapter cleanup contract
    workflow.close_video()

    assert handle.closed is True
    assert workflow.metadata is None
    with pytest.raises(GeometryError, match="no video is loaded"):
        workflow.current_frame()


def test_loading_a_replacement_video_closes_the_previous_handle() -> None:
    workflow, source = _workflow_with_video()
    first_handle = workflow._open_video  # noqa: SLF001

    source.register(Path("second.avi"), (_frame(9),))
    workflow.load_video(Path("second.avi"))

    assert first_handle.closed is True


# --- frame scrubbing ---------------------------------------------------------


def test_step_and_jump_frame_are_clamped_to_valid_bounds() -> None:
    workflow, _ = _workflow_with_video(frame_count=3)

    workflow.step_frame(1)
    assert workflow.snapshot.frame_index == 1
    workflow.step_frame(-10)
    assert workflow.snapshot.frame_index == 0
    workflow.jump_frame("last")
    assert workflow.snapshot.frame_index == 2
    workflow.step_frame(10)
    assert workflow.snapshot.frame_index == 2
    workflow.jump_frame("first")
    assert workflow.snapshot.frame_index == 0


def test_frame_out_of_range_is_rejected() -> None:
    workflow, _ = _workflow_with_video(frame_count=3)
    with pytest.raises(GeometryError, match="out of range"):
        workflow.frame(3)


def test_representative_frame_defaults_to_frame_zero_and_is_choosable() -> None:
    workflow, _ = _workflow_with_video(frame_count=3)
    assert workflow.snapshot.representative_frame_index == 0

    workflow.step_frame(2)
    workflow.set_representative_frame()
    assert workflow.snapshot.representative_frame_index == 2

    workflow.set_representative_frame(1)
    assert workflow.snapshot.representative_frame_index == 1

    with pytest.raises(GeometryError, match="out of range"):
        workflow.set_representative_frame(99)


# --- reverse / out-of-bounds geometry + drag-direction normalization --------


def test_roi_corner_drag_direction_is_normalized() -> None:
    workflow, _ = _workflow_with_video()
    roi = workflow.set_roi_corners(180, 115, 10, 15)

    assert (roi.left, roi.top, roi.right, roi.bottom) == (10, 15, 180, 115)


def test_roi_rejects_out_of_bounds_selection() -> None:
    workflow, _ = _workflow_with_video()
    with pytest.raises(GeometryError, match="outside"):
        workflow.set_roi_xywh(160, 100, 80, 60)


def test_base_and_tip_points_reject_out_of_bounds_selection() -> None:
    workflow, _ = _workflow_with_video()
    with pytest.raises(GeometryError, match="outside"):
        workflow.set_base_point(WIDTH, 0)
    with pytest.raises(GeometryError, match="outside"):
        workflow.set_tip_point(-1, 0)


# --- keyboard nudging alternatives -------------------------------------------


def test_nudge_base_tip_and_roi_move_by_the_requested_delta() -> None:
    workflow, _ = _workflow_with_video()
    workflow.set_base_point(20, 96)
    workflow.set_tip_point(140, 36)
    workflow.set_roi_xywh(10, 15, 170, 100)

    workflow.nudge_base(1, -1)
    workflow.nudge_tip(-1, 1)
    workflow.nudge_roi(2, 2)

    snapshot = workflow.snapshot
    assert (snapshot.base_point.x, snapshot.base_point.y) == (21, 95)
    assert (snapshot.tip_point.x, snapshot.tip_point.y) == (139, 37)
    assert (snapshot.roi.left, snapshot.roi.top) == (12, 17)


def test_nudge_clamps_at_frame_bounds_instead_of_raising() -> None:
    workflow, _ = _workflow_with_video()
    workflow.set_base_point(0, 0)
    workflow.nudge_base(-5, -5)
    assert (workflow.snapshot.base_point.x, workflow.snapshot.base_point.y) == (0, 0)

    workflow.set_roi_xywh(0, 0, 170, 100)
    workflow.nudge_roi(-50, 500)
    roi = workflow.snapshot.roi
    assert roi.left == 0
    assert roi.top == HEIGHT - roi.height


def test_nudge_requires_an_existing_selection() -> None:
    workflow, _ = _workflow_with_video()
    with pytest.raises(GeometryError, match="select a base point"):
        workflow.nudge_base(1, 0)


# --- undo / redo / reset ------------------------------------------------------


def test_undo_redo_restore_and_reapply_geometry_edits() -> None:
    workflow, _ = _workflow_with_video()
    workflow.set_base_point(20, 96)
    workflow.set_tip_point(140, 36)

    assert workflow.undo() is True
    assert workflow.snapshot.tip_point is None
    assert workflow.redo() is True
    assert workflow.snapshot.tip_point == PixelPoint(140, 36)

    assert workflow.undo() is True
    assert workflow.undo() is True
    assert workflow.undo() is False  # nothing left to undo


def test_new_edit_after_undo_clears_the_redo_stack() -> None:
    workflow, _ = _workflow_with_video()
    workflow.set_base_point(20, 96)
    workflow.undo()
    workflow.set_base_point(30, 90)

    assert workflow.redo() is False


def test_reset_clears_all_selections_but_keeps_frame_dimensions_and_is_undoable() -> None:
    workflow, _ = _workflow_with_video()
    workflow.set_base_point(20, 96)
    workflow.set_tip_point(140, 36)
    workflow.set_roi_xywh(10, 15, 170, 100)

    workflow.reset()
    snapshot = workflow.snapshot
    assert snapshot.base_point is None
    assert snapshot.tip_point is None
    assert snapshot.roi is None
    assert snapshot.frame_size == FrameSize(WIDTH, HEIGHT)
    assert snapshot.is_ready is False

    assert workflow.undo() is True
    assert workflow.snapshot.roi is not None


# --- overlay visibility --------------------------------------------------------


def test_overlay_visibility_toggle() -> None:
    workflow, _ = _workflow_with_video()
    assert workflow.snapshot.overlay_visible is True
    workflow.set_overlay_visible(False)
    assert workflow.snapshot.overlay_visible is False


# --- zoom / pan / fit / reset --------------------------------------------------


def test_zoom_in_out_and_fit_reset_view() -> None:
    workflow, _ = _workflow_with_video()
    workflow.zoom_in()
    zoomed_in = workflow.snapshot.view
    assert zoomed_in.zoom > 1.0

    workflow.zoom_out()
    assert workflow.snapshot.view.zoom == pytest.approx(1.0)

    workflow.zoom_in()
    workflow.pan(0.5, 0.0)
    assert workflow.snapshot.view.center_x > 0.5

    workflow.fit_view()
    assert workflow.snapshot.view == ViewTransform()


def test_zoom_does_not_exceed_configured_bounds() -> None:
    workflow, _ = _workflow_with_video()
    for _ in range(50):
        workflow.zoom_in()
    assert workflow.snapshot.view.zoom <= 8.0
    for _ in range(50):
        workflow.zoom_out()
    assert workflow.snapshot.view.zoom == pytest.approx(1.0)


def test_visible_rect_shrinks_when_zoomed_in() -> None:
    workflow, _ = _workflow_with_video()
    full_rect = workflow.visible_rect()
    workflow.zoom_in()
    zoomed_rect = workflow.visible_rect()

    assert zoomed_rect.width < full_rect.width
    assert zoomed_rect.height < full_rect.height


# --- coordinate transforms ------------------------------------------------------


def test_frame_to_widget_and_back_round_trips_without_zoom() -> None:
    frame_size = FrameSize(WIDTH, HEIGHT)
    visible = ViewTransform().visible_rect(frame_size)
    point = PixelPoint(40, 96)

    widget_x, widget_y = frame_to_widget_point(point, frame_size, visible, 384, 256)
    recovered = widget_point_to_frame(widget_x, widget_y, frame_size, visible, 384, 256)

    assert recovered.x == pytest.approx(point.x)
    assert recovered.y == pytest.approx(point.y)


def test_frame_to_widget_centers_letterboxing_for_mismatched_aspect_ratios() -> None:
    frame_size = FrameSize(WIDTH, HEIGHT)
    visible = ViewTransform().visible_rect(frame_size)
    # A tall widget produces vertical letterboxing (origin_y > 0).
    top_left_x, top_left_y = frame_to_widget_point(PixelPoint(0, 0), frame_size, visible, 200, 400)
    assert top_left_x == pytest.approx(0.0)
    assert top_left_y > 0.0


def test_coordinate_transform_round_trip_with_zoom_and_pan() -> None:
    frame_size = FrameSize(WIDTH, HEIGHT)
    view = ViewTransform(zoom=2.0, center_x=0.6, center_y=0.4)
    visible = view.visible_rect(frame_size)
    point = PixelPoint(120, 40)

    widget_x, widget_y = frame_to_widget_point(point, frame_size, visible, 320, 240)
    recovered = widget_point_to_frame(widget_x, widget_y, frame_size, visible, 320, 240)

    assert recovered.x == pytest.approx(point.x)
    assert recovered.y == pytest.approx(point.y)


# --- persistence: versioned save/load ------------------------------------------


def test_save_requires_base_tip_and_roi_and_never_fabricates_defaults(tmp_path: Path) -> None:
    workflow, _ = _workflow_with_video()
    store = ArtifactFileStore(tmp_path)

    with pytest.raises(GeometryError, match="required before saving"):
        workflow.save(store)

    workflow.set_base_point(20, 96)
    with pytest.raises(GeometryError, match="required before saving"):
        workflow.save(store)

    workflow.set_tip_point(140, 36)
    with pytest.raises(GeometryError, match="required before saving"):
        workflow.save(store)

    workflow.set_roi_xywh(10, 15, 170, 100)
    document = workflow.save(store)
    assert workflow.snapshot.artifact_id == document.metadata.identity.artifact_id


def test_versioned_save_then_load_round_trips_geometry(tmp_path: Path) -> None:
    workflow, _ = _workflow_with_video()
    store = ArtifactFileStore(tmp_path)
    workflow.set_base_point(20, 96)
    workflow.set_tip_point(140, 36)
    workflow.set_roi_xywh(10, 15, 170, 100)
    workflow.set_representative_frame(1)
    saved = workflow.save(store)

    reloaded = VideoGeometryWorkflow(FakeVideoFrameSource())
    reloaded.load(store, saved.metadata.identity.artifact_id)

    snapshot = reloaded.snapshot
    assert snapshot.base_point == PixelPoint(20, 96)
    assert snapshot.tip_point == PixelPoint(140, 36)
    assert snapshot.roi == NormalizedRoi(10, 15, 180, 115)
    assert snapshot.is_ready is True


def test_load_document_rejects_mismatched_frame_size_when_a_video_is_loaded() -> None:
    workflow, _ = _workflow_with_video()
    # Build a document for a different frame size directly.
    mismatched_source = FakeVideoFrameSource()
    mismatched_source.register(Path("odd.avi"), (np.full((64, 96, 3), 0, dtype=np.uint8),))
    mismatched = VideoGeometryWorkflow(mismatched_source)
    mismatched.load_video(Path("odd.avi"))
    mismatched.set_base_point(1, 1)
    mismatched.set_tip_point(2, 2)
    mismatched.set_roi_xywh(0, 0, 50, 40)
    document = mismatched.as_document()

    with pytest.raises(GeometryError, match="frame size does not match"):
        workflow.load_document(document)


# --- legacy import / export ---------------------------------------------------


def test_legacy_import_uses_the_loaded_videos_frame_size(tmp_path: Path) -> None:
    path = Path("synthetic.avi")
    source = FakeVideoFrameSource()
    source.register(path, tuple(_frame(fill) for fill in range(3)))
    workflow = VideoGeometryWorkflow(source)
    workflow.load_video(path)
    store = ArtifactFileStore(tmp_path)

    document = workflow.import_legacy(store, FIXTURES / "geometry" / "valid-synthetic-red-marker_config.json")

    snapshot = workflow.snapshot
    assert snapshot.base_point == PixelPoint(20, 96)
    assert snapshot.tip_point == PixelPoint(140, 36)
    assert document.metadata.identity.artifact_type is ArtifactType.GEOMETRY


def test_legacy_import_without_a_loaded_video_requires_explicit_frame_size(tmp_path: Path) -> None:
    workflow = VideoGeometryWorkflow(FakeVideoFrameSource())
    store = ArtifactFileStore(tmp_path)

    with pytest.raises(GeometryError, match="requires known frame dimensions"):
        workflow.import_legacy(store, FIXTURES / "geometry" / "valid-synthetic-red-marker_config.json")

    document = workflow.import_legacy(
        store, FIXTURES / "geometry" / "valid-synthetic-red-marker_config.json", frame_size=(WIDTH, HEIGHT)
    )
    assert workflow.snapshot.is_ready is True
    assert document is not None


def test_legacy_import_rejects_reverse_and_out_of_bounds_fixtures(tmp_path: Path) -> None:
    workflow, _ = _workflow_with_video()
    store = ArtifactFileStore(tmp_path)

    with pytest.raises(DomainError, match="actuator_roi"):
        workflow.import_legacy(store, FIXTURES / "geometry" / "reverse-order-roi_config.json")
    with pytest.raises(DomainError, match="actuator_roi"):
        workflow.import_legacy(store, FIXTURES / "geometry" / "out-of-bounds-roi_config.json")
    with pytest.raises(DomainError, match="angle_base_point"):
        workflow.import_legacy(store, FIXTURES / "geometry" / "missing-points_config.json")


def test_export_legacy_writes_the_narrow_historical_shape(tmp_path: Path) -> None:
    workflow, _ = _workflow_with_video()
    workflow.set_base_point(20, 96)
    workflow.set_tip_point(140, 36)
    workflow.set_roi_xywh(10, 15, 170, 100)
    store = ArtifactFileStore(tmp_path)
    destination = tmp_path / "exported_config.json"

    workflow.export_legacy(store, destination)

    imported_back = LegacyArtifactImporter().import_file(destination, ArtifactType.GEOMETRY, frame_size=(WIDTH, HEIGHT))
    assert imported_back.payload["base_point"] == {"x": 20.0, "y": 96.0}


def test_export_legacy_also_requires_a_complete_selection(tmp_path: Path) -> None:
    workflow, _ = _workflow_with_video()
    store = ArtifactFileStore(tmp_path)
    with pytest.raises(GeometryError, match="required before saving"):
        workflow.export_legacy(store, tmp_path / "config.json")
