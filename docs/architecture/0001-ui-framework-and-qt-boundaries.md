# ADR 0001: UI framework and Qt-free domain/application boundary

**Status:** Accepted.
**Date:** 2026-07-11.
**Related todo:** `architecture-record` (Phase 0) in the approved Unified
SoftActuatorTesting UI Implementation Plan.

## Context

The legacy software is four separate, unrelated interactive experiences: two
Tkinter GUIs (`PressureCalibration.py`, `DataCollection-V2.py`), one OpenCV
first-frame annotation tool (`VideoConfig.py`), and one Jupyter/OpenCV
analysis notebook — see
[`../initial-implementation/README.md`](../initial-implementation/README.md).
None separate scientific/device logic from GUI event handlers: calibration
math, serial parsing, and geometry validation live inside Tkinter callbacks
and module-level globals.

The rewrite combines these workflows into one desktop application (Windows
and Linux) and must also support two competing UI shells built from the same
underlying logic (see
[`0005-ui-shell-evaluation.md`](0005-ui-shell-evaluation.md)), and pure-Python
unit tests that run with no display and no hardware. That requires the
scientific/domain logic to be usable without importing any GUI toolkit.

## Decision

- **UI toolkit:** PySide6 (Qt for Python) with Qt Widgets for the desktop
  shell, dialogs, and controls.
- **Scientific/interactive plotting:** PyQtGraph, wrapped behind
  project-owned plot/video widgets rather than used directly from
  presenters or domain code.
- **Video/image processing:** OpenCV and NumPy (already legacy runtime
  dependencies; see [`dependency-licenses.md`](dependency-licenses.md)).
- **Strict Qt-free core:** `domain/` and `application/` packages must not
  import PySide6, any `Qt*` module, or PyQtGraph. Only `ui/` and explicitly
  named Qt-touching infrastructure adapters may import Qt.

### Package layout and dependency direction

```text
src/soft_actuator_testing/
  __main__.py
  bootstrap.py
  domain/            # Qt-free: models, calibration, geometry,
                     #  marker_detection, angle_analysis, run_state, errors
  application/       # Qt-free: commands, services, workspace_store,
                     #  run_controller, analysis_controller
  infrastructure/     # artifact_store, legacy_import, serial_adapter,
                     #  camera, ffmpeg_recorder, settings, logging
  ui/                # app, presenters/, views/, widgets/, shells/, themes/
tests/
  domain/ application/ infrastructure/ ui/ fixtures/
```

```text
ui           -> application -> domain
infrastructure -> application/domain interfaces
domain       -> Python standard library and numerical primitives only
```

- Domain models and service interfaces are Python `dataclasses`, `enum`s, and
  `typing.Protocol`s — no Qt types.
- Marker detectors implement a project-owned `MarkerDetector` protocol that
  returns plain Python/NumPy results, not Qt types.
- Serial, camera, recorder, file, and any future NI adapter live in
  `infrastructure/` behind protocols owned by `application/`, so they are
  replaceable without touching `ui/`.
- Theme tokens and component behavior are project-owned Python code, not Qt
  Designer resources.

### Python-only source and style construction

- All application code, view construction, and styling stay in Python.
- Do **not** add QML, JavaScript, or Qt Designer-generated `.ui`/`.qrc`
  files. Views are built by hand in Python (`ui/views/`, `ui/widgets/`),
  and theme tokens (spacing, typography, color, elevation) are plain Python
  data (dataclasses/enums/dicts), not a second markup or styling language.
- Do not introduce a second package manager. `uv` and `pyproject.toml` remain
  the single source of truth for dependencies and environment, per
  `AGENTS.md`.

### Enforcement

- Code review must reject any `import PySide6`, `import Qt*`, or
  `import pyqtgraph` inside `domain/` or `application/`.
- `project-scaffold`/`domain-contracts` (Phase 1) should add an automated
  import-boundary test (for example, scanning `domain/` and `application/`
  source for forbidden imports) so the boundary is enforced by
  `uv run pytest`, not only by review. That test does not exist yet — this
  document only records the requirement, since no `src/` tree exists at the
  time of writing.

## Alternatives considered

| Option | Why not chosen |
| --- | --- |
| Keep Tkinter | No native docking, weak theming/accessibility hooks, and no mature interactive-plot/video-overlay widget comparable to PyQtGraph; legacy Tkinter GUIs are exactly what this rewrite replaces. |
| PyQt6 | Near-identical API to PySide6, but PySide6 is the Qt Company's own official binding with LGPLv3-first licensing intent; picking one binding avoids maintaining two nearly-identical adapter layers. |
| Qt Quick / QML | Introduces a second language and a declarative UI file format, which conflicts with the Python-only source/style rule above. |
| Kivy / wxPython / Dear PyGui | Less mature desktop docking, accessibility, and scientific-plotting ecosystem than Qt Widgets + PyQtGraph; no existing project experience with these toolkits. |
| Embedding raw Matplotlib canvases (as the legacy notebook does) | Matplotlib is retained as an optional legacy/notebook dependency, but is not interactive/responsive enough for live telemetry and dockable panels; PyQtGraph is designed for that use case. |

## Consequences

- GUI tests need `pytest-qt` (see [`test-plan.md`](test-plan.md)); domain and
  application tests do not need a display and must stay Qt-free so they can
  run in headless CI.
- Packaging must account for PySide6/Qt's larger runtime footprint and
  licensing terms (see [`dependency-licenses.md`](dependency-licenses.md)).
- Both prototype shells (`prototype-console`, `prototype-studio`) can share
  every domain/application module and swap only `ui/shells/*` and
  `ui/views/*`, which is required for the fair comparison in
  [`0005-ui-shell-evaluation.md`](0005-ui-shell-evaluation.md).
