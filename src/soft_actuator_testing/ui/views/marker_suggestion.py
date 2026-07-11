"""Guided red-marker suggestion widget; a bare, embeddable ``QWidget``.

Composes the Qt/cv2-free :class:`MarkerSuggestionWorkflow` engine with the
existing :class:`VideoGeometryView` (base/tip/ROI authoring) so a detection
run always operates on the *same* frame/ROI/base-point the operator is
looking at, and an accepted candidate is applied through
``VideoGeometryWorkflow.accept_marker_suggestion`` — never bypassing manual
authoring, never fabricating a tip, and always leaving the operator free to
correct it afterwards with the existing manual controls.

Detection itself (dual-hue HSV masking, morphology, contour scoring) is
heavy per-frame pixel work, so it always runs on a bounded, cancellable
``QThread`` (mirroring ``_CalibrationCaptureThread`` in
``ui/views/workflows/calibration.py``); the GUI thread only ever touches the
resulting immutable :class:`MarkerSuggestionResult`, and a result is only
ever applied if :meth:`MarkerSuggestionWorkflow.is_current` says it is not
stale *and* it was computed for the frame currently on screen.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from soft_actuator_testing.application.marker_suggestion import (
    HsvRedThresholds,
    MarkerSuggestionCancellation,
    MarkerSuggestionCancelled,
    MarkerSuggestionResult,
    MarkerSuggestionState,
    MarkerSuggestionWorkflow,
)
from soft_actuator_testing.application.video_geometry_workflow import frame_to_widget_point
from soft_actuator_testing.domain.errors import GeometryError
from soft_actuator_testing.domain.geometry import PixelPoint
from soft_actuator_testing.infrastructure.red_marker_detector import OpenCvRedMarkerFrameDetector
from soft_actuator_testing.ui.views.video_geometry import VideoGeometryView
from soft_actuator_testing.ui.widgets import AccessibleButton, VideoCanvas

_STALENESS_POLL_MS = 250


class _MarkerDetectionThread(QThread):
    """Own one bounded ``MarkerSuggestionWorkflow.suggest`` call off the GUI thread."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        engine: MarkerSuggestionWorkflow,
        frame: Any,
        *,
        frame_index: int,
        frame_size: Any,
        roi: Any,
        base_point: Any,
        previous_tip: Any,
        cancellation: MarkerSuggestionCancellation,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._frame = frame
        self._frame_index = frame_index
        self._frame_size = frame_size
        self._roi = roi
        self._base_point = base_point
        self._previous_tip = previous_tip
        self._cancellation = cancellation

    def cancel(self) -> None:
        self._cancellation.cancel()

    def run(self) -> None:
        try:
            result = self._engine.suggest(
                self._frame,
                frame_index=self._frame_index,
                frame_size=self._frame_size,
                roi=self._roi,
                base_point=self._base_point,
                previous_tip=self._previous_tip,
                cancellation=self._cancellation,
            )
        except MarkerSuggestionCancelled:
            self.failed.emit("Marker detection was cancelled.")
        except Exception as error:  # pragma: no cover - defensive; surfaced to the operator
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(result)


