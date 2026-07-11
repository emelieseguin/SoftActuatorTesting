# ADR 0003: Concurrency, resource ownership, and idempotent run finalization

**Status:** Accepted.
**Date:** 2026-07-11.
**Related todo:** `architecture-record` (Phase 0) in the approved Unified
SoftActuatorTesting UI Implementation Plan.

## Context

The legacy `DataCollection-V2.py` GUI has several documented concurrency and
lifecycle defects (see
[`../initial-implementation/data-collection.md`](../initial-implementation/data-collection.md)):

- `FFmpegRecorder.stop()` blocks on `wait()` on the GUI thread.
- Run state (`run_active`, `run_file`, `current_run_dir`, `_ffmpeg`, ...) is
  not initialized up front and is only reliably cleaned up when firmware
  emits an `--- end run ---` marker; there is no single finalize path for
  Stop, window close, controller fault, or an unhandled exception.
- Camera "connected" status is recorded without proving capture actually
  works.
- Broad exception handling in places like `detect_cameras()` can silently
  return empty results, and GUI callbacks can silently stop polling.

## Decision

### One owner per hardware resource

Each hardware resource (serial connection, camera/recorder) has exactly one
owning component in `infrastructure/` (`serial_adapter.py`, `camera.py`,
`ffmpeg_recorder.py`). No other module opens, reads, or closes that resource
directly.

### Nothing hardware- or file-heavy runs on the GUI thread

Serial reads, video reads, marker/angle processing, FFmpeg process waits,
and file-heavy analysis all run on worker threads/processes, never on the Qt
GUI thread. Workers communicate with the UI through bounded queues and
marshal only immutable snapshots/results — never live mutable state.

### Preview and analysis never starve recording

- Full-resolution recording is the priority consumer of the camera feed.
- The UI preview and provisional live analysis are fed from a
  downscaled/decimated branch or a bounded "keep latest frame" channel; a
  slow preview or analysis consumer must never apply backpressure that
  blocks the recording path. This must be provable with a deterministic test
  using an artificially slow consumer (see
  [`test-plan.md`](test-plan.md)).
- Prefer opening the physical camera once and splitting/teeing that single
  stream. Opening the camera more than once is only acceptable if the
  target hardware proves 4K60 recording remains stable that way (see
  [`0004-capture-pipeline-benchmark.md`](0004-capture-pipeline-benchmark.md)).
- Plot updates are decimated for UI responsiveness without discarding any
  persisted sample.
- Live marker/angle results shown during capture are provisional; only the
  finalized recording is authoritative for later analysis.

### Cancellation and fault reporting

- Cancellation is explicit (a token/event the worker observes), and callers
  wait for deterministic cleanup with a timeout rather than assuming
  instantaneous stop.
- Worker faults are surfaced through typed application events; a worker must
  never fail by silently stopping without telling the application layer.

### Run state machine

```text
DISCONNECTED -> CONNECTING -> IDLE -> READY
READY -> STARTING -> RUNNING -> STOPPING -> COMPLETED
CONNECTING/IDLE/READY/STARTING/RUNNING/STOPPING -> FAULT
FAULT -> IDLE or DISCONNECTED after explicit recovery
```

- The state machine is pure Python (Qt-free, per
  [`0001-ui-framework-and-qt-boundaries.md`](0001-ui-framework-and-qt-boundaries.md))
  with explicit transitions.
- An illegal transition returns a typed error and produces a visible event
  instead of silently doing nothing or corrupting state.
- Start requires a successful readiness evaluation first.

### Idempotent Stop and finalize

- **Stop is idempotent**: calling it while already stopping/stopped is a
  no-op, not an error.
- **Finalization is idempotent** and is the single code path that closes and
  flushes the pressure CSV, stops the recorder, stops/cleans hardware, and
  records a clean, stopped, aborted, or faulted completion state in the run
  manifest (see
  [`0002-artifact-versioning-and-legacy-compatibility.md`](0002-artifact-versioning-and-legacy-compatibility.md)).
- The same finalize service is invoked for: operator Stop, controller
  timeout/fault, camera disconnect, window close during an active run, and
  an unhandled exception — there is exactly one implementation, not one per
  trigger.

### Presenter command clarification — 2026-07-11

The `presenter-state-integration` gate makes the previously generic word
"Stop" explicit at the UI/application boundary:

- `CompleteRun` records ordinary controller-declared `CLEAN` completion.
- `RequestRunStop` followed by `ConfirmRunStopped` records an ordinary
  operator-requested `STOPPED` outcome.
- `GlobalStop` is the idempotent emergency-abort command. Starting/running
  transitions through stopping and finalizes `ABORTED`; stopping finalizes
  `ABORTED`; disconnected/inactive/duplicate calls are acknowledged no-ops.
- timeout and worker/device fault commands finalize an active/stopping run as
  `FAULTED`.

These are software lifecycle/cleanup outcomes only. They deliberately do not
assert a physical hardware safe state. See
[`presenter-state-contracts.md`](presenter-state-contracts.md).

## Consequences

- `application/run_controller.py` owns run lifecycle logic; views only
  dispatch commands and render state snapshots, per
  [`0001-ui-framework-and-qt-boundaries.md`](0001-ui-framework-and-qt-boundaries.md)'s
  application-state design.
- Window close handlers, exception handlers, and the Stop button must all
  route through the same finalize call — this is directly testable without
  hardware using fakes (see
  [`test-plan.md`](test-plan.md)'s "close/disconnect during a run"
  integration test).
- A fault in one worker (e.g., camera disconnect) must not silently leave
  another resource (e.g., serial) open or an unclosed CSV file, because
  finalize always runs all cleanup steps regardless of which fault triggered
  it.
