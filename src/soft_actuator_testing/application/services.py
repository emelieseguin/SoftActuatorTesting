"""Qt-free protocols implemented later by artifact and hardware adapters."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from soft_actuator_testing.domain.analysis import AnalysisFrameResult, MarkerDetectionResult
from soft_actuator_testing.domain.artifacts import ArtifactIdentity, ArtifactMetadata, ArtifactType
from soft_actuator_testing.domain.geometry import FrameSize, PixelPoint, VideoGeometry
from soft_actuator_testing.domain.run_state import RunCompletion, RunSnapshot, TransitionResult


@dataclass(frozen=True)
class ArtifactDocument:
    """A validated versioned document passed to persistence adapters."""

    metadata: ArtifactMetadata
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class SerialTelemetry:
    timestamp_seconds: float
    volts: float | None
    raw_line: str


@dataclass(frozen=True)
class CameraFrame:
    frame_index: int
    timestamp_seconds: float
    image: Any


@runtime_checkable
class ArtifactStore(Protocol):
    """Version-aware persistence boundary; implementations own filesystem I/O."""

    def load(self, artifact_type: ArtifactType, artifact_id: str) -> ArtifactDocument: ...

    def save(self, document: ArtifactDocument) -> None: ...

    def import_legacy(self, source: Path, artifact_type: ArtifactType) -> ArtifactDocument: ...

    def export_legacy(self, document: ArtifactDocument, destination: Path) -> None: ...


@runtime_checkable
class SerialService(Protocol):
    """Single-owner serial boundary for future controller adapters."""

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def send_command(self, command: str) -> None: ...

    def telemetry(self) -> Iterator[SerialTelemetry]: ...


@runtime_checkable
class CameraService(Protocol):
    """Single-owner camera boundary; no GUI image types cross it."""

    def open(self) -> FrameSize: ...

    def frames(self) -> Iterator[CameraFrame]: ...

    def close(self) -> None: ...


@runtime_checkable
class MarkerDetector(Protocol):
    """Project-owned detector interface returning plain domain results."""

    def detect(self, frame: CameraFrame, geometry: VideoGeometry) -> MarkerDetectionResult: ...


@runtime_checkable
class RunLifecycleService(Protocol):
    """Application-owned lifecycle coordinator with an idempotent finalizer."""

    def readiness(self) -> bool: ...

    def connect(self) -> TransitionResult: ...

    def mark_idle(self) -> TransitionResult: ...

    def mark_ready(self) -> TransitionResult: ...

    def begin_start(self) -> TransitionResult: ...

    def mark_running(self) -> TransitionResult: ...

    def mark_fault(self) -> TransitionResult: ...

    def start(self) -> TransitionResult: ...

    def stop(self) -> TransitionResult: ...

    def finalize(self, completion: RunCompletion) -> TransitionResult: ...

    def disconnect(self) -> TransitionResult: ...

    def snapshot(self) -> RunSnapshot: ...


@runtime_checkable
class AnalysisService(Protocol):
    """Batch-analysis boundary for finalized video artifacts."""

    def analyze(self, source_video: Path, geometry: VideoGeometry) -> Iterator[AnalysisFrameResult]: ...


@runtime_checkable
class CancellationToken(Protocol):
    def is_cancelled(self) -> bool: ...
