"""Tests for the project-owned PyQtGraph plot wrapper."""

from __future__ import annotations

from soft_actuator_testing.ui.themes.tokens import DARK_THEME
from soft_actuator_testing.ui.widgets.plot import PlotCanvas


def test_plot_canvas_construction_sets_title_and_labels(qtbot) -> None:
    plot = PlotCanvas(title="Telemetry", x_label="Time (s)", y_label="Volts")
    qtbot.addWidget(plot)
    assert plot.accessibleName() == "Telemetry"


def test_set_series_creates_then_updates_named_curve(qtbot) -> None:
    plot = PlotCanvas()
    qtbot.addWidget(plot)
    plot.set_series("volts", [0, 1, 2], [0.1, 0.2, 0.3])
    assert plot.series_names() == ("volts",)

    plot.set_series("volts", [0, 1, 2, 3], [0.1, 0.2, 0.3, 0.4])
    assert plot.series_names() == ("volts",)  # still one curve, not duplicated


def test_set_series_rejects_mismatched_lengths(qtbot) -> None:
    import pytest

    plot = PlotCanvas()
    qtbot.addWidget(plot)
    with pytest.raises(ValueError):
        plot.set_series("bad", [0, 1], [0.1])


def test_apply_theme_colors_series_from_chart_palette(qtbot) -> None:
    plot = PlotCanvas()
    qtbot.addWidget(plot)
    plot.set_series("a", [0, 1], [0, 1])
    plot.set_series("b", [0, 1], [1, 0])
    plot.apply_theme(DARK_THEME)
    # Two series should receive two distinct theme series colors.
    assert len(plot.series_names()) == 2


def test_clear_series_removes_all_curves(qtbot) -> None:
    plot = PlotCanvas()
    qtbot.addWidget(plot)
    plot.set_series("a", [0, 1], [0, 1])
    plot.clear_series()
    assert plot.series_names() == ()
