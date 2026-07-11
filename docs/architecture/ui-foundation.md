# UI foundation implementation status

**Status:** Informational — implemented, keep in sync as later phases add UI
code.
**Date:** 2026-07-11.
**Related todo:** `ui-foundation` (Phase 1) in the approved Unified
SoftActuatorTesting UI Implementation Plan: "Create Python theme tokens,
accessible controls, state binding, file pickers, plot/video wrappers, and
deterministic fake services."

## Scope

This document records what exists under `src/soft_actuator_testing/ui/` so
later phases (`prototype-console`, `prototype-studio`, and every workflow
screen) can build on a single shared, already-tested foundation instead of
re-inventing tokens/controls per shell. It intentionally implements neither
production shell (see
[`0005-ui-shell-evaluation.md`](0005-ui-shell-evaluation.md)), artifact
persistence, hardware adapters, or scientific workflows — those remain
separate todos.

## Module layout

```text
src/soft_actuator_testing/ui/
  app.py                 # safe bootstrap smoke window (not a shell)
  themes/
    tokens.py             # Qt-free dataclasses: color, spacing, typography,
                          #  focus, semantic-state, and chart tokens
    qt_bridge.py           # the only place tokens become QPalette/QFont
  widgets/
    controls.py            # AccessibleButton + FocusRingMixin
    status.py               # StatusIndicator (color + glyph + label)
    notifications.py        # Notification, NotificationBanner, NotificationCenter
    plot.py                  # PlotCanvas (project-owned PyQtGraph wrapper)
    video_canvas.py          # VideoCanvas (frame display + keyboard + overlays)
    file_picker.py           # FilePicker protocol, QtFilePicker, FakeFilePicker
  presenters/
    binding.py               # SnapshotStore, CommandDispatcher, bind_text
  views/
    pages.py                  # shell-independent deterministic workflow pages
    registry.py               # page metadata/factories consumed by both shells
  demo/
    fake_services.py         # deterministic Serial/Camera/Detector/RunLifecycle/
                             #  Analysis/ArtifactStore doubles
    state.py                 # build_demo_environment(): one wired demo bundle
```

## Key decisions

- **No QML/JS/.ui/.qss.** `themes/tokens.py` is plain Python dataclasses with
  no Qt import at all (unit-testable with no display); `themes/qt_bridge.py`
  is the only module that turns tokens into `QPalette`/`QFont`. No widget
  anywhere calls `setStyleSheet` with a QSS string; focus rings, colors, and
  fonts are all set through typed Qt API calls driven by token data.
- **Non-color-only state.** Every `SemanticState` (neutral/info/success/
  warning/error) carries a color *and* a short text glyph *and* a label, and
  `StatusIndicator`/`NotificationBanner` always render and announce the
  label, not just a color.
- **Accessible names by construction.** `AccessibleButton` keeps
  `accessibleName` in sync with its visible text; `StatusIndicator` and
  `NotificationBanner` set both `accessibleName`/`accessibleDescription` to
  human-readable state text.
- **Keyboard alternative for the video canvas.** Mouse dragging is not
  accessible, so `VideoCanvas` exposes Left/Right (±1 frame), Shift+Left/
  Right (±10 frames), and Home/End (first/last) as signals, plus an
  always-current accessible description of the visible frame — independent
  of any mouse interaction. `register_overlay`/unsubscribe lets future
  geometry/marker screens draw on top without subclassing the canvas.
- **File pickers are never called directly.** All Open/Save/choose-folder
  behavior goes through the `FilePicker` protocol; `QtFilePicker` wraps the
  real `QFileDialog` statics (only constructed outside tests), and
  `FakeFilePicker` returns a scripted FIFO queue of results and records every
  call, so no test opens a native dialog.
- **One-way state binding without leaking Qt into `application`/`domain`.**
  `SnapshotStore`/`CommandDispatcher`/`bind_text` live in `ui/presenters/`
  and use a Qt `Signal` internally, but the snapshots and commands they carry
  are plain dataclasses defined by whichever module needs them — the
  `application`/`domain` layers never import Qt to participate in binding.
- **Demo namespace is UI-owned.** `ui/demo/` fakes every
  `application.services` protocol (serial, camera, marker detector, run
  lifecycle, analysis, artifact store) deterministically — fixed sample
  counts, synthetic gradient frames, no randomness, no wall-clock reads, no
  real files. `build_demo_environment()` wires them into one bundle so any
  future screen can render fully before `serial-integration`,
  `camera-integration`, or `artifact-compatibility` land.
- **Bootstrap follows the ADR 0005 selection.** The normal `ui/app.py` entry
  point now creates Instrument Console with deterministic demo services;
  Experiment Studio requires the explicit `--prototype experiment-studio`
  development option. The original foundation-only window remains a focused
  test helper for `NotificationCenter`, bound `StatusIndicator`, `PlotCanvas`,
  and `VideoCanvas`; it is not a third launchable shell.
- **Workflow content remains shell-independent.** `ui.views` provides every
  planned prototype workflow as an ordinary Qt Widgets page and a single
  ordered metadata/factory registry.  Those pages intentionally contain no
  shell navigation or global Stop control, so the two ADR 0005 shells can
  render the same workflows without duplicating content.

## Tests

`tests/ui/` adds `pytest-qt` coverage (headless `offscreen` mode, forced by
`tests/ui/conftest.py` before any Qt import) for: theme tokens (Qt-free),
the token-to-`QPalette`/`QFont` bridge, control accessible names/focus/
target size, status-indicator non-color-only states, notification behavior
and ordering, plot construction/series updates, video-canvas construction,
keyboard alternatives, and overlay hooks, the fake file picker (never opens
a dialog), state-binding primitives, deterministic demo-service output, and
the foundation smoke window, default/alternate shell selection, and
construction-time hardware disconnection. None of these tests access
hardware or open a native dialog; `uv run pytest` remains hardware-free by default per
[`test-plan.md`](test-plan.md).

`test_workflow_pages.py` also constructs all registered workflow pages,
exercises their primary fake-service and file-picker actions, verifies all
explicit empty/loading/ready/running/completed/fault presentations and
accessible labels, and confirms the pages do not grow shell navigation.
