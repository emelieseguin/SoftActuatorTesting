# UI accessibility, keyboard/focus, scaling, and lifecycle hardening

**Date:** 2026-07-13
**Related todo:** `quality-ui-accessibility`
**Scope:** `src/soft_actuator_testing/ui/**` and `tests/ui/**` only. Application,
domain, and infrastructure modules and their tests are explicitly out of
scope for this pass (see the sibling `quality-resource-lifecycle.md` and
`quality-data-integrity.md` test plans for that work).

## Method

Read `AGENTS.md`, `docs/continuation-plan.md`, ADR 0001 (Qt boundary rules),
ADR 0005 (Instrument Console selection; item 6 of "mandatory work before
production" is this exact audit), `ui-foundation.md`,
`production-instrument-console-composition.md`, `analysis-review-ui.md`, and
every file under `src/soft_actuator_testing/ui/**` and `tests/ui/**`
directly (delegated sub-agent exploration was tried first but returned
shallow/incorrect results for a codebase this size, so it was abandoned in
favor of a full direct read).

## Audit findings

The codebase is already substantially hardened from prior work. The
following convention is used consistently and was verified, not
re-implemented: widgets owning a `QThread`/`QTimer` stop it via **both** (a)
a `closeEvent` override (for direct/top-level `.close()` calls) **and** (b) a
`self.destroyed.connect(...)` fallback (for the case where the widget is
destroyed as part of a parent's deletion cascade, e.g. `qtbot.addWidget`
teardown, without ever receiving its own `closeEvent`). Both are required
because a child widget embedded in a `QStackedWidget`/dock never receives its
own `closeEvent` when an owning top-level `QMainWindow` closes (no
`WA_DeleteOnClose` is set anywhere in this app, confirmed by repo-wide grep;
`QMainWindow.close()` only hides the window, it does not destroy the widget
tree). `ui/presenters/binding.py`'s `bind_view` (weak owner ref + `WeakMethod`
+ `owner.destroyed.connect(subscription.dispose)`) is the gold-standard
reference implementation of lifecycle-safe presenter binding and needed no
changes.

No `setStyleSheet` usage, no direct `QFileDialog`/`QMessageBox` usage outside
`ui/widgets/file_picker.py`'s `QtFilePicker` boundary, and no keyboard traps
were found (mouse-only tools in `video_geometry.py` all have full keyboard
alternatives: nudge buttons, numeric spin boxes, and arrow/Home/End frame
stepping). `QFormLayout.addRow(label, widget)` rows and `QCheckBox`/
`QPushButton` widgets get their accessible name from Qt's own
label-buddy/button-text fallback, so the absence of an explicit
`setAccessibleName` call on those specific widgets is not a gap.

Four concrete, high-confidence gaps were found and fixed:

1. **`ProductionLiveRunPage` (`ui/views/production_run.py`) had no
   `closeEvent`/`destroyed` cleanup for its 50ms polling `QTimer`** — the
   only page-with-a-timer in the codebase missing this convention.
