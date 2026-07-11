"""Compatibility exports for the former monolithic workflow page module."""

from .base import PageScenario, WorkflowPage
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

__all__ = [
    "AnalysisPage",
    "CalibrationPage",
    "ConnectionsDiagnosticsPage",
    "ExperimentSetupReadinessPage",
    "HomeWorkspacePage",
    "LiveRunPage",
    "PageScenario",
    "SettingsProfilesHelpPage",
    "VideoGeometryMarkerSetupPage",
    "WorkflowPage",
]
