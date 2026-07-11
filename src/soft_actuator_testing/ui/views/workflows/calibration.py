"""Calibration authoring page; all state and capture policy remain Qt-free."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from soft_actuator_testing.application.calibration_workflow import (
    CalibrationWorkflowService,
    CaptureCancellation,
    FakeCalibrationSampleSource,
)
from soft_actuator_testing.application.presentation import (
    ApplicationSnapshot,
    CollectCalibrationSamples,
    FitCalibration,
)
from soft_actuator_testing.application.services import ArtifactStore
from soft_actuator_testing.domain.calibration import CalibrationModelType
from soft_actuator_testing.ui.views.base import PageScenario, WorkflowPage
from soft_actuator_testing.ui.widgets import AccessibleButton, PlotCanvas
from soft_actuator_testing.ui.widgets.file_picker import FileFilter


class _NumericItem(QTableWidgetItem):
    """Table item that keeps pressure and voltage columns numerically sortable."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        try:
            return float(self.text()) < float(other.text())
        except ValueError:
            return super().__lt__(other)


class _CalibrationCaptureThread(QThread):
    """Own one bounded real-controller request outside Qt's GUI thread."""

    captured = Signal(object, object)
    failed = Signal(str)

    def __init__(self, service: CalibrationWorkflowService, timeout_seconds: float) -> None:
        super().__init__()
        self._service = service
        self._timeout_seconds = timeout_seconds
        self._cancellation = CaptureCancellation()

    def cancel(self) -> None:
        self._cancellation.cancel()

    def run(self) -> None:
        try:
            baseline, measurement = self._service.capture_sample(
                timeout_seconds=self._timeout_seconds,
                cancellation=self._cancellation,
            )
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.captured.emit(baseline, measurement)


