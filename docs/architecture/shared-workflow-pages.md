# Shared workflow-page prototype and test plan

**Status:** Presenter-backed shared contract; prototype visual fixtures retained.
**Date:** 2026-07-11.
**Related todo:** `prototype-shared-pages`.

## Purpose

`ui/views/` supplies the shell-independent workflow content required by ADR
0005. It owns neither main-window layout nor navigation/global chrome. Pages
render immutable `ApplicationSnapshot` projections and dispatch typed
application commands through one `PresenterSession`.

The registry contains metadata and factories for Home/Workspace, Connections/
Diagnostics, Calibration, Video Geometry/Marker Setup, Experiment Setup/
Readiness, Live Run, Analysis, and Settings/Profiles/Help. Pages take a
presenter and `FilePicker`; deterministic services are wired behind the
presenter in `ui.demo`, so pages never invoke a service directly.

Every page exposes `set_scenario()` for explicit empty, loading, ready,
running, completed, and fault prototype presentations. It changes only the
visual-fixture banner. Readiness, workflow completion, Start/Stop enablement,
and shell status always come from the presenter snapshot.

Concrete snapshot/command and Stop contracts are documented in
[`presenter-state-contracts.md`](presenter-state-contracts.md).

## Presenter-state integration test plan (2026-07-11)

- Pure application tests will cover immutable aggregate snapshots, typed
  command dispatch, disconnect/reconnect/fault propagation, readiness guidance,
  clean completion, emergency abort, duplicate Stop, Stop while starting or
  stopping, and timeout/fault finalization.
- `pytest-qt` tests will prove shared pages and Instrument Console render the
  same subscribed snapshot, dispatch commands instead of reading widgets as
  state, retain fixed run/fault/Global Stop chrome, progressively disclose
  diagnostics, and ignore updates after a view is destroyed.
- Existing deterministic page, Console, rejected-Studio prototype, bootstrap,
  and complete hardware-excluded suites will remain runnable.

## Test plan

- Construct every registered page headlessly and check its accessible name and
  absence of shell navigation controls.
- Exercise each page's primary demo action: workspace/file selection,
  connection/diagnostics, calibration sample/fit, manual/automatic geometry,
  readiness gating, run start/stop preview/logs, analysis/review, and settings.
- Render all six scenarios on each page and assert their non-color-only text.
- Use `FakeFilePicker` and the deterministic fake services; assert no real
  hardware or native dialog dependency is introduced.
- Run the targeted `tests/ui/test_workflow_pages.py` suite, then the complete
  default `uv run pytest` suite.
