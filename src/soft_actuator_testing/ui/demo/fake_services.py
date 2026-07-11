"""Deterministic fake services implementing the Qt-free application protocols.

Every later screen must be able to render fully without any real hardware or
persistence adapter (serial controller, camera, artifact store). This module
provides in-memory, deterministic doubles for every protocol in
``application.services`` so screens/presenters can be built and demoed before
``serial-integration``, ``camera-integration``, and ``artifact-compatibility``
land. Nothing here performs real I/O, threading, blocking waits, randomness,
or wall-clock timing — every sequence is fixed so tests and demo runs are
reproducible.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from soft_actuator_testing.application.services import (
    AnalysisFrameResult,
    ArtifactDocument,
    CameraFrame,
    MarkerDetectionResult,
    SerialTelemetry,
)
from soft_actuator_testing.domain.analysis import DetectionState, actuator_angle_degrees
from soft_actuator_testing.domain.artifacts import ArtifactIdentity, ArtifactMetadata, ArtifactType
from soft_actuator_testing.domain.geometry import FrameSize, NormalizedRoi, PixelPoint, VideoGeometry
from soft_actuator_testing.domain.run_state import (
    RunCompletion,
    RunSnapshot,
    RunState,
    TransitionResult,
    finalize_run,
    request_stop,
    transition,
)

_FIXED_TIMESTAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


def demo_frame_size() -> FrameSize:
    return FrameSize(width=64, height=48)


def demo_video_geometry() -> VideoGeometry:
    frame_size = demo_frame_size()
    return VideoGeometry(
        frame_size=frame_size,
        base_point=PixelPoint(8.0, 40.0),
        initial_tip_point=PixelPoint(32.0, 12.0),
        actuator_roi=NormalizedRoi(4.0, 4.0, 60.0, 44.0),
    )


class FakeSerialService:
    """Deterministic serial double: fixed telemetry, no real port access."""

    def __init__(self, sample_count: int = 20) -> None:
        self._connected = False
        self.sent_commands: list[str] = []
        self._sample_count = sample_count

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def send_command(self, command: str) -> None:
        self.sent_commands.append(command)

    def telemetry(self) -> Iterator[SerialTelemetry]:
        for index in range(self._sample_count):
            timestamp = index * 0.05
            volts = 1.0 + 0.25 * np.sin(index / 3.0)
            yield SerialTelemetry(
                timestamp_seconds=timestamp,
                volts=float(volts),
                raw_line=f"V,{timestamp:.2f},{volts:.4f}",
            )


class FakeCameraService:
    """Deterministic camera double producing synthetic RGB frames."""

    def __init__(self, frame_count: int = 8) -> None:
        self._opened = False
        self._closed = False
        self._frame_count = frame_count

    def open(self) -> FrameSize:
        self._opened = True
        self._closed = False
        return demo_frame_size()

    def frames(self) -> Iterator[CameraFrame]:
        size = demo_frame_size()
        for index in range(self._frame_count):
            image = _synthetic_frame(size, index)
            yield CameraFrame(frame_index=index, timestamp_seconds=index / 30.0, image=image)

    def close(self) -> None:
        self._closed = True

    @property
    def is_open(self) -> bool:
        return self._opened and not self._closed


def _synthetic_frame(size: FrameSize, index: int) -> np.ndarray:
    """Return a small deterministic HxWx3 uint8 RGB gradient frame."""

    row = np.linspace(0, 255, size.height, dtype=np.uint8).reshape(-1, 1)
    col = np.linspace(0, 255, size.width, dtype=np.uint8).reshape(1, -1)
    red = np.broadcast_to(row, (size.height, size.width))
    green = np.broadcast_to(col, (size.height, size.width))
    blue = np.full((size.height, size.width), (index * 32) % 256, dtype=np.uint8)
    return np.stack([red, green, blue], axis=-1).astype(np.uint8)


class FakeMarkerDetector:
    """Deterministic detector cycling detected/held/missing by frame parity."""

    def detect(self, frame: CameraFrame, geometry: VideoGeometry) -> MarkerDetectionResult:
        cycle = frame.frame_index % 4
        if cycle == 3:
            return MarkerDetectionResult.missing()
        base_tip = geometry.initial_tip_point or PixelPoint(
            geometry.actuator_roi.left, geometry.actuator_roi.top
        )
        point = PixelPoint(base_tip.x + frame.frame_index, base_tip.y)
        if cycle == 2:
            return MarkerDetectionResult.held(point, confidence=0.5)
        state = DetectionState.DETECTED
        return MarkerDetectionResult(state=state, point=point, confidence=0.9)


class FakeRunLifecycleService:
    """Deterministic run lifecycle double built on the pure domain state machine."""

    def __init__(self) -> None:
        self._snapshot = RunSnapshot(RunState.DISCONNECTED)

    def readiness(self) -> bool:
        return self._snapshot.state is RunState.READY

    def _apply(self, result: TransitionResult) -> TransitionResult:
        self._snapshot = result.snapshot
        return result

    def connect(self) -> TransitionResult:
        return self._apply(transition(self._snapshot, RunState.CONNECTING))

    def mark_idle(self) -> TransitionResult:
        return self._apply(transition(self._snapshot, RunState.IDLE))

    def mark_ready(self) -> TransitionResult:
        return self._apply(transition(self._snapshot, RunState.READY))

    def begin_start(self) -> TransitionResult:
        return self._apply(transition(self._snapshot, RunState.STARTING))

    def mark_running(self) -> TransitionResult:
        return self._apply(transition(self._snapshot, RunState.RUNNING))

    def mark_fault(self) -> TransitionResult:
        return self._apply(transition(self._snapshot, RunState.FAULT))

    def start(self) -> TransitionResult:
        self.begin_start()
        return self.mark_running()

    def stop(self) -> TransitionResult:
        return self._apply(request_stop(self._snapshot))

    def finalize(self, completion: RunCompletion) -> TransitionResult:
        return self._apply(finalize_run(self._snapshot, completion))

    def disconnect(self) -> TransitionResult:
        if self._snapshot.state is RunState.DISCONNECTED:
            return TransitionResult(self._snapshot, idempotent=True)
        if self._snapshot.state is RunState.CONNECTING:
            self._snapshot = RunSnapshot(RunState.FAULT)
        if self._snapshot.state is RunState.READY:
            self._apply(transition(self._snapshot, RunState.IDLE))
        if self._snapshot.state in {
            RunState.STARTING,
            RunState.RUNNING,
            RunState.STOPPING,
        }:
            raise RuntimeError("active runs must be finalized before disconnect")
        return self._apply(transition(self._snapshot, RunState.DISCONNECTED))

    def snapshot(self) -> RunSnapshot:
        return self._snapshot


class FakeAnalysisService:
    """Deterministic analysis double producing a fixed set of frame results."""

    def __init__(self, detector: FakeMarkerDetector | None = None, frame_count: int = 6) -> None:
        self._detector = detector or FakeMarkerDetector()
        self._frame_count = frame_count

    def analyze(self, source_video: Path, geometry: VideoGeometry) -> Iterator[AnalysisFrameResult]:
        for index in range(self._frame_count):
            frame = CameraFrame(frame_index=index, timestamp_seconds=index / 30.0, image=None)
            detection = self._detector.detect(frame, geometry)
            angle = (
                None
                if detection.point is None
                else actuator_angle_degrees(geometry.base_point, detection.point)
            )
            yield AnalysisFrameResult(
                frame_index=index,
                video_time_seconds=frame.timestamp_seconds,
                detection=detection,
                actuator_angle_degrees=angle,
            )


@dataclass
class FakeArtifactStore:
    """In-memory artifact double: no filesystem I/O, deterministic identities."""

    _documents: dict[tuple[ArtifactType, str], ArtifactDocument] = field(default_factory=dict)
    exported: list[tuple[str, Path]] = field(default_factory=list)

    def load(self, artifact_type: ArtifactType, artifact_id: str) -> ArtifactDocument:
        key = (artifact_type, artifact_id)
        if key not in self._documents:
            raise KeyError(f"no demo artifact {artifact_type.value}/{artifact_id}")
        return self._documents[key]

    def save(self, document: ArtifactDocument) -> None:
        identity = document.metadata.identity
        self._documents[(identity.artifact_type, identity.artifact_id)] = document

    def import_legacy(self, source: Path, artifact_type: ArtifactType) -> ArtifactDocument:
        identity = ArtifactIdentity.new(artifact_type)
        metadata = ArtifactMetadata(identity, _FIXED_TIMESTAMP, _FIXED_TIMESTAMP)
        document = ArtifactDocument(metadata=metadata, payload={"legacy_source": str(source)})
        self.save(document)
        return document

    def export_legacy(self, document: ArtifactDocument, destination: Path) -> None:
        # The demo double never writes real files; it only records intent so
        # tests can assert an export was requested for a given document.
        self.exported.append((document.metadata.identity.artifact_id, destination))
