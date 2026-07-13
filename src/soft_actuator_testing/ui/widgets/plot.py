"""Project-owned PyQtGraph plot wrapper.

Per ADR 0001, PyQtGraph is used only behind this project-owned widget — no
presenter or domain/application code imports ``pyqtgraph`` directly. Series
colors and grid/axis colors come only from :mod:`soft_actuator_testing.ui.themes.tokens`.

Because a plot's visible data cannot be conveyed by a screen reader from the
rendered curves alone, this widget keeps an always-current text
``accessibleDescription`` summarizing the plotted series and point counts,
mirroring the pattern used by :class:`~soft_actuator_testing.ui.widgets.video_canvas.VideoCanvas`.
"""

from __future__ import annotations

from collections.abc import Sequence

import pyqtgraph as pg
from PySide6.QtWidgets import QWidget

from soft_actuator_testing.ui.themes.qt_bridge import to_qcolor
from soft_actuator_testing.ui.themes.tokens import Theme


class PlotCanvas(pg.PlotWidget):
    """A themed line-plot widget for live telemetry and analysis review.

    Series are referenced by name so callers can update/replace a curve's
    data (for example on each telemetry sample) without re-creating plot
    items, keeping decimated live updates cheap (see ADR 0003).
    """

    def __init__(
        self,
        *,
        title: str | None = None,
        x_label: str | None = None,
        y_label: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent=parent)
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._theme: Theme | None = None
        self._title = title or "Plot"
        self.showGrid(x=True, y=True, alpha=0.3)
        self.setAccessibleName(self._title)
        if title:
            self.setTitle(title)
        if x_label:
            self.setLabel("bottom", x_label)
        if y_label:
            self.setLabel("left", y_label)
        self._update_accessible_description()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        chart = theme.chart
        self.setBackground(to_qcolor(chart.background))
        axis_color = to_qcolor(chart.axis)
        for axis_name in ("left", "bottom"):
            axis = self.getAxis(axis_name)
            axis.setPen(axis_color)
            axis.setTextPen(axis_color)
        for index, name in enumerate(self._curves):
            self._curves[name].setPen(to_qcolor(chart.series_color(index)))

    def series_names(self) -> tuple[str, ...]:
        return tuple(self._curves.keys())

    def set_series(self, name: str, x: Sequence[float], y: Sequence[float]) -> None:
        """Create or update the named series with new deterministic data."""

        if len(x) != len(y):
            raise ValueError("x and y must have the same length")
        if name not in self._curves:
            index = len(self._curves)
            color = (
                to_qcolor(self._theme.chart.series_color(index))
                if self._theme is not None
                else None
            )
            self._curves[name] = self.plot(list(x), list(y), pen=color, name=name)
        else:
            self._curves[name].setData(list(x), list(y))
        self._update_accessible_description()

    def clear_series(self) -> None:
        for curve in self._curves.values():
            self.removeItem(curve)
        self._curves.clear()
        self._update_accessible_description()

    def _update_accessible_description(self) -> None:
        if not self._curves:
            text = f"{self._title}: no data plotted yet."
        else:
            parts = []
            for name, curve in self._curves.items():
                x_data, _ = curve.getData()
                count = 0 if x_data is None else len(x_data)
                parts.append(f"{name} ({count} point{'s' if count != 1 else ''})")
            text = f"{self._title}: " + ", ".join(parts)
        self.setAccessibleDescription(text)
