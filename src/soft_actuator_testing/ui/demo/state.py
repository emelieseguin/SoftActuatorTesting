"""Deterministic demo state: everything a later screen needs to render.

:func:`build_demo_environment` wires the fakes in this package into one
immutable-enough bundle so any screen/presenter can be developed and shown
before real hardware/persistence adapters exist, per ADR 0001's shared
demo-mode requirement (also used by both prototype shells' evaluation in
ADR 0005). Nothing here depends on wall-clock time, randomness, or real I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from soft_actuator_testing.domain.calibration import (
    CalibrationFit,
    CalibrationModelType,
    CalibrationSample,
    fit_calibration,
)
from soft_actuator_testing.domain.geometry import VideoGeometry
from soft_actuator_testing.domain.run_state import RunSnapshot
from soft_actuator_testing.ui.demo.fake_services import (
    FakeAnalysisService,
    FakeArtifactStore,
    FakeCameraService,
    FakeMarkerDetector,
    FakeRunLifecycleService,
    FakeSerialService,
    demo_video_geometry,
)
from soft_actuator_testing.ui.widgets.notifications import Notification
from soft_actuator_testing.ui.themes.tokens import SemanticState

_DEMO_CALIBRATION_SAMPLES = (
    CalibrationSample(known_pressure_kpa=0.0, measured_voltage=0.5),
    CalibrationSample(known_pressure_kpa=50.0, measured_voltage=1.5),
    CalibrationSample(known_pressure_kpa=100.0, measured_voltage=2.5),
)


def demo_calibration_fit() -> CalibrationFit:
    return fit_calibration(_DEMO_CALIBRATION_SAMPLES, CalibrationModelType.LINEAR)


def demo_notifications() -> tuple[Notification, ...]:
    return (
        Notification(
            message="Demo mode: hardware is disconnected; every panel uses fake data.",
            severity=SemanticState.INFO,
            dismissible=False,
        ),
    )


@dataclass
class DemoServices:
    """One instance of every fake service, so screens depend on one bundle."""

    serial: FakeSerialService = field(default_factory=FakeSerialService)
    camera: FakeCameraService = field(default_factory=FakeCameraService)
    detector: FakeMarkerDetector = field(default_factory=FakeMarkerDetector)
    run_lifecycle: FakeRunLifecycleService = field(default_factory=FakeRunLifecycleService)
    analysis: FakeAnalysisService = field(default_factory=FakeAnalysisService)
    artifact_store: FakeArtifactStore = field(default_factory=FakeArtifactStore)


@dataclass(frozen=True)
class DemoEnvironment:
    """A deterministic, fully wired demo snapshot for building/showing screens."""

    services: DemoServices
    geometry: VideoGeometry
    calibration_fit: CalibrationFit
    calibration_samples: tuple[CalibrationSample, ...]
    notifications: tuple[Notification, ...]

    @property
    def run_snapshot(self) -> RunSnapshot:
        """Compatibility projection of the one authoritative lifecycle source."""

        return self.services.run_lifecycle.snapshot()


def build_demo_environment() -> DemoEnvironment:
    """Build one deterministic demo environment (fresh instance per call)."""

    services = DemoServices()
    return DemoEnvironment(
        services=services,
        geometry=demo_video_geometry(),
        calibration_fit=demo_calibration_fit(),
        calibration_samples=_DEMO_CALIBRATION_SAMPLES,
        notifications=demo_notifications(),
    )
