"""pytest-qt coverage for calibration authoring widgets without hardware."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Event

from PySide6.QtCore import QTimer, Qt

from soft_actuator_testing.application.calibration_workflow import (
    CalibrationCaptureCancelled,
    CalibrationMeasurement,
    CalibrationWorkflowService,
    FakeCalibrationSampleSource,
    PressureRange,
)
from soft_actuator_testing.ui.views import CalibrationPage, PageScenario


def _page_service() -> CalibrationWorkflowService:
    timestamp = datetime(2026, 7, 11, tzinfo=timezone.utc)
    return CalibrationWorkflowService(
        FakeCalibrationSampleSource(
            CalibrationMeasurement(index, timestamp, volts, "fake")
            for index, volts in enumerate((0.5, 1.5, 2.5), start=1)
        ),
        pressure_range=PressureRange(0, 200),
    )


def _record(page: CalibrationPage, pressure: int) -> None:
    page.request_sample()
    page.known_pressure.setText(str(pressure))
    page.record_sample()


def test_editable_sortable_samples_and_residual_plot(qtbot) -> None:
    service = _page_service()
    page = CalibrationPage(calibration_service=service)
    qtbot.addWidget(page)
    for pressure in (0, 50, 100):
        _record(page, pressure)

    page.samples_table.sortItems(0, Qt.SortOrder.DescendingOrder)
    assert page.samples_table.item(0, 0).text() == "100"
    page.samples_table.item(0, 0).setText("90")
    assert any(item.sample.known_pressure_kpa == 90 for item in service.snapshot.samples)

    page.fit_calibration()
    assert page.residual_plot.series_names() == ("residuals",)
    assert "R²" in page.fit_summary.text()


def test_invalid_pressure_is_actionable_and_never_contacts_hardware(qtbot) -> None:
    source = FakeCalibrationSampleSource.demo()
    page = CalibrationPage(calibration_service=CalibrationWorkflowService(source, pressure_range=PressureRange(0, 10)))
    qtbot.addWidget(page)
    page.request_sample()
    page.known_pressure.setText("not-a-number")
    page.record_sample()
    assert page.scenario is PageScenario.FAULT
    assert "known pressure" in page.capture_status.text()
    assert source.requests == [0]


def test_hardware_capture_runs_off_gui_thread_rejects_duplicates_and_cleans_up(qtbot) -> None:
    class WaitingHardwareSource:
        requires_background_capture = True

        def __init__(self) -> None:
            self.started = Event()
            self.cancelled = Event()
            self.requests = 0

        def current_sequence(self) -> int:
            return 0

        def request_after(self, sequence: int, *, timeout_seconds=None, cancellation=None):
            del sequence, timeout_seconds
            self.requests += 1
            self.started.set()
            while cancellation is None or not cancellation.is_cancelled():
                Event().wait(0.005)
            self.cancelled.set()
            raise CalibrationCaptureCancelled()

    source = WaitingHardwareSource()
    page = CalibrationPage(
        calibration_service=CalibrationWorkflowService(source),
        capture_timeout_seconds=1.0,
    )
    qtbot.addWidget(page)
    responsive = Event()
    QTimer.singleShot(0, responsive.set)

    page.request_sample()
    page.request_sample()
    qtbot.waitUntil(source.started.is_set, timeout=500)
    qtbot.waitUntil(responsive.is_set, timeout=500)
    assert source.requests == 1
    assert page.request_button.isEnabled() is False
    assert page.cancel_capture_button.isEnabled() is True

    page.cancel_capture()
    qtbot.waitUntil(source.cancelled.is_set, timeout=500)
    qtbot.waitUntil(lambda: page._capture_thread is None, timeout=500)
    assert page.request_button.isEnabled() is True
    assert "cancelled" in page.capture_status.text()
