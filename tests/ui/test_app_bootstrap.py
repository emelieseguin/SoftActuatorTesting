"""Tests for the safe app bootstrap and retained foundation smoke window.

These tests exercise shell selection and reusable foundation wiring without
opening a native dialog or touching real hardware.
"""

from __future__ import annotations

from soft_actuator_testing.domain.run_state import RunState
from soft_actuator_testing.ui.app import (
    DEFAULT_SHELL,
    EXPERIMENT_STUDIO_PROTOTYPE,
    _build_foundation_window,
    create_application_window,
)
from soft_actuator_testing.ui.demo import build_demo_environment
from soft_actuator_testing.ui.shells.experiment_studio import ExperimentStudioWindow
from soft_actuator_testing.ui.shells.instrument_console import InstrumentConsoleWindow
from soft_actuator_testing.ui.widgets import NotificationCenter, PlotCanvas, StatusIndicator, VideoCanvas
from soft_actuator_testing.ui.production import create_production_composition


def test_foundation_window_builds_with_demo_environment(qtbot) -> None:
    env = build_demo_environment()
    window = _build_foundation_window(env)
    qtbot.addWidget(window)
    assert window.windowTitle() == "Soft Actuator Testing — UI foundation"
    assert window.centralWidget() is not None


def test_foundation_window_contains_every_foundation_widget_type(qtbot) -> None:
    window = _build_foundation_window()
    qtbot.addWidget(window)
    central = window.centralWidget()

    assert central.findChild(NotificationCenter) is not None
    assert central.findChildren(StatusIndicator)
    assert central.findChild(PlotCanvas) is not None
    assert central.findChild(VideoCanvas) is not None


def test_foundation_window_plot_has_demo_pressure_series(qtbot) -> None:
    window = _build_foundation_window()
    qtbot.addWidget(window)
    plot = window.centralWidget().findChild(PlotCanvas)
    assert plot.series_names() == ("pressure",)


def test_foundation_window_video_canvas_shows_demo_frame(qtbot) -> None:
    window = _build_foundation_window()
    qtbot.addWidget(window)
    video = window.centralWidget().findChild(VideoCanvas)
    assert "synthetic demo gradient frame" in video.accessibleDescription()


def test_default_application_window_is_selected_instrument_console(qtbot) -> None:
    window = create_application_window()
    qtbot.addWidget(window)

    assert DEFAULT_SHELL == "instrument-console"
    assert isinstance(window, InstrumentConsoleWindow)


def test_rejected_studio_requires_explicit_prototype_selection(qtbot) -> None:
    window = create_application_window(prototype_shell=EXPERIMENT_STUDIO_PROTOTYPE)
    qtbot.addWidget(window)

    assert isinstance(window, ExperimentStudioWindow)


def test_both_shell_choices_construct_without_hardware_access(qtbot) -> None:
    windows = (
        create_application_window(),
        create_application_window(prototype_shell=EXPERIMENT_STUDIO_PROTOTYPE),
    )

    for window in windows:
        qtbot.addWidget(window)
        services = window.environment.services
        assert services.serial.is_connected is False
        assert services.camera.is_open is False
        assert services.run_lifecycle.snapshot().state is RunState.DISCONNECTED


def test_production_composition_binds_console_without_opening_hardware(qtbot) -> None:
    composition = create_production_composition()
    qtbot.addWidget(composition.window)

    assert "Production" in composition.window.windowTitle()
    assert composition.run_controller.snapshot.lifecycle.state is RunState.DISCONNECTED
    composition.window.trigger_global_stop()
    composition.window.close()
