# Implementation test plan: default vs. hardware-gated tests

**Status:** Accepted. The `project-scaffold` task created the `src/`/`tests/`
trees and configured the default runner; later phases add the workflow-specific
tests described below. This document was created ahead of that code per
`AGENTS.md`'s requirement to write a short test plan before implementing a
non-trivial feature.
**Date:** 2026-07-11.
**Related todo:** `architecture-record` (Phase 0) in the approved Unified
SoftActuatorTesting UI Implementation Plan.

## Context

There are no automated tests for application behavior in the legacy code
today (see
[`../initial-implementation/README.md`](../initial-implementation/README.md)).
`AGENTS.md` requires `pytest` (run via `uv run pytest`) as the standard
framework, with tests mirroring the `src/` structure. The plan requires the
default test run to never depend on real hardware, while still proving the
hardware-facing behavior (serial protocol, sustained 4K60 capture, run
lifecycle) is correct through fakes, and reserving a clearly separate
hardware-gated suite for the real rig.

## Runner

`uv run pytest`, executed from the rewrite's project root. `pyproject.toml`
excludes the `hardware` marker by default; the mirrored tree includes
`tests/domain`, `tests/application`, `tests/infrastructure`, `tests/ui`,
`tests/hardware`, and `tests/fixtures`.

## Default suite (no hardware; must pass in plain `uv run pytest` / CI)

### Unit tests

- Calibration fit validity, coefficient arity, valid domains, and legacy
  parity with the existing `model`/`samples` shape.
- Geometry normalization, bounds, missing-selection handling (rejecting
  incomplete geometry instead of the legacy silent-default behavior — see
  [`0002-artifact-versioning-and-legacy-compatibility.md`](0002-artifact-versioning-and-legacy-compatibility.md)),
  and coordinate conversion.
- Run state-machine legal/illegal transitions and idempotent Stop/finalize
  (see
  [`0003-concurrency-and-run-finalization.md`](0003-concurrency-and-run-finalization.md)).
- Serial parser frame types, malformed input, units, markers, and timeouts.
- Artifact schema validation, migration, path resolution, atomic
  persistence, and collision behavior.
- Marker candidate scoring, missing/held policy, smoothing, and angle math.
- CSV rows, timestamps, provenance, and legacy export correctness.

### Artifact compatibility test plan (implemented 2026-07-11)

- Import every sanitized legacy calibration, geometry, pressure, and angle
  fixture; assert source bytes are unchanged and raw pressure/NaN/blank angle
  values become explicit missing values.
- Reject malformed calibration, missing/reverse/out-of-bounds geometry, and
  non-artifact serial fixtures with actionable field paths.
- Round-trip each V1 artifact type, verify collision refusal, same-directory
  atomic-write cleanup after injected replacement failure, fail-closed newer
  schemas, and workspace-relative path relocation/traversal behavior.

### Domain-contract foundation (implemented 2026-07-11)

- The pure domain contract rejects non-finite calibration, geometry,
  confidence, and analysis-time values. Calibration coefficients use the
  legacy order: linear `pressure_kPa = a * volts + b`; quadratic
  `pressure_kPa = a * volts**2 + b * volts + c`. Fits require two/three
  distinct voltage samples respectively and expose R²/RMSE metrics without
  inventing an acceptance threshold.
- Geometry uses a frame-bounded, exclusive-edge normalized ROI. Reverse
  corners normalize to one rectangle; empty or out-of-frame selections are
  rejected rather than clipped or defaulted.
- Run state transitions, stop requests, and finalization are pure functions.
  A repeated stop/finalization with the same outcome is idempotent; a
  conflicting completion outcome is rejected. Adapter implementations remain
  future work behind Qt-free application protocols.

### Integration tests without hardware

- A fake serial controller with deterministic transcripts and injected
  faults.
- A synthetic/prerecorded camera source, including simulated recording
  failures.
- A deterministic record-plus-preview fan-out test with a deliberately slow
  preview/analysis consumer, proving it cannot block persisted recording
  (see
  [`0003-concurrency-and-run-finalization.md`](0003-concurrency-and-run-finalization.md)).
- A complete simulated run lifecycle, including window close/disconnect
  during an active run.
