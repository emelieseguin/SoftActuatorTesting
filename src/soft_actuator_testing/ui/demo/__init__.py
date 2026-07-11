"""Deterministic fake/demo services and state so screens render without hardware.

Everything under ``ui.demo`` is a UI-owned test/demo double for the
Qt-free ``application.services`` protocols (see ADR 0001) — it must never be
imported from ``domain/`` or ``application/``, only from ``ui/`` and its
tests.
"""

from __future__ import annotations

from .fake_services import (
    FakeAnalysisService,
    FakeArtifactStore,
    FakeCameraService,
    FakeMarkerDetector,
    FakeRunLifecycleService,
    FakeSerialService,
    demo_frame_size,
    demo_video_geometry,
)
from .state import (
    DemoEnvironment,
    DemoServices,
    build_demo_environment,
    demo_calibration_fit,
    demo_notifications,
)
from .presenter import build_demo_controller, build_demo_presenter

__all__ = [
    "DemoEnvironment",
    "DemoServices",
    "FakeAnalysisService",
    "FakeArtifactStore",
    "FakeCameraService",
    "FakeMarkerDetector",
    "FakeRunLifecycleService",
    "FakeSerialService",
    "build_demo_environment",
    "build_demo_controller",
    "build_demo_presenter",
    "demo_calibration_fit",
    "demo_frame_size",
    "demo_notifications",
    "demo_video_geometry",
]
