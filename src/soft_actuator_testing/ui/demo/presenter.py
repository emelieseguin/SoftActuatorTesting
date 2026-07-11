"""Wire deterministic demo adapters behind application presenter contracts."""

from __future__ import annotations

from soft_actuator_testing.application.presentation import PresenterSession, WorkflowController

from .state import DemoEnvironment, build_demo_environment


def build_demo_controller(environment: DemoEnvironment | None = None) -> WorkflowController:
    environment = environment or build_demo_environment()
    services = environment.services
    return WorkflowController(
        serial=services.serial,
        camera=services.camera,
        detector=services.detector,
        lifecycle=services.run_lifecycle,
        analysis=services.analysis,
        geometry=environment.geometry,
        calibration_fit=environment.calibration_fit,
        calibration_samples=environment.calibration_samples,
    )


def build_demo_presenter(environment: DemoEnvironment | None = None) -> PresenterSession:
    return build_demo_controller(environment).session()


__all__ = ["build_demo_controller", "build_demo_presenter"]