- The video regression corpus run through the detector and analysis
  pipeline (corpus described in the plan's "Vision acceptance evidence"
  section; building it is a later todo, not part of this documentation-only
  todo).
- Headless application-service tests that import no Qt module (verifying
  the boundary from
  [`0001-ui-framework-and-qt-boundaries.md`](0001-ui-framework-and-qt-boundaries.md)).

### Run lifecycle implementation evidence (2026-07-11)

- `tests/application/test_run_controller.py` proves the production coordinator
  does not require real hardware, rejects readiness failures, defaults cyclic
  recording on, proves camera output before `CMD:START`, and preserves exact
  legacy parameter/start ordering without asserting ACK semantics.
- It persists every fake decoded voltage sample before bounded UI decimation,
  records explicit blank raw-only pressure when calibration becomes unavailable,
  and exercises clean/stop/abort/timeout/controller/camera/close paths.
- Injected serial and camera cleanup failures still leave pressure output and a
  faulted manifest, while duplicate Stop/finalize sends no duplicate command.

### Camera capture test plan (implemented 2026-07-11)

- Scripted process/device tests prove exact platform commands, negotiated
  3840x2160@60 startup gates, continuously drained preview fan-out, bounded
  slow-consumer behavior, progress/drop accounting, encoder fallback, partial
  retention/promotion, and idempotent timeout/fault/disconnect/close cleanup.
- Package tests fail with actionable diagnostics when FFmpeg is absent.
- `external_ffmpeg` is a separate opt-in marker using only a synthetic source;
  `hardware` remains the separate physical-rig gate. Implementation boundaries
  and remaining evidence are recorded in
  [`camera-capture-implementation.md`](camera-capture-implementation.md).

### GUI tests (still hardware-free)

- `pytest-qt` tests for navigation, state rendering, controls, dialogs,
  validation, progress, cancellation, and keyboard behavior.
- Deterministic widget screenshots for both prototype shells during
  selection (see
  [`0005-ui-shell-evaluation.md`](0005-ui-shell-evaluation.md)) and for the
  selected production shell afterward.
- Separate Windows and Linux visual baselines where native rendering
  differs.
- Layout tests at 1280x720, 1920x1080, and high-DPI scaling.
- Verifying the global Stop control remains reachable throughout active-run
  states.

### Packaging checks

- `uv lock --check`, `uv sync`, build/import checks, and targeted `pytest`
  subsets.
- Windows and Linux packaged launch.
- Opening a demo workspace, rendering video/plots, saving artifacts, and
  exiting cleanly.
- Verifying native/external dependency error messages when FFmpeg, drivers,
  codecs, or camera access are unavailable.

## Hardware-gated suite (never in the default run)

These tests require a real, approved rig and must be excluded from the
default `uv run pytest` collection — for example under a dedicated
`tests/hardware/` package and/or an explicit `@pytest.mark.hardware` marker
excluded by default configuration. The exact marker/config mechanism is
finalized when `project-scaffold` creates `pyproject.toml`; this document
only fixes the requirement that they are never on by default.

- Serial discovery, connection, authoritative parsing, commands, abort, and
  disconnect against real firmware.
- Camera modes, requested/negotiated format, sustained 4K60 recording,
  concurrent preview/provisional analysis, frame/drop accounting, storage
  throughput, corrupted/startup-failure handling, and clean shutdown on
  representative Windows/Linux systems — this is where the
  [4K60 capture-pipeline benchmark](0004-capture-pipeline-benchmark.md)'s
  measurements are produced and later re-validated.
- Default cyclic-run recording proof: pre-start proof through normal
  completion, manual Stop, controller timeout/fault, disconnect, and
  application close.
- Sensor range/noise checks against approved limits.
- End-to-end run completion and fault-safe cleanup on the real rig.

`AGENTS.md`-required lab-safety gating applies to all of the above: unknown
firmware/safety facts (see the plan's "Blocking inputs and decision points")
must not be invented to make these tests runnable.

## Relationship to marker-detection acceptance evidence

Precision/recall, localization error, angle error, missing-detection
behavior, and processing throughput for the marker detector are a
specialized extension of the unit and regression tests above, run against
the versioned regression corpus described in the plan's "Vision acceptance
evidence" section. That corpus and its ground truth are built in a later
phase, not as part of this documentation-only todo.
