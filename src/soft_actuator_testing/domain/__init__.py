"""Qt-free domain models and scientific logic."""
"""Qt-free domain models and pure scientific workflow contracts."""

from .analysis import AnalysisFrameResult, DetectionState, MarkerCandidate, MarkerDetectionResult
from .artifacts import ArtifactIdentity, ArtifactMetadata, ArtifactType, Unit, require_supported_schema_version
from .calibration import (
    CalibrationFit,
    CalibrationModel,
    CalibrationModelType,
    CalibrationSample,
    FitAdequacy,
    FitQualityPolicy,
    FitResidual,
    VoltageDomain,
    apply_calibration,
    fit_calibration,
)
from .geometry import FrameSize, NormalizedRoi, PixelPoint, VideoGeometry
from .run_state import RunCompletion, RunSnapshot, RunState, finalize_run, request_stop, transition

__all__ = [
    "AnalysisFrameResult",
    "ArtifactIdentity",
    "ArtifactMetadata",
    "ArtifactType",
    "CalibrationFit",
    "CalibrationModel",
    "CalibrationModelType",
    "CalibrationSample",
    "DetectionState",
    "FitAdequacy",
    "FitQualityPolicy",
    "FitResidual",
    "FrameSize",
    "MarkerCandidate",
    "MarkerDetectionResult",
    "NormalizedRoi",
    "PixelPoint",
    "RunCompletion",
    "RunSnapshot",
    "RunState",
    "Unit",
    "VideoGeometry",
    "VoltageDomain",
    "apply_calibration",
    "finalize_run",
    "fit_calibration",
    "request_stop",
    "require_supported_schema_version",
    "transition",
]