class MarkerSuggestionView(QWidget):
    """Guided red-marker candidate detection that complements manual authoring.

    Never runs automatically: the operator explicitly requests a detection
    run for the frame currently shown in ``geometry_view``, reviews the
    ranked candidates/reasons/mask preview, and explicitly accepts one (or
    ignores all of them and keeps authoring manually).
    """

    def __init__(
        self,
        geometry_view: VideoGeometryView,
        *,
        engine: MarkerSuggestionWorkflow | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("marker-suggestion-view")
        self.setAccessibleName("Guided marker suggestions")
        self.geometry_view = geometry_view
        self.engine = engine or MarkerSuggestionWorkflow(OpenCvRedMarkerFrameDetector())
        self._detection_thread: _MarkerDetectionThread | None = None
        self._last_result: MarkerSuggestionResult | None = None

        layout = QVBoxLayout(self)

        # --- configurable thresholds -----------------------------------------------
        thresholds_layout = QFormLayout()
        initial = self.engine.thresholds
        self.hue_low_max = self._spin("suggest-hue-low-max", 0, 179, initial.hue_low_max)
        self.hue_high_min = self._spin("suggest-hue-high-min", 0, 179, initial.hue_high_min)
        self.saturation_min = self._spin("suggest-saturation-min", 0, 255, initial.saturation_min)
        self.saturation_max = self._spin("suggest-saturation-max", 0, 255, initial.saturation_max)
        self.value_min = self._spin("suggest-value-min", 0, 255, initial.value_min)
        self.value_max = self._spin("suggest-value-max", 0, 255, initial.value_max)
        self.morphology_kernel_size = self._spin("suggest-morph-kernel", 1, 99, initial.morphology_kernel_size)
        self.morph_open_iterations = self._spin("suggest-morph-open", 0, 10, initial.morph_open_iterations)
        self.morph_close_iterations = self._spin("suggest-morph-close", 0, 10, initial.morph_close_iterations)
        self.min_area_pixels = self._double_spin("suggest-min-area", 0.01, 1_000_000.0, initial.min_area_pixels)
        self.min_circularity = self._double_spin("suggest-min-circularity", 0.0, 1.0, initial.min_circularity, step=0.05)
        self.exclusion_radius_pixels = self._double_spin("suggest-exclusion-radius", 0.0, 1_000_000.0, initial.exclusion_radius_pixels)
        self.max_candidates = self._spin("suggest-max-candidates", 1, 50, initial.max_candidates)
        self.ambiguity_margin = self._double_spin("suggest-ambiguity-margin", 0.0, 1.0, initial.ambiguity_margin, step=0.01)
        thresholds_layout.addRow("Hue low max", self.hue_low_max)
        thresholds_layout.addRow("Hue high min", self.hue_high_min)
        thresholds_layout.addRow("Saturation min", self.saturation_min)
        thresholds_layout.addRow("Saturation max", self.saturation_max)
        thresholds_layout.addRow("Value min", self.value_min)
        thresholds_layout.addRow("Value max", self.value_max)
        thresholds_layout.addRow("Morphology kernel size", self.morphology_kernel_size)
        thresholds_layout.addRow("Morphology open iterations", self.morph_open_iterations)
        thresholds_layout.addRow("Morphology close iterations", self.morph_close_iterations)
        thresholds_layout.addRow("Minimum area (px)", self.min_area_pixels)
        thresholds_layout.addRow("Minimum circularity", self.min_circularity)
        thresholds_layout.addRow("Base-point exclusion radius (px)", self.exclusion_radius_pixels)
        thresholds_layout.addRow("Max candidates", self.max_candidates)
        thresholds_layout.addRow("Ambiguity margin", self.ambiguity_margin)
        self.apply_thresholds_button = AccessibleButton("Apply thresholds", parent=self)
        self.apply_thresholds_button.setObjectName("suggest-apply-thresholds")
        self.apply_thresholds_button.clicked.connect(self.apply_thresholds)
        thresholds_layout.addRow(self.apply_thresholds_button)
        layout.addLayout(thresholds_layout)

        # --- detect / cancel ---------------------------------------------------------
        detect_row = QHBoxLayout()
        self.detect_button = AccessibleButton("Detect marker candidates", parent=self)
        self.detect_button.setObjectName("suggest-detect")
        self.detect_button.clicked.connect(self.detect)
        self.cancel_button = AccessibleButton("Cancel detection", parent=self)
        self.cancel_button.setObjectName("suggest-cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_detection)
        detect_row.addWidget(self.detect_button)
        detect_row.addWidget(self.cancel_button)
        layout.addLayout(detect_row)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("suggest-status")
        self.status_label.setWordWrap(True)
        self.status_label.setText("No detection has been run yet.")
        layout.addWidget(self.status_label)

        # --- ranked candidates ---------------------------------------------------------
        self.candidates_table = QTableWidget(0, 4, self)
        self.candidates_table.setObjectName("suggest-candidates")
        self.candidates_table.setAccessibleName("Ranked marker suggestion candidates")
        self.candidates_table.setHorizontalHeaderLabels(["Rank", "Confidence", "Tip point", "Reasons"])
        self.candidates_table.itemSelectionChanged.connect(self._update_accept_enabled)
        layout.addWidget(self.candidates_table)

        self.accept_button = AccessibleButton("Accept selected candidate", parent=self)
        self.accept_button.setObjectName("suggest-accept")
        self.accept_button.setEnabled(False)
        self.accept_button.clicked.connect(self.accept_selected_candidate)
        layout.addWidget(self.accept_button)

        # --- mask preview ---------------------------------------------------------------
        layout.addWidget(QLabel("Detection mask preview", self))
        self.mask_preview = VideoCanvas(accessible_title="Detection mask preview", parent=self)
        self.mask_preview.setObjectName("suggest-mask-preview")
        self.mask_preview.setMinimumHeight(120)
        layout.addWidget(self.mask_preview)

        self._unsubscribe_overlay = self.geometry_view.canvas.register_overlay(self._paint_candidates_overlay)

        self._staleness_timer = QTimer(self)
        self._staleness_timer.setInterval(_STALENESS_POLL_MS)
        self._staleness_timer.timeout.connect(self.check_staleness)
        self._staleness_timer.start()
        self.destroyed.connect(lambda: self._cancel_detection_for_shutdown())

    # --- small widget factories --------------------------------------------------------
    def _spin(self, object_name: str, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox(self)
        spin.setObjectName(object_name)
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        return spin

    def _double_spin(self, object_name: str, minimum: float, maximum: float, value: float, *, step: float = 1.0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setObjectName(object_name)
        spin.setDecimals(3)
        spin.setSingleStep(step)
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        return spin

    # --- thresholds ---------------------------------------------------------------------
    def apply_thresholds(self) -> None:
        try:
            thresholds = HsvRedThresholds(
                hue_low_max=self.hue_low_max.value(),
                hue_high_min=self.hue_high_min.value(),
                saturation_min=self.saturation_min.value(),
                saturation_max=self.saturation_max.value(),
                value_min=self.value_min.value(),
                value_max=self.value_max.value(),
                morphology_kernel_size=self.morphology_kernel_size.value(),
                morph_open_iterations=self.morph_open_iterations.value(),
                morph_close_iterations=self.morph_close_iterations.value(),
                min_area_pixels=self.min_area_pixels.value(),
                min_circularity=self.min_circularity.value(),
                exclusion_radius_pixels=self.exclusion_radius_pixels.value(),
                max_candidates=self.max_candidates.value(),
                ambiguity_margin=self.ambiguity_margin.value(),
            )
        except GeometryError as error:
            self.status_label.setText(str(error))
            return
        self.engine.set_thresholds(thresholds)
        self.status_label.setText("Thresholds applied; run detection again to see their effect.")

    # --- detection lifecycle --------------------------------------------------------------
    def detect(self) -> None:
        if self._detection_thread is not None:
            self.status_label.setText("A detection run is already active; wait for it or cancel it.")
            return
        snapshot = self.geometry_view.workflow.snapshot
        if snapshot.frame_size is None:
            self.status_label.setText("Load a video before requesting marker suggestions.")
            return
        try:
            frame = self.geometry_view.workflow.current_frame()
        except Exception as error:
            self.status_label.setText(str(error))
            return

        cancellation = MarkerSuggestionCancellation()
        thread = _MarkerDetectionThread(
            self.engine,
            frame,
            frame_index=snapshot.frame_index,
            frame_size=snapshot.frame_size,
            roi=snapshot.roi,
            base_point=snapshot.base_point,
            previous_tip=snapshot.tip_point,
            cancellation=cancellation,
        )
        self._detection_thread = thread
        self.detect_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status_label.setText(f"Detecting red-marker candidates on frame {snapshot.frame_index}…")
        thread.succeeded.connect(self._detection_succeeded)
        thread.failed.connect(self._detection_failed)
        thread.finished.connect(self._detection_finished)
        thread.start()

    def cancel_detection(self) -> None:
        if self._detection_thread is None:
            return
        self._detection_thread.cancel()
        self.cancel_button.setEnabled(False)
        self.status_label.setText("Cancelling marker detection…")

    def _detection_succeeded(self, result: MarkerSuggestionResult) -> None:
        if not self.engine.is_current(result):
            self.status_label.setText("Discarded a stale detection result (a newer request was issued).")
            return
        current_frame_index = self.geometry_view.workflow.snapshot.frame_index
        if result.frame_index != current_frame_index:
            self.status_label.setText(
                f"Discarded a detection result for frame {result.frame_index}; the current frame is now {current_frame_index}. Rerun detection."
            )
            return
        self._last_result = result
        self._render_result(result)

    def _detection_failed(self, message: str) -> None:
        self.status_label.setText(message)

    def _detection_finished(self) -> None:
        thread = self._detection_thread
        self._detection_thread = None
        self.detect_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if thread is not None:
            thread.deleteLater()

    def _cancel_detection_for_shutdown(self) -> None:
        thread = self._detection_thread
        if thread is None:
            return
        thread.cancel()
        # Detection polls the cancellation token between bounded stages
        # (see infrastructure/red_marker_detector.py), so this bounded join
        # cannot orphan the thread.
        thread.wait(2_000)
        self._detection_thread = None

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override naming
        self._cancel_detection_for_shutdown()
        self._staleness_timer.stop()
        self._unsubscribe_overlay()
        super().closeEvent(event)

    # --- rendering ------------------------------------------------------------------------
    def _render_result(self, result: MarkerSuggestionResult) -> None:
        self.candidates_table.setRowCount(len(result.candidates))
        for row, candidate in enumerate(result.candidates):
            self.candidates_table.setItem(row, 0, QTableWidgetItem(str(candidate.rank)))
            self.candidates_table.setItem(row, 1, QTableWidgetItem(f"{candidate.confidence:.2f}"))
            self.candidates_table.setItem(
                row, 2, QTableWidgetItem(f"({candidate.tip_point.x:.1f}, {candidate.tip_point.y:.1f})")
            )
            self.candidates_table.setItem(row, 3, QTableWidgetItem("; ".join(candidate.reasons)))
        self.candidates_table.resizeColumnsToContents()

        if result.state is MarkerSuggestionState.NO_DETECTION:
            prefix = "No detection"
        elif result.state is MarkerSuggestionState.AMBIGUOUS:
            prefix = "Ambiguous"
        else:
            prefix = "Resolved"
        self.status_label.setText(f"{prefix}: {result.message}")

        self._render_mask_preview(result.mask_preview)
        self._update_accept_enabled()
        self.geometry_view.canvas.update()

    def _render_mask_preview(self, mask: Any) -> None:
        if mask is None:
            self.mask_preview.set_frame(None)
            return
        rgb = np.repeat(np.asarray(mask)[:, :, np.newaxis], 3, axis=2).astype(np.uint8)
        self.mask_preview.set_frame(rgb)

    def _update_accept_enabled(self) -> None:
        has_selection = bool(self.candidates_table.selectedItems())
        has_candidates = self._last_result is not None and bool(self._last_result.candidates)
        self.accept_button.setEnabled(has_selection and has_candidates)

    def accept_selected_candidate(self) -> None:
        if self._last_result is None:
            return
        selected_rows = {index.row() for index in self.candidates_table.selectedIndexes()}
        if not selected_rows:
            return
        row = next(iter(selected_rows))
        if row >= len(self._last_result.candidates):
            return
        candidate = self._last_result.candidates[row]
        try:
            self.geometry_view.workflow.accept_marker_suggestion(
                candidate.tip_point,
                confidence=candidate.confidence,
                reasons=candidate.reasons,
                settings=self._last_result.thresholds.as_dict(),
            )
        except GeometryError as error:
            self.status_label.setText(str(error))
            return
        self.geometry_view.refresh()
        self.status_label.setText(
            f"Accepted candidate #{candidate.rank} (confidence {candidate.confidence:.2f}) as the tip point. "
            "Correct it manually at any time; that will clear the suggestion provenance."
        )

    # --- staleness guard ------------------------------------------------------------------
    def check_staleness(self) -> None:
        """Disable acceptance once the on-screen frame no longer matches the last result.

        Polled by an internal timer for live GUI use, and callable directly
        in tests for determinism. Never lets an operator accept a candidate
        that was computed for a different frame than the one now displayed.
        """

        if self._last_result is None:
            return
        current_frame_index = self.geometry_view.workflow.snapshot.frame_index
        if self._last_result.frame_index != current_frame_index:
            self.accept_button.setEnabled(False)
            self.status_label.setText(
                f"Frame changed to {current_frame_index}; the displayed candidates are for frame "
                f"{self._last_result.frame_index} and are now stale. Rerun detection."
            )

    # --- overlay --------------------------------------------------------------------------
    def _paint_candidates_overlay(self, painter: QPainter, rect) -> None:
        result = self._last_result
        if result is None or not result.candidates:
            return
        snapshot = self.geometry_view.workflow.snapshot
        if snapshot.frame_size is None or result.frame_index != snapshot.frame_index:
            return
        visible = self.geometry_view.workflow.visible_rect()
        width, height = rect.width(), rect.height()
        for candidate in result.candidates:
            color = QColor("#2ecc71") if candidate.rank == 1 else QColor("#f1c40f")
            top_left = frame_to_widget_point(
                PixelPoint(candidate.bounding_box.left, candidate.bounding_box.top), snapshot.frame_size, visible, width, height
            )
            bottom_right = frame_to_widget_point(
                PixelPoint(candidate.bounding_box.right, candidate.bounding_box.bottom), snapshot.frame_size, visible, width, height
            )
            painter.setPen(QPen(color, 2))
            painter.drawRect(
                int(top_left[0]),
                int(top_left[1]),
                int(bottom_right[0] - top_left[0]),
                int(bottom_right[1] - top_left[1]),
            )
            painter.drawText(int(top_left[0]), max(10, int(top_left[1]) - 4), f"#{candidate.rank} {candidate.confidence:.2f}")


__all__ = ["MarkerSuggestionView"]
