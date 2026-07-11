"""Composable manual video geometry editor; a bare, embeddable widget.

Mirrors the ``HomeWorkspaceView``/``SerialControlPanel`` pattern: a plain
``QWidget`` that owns a Qt-free :class:`VideoGeometryWorkflow` service and
renders its snapshot, so it can be embedded into any host page (here,
``VideoGeometryMarkerSetupPage``) without depending on the shared demo
presenter/command seam.

Automatic marker detection is explicitly out of scope for this widget; it
only supports manual base/tip/ROI authoring.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from soft_actuator_testing.application.services import ArtifactStore
from soft_actuator_testing.application.video_geometry_workflow import (
    VideoGeometryWorkflow,
    frame_to_widget_point,
    widget_point_to_frame,
)
from soft_actuator_testing.domain.geometry import FrameSize, PixelPoint
from soft_actuator_testing.infrastructure.video_file_reader import OpenCvVideoFileReader
from soft_actuator_testing.ui.widgets import AccessibleButton, VideoCanvas
from soft_actuator_testing.ui.widgets.file_picker import FileFilter, FilePicker, QtFilePicker

_TOOL_NONE = "No placement tool"
_TOOL_BASE = "Place base point"
_TOOL_TIP = "Place tip point"
_TOOL_ROI = "Draw ROI"
_NUDGE_STEP = 1.0


def _round_bounds(value: float, upper: int) -> int:
    return max(0, min(int(round(value)), upper))


class VideoGeometryView(QWidget):
    """Render/edit manual video geometry from an injected, Qt-free workflow.

    The widget never imports ``cv2`` directly; ``OpenCvVideoFileReader`` is
    the default *adapter* injected at construction time only, and any other
    :class:`VideoFrameSource` implementation can be substituted by passing a
    different ``workflow``.
    """

    def __init__(
        self,
        workflow: VideoGeometryWorkflow | None = None,
        *,
        file_picker: FilePicker | None = None,
        artifact_store: ArtifactStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("video-geometry-view")
        self.setAccessibleName("Manual video geometry editor")
        self.workflow = workflow or VideoGeometryWorkflow(OpenCvVideoFileReader())
        self.file_picker = file_picker or QtFilePicker(self)
        self.artifact_store = artifact_store
        self._rendering = False
        self._roi_drag_start: tuple[float, float] | None = None

        layout = QVBoxLayout(self)

        # --- video load / scrub -------------------------------------------------
        video_row = QHBoxLayout()
        self.load_button = AccessibleButton("Choose video…", parent=self)
        self.load_button.setObjectName("geometry-load-video")
        self.load_button.clicked.connect(self.choose_video)
        self.close_button = AccessibleButton("Close video", parent=self)
        self.close_button.setObjectName("geometry-close-video")
        self.close_button.clicked.connect(self.close_video)
        video_row.addWidget(self.load_button)
        video_row.addWidget(self.close_button)
        layout.addLayout(video_row)

        self.video_summary = QLabel(self)
        self.video_summary.setObjectName("geometry-video-summary")
        self.video_summary.setWordWrap(True)
        layout.addWidget(self.video_summary)

        self.canvas = VideoCanvas(accessible_title="Geometry authoring preview", parent=self)
        self.canvas.setObjectName("geometry-canvas")
        self.canvas.frame_step_requested.connect(self.step_frame)
        self.canvas.jump_requested.connect(self.jump_frame)
        self.canvas.installEventFilter(self)
        self.canvas.setMouseTracking(True)
        self.canvas.register_overlay(self._paint_overlay)
        layout.addWidget(self.canvas)

        scrub_row = QHBoxLayout()
        self.first_frame_button = AccessibleButton("First frame", parent=self)
        self.first_frame_button.clicked.connect(lambda: self.jump_frame("first"))
        self.prev_frame_button = AccessibleButton("Previous frame", parent=self)
        self.prev_frame_button.clicked.connect(lambda: self.step_frame(-1))
        self.next_frame_button = AccessibleButton("Next frame", parent=self)
        self.next_frame_button.clicked.connect(lambda: self.step_frame(1))
        self.last_frame_button = AccessibleButton("Last frame", parent=self)
        self.last_frame_button.clicked.connect(lambda: self.jump_frame("last"))
        self.representative_button = AccessibleButton("Use as representative frame", parent=self)
        self.representative_button.setObjectName("geometry-set-representative")
        self.representative_button.clicked.connect(self.set_representative_frame)
        for button in (
            self.first_frame_button,
            self.prev_frame_button,
            self.next_frame_button,
            self.last_frame_button,
            self.representative_button,
        ):
            scrub_row.addWidget(button)
        layout.addLayout(scrub_row)

        # --- zoom / pan / fit ----------------------------------------------------
        view_row = QHBoxLayout()
        self.zoom_in_button = AccessibleButton("Zoom in", parent=self)
        self.zoom_in_button.clicked.connect(self.zoom_in)
        self.zoom_out_button = AccessibleButton("Zoom out", parent=self)
        self.zoom_out_button.clicked.connect(self.zoom_out)
        self.pan_left_button = AccessibleButton("Pan left", parent=self)
        self.pan_left_button.clicked.connect(lambda: self.pan(-0.1, 0.0))
        self.pan_right_button = AccessibleButton("Pan right", parent=self)
        self.pan_right_button.clicked.connect(lambda: self.pan(0.1, 0.0))
        self.pan_up_button = AccessibleButton("Pan up", parent=self)
        self.pan_up_button.clicked.connect(lambda: self.pan(0.0, -0.1))
        self.pan_down_button = AccessibleButton("Pan down", parent=self)
        self.pan_down_button.clicked.connect(lambda: self.pan(0.0, 0.1))
        self.fit_view_button = AccessibleButton("Fit to frame", parent=self)
        self.fit_view_button.clicked.connect(self.fit_view)
        self.reset_view_button = AccessibleButton("Reset view", parent=self)
        self.reset_view_button.clicked.connect(self.reset_view)
        for button in (
            self.zoom_in_button,
            self.zoom_out_button,
            self.pan_left_button,
            self.pan_right_button,
            self.pan_up_button,
            self.pan_down_button,
            self.fit_view_button,
            self.reset_view_button,
        ):
            view_row.addWidget(button)
        layout.addLayout(view_row)

        # --- placement tool + overlay toggle --------------------------------------
        tool_row = QHBoxLayout()
        self.tool_selector = QComboBox(self)
        self.tool_selector.setObjectName("geometry-tool")
        self.tool_selector.setAccessibleName("Geometry placement tool")
        self.tool_selector.addItems([_TOOL_NONE, _TOOL_BASE, _TOOL_TIP, _TOOL_ROI])
        self.overlay_checkbox = QCheckBox("Show geometry overlay", parent=self)
        self.overlay_checkbox.setObjectName("geometry-overlay-visible")
        self.overlay_checkbox.setChecked(True)
        self.overlay_checkbox.toggled.connect(self.set_overlay_visible)
        tool_row.addWidget(self.tool_selector)
        tool_row.addWidget(self.overlay_checkbox)
        layout.addLayout(tool_row)

        # --- numeric geometry editing ---------------------------------------------
        numeric_form = QFormLayout()
        self.base_x = self._make_spinbox("geometry-base-x")
        self.base_y = self._make_spinbox("geometry-base-y")
        base_row = QHBoxLayout()
        base_row.addWidget(self.base_x)
        base_row.addWidget(self.base_y)
        self.base_nudge_up = AccessibleButton("Base ↑", parent=self)
        self.base_nudge_down = AccessibleButton("Base ↓", parent=self)
        self.base_nudge_left = AccessibleButton("Base ←", parent=self)
        self.base_nudge_right = AccessibleButton("Base →", parent=self)
        for button in (self.base_nudge_left, self.base_nudge_up, self.base_nudge_down, self.base_nudge_right):
            base_row.addWidget(button)
        self.base_nudge_up.clicked.connect(lambda: self._call(lambda: self.workflow.nudge_base(0, -_NUDGE_STEP)))
        self.base_nudge_down.clicked.connect(lambda: self._call(lambda: self.workflow.nudge_base(0, _NUDGE_STEP)))
        self.base_nudge_left.clicked.connect(lambda: self._call(lambda: self.workflow.nudge_base(-_NUDGE_STEP, 0)))
        self.base_nudge_right.clicked.connect(lambda: self._call(lambda: self.workflow.nudge_base(_NUDGE_STEP, 0)))
        numeric_form.addRow("Base point (x, y)", base_row)

        self.tip_x = self._make_spinbox("geometry-tip-x")
        self.tip_y = self._make_spinbox("geometry-tip-y")
        tip_row = QHBoxLayout()
        tip_row.addWidget(self.tip_x)
        tip_row.addWidget(self.tip_y)
        self.tip_nudge_up = AccessibleButton("Tip ↑", parent=self)
        self.tip_nudge_down = AccessibleButton("Tip ↓", parent=self)
        self.tip_nudge_left = AccessibleButton("Tip ←", parent=self)
        self.tip_nudge_right = AccessibleButton("Tip →", parent=self)
        for button in (self.tip_nudge_left, self.tip_nudge_up, self.tip_nudge_down, self.tip_nudge_right):
            tip_row.addWidget(button)
        self.tip_nudge_up.clicked.connect(lambda: self._call(lambda: self.workflow.nudge_tip(0, -_NUDGE_STEP)))
        self.tip_nudge_down.clicked.connect(lambda: self._call(lambda: self.workflow.nudge_tip(0, _NUDGE_STEP)))
        self.tip_nudge_left.clicked.connect(lambda: self._call(lambda: self.workflow.nudge_tip(-_NUDGE_STEP, 0)))
        self.tip_nudge_right.clicked.connect(lambda: self._call(lambda: self.workflow.nudge_tip(_NUDGE_STEP, 0)))
        self.clear_tip_button = AccessibleButton("Clear tip point", parent=self)
        self.clear_tip_button.clicked.connect(lambda: self._call(self.workflow.clear_tip))
        tip_row.addWidget(self.clear_tip_button)
        numeric_form.addRow("Tip point (x, y)", tip_row)

        self.roi_x = self._make_spinbox("geometry-roi-x")
        self.roi_y = self._make_spinbox("geometry-roi-y")
        self.roi_w = self._make_spinbox("geometry-roi-w", minimum=1.0)
        self.roi_h = self._make_spinbox("geometry-roi-h", minimum=1.0)
        roi_row = QHBoxLayout()
        for spin in (self.roi_x, self.roi_y, self.roi_w, self.roi_h):
            roi_row.addWidget(spin)
        self.roi_nudge_up = AccessibleButton("ROI ↑", parent=self)
        self.roi_nudge_down = AccessibleButton("ROI ↓", parent=self)
        self.roi_nudge_left = AccessibleButton("ROI ←", parent=self)
        self.roi_nudge_right = AccessibleButton("ROI →", parent=self)
        for button in (self.roi_nudge_left, self.roi_nudge_up, self.roi_nudge_down, self.roi_nudge_right):
            roi_row.addWidget(button)
        self.roi_nudge_up.clicked.connect(lambda: self._call(lambda: self.workflow.nudge_roi(0, -_NUDGE_STEP)))
        self.roi_nudge_down.clicked.connect(lambda: self._call(lambda: self.workflow.nudge_roi(0, _NUDGE_STEP)))
        self.roi_nudge_left.clicked.connect(lambda: self._call(lambda: self.workflow.nudge_roi(-_NUDGE_STEP, 0)))
        self.roi_nudge_right.clicked.connect(lambda: self._call(lambda: self.workflow.nudge_roi(_NUDGE_STEP, 0)))
        numeric_form.addRow("ROI (x, y, w, h)", roi_row)
        layout.addLayout(numeric_form)

        for spin, callback in (
            (self.base_x, self._apply_base_from_fields),
            (self.base_y, self._apply_base_from_fields),
            (self.tip_x, self._apply_tip_from_fields),
            (self.tip_y, self._apply_tip_from_fields),
            (self.roi_x, self._apply_roi_from_fields),
            (self.roi_y, self._apply_roi_from_fields),
            (self.roi_w, self._apply_roi_from_fields),
            (self.roi_h, self._apply_roi_from_fields),
        ):
            spin.editingFinished.connect(callback)

        # --- undo / redo / reset ---------------------------------------------------
        history_row = QHBoxLayout()
        self.undo_button = AccessibleButton("Undo", parent=self)
        self.undo_button.setObjectName("geometry-undo")
        self.undo_button.clicked.connect(self.undo)
        self.redo_button = AccessibleButton("Redo", parent=self)
        self.redo_button.setObjectName("geometry-redo")
        self.redo_button.clicked.connect(self.redo)
        self.reset_button = AccessibleButton("Reset selections", parent=self)
        self.reset_button.setObjectName("geometry-reset")
        self.reset_button.clicked.connect(self.reset_selections)
        for button in (self.undo_button, self.redo_button, self.reset_button):
            history_row.addWidget(button)
        layout.addLayout(history_row)

        # --- persistence -------------------------------------------------------------
        persistence_form = QFormLayout()
        self.artifact_id_field = QLineEdit(self)
        self.artifact_id_field.setObjectName("geometry-artifact-id")
        self.save_button = AccessibleButton("Save versioned", parent=self)
        self.save_button.setObjectName("geometry-save")
        self.save_button.clicked.connect(self.save_versioned)
        self.load_button_versioned = AccessibleButton("Load versioned", parent=self)
        self.load_button_versioned.setObjectName("geometry-load-versioned")
        self.load_button_versioned.clicked.connect(self.load_versioned)
        self.import_button = AccessibleButton("Import legacy JSON", parent=self)
        self.import_button.setObjectName("geometry-import-legacy")
        self.import_button.clicked.connect(self.import_legacy)
        self.export_button = AccessibleButton("Export legacy JSON", parent=self)
        self.export_button.setObjectName("geometry-export-legacy")
        self.export_button.clicked.connect(self.export_legacy)
        store_row = QHBoxLayout()
        for button in (self.save_button, self.load_button_versioned, self.import_button, self.export_button):
            store_row.addWidget(button)
        persistence_form.addRow("Artifact ID", self.artifact_id_field)
        persistence_form.addRow(store_row)
        layout.addLayout(persistence_form)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("geometry-status")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Geometry workflow status")
        layout.addWidget(self.status_label)

        self.destroyed.connect(self._shutdown)
        self._render()

    @staticmethod
    def _make_spinbox(object_name: str, *, minimum: float = 0.0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setObjectName(object_name)
        spin.setDecimals(2)
        spin.setRange(minimum, 1_000_000.0)
        spin.setKeyboardTracking(False)
        return spin

    # --- video lifecycle -----------------------------------------------------------
    def choose_video(self) -> None:
        source = self.file_picker.get_open_file(
            caption="Choose a prerecorded video",
            filters=(FileFilter("Video files", ("*.avi", "*.mp4", "*.mov", "*.mkv")),),
        )
        if source is not None:
            self._call(lambda: self.workflow.load_video(Path(source)))

    def close_video(self) -> None:
        self._call(self.workflow.close_video)

    def step_frame(self, delta: int) -> None:
        self._call(lambda: self.workflow.step_frame(delta))

    def jump_frame(self, where: str) -> None:
        self._call(lambda: self.workflow.jump_frame(where))

    def set_representative_frame(self) -> None:
        self._call(lambda: self.workflow.set_representative_frame())

    # --- view -----------------------------------------------------------------------
    def zoom_in(self) -> None:
        self._call(self.workflow.zoom_in)

    def zoom_out(self) -> None:
        self._call(self.workflow.zoom_out)

    def pan(self, dx: float, dy: float) -> None:
        self._call(lambda: self.workflow.pan(dx, dy))

    def fit_view(self) -> None:
        self._call(self.workflow.fit_view)

    def reset_view(self) -> None:
        self._call(self.workflow.reset_view)

    def set_overlay_visible(self, visible: bool) -> None:
        if self._rendering:
            return
        self.workflow.set_overlay_visible(visible)
        self.canvas.update()

    # --- numeric field application ---------------------------------------------------
    def _apply_base_from_fields(self) -> None:
        if self._rendering:
            return
        self._call(lambda: self.workflow.set_base_point(self.base_x.value(), self.base_y.value()))

    def _apply_tip_from_fields(self) -> None:
        if self._rendering:
            return
        self._call(lambda: self.workflow.set_tip_point(self.tip_x.value(), self.tip_y.value()))

    def _apply_roi_from_fields(self) -> None:
        if self._rendering:
            return
        self._call(
            lambda: self.workflow.set_roi_xywh(
                self.roi_x.value(), self.roi_y.value(), self.roi_w.value(), self.roi_h.value()
            )
        )

    # --- undo / redo / reset -----------------------------------------------------------
    def undo(self) -> None:
        self.workflow.undo()
        self._render()

    def redo(self) -> None:
        self.workflow.redo()
        self._render()

    def reset_selections(self) -> None:
        self._call(self.workflow.reset)

    # --- persistence --------------------------------------------------------------------
    def save_versioned(self) -> None:
        if self.artifact_store is None:
            self._show_error("No ArtifactFileStore is configured; choose a workspace-backed store before saving.")
            return
        self._call(lambda: self.workflow.save(self.artifact_store))

    def load_versioned(self) -> None:
        if self.artifact_store is None:
            self._show_error("No ArtifactFileStore is configured; choose a workspace-backed store before loading.")
            return
        artifact_id = self.artifact_id_field.text().strip()
        self._call(lambda: self.workflow.load(self.artifact_store, artifact_id))

    def import_legacy(self) -> None:
        if self.artifact_store is None:
            self._show_error("No ArtifactFileStore is configured; choose a workspace-backed store before importing.")
            return
        source = self.file_picker.get_open_file(
            caption="Import legacy geometry JSON",
            filters=(FileFilter("JSON files", ("*.json",)),),
        )
        if source is not None:
            self._call(lambda: self.workflow.import_legacy(self.artifact_store, Path(source)))

    def export_legacy(self) -> None:
        if self.artifact_store is None:
            self._show_error("No ArtifactFileStore is configured; choose a workspace-backed store before exporting.")
            return
        destination = self.file_picker.get_save_file(
            caption="Export legacy geometry JSON",
            filters=(FileFilter("JSON files", ("*.json",)),),
        )
        if destination is not None:
            self._call(lambda: self.workflow.export_legacy(self.artifact_store, Path(destination)))

    # --- mouse-based placement (keyboard/numeric fields remain fully sufficient) --------
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override naming
        if watched is self.canvas and self.workflow.metadata is not None:
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
                self._handle_canvas_press(event)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
                self._handle_canvas_release(event)
                return True
        return super().eventFilter(watched, event)

    def _handle_canvas_press(self, event: QMouseEvent) -> None:
        tool = self.tool_selector.currentText()
        if tool != _TOOL_ROI:
            return
        position = event.position()
        self._roi_drag_start = (position.x(), position.y())

    def _handle_canvas_release(self, event: QMouseEvent) -> None:
        tool = self.tool_selector.currentText()
        if tool == _TOOL_NONE:
            return
        position = event.position()
        try:
            frame_point = self._widget_to_frame(position.x(), position.y())
        except Exception as error:  # pragma: no cover - defensive; _call surfaces the message
            self._show_error(str(error))
            return
        if tool == _TOOL_BASE:
            self._call(lambda: self.workflow.set_base_point(frame_point[0], frame_point[1]))
        elif tool == _TOOL_TIP:
            self._call(lambda: self.workflow.set_tip_point(frame_point[0], frame_point[1]))
        elif tool == _TOOL_ROI and self._roi_drag_start is not None:
            start = self._widget_to_frame(*self._roi_drag_start)
            self._roi_drag_start = None
            self._call(lambda: self.workflow.set_roi_corners(start[0], start[1], frame_point[0], frame_point[1]))

    def _widget_to_frame(self, x: float, y: float) -> tuple[float, float]:
        snapshot = self.workflow.snapshot
        frame_size = snapshot.frame_size
        if frame_size is None:
            raise ValueError("frame dimensions are not known yet")
        visible = self.workflow.visible_rect()
        point = widget_point_to_frame(x, y, frame_size, visible, self.canvas.width(), self.canvas.height())
        return point.x, point.y

    # --- overlay painting ------------------------------------------------------------
    def _paint_overlay(self, painter: QPainter, rect) -> None:
        snapshot = self.workflow.snapshot
        if not snapshot.overlay_visible or snapshot.frame_size is None:
            return
        frame_size = snapshot.frame_size
        visible = self.workflow.visible_rect()
        width, height = rect.width(), rect.height()

        def to_widget(point) -> tuple[float, float]:
            return frame_to_widget_point(point, frame_size, visible, width, height)

        if snapshot.base_point is not None:
            x, y = to_widget(snapshot.base_point)
            painter.setPen(QPen(QColor("#2ecc71"), 2))
            painter.drawEllipse(int(x) - 5, int(y) - 5, 10, 10)
        if snapshot.tip_point is not None:
            x, y = to_widget(snapshot.tip_point)
            painter.setPen(QPen(QColor("#e74c3c"), 2))
            painter.drawEllipse(int(x) - 5, int(y) - 5, 10, 10)
        if snapshot.base_point is not None and snapshot.tip_point is not None:
            bx, by = to_widget(snapshot.base_point)
            tx, ty = to_widget(snapshot.tip_point)
            painter.setPen(QPen(QColor("#2ecc71"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(int(bx), int(by), int(tx), int(ty))
        if snapshot.roi is not None:
            top_left = to_widget(PixelPoint(snapshot.roi.left, snapshot.roi.top))
            bottom_right = to_widget(PixelPoint(snapshot.roi.right, snapshot.roi.bottom))
            painter.setPen(QPen(QColor("#3498db"), 2))
            painter.drawRect(
                int(top_left[0]),
                int(top_left[1]),
                int(bottom_right[0] - top_left[0]),
                int(bottom_right[1] - top_left[1]),
            )

    # --- error handling / rendering -----------------------------------------------------
    def _call(self, action) -> None:
        try:
            action()
        except Exception as error:
            self._show_error(str(error))
            return
        self._render()

    def _show_error(self, message: str) -> None:
        self.status_label.setText(message)

    def _render(self) -> None:
        snapshot = self.workflow.snapshot
        self._rendering = True
        try:
            if snapshot.video_path is None:
                self.video_summary.setText("No video loaded.")
                self.canvas.set_frame(None)
            else:
                metadata = snapshot.metadata
                metadata_text = f"{metadata.frame_size.width}x{metadata.frame_size.height} @ {metadata.fps:g}fps, {metadata.frame_count} frame(s)" if metadata else ""
                self.video_summary.setText(f"{snapshot.video_path.name}: {metadata_text}")
                self._render_frame(snapshot)

            self._render_spinbox_bounds(snapshot.frame_size)
            self._set_spin_value(self.base_x, snapshot.base_point.x if snapshot.base_point else None)
            self._set_spin_value(self.base_y, snapshot.base_point.y if snapshot.base_point else None)
            self._set_spin_value(self.tip_x, snapshot.tip_point.x if snapshot.tip_point else None)
            self._set_spin_value(self.tip_y, snapshot.tip_point.y if snapshot.tip_point else None)
            self._set_spin_value(self.roi_x, snapshot.roi.left if snapshot.roi else None)
            self._set_spin_value(self.roi_y, snapshot.roi.top if snapshot.roi else None)
            self._set_spin_value(self.roi_w, snapshot.roi.width if snapshot.roi else None)
            self._set_spin_value(self.roi_h, snapshot.roi.height if snapshot.roi else None)
            self.overlay_checkbox.setChecked(snapshot.overlay_visible)
            self.undo_button.setEnabled(snapshot.can_undo)
            self.redo_button.setEnabled(snapshot.can_redo)
            if snapshot.artifact_id:
                self.artifact_id_field.setText(snapshot.artifact_id)
            self.save_button.setEnabled(snapshot.is_ready)
            self.status_label.setText(snapshot.message)
            self.canvas.update()
        finally:
            self._rendering = False

    def _render_frame(self, snapshot) -> None:
        try:
            frame = self.workflow.current_frame()
        except Exception:
            self.canvas.set_frame(None)
            return
        visible = self.workflow.visible_rect()
        left = _round_bounds(visible.left, snapshot.frame_size.width - 1)
        top = _round_bounds(visible.top, snapshot.frame_size.height - 1)
        right = max(left + 1, _round_bounds(visible.right, snapshot.frame_size.width))
        bottom = max(top + 1, _round_bounds(visible.bottom, snapshot.frame_size.height))
        cropped = frame[top:bottom, left:right]
        self.canvas.set_frame(
            cropped,
            frame_index=snapshot.frame_index,
            frame_count=snapshot.metadata.frame_count if snapshot.metadata else 0,
            description=snapshot.message,
        )

    def _render_spinbox_bounds(self, frame_size: FrameSize | None) -> None:
        enabled = frame_size is not None
        for spin in (self.base_x, self.tip_x, self.roi_x, self.roi_w):
            spin.setEnabled(enabled)
            if frame_size is not None:
                spin.setRange(0.0 if spin not in (self.roi_w,) else 1.0, float(frame_size.width))
        for spin in (self.base_y, self.tip_y, self.roi_y, self.roi_h):
            spin.setEnabled(enabled)
            if frame_size is not None:
                spin.setRange(0.0 if spin not in (self.roi_h,) else 1.0, float(frame_size.height))

    def _set_spin_value(self, spin: QDoubleSpinBox, value: float | None) -> None:
        spin.blockSignals(True)
        spin.setValue(value if value is not None else 0.0)
        spin.blockSignals(False)

    def _shutdown(self, *_: object) -> None:
        self.workflow.close_video()

    # --- cross-widget refresh --------------------------------------------------------
    def refresh(self) -> None:
        """Re-render after another widget mutated the shared ``workflow`` directly.

        ``MarkerSuggestionView`` calls this after accepting a candidate (or
        after any other external mutation of the shared, Qt-free
        ``VideoGeometryWorkflow``) so the canvas/overlay/spinboxes reflect the
        new tip without duplicating the video display in a second canvas.
        """

        self._render()


__all__ = ["VideoGeometryView"]