class CalibrationPage(WorkflowPage):
    """Render the workflow service snapshot and dispatch only presenter commands."""

    def __init__(
        self,
        *,
        calibration_service: CalibrationWorkflowService | None = None,
        artifact_store: ArtifactStore | None = None,
        capture_timeout_seconds: float = 2.0,
        **kwargs,
    ) -> None:
        if capture_timeout_seconds <= 0:
            raise ValueError("capture_timeout_seconds must be positive")
        super().__init__("Calibration", **kwargs)
        self.calibration_service = calibration_service or CalibrationWorkflowService(FakeCalibrationSampleSource.demo())
        self.artifact_store = artifact_store
        self._rendering = False
        self._presenter_samples_imported = False
        self._capture_timeout_seconds = capture_timeout_seconds
        self._capture_thread: _CalibrationCaptureThread | None = None

        capture_group = self.section("Capture a fresh voltage")
        capture_layout = QFormLayout(capture_group)
        self.known_pressure = QLineEdit(capture_group)
        self.known_pressure.setObjectName("known-pressure")
        self.known_pressure.setPlaceholderText("Known pressure (kPa)")
        self.request_button = AccessibleButton("Request fresh sample")
        self.request_button.setObjectName("request-sample")
        self.request_button.clicked.connect(self.request_sample)
        self.cancel_capture_button = AccessibleButton("Cancel capture")
        self.cancel_capture_button.setObjectName("cancel-capture")
        self.cancel_capture_button.setEnabled(False)
        self.cancel_capture_button.clicked.connect(self.cancel_capture)
        self.record_button = AccessibleButton("Record sample")
        self.record_button.setObjectName("record-sample")
        self.record_button.clicked.connect(self.record_sample)
        request_row = QHBoxLayout()
        request_row.addWidget(self.request_button)
        request_row.addWidget(self.cancel_capture_button)
        request_row.addWidget(self.record_button)
        self.capture_status = QLabel(capture_group)
        self.capture_status.setObjectName("capture-status")
        self.capture_status.setWordWrap(True)
        capture_layout.addRow("Known pressure", self.known_pressure)
        capture_layout.addRow(request_row)
        capture_layout.addRow("Capture", self.capture_status)

        samples_group = self.section("Pressure / voltage samples")
        samples_layout = QVBoxLayout(samples_group)
        self.samples_table = QTableWidget(0, 4, samples_group)
        self.samples_table.setHorizontalHeaderLabels(["Pressure (kPa)", "Voltage (V)", "Captured (UTC)", "Sequence"])
        self.samples_table.setAccessibleName("Calibration samples")
        self.samples_table.setSortingEnabled(True)
        self.samples_table.cellChanged.connect(self.edit_sample)
        samples_layout.addWidget(self.samples_table)
        edit_row = QHBoxLayout()
        self.remove_button = AccessibleButton("Remove selected")
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button = AccessibleButton("Clear samples")
        self.clear_button.clicked.connect(self.clear_samples)
        self.undo_button = AccessibleButton("Undo")
        self.undo_button.clicked.connect(self.undo)
        for button in (self.remove_button, self.clear_button, self.undo_button):
            edit_row.addWidget(button)
        samples_layout.addLayout(edit_row)
        self.collect_button = AccessibleButton("Load presenter demo samples")
        self.collect_button.setObjectName("collect-samples")
        self.collect_button.clicked.connect(self.collect_samples)
        samples_layout.addWidget(self.collect_button)

        fit_group = self.section("Calibration fit")
        fit_layout = QFormLayout(fit_group)
        self.model_type = QComboBox(fit_group)
        self.model_type.addItem("Linear", CalibrationModelType.LINEAR)
        self.model_type.addItem("Quadratic", CalibrationModelType.QUADRATIC)
        self.fit_button = AccessibleButton("Fit calibration")
        self.fit_button.setObjectName("fit-calibration")
        self.fit_button.clicked.connect(self.fit_calibration)
        self.fit_summary = QLabel(fit_group)
        self.fit_summary.setObjectName("fit-summary")
        self.fit_summary.setAccessibleName("Calibration fit result")
        self.fit_summary.setWordWrap(True)
        self.fit_warnings = QLabel(fit_group)
        self.fit_warnings.setObjectName("fit-warnings")
        self.fit_warnings.setWordWrap(True)
        fit_layout.addRow("Model", self.model_type)
        fit_layout.addRow(self.fit_button)
        fit_layout.addRow("Result", self.fit_summary)
        fit_layout.addRow("Warnings", self.fit_warnings)

        plots_group = self.section("Sample, fit, and residual plots")
        plots_layout = QVBoxLayout(plots_group)
        self.fit_plot = PlotCanvas(title="Calibration samples and fit", x_label="Voltage (V)", y_label="Pressure (kPa)")
        self.residual_plot = PlotCanvas(title="Fit residuals", x_label="Voltage (V)", y_label="Observed − predicted (kPa)")
        plots_layout.addWidget(self.fit_plot)
        plots_layout.addWidget(self.residual_plot)

        persistence_group = self.section("Versioned calibration artifacts")
        persistence_layout = QFormLayout(persistence_group)
        self.notes = QLineEdit(persistence_group)
        self.notes.setObjectName("calibration-notes")
        self.notes.editingFinished.connect(lambda: self.calibration_service.set_notes(self.notes.text()))
        self.artifact_id = QLineEdit(persistence_group)
        self.artifact_id.setObjectName("calibration-artifact-id")
        self.save_button = AccessibleButton("Save versioned")
        self.save_button.clicked.connect(self.save_versioned)
        self.load_button = AccessibleButton("Load versioned")
        self.load_button.clicked.connect(self.load_versioned)
        self.import_button = AccessibleButton("Import legacy JSON")
        self.import_button.clicked.connect(self.import_legacy)
        self.export_button = AccessibleButton("Export legacy JSON")
        self.export_button.clicked.connect(self.export_legacy)
        store_row = QHBoxLayout()
        for button in (self.save_button, self.load_button, self.import_button, self.export_button):
            store_row.addWidget(button)
        self.persistence_status = QLabel(persistence_group)
        self.persistence_status.setObjectName("calibration-persistence-status")
        self.persistence_status.setWordWrap(True)
        persistence_layout.addRow("Notes", self.notes)
        persistence_layout.addRow("Artifact ID", self.artifact_id)
        persistence_layout.addRow(store_row)
        persistence_layout.addRow("Status", self.persistence_status)

        self.layout.addStretch(1)
        self._bind_presenter()
        self.destroyed.connect(lambda: self._cancel_capture_for_shutdown())
        self._render_workflow()

    def render_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        # The established presentation command/snapshot seam remains the source
        # of demo data.  Operator actions thereafter use the Qt-free workflow.
        if snapshot.calibration.samples and not self._presenter_samples_imported and not self.calibration_service.snapshot.samples:
            self.calibration_service.replace_samples(snapshot.calibration.samples)
            self._presenter_samples_imported = True
            self._render_workflow()

    def request_sample(self) -> None:
        if not self.calibration_service.capture_requires_background_worker:
            self._call(self.calibration_service.request_sample, scenario=PageScenario.READY)
            return
        if self._capture_thread is not None:
            self._show_error("A calibration capture is already active; wait for it or cancel it.")
            return
        thread = _CalibrationCaptureThread(self.calibration_service, self._capture_timeout_seconds)
        self._capture_thread = thread
        self.request_button.setEnabled(False)
        self.cancel_capture_button.setEnabled(True)
        self.capture_status.setText("Waiting for a fresh structured controller sample…")
        thread.captured.connect(self._capture_succeeded)
        thread.failed.connect(self._capture_failed)
        thread.finished.connect(self._capture_finished)
        thread.start()

    def cancel_capture(self) -> None:
        if self._capture_thread is None:
            return
        self._capture_thread.cancel()
        self.cancel_capture_button.setEnabled(False)
        self.capture_status.setText("Cancelling calibration capture and releasing controller streaming…")

    def _capture_succeeded(self, baseline: object, measurement: object) -> None:
        try:
            self.calibration_service.accept_capture(int(baseline), measurement)
        except Exception as error:
            self._show_error(str(error))
            return
        self._render_workflow()
        self.set_scenario(PageScenario.READY)

    def _capture_failed(self, message: str) -> None:
        self.capture_status.setText(message)
        self.persistence_status.setText(message)
        self.set_scenario(PageScenario.FAULT)

    def _capture_finished(self) -> None:
        thread = self._capture_thread
        self._capture_thread = None
        self.request_button.setEnabled(True)
        self.cancel_capture_button.setEnabled(False)
        if thread is not None:
            thread.deleteLater()

    def _cancel_capture_for_shutdown(self) -> None:
        thread = self._capture_thread
        if thread is None:
            return
        thread.cancel()
        # The source polls a controller-owned queue and observes cancellation
        # within its configured interval, so this bounded join cannot orphan it.
        thread.wait(int((self._capture_timeout_seconds + 0.25) * 1000))
        self._capture_thread = None

    def closeEvent(self, event) -> None:
        self._cancel_capture_for_shutdown()
        super().closeEvent(event)

    def record_sample(self) -> None:
        self._call(lambda: self.calibration_service.record_sample(self.known_pressure.text()), scenario=PageScenario.READY)

    def collect_samples(self) -> None:
        self.dispatch(CollectCalibrationSamples())
        self.calibration_service.replace_samples(self.application_snapshot.calibration.samples)
        self._presenter_samples_imported = True
        self._render_workflow()
        self.set_scenario(PageScenario.READY)

    def edit_sample(self, row: int, column: int) -> None:
        if self._rendering or column not in (0, 1):
            return
        identifier_item = self.samples_table.item(row, 0)
        if identifier_item is None:
            return
        identifier = identifier_item.data(Qt.ItemDataRole.UserRole)
        pressure = self.samples_table.item(row, 0).text()
        voltage = self.samples_table.item(row, 1).text()
        self._call(lambda: self.calibration_service.edit_sample(identifier, pressure, voltage))

    def remove_selected(self) -> None:
        row = self.samples_table.currentRow()
        item = self.samples_table.item(row, 0) if row >= 0 else None
        if item is None:
            self._show_error("Select a sample to remove.")
            return
        self._call(lambda: self.calibration_service.remove_sample(item.data(Qt.ItemDataRole.UserRole)))

    def clear_samples(self) -> None:
        self._call(self.calibration_service.clear_samples)

    def undo(self) -> None:
        self.calibration_service.undo()
        self._render_workflow()

    def fit_calibration(self) -> None:
        model_type = CalibrationModelType(self.model_type.currentData())
        self._call(lambda: self.calibration_service.fit(model_type), scenario=PageScenario.COMPLETED)
        if self.calibration_service.snapshot.fit is not None:
            self.dispatch(FitCalibration())

    def save_versioned(self) -> None:
        if self.artifact_store is None:
            self._show_error("No ArtifactFileStore is configured; choose a workspace-backed store before saving.")
            return
        self._call(lambda: self.calibration_service.save(self.artifact_store), scenario=PageScenario.COMPLETED)

    def load_versioned(self) -> None:
        if self.artifact_store is None:
            self._show_error("No ArtifactFileStore is configured; choose a workspace-backed store before loading.")
            return
        self._call(lambda: self.calibration_service.load(self.artifact_store, self.artifact_id.text().strip()))

    def import_legacy(self) -> None:
        if self.artifact_store is None:
            self._show_error("No ArtifactFileStore is configured; choose a workspace-backed store before importing.")
            return
        source = self.file_picker.get_open_file(
            caption="Import legacy calibration JSON",
            filters=(FileFilter("JSON files", ("*.json",)),),
        )
        if source is not None:
            self._call(lambda: self.calibration_service.import_legacy(self.artifact_store, source))

    def export_legacy(self) -> None:
        if self.artifact_store is None:
            self._show_error("No ArtifactFileStore is configured; choose a workspace-backed store before exporting.")
            return
        destination = self.file_picker.get_save_file(
            caption="Export legacy calibration JSON",
            filters=(FileFilter("JSON files", ("*.json",)),),
        )
        if destination is not None:
            self._call(lambda: self.calibration_service.export_legacy(self.artifact_store, Path(destination)))

    def _call(self, action, *, scenario: PageScenario | None = None) -> None:
        try:
            action()
        except Exception as error:
            self._show_error(str(error))
            return
        self._render_workflow()
        if scenario is not None:
            self.set_scenario(scenario)

    def _show_error(self, message: str) -> None:
        self.capture_status.setText(message)
        self.persistence_status.setText(message)
        self.set_scenario(PageScenario.FAULT)

    def _render_workflow(self) -> None:
        snapshot = self.calibration_service.snapshot
        self._rendering = True
        try:
            self.samples_table.setSortingEnabled(False)
            self.samples_table.setRowCount(0)
            for item in snapshot.samples:
                row = self.samples_table.rowCount()
                self.samples_table.insertRow(row)
                pressure = _NumericItem(f"{item.sample.known_pressure_kpa:.8g}")
                pressure.setData(Qt.ItemDataRole.UserRole, item.identifier)
                self.samples_table.setItem(row, 0, pressure)
                self.samples_table.setItem(row, 1, _NumericItem(f"{item.sample.measured_voltage:.8g}"))
                captured = item.captured_at.isoformat() if item.captured_at else "—"
                self.samples_table.setItem(row, 2, QTableWidgetItem(captured))
                self.samples_table.setItem(row, 3, QTableWidgetItem(str(item.source_sequence or "—")))
            self.samples_table.setSortingEnabled(True)
            self.capture_status.setText(
                snapshot.message
                if snapshot.pending_measurement is None
                else f"{snapshot.message} Enter known pressure, then record it."
            )
            fit = snapshot.fit
            self.fit_button.setEnabled(bool(snapshot.samples))
            if fit is None:
                self.fit_summary.setText("Draft — no fit yet.")
            else:
                self.fit_summary.setText(
                    f"{fit.model.model_type.value.title()} • R² {fit.adequacy.r_squared:.4f} • "
                    f"RMSE {fit.adequacy.rmse_kpa:.4g} kPa • condition {fit.adequacy.condition_number:.3g} • "
                    f"{snapshot.validation_status.value}"
                )
            self.fit_warnings.setText("\n".join(snapshot.warnings) or "No fit warnings.")
            self.notes.setText(snapshot.notes)
            if snapshot.artifact_id:
                self.artifact_id.setText(snapshot.artifact_id)
            self.persistence_status.setText(snapshot.message)
            self._render_plots()
        finally:
            self._rendering = False

    def _render_plots(self) -> None:
        snapshot = self.calibration_service.snapshot
        samples = snapshot.samples
        volts = [item.sample.measured_voltage for item in samples]
        pressures = [item.sample.known_pressure_kpa for item in samples]
        self.fit_plot.clear_series()
        self.residual_plot.clear_series()
        if samples:
            self.fit_plot.set_series("samples", volts, pressures)
        fit = snapshot.fit
        if fit is not None and fit.model.input_domain is not None:
            domain = fit.model.input_domain
            line_volts = [domain.minimum_volts + (domain.maximum_volts - domain.minimum_volts) * index / 100 for index in range(101)]
            self.fit_plot.set_series("fit", line_volts, [fit.model.apply(value) for value in line_volts])
            self.residual_plot.set_series(
                "residuals",
                [item.voltage for item in fit.residuals],
                [item.residual_kpa for item in fit.residuals],
            )
