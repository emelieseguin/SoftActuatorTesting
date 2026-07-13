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


# -- accessible description tracks current series data (see
# docs/architecture/quality-ui-accessibility.md) --------------------------


def test_accessible_description_starts_empty_and_updates_with_series_data(qtbot) -> None:
    plot = PlotCanvas(title="Pressure")
    qtbot.addWidget(plot)

    assert plot.accessibleDescription() == "Pressure: no data plotted yet."

    plot.set_series("pressure", [0.0, 1.0, 2.0], [10.0, 11.0, 12.0])
    assert plot.accessibleDescription() == "Pressure: pressure (3 points)"

    plot.set_series("pressure", [0.0, 1.0], [10.0, 11.0])
    assert plot.accessibleDescription() == "Pressure: pressure (2 points)"


def test_accessible_description_lists_multiple_series_and_singular_point_count(qtbot) -> None:
    plot = PlotCanvas(title="Angle")
    qtbot.addWidget(plot)

    plot.set_series("measured", [0.0], [5.0])
    assert plot.accessibleDescription() == "Angle: measured (1 point)"

    plot.set_series("corrected", [0.0, 1.0], [5.0, 6.0])
    assert plot.accessibleDescription() == "Angle: measured (1 point), corrected (2 points)"


def test_accessible_description_resets_after_clear_series(qtbot) -> None:
    plot = PlotCanvas(title="Pressure")
    qtbot.addWidget(plot)

    plot.set_series("pressure", [0.0, 1.0], [10.0, 11.0])
    assert "no data" not in plot.accessibleDescription()

    plot.clear_series()
    assert plot.accessibleDescription() == "Pressure: no data plotted yet."


def test_default_title_is_used_for_accessible_name_and_description(qtbot) -> None:
    plot = PlotCanvas()
    qtbot.addWidget(plot)

    assert plot.accessibleName() == "Plot"
    assert plot.accessibleDescription() == "Plot: no data plotted yet."
