"""Shell-independent workflow pages and their shared navigation registry."""

from .base import PageScenario, WorkflowPage
from .home_workspace import HomeWorkspaceView
from .workflows import (
    AnalysisPage,
    CalibrationPage,
    ConnectionsDiagnosticsPage,
    ExperimentSetupReadinessPage,
    HomeWorkspacePage,
    LiveRunPage,
    SettingsProfilesHelpPage,
    VideoGeometryMarkerSetupPage,
)
from .registry import PAGE_REGISTRY, PageNavigation, page_for_key, page_navigation

__all__ = [
    "AnalysisPage",
    "CalibrationPage",
    "ConnectionsDiagnosticsPage",
    "ExperimentSetupReadinessPage",
    "HomeWorkspacePage",
    "HomeWorkspaceView",
    "LiveRunPage",
    "PAGE_REGISTRY",
    "PageNavigation",
    "PageScenario",
    "SettingsProfilesHelpPage",
    "VideoGeometryMarkerSetupPage",
    "WorkflowPage",
    "page_for_key",
    "page_navigation",
]
