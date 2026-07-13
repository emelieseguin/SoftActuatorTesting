"""Hardware-disconnected Qt application entry point.

ADR 0005 selects Instrument Console as the normal shell. Experiment Studio is
retained only as an explicit development prototype; both still use the same
deterministic demo environment and never construct hardware adapters.
"""

from __future__ import annotations

DEFAULT_SHELL = "instrument-console"
EXPERIMENT_STUDIO_PROTOTYPE = "experiment-studio"


def _run_state_label(run_state) -> str:
    return run_state.value.replace("_", " ").title()


def _build_foundation_window(env=None) -> QMainWindow:
    """Assemble one window exercising the foundation with deterministic demo data."""

    from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

    from soft_actuator_testing.ui.demo import build_demo_environment
    from soft_actuator_testing.ui.presenters import SnapshotStore, bind_text
    from soft_actuator_testing.ui.themes import DARK_THEME, SemanticState
    from soft_actuator_testing.ui.widgets import NotificationCenter, PlotCanvas, StatusIndicator, VideoCanvas

    demo_environment = env or build_demo_environment()

    window = QMainWindow()
    window.setWindowTitle("Soft Actuator Testing — UI foundation")

    central = QWidget()
    layout = QVBoxLayout(central)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(12)

    notifications = NotificationCenter()
    notifications.apply_theme(DARK_THEME)
    for notification in demo_environment.notifications:
        notifications.notify(notification)
    layout.addWidget(notifications)

    connection_status = StatusIndicator("Connection")
    connection_status.apply_theme(DARK_THEME)
    connection_status.set_state(SemanticState.NEUTRAL)
    layout.addWidget(connection_status)

    run_store: SnapshotStore = SnapshotStore(demo_environment.run_snapshot)
    run_label_holder = StatusIndicator("Run state")
    run_label_holder.apply_theme(DARK_THEME)
    bind_text(
        run_store,
        lambda text: run_label_holder.setAccessibleDescription(f"Run state: {text}"),
        lambda snapshot: _run_state_label(snapshot.state),
    )
    layout.addWidget(run_label_holder)

    plot = PlotCanvas(title="Demo telemetry (kPa)", x_label="Time (s)", y_label="Pressure (kPa)")
    plot.apply_theme(DARK_THEME)
    telemetry = list(demo_environment.services.serial.telemetry())
    times = [sample.timestamp_seconds for sample in telemetry]
    pressures = [
        demo_environment.calibration_fit.model.apply(sample.volts, require_in_domain=False)
        for sample in telemetry
    ]
    plot.set_series("pressure", times, pressures)
    layout.addWidget(plot, stretch=1)

    video = VideoCanvas(accessible_title="Demo camera preview")
    demo_environment.services.camera.open()
    first_frame = next(iter(demo_environment.services.camera.frames()))
    video.set_frame(
        first_frame.image,
        frame_index=first_frame.frame_index,
        frame_count=8,
        description="synthetic demo gradient frame",
    )
    demo_environment.services.camera.close()
    layout.addWidget(video, stretch=1)

    window.setCentralWidget(central)
    window.resize(720, 640)
    return window


def create_application_window(
    *,
    prototype_shell: str | None = None,
    production: bool = False,
) -> object:
    """Create the selected demo-backed shell without constructing hardware adapters.

    ``production`` and ``prototype_shell`` are mutually exclusive: prototype
    shells (currently only Experiment Studio) are always a demo-only
    development comparison and never a second production shell (see ADR
    0005). The CLI (``bootstrap.py``) rejects this combination before it ever
    reaches this function; this check is a second, programmatic-caller-facing
    guard so the precedence stays unambiguous regardless of entry point.
    """

    if production and prototype_shell is not None:
        raise ValueError(
            f"production=True is incompatible with prototype_shell={prototype_shell!r}: "
            "prototype shells are a demo-only development comparison, never a second "
            "production shell. Pass only one of production=True or a prototype_shell."
        )
    if production:
        from soft_actuator_testing.ui.production import create_production_composition

        return create_production_composition().window
    from soft_actuator_testing.ui.shells.experiment_studio import (
        ExperimentStudioWindow,
        create_experiment_studio_shell,
    )
    from soft_actuator_testing.ui.shells.instrument_console import (
        InstrumentConsoleWindow,
        create_instrument_console_shell,
    )

    if prototype_shell is None:
        return create_instrument_console_shell()
    if prototype_shell == EXPERIMENT_STUDIO_PROTOTYPE:
        return create_experiment_studio_shell()
    raise ValueError(f"unknown prototype shell: {prototype_shell}")


def run_application(*, prototype_shell: str | None = None, production: bool = False) -> int:
    """Show the selected shell using deterministic, hardware-disconnected services."""

    from PySide6.QtWidgets import QApplication

    from soft_actuator_testing.ui.themes import DARK_THEME, LIGHT_THEME, apply_theme

    application = QApplication.instance() or QApplication([])
    application.setApplicationName("Soft Actuator Testing")
    theme = LIGHT_THEME if prototype_shell == EXPERIMENT_STUDIO_PROTOTYPE else DARK_THEME
    apply_theme(application, theme)

    window = create_application_window(prototype_shell=prototype_shell, production=production)
    window.show()
    return application.exec()