2. **`CameraPanel` (`ui/widgets/camera_panel.py`), when embedded as a child
   widget inside `ProductionConnectionsPage`, never had its own `closeEvent`
   invoked** by anything when the production window closed, so its 100ms
   poll timer kept running after "close." Its existing `closeEvent` (used
   when the panel is top-level/standalone, as in `tests/ui/test_camera_panel.py`)
   was preserved unchanged; a new `stop_polling()` method lets an owning
   composition stop the timer deterministically without double-invoking
   `presenter.close()` (which the composition already calls directly, with
   its own timeout).
   `ui/production.py`'s composition-level `close()` now explicitly calls
   `live.close()` (triggering `ProductionLiveRunPage`'s new `closeEvent`) and
   `connections.camera_panel.stop_polling()` so both timers stop
   deterministically and boundedly as part of the same closing sequence
   already covered by `test_production_composition.py`'s `<1.0s` bound,
   instead of relying on eventual process exit.
3. **`PlotCanvas` (`ui/widgets/plot.py`) never exposed an
   `accessibleDescription` reflecting its current series/data** — unlike
   every other dynamic widget in the codebase (`VideoCanvas`,
   `StatusIndicator`, `NotificationBanner`), which all keep accessible text
   in sync with state. Only a static `accessibleName` (the title) was set at
   construction, so a screen-reader user had no way to know what data (or
   whether any) was plotted. Fixed by computing and maintaining a
   description (`"<title>: <series> (<n> points), ..."` or `"<title>: no
   data plotted yet."`) in `set_series()`/`clear_series()`, mirroring
   `VideoCanvas._update_accessible_description()`'s pattern. This benefits
   every `PlotCanvas` instance in the app (analysis angle/live plots,
   production live-run pressure plot, foundation demo plot) with one change.
4. **`AnalysisPage.export_button` (`ui/views/workflows/analysis.py`) was
   always enabled**, unlike every other stateful control on the same page
   (`run_button`, `cancel_button`, `apply_correction_button`,
   `clear_marker_button`), which correctly enable/disable to match
   readiness. Clicking export when no authoritative result or output
   location existed only produced a refusal message *after* the click,
   rather than preventing the invalid action up front. Fixed by adding
   `_refresh_export_availability()` (enabled only when
   `_current_result is not None and _current_result.authoritative and
   artifact_store is not None`), called on construction and at every point
   `_current_result`/`artifact_store` change (run start reset, run success,
   correction/recompute, output-location selection). `export_results()`'s
   existing refusal-message behavior is unchanged (still exercised directly
   by existing tests that call the method without simulating a click), so
   this is purely an additive proactive-prevention improvement layered on
   top of the pre-existing defensive check.

No other high-confidence issues were found in: both shells
(`instrument_console.py`, `experiment_studio.py`), all eight workflow pages,
`camera_panel.py`'s render/theme logic, `video_canvas.py`, `file_picker.py`,
`home_workspace.py`, `registry.py`, `pages.py`, or `ui/demo/**` (no
timers/threads/dialogs there at all).

## Regression test plan

- `tests/ui/test_production_run_page.py` (new): a standalone
  `ProductionLiveRunPage` stops its refresh timer on `close()` (spy on
  `QTimer.isActive()`/timeout count before and after).
- `tests/ui/test_camera_panel.py`: add a case constructing a `CameraPanel`
  as a child of another widget (not top-level) and asserting
  `stop_polling()` stops the timer while leaving the existing standalone
  `closeEvent`-driven `panel.close()` → `close_timeouts == [10.0]` assertion
  intact and unchanged.
- `tests/ui/test_production_composition.py`: extend the existing end-to-end
  composition test to assert both `composition.live_run_page`'s timer and
  the embedded `CameraPanel`'s poll timer are stopped after
  `composition.window.close()`, in addition to the existing serial/camera
  service assertions.
- `tests/ui/test_plot_canvas.py` (new): `PlotCanvas.accessibleDescription()`
  starts as "no data plotted yet", updates to name/point-count text after
  `set_series(...)`, updates again after a second `set_series(...)` call
  with more points, and resets after `clear_series()`.
- `tests/ui/test_analysis_review.py`: add a case asserting
  `export_button.isEnabled()` is `False` before any run, `False` after a
  cancelled/non-authoritative run, `True` after a completed authoritative
  run with an output location chosen, and `False` again if the output
  location was never chosen (kept `artifact_store is None`).

Targeted UI tests run first
(`uv run pytest tests/ui/test_production_run_page.py
tests/ui/test_camera_panel.py tests/ui/test_production_composition.py
tests/ui/test_plot_canvas.py tests/ui/test_analysis_review.py`), followed by
the full `uv run pytest` to confirm no regressions elsewhere.

## Implementation outcome

All four fixes above were implemented as small, additive changes with no
behavior changes to any already-tested path (verified by running the
pre-existing `test_camera_panel.py` and `test_production_composition.py`
assertions unchanged alongside the new ones). Regressions added:
`tests/ui/test_production_run_page.py` (new, 2 tests),
`tests/ui/test_camera_panel.py` (+1 test), `tests/ui/test_production_composition.py`
(+1 test, plus one new assertion appended to the existing end-to-end test),
`tests/ui/test_plot_canvas.py` (+4 tests), `tests/ui/test_analysis_review.py`
(+1 test).

Targeted UI regressions passed (**37 tests**:
`test_production_run_page.py`, `test_camera_panel.py`,
`test_production_composition.py`, `test_plot_canvas.py`,
`test_analysis_review.py`). The full `tests/ui` directory passed
(**235 tests**). The full default suite passed
(**528 tests, 1 deselected**).

## Follow-up remediation — 2026-07-14 (`shell-workspace-remediation`)

A later, independent acceptance pass focused specifically on the Instrument
Console shell/workspace/accessibility surface (scope: `bootstrap.py`,
`ui/app.py`, `ui/shells/instrument_console.py`, `ui/views/home_workspace.py`,
`ui/widgets/file_picker.py`) found and fixed additional gaps this document's
"528 tests passing" claim did not cover, because no existing test exercised
them:

- The Save/Restore/Global-Stop keyboard shortcuts were only tested for
  *registration* (`shortcut.key() == QKeySequence(...)`), never for actually
  *activating* the connected action. Added activation tests that send real
  key events and assert the resulting state/log change.
- No test exercised keyboard Tab/Shift+Tab focus traversal order at all.
  Added `test_tab_key_focus_traversal_reaches_navigation_and_workspace_controls`
  and `test_shift_tab_focus_traversal_moves_backwards`.
- No test rendered the console at non-100% DPI scale factors. Added
  subprocess-based `test_console_renders_at_representative_100_150_200_percent_scaling`
  (100/150/200% via `QT_SCALE_FACTOR`), asserting on `grab()` pixmap
  dimensions since Qt Widgets logical geometry is scale-invariant.
- Production-mode wording and layout persistence (see the
  `0005-ui-shell-evaluation.md` "Shell/workspace/accessibility remediation
  outcome — 2026-07-14" section for the full write-up) had zero prior test
  coverage in either direction.

Full details, rationale, and the complete list of new/changed tests are in
`docs/architecture/0005-ui-shell-evaluation.md`. Manual (real
screen-reader/OS-DPI/operator-study) evidence remains tracked, and explicitly
pending, in `docs/architecture/manual-accessibility-dpi-evidence-matrix.md`.
