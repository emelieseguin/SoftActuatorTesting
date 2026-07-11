"""Navigation metadata and page factories shared by both prototype shells."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtWidgets import QWidget

from soft_actuator_testing.application.presentation import PresenterSession
from soft_actuator_testing.ui.views.base import WorkflowPage
from soft_actuator_testing.ui.views.workflows import (
    AnalysisPage,
    CalibrationPage,
    ConnectionsDiagnosticsPage,
    ExperimentSetupReadinessPage,
    HomeWorkspacePage,
    LiveRunPage,
    SettingsProfilesHelpPage,
    VideoGeometryMarkerSetupPage,
)
from soft_actuator_testing.ui.widgets.file_picker import FilePicker

PageFactory = Callable[[PresenterSession | object | None, FilePicker | None, QWidget | None], WorkflowPage]


@dataclass(frozen=True)
class PageNavigation:
    """Shell-neutral metadata; shells decide how and where to render it."""

    key: str
    title: str
    short_title: str
    description: str
    order: int
    factory: PageFactory


def _factory(page_type: type[WorkflowPage]) -> PageFactory:
    def create(
        presenter: PresenterSession | object | None = None,
        file_picker: FilePicker | None = None,
        parent: QWidget | None = None,
    ) -> WorkflowPage:
        return page_type(presenter=presenter, file_picker=file_picker, parent=parent)

    return create


PAGE_REGISTRY: tuple[PageNavigation, ...] = (
    PageNavigation("home", "Home / Workspace", "Home", "Choose and summarize a workspace.", 10, _factory(HomeWorkspacePage)),
    PageNavigation("connections", "Connections / Diagnostics", "Connect", "Connect fake devices and inspect diagnostics.", 20, _factory(ConnectionsDiagnosticsPage)),
    PageNavigation("calibration", "Calibration", "Calibrate", "Collect demo samples and review a fit.", 30, _factory(CalibrationPage)),
    PageNavigation("geometry", "Video Geometry / Marker Setup", "Video", "Set geometry manually or detect a demo marker.", 40, _factory(VideoGeometryMarkerSetupPage)),
    PageNavigation("experiment", "Experiment Setup / Readiness", "Prepare", "Configure a demo and evaluate readiness.", 50, _factory(ExperimentSetupReadinessPage)),
    PageNavigation("live-run", "Live Run", "Run", "Preview deterministic telemetry and control a fake run.", 60, _factory(LiveRunPage)),
    PageNavigation("analysis", "Analysis", "Analyze", "Review recorded-file or live-capture demo analysis.", 70, _factory(AnalysisPage)),
    PageNavigation("settings", "Settings / Profiles / Help", "Settings", "Use session-only settings and read help.", 80, _factory(SettingsProfilesHelpPage)),
)

_PAGES_BY_KEY = {page.key: page for page in PAGE_REGISTRY}


def page_navigation() -> tuple[PageNavigation, ...]:
    """Return ordered shell-neutral navigation metadata."""

    return PAGE_REGISTRY


def page_for_key(key: str) -> PageNavigation:
    """Look up metadata for a shell navigation selection."""

    return _PAGES_BY_KEY[key]
