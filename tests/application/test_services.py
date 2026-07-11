"""Contract tests for adapter-facing services without UI or device adapters."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from soft_actuator_testing.application.services import (
    AnalysisService,
    CameraFrame,
    MarkerDetector,
    SerialService,
    SerialTelemetry,
)
from soft_actuator_testing.domain.analysis import AnalysisFrameResult, MarkerDetectionResult
from soft_actuator_testing.domain.geometry import FrameSize, VideoGeometry


class FakeSerial:
    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def send_command(self, command: str) -> None: ...

    def telemetry(self) -> Iterator[SerialTelemetry]:
        return iter(())


class FakeDetector:
    def detect(self, frame: CameraFrame, geometry: VideoGeometry) -> MarkerDetectionResult:
        return MarkerDetectionResult.missing()


class FakeAnalysis:
    def analyze(self, source_video: Path, geometry: VideoGeometry) -> Iterator[AnalysisFrameResult]:
        return iter(())


def test_future_adapter_protocols_are_runtime_checkable_without_qt() -> None:
    assert isinstance(FakeSerial(), SerialService)
    assert isinstance(FakeDetector(), MarkerDetector)
    assert isinstance(FakeAnalysis(), AnalysisService)
    assert FrameSize(1, 1).width == 1
