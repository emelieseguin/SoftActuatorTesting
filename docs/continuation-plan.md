# Unified application continuation plan

**Snapshot date:** 2026-07-11
**Branch at handoff:** `main`
**Purpose:** durable restart context for completing the unified
SoftActuatorTesting desktop application after the implementation fleet was
stopped.

## Resume summary

The rewrite is a substantial, tested implementation rather than a scaffold.
Twenty-three of the thirty tracked work items are complete. One item remains in
progress, five are pending, and one is blocked on external hardware decisions.

The default non-hardware suite passed with **460 tests and one deselection** at
the end of the marker-suggestion implementation. The deselected test is
hardware/external-FFmpeg gated.

The immediate priority is not marker detection. It is replacing the incomplete
three-tab production composition with the selected full Instrument Console and
wiring every real workflow into it.

## Current work graph

### Complete

- Architecture records, dependency boundaries, test plan, compatibility
  fixtures, and the `uv` project scaffold.
- Versioned, atomic artifact persistence and legacy import/export.
- Qt-free domain contracts for calibration, geometry, analysis state, artifacts,
  and run lifecycle.
- Shared Qt foundation, immutable presenter state, typed commands, and an
  idempotent Global Stop path.
- Both functional UI prototypes and the ADR selecting Instrument Console.
- Workspace lifecycle and recent-workspace persistence.
- Serial transport, parser profiles, diagnostics, bounded queues, command
  receipts, and duplicate-ACK protection.
- Calibration capture, fitting, residuals, persistence, cancellation, timeout,
  and off-GUI-thread operation.
- FFmpeg discovery, capability probes, single-input record/preview fan-out,
  startup proof, health, partial-file preservation, verification, promotion,
  and bounded cleanup.
- Manual video geometry, persistence, legacy conversion, and review UI.
- Guided red-marker suggestions with dual-hue HSV detection, explainable
  ranking, ambiguity handling, mask preview, cancellation, staleness guards,
  operator acceptance, and manual correction.
- Core cyclic-run orchestration: camera-before-controller ordering, durable
  telemetry, watchdog, automatic end-marker completion, fault handling, and a
  single idempotent finalizer.

### In progress

#### Full production composition

`src/soft_actuator_testing/ui/production.py` currently constructs
`ProductionConsoleWindow`, a minimal three-tab window containing only
Connections, Readiness, and Live Run. This is not the ADR 0005-selected unified
Instrument Console.

Specific defects:

- no production workspace page is presented;
- no production calibration page is presented;
- no production geometry/marker page is presented;
- no production analysis or settings/help page is presented;
- readiness receives `calibration=lambda: None` and
  `geometry=lambda: None`, so normal production readiness cannot succeed;
- output root can fall back to the user's home directory before a workspace is
  chosen;
- tests prove construction and cleanup but not a complete operator workflow.

Do not mark this item done until the selected Instrument Console is the real
production shell and a hardware-disconnected end-to-end composition test proves
workspace, calibration, geometry, readiness, run, analysis handoff, and cleanup.

### Pending

1. **Analysis pipeline**
   - Extract and correct the legacy notebook algorithm.
   - Process frame zero.
   - Use measured frame rate for timestamps.
   - Emit explicit detected/missing/ambiguous states, confidence, and reasons.
   - Support cancellation, operator corrections, recomputation, and versioned
     export.
   - Keep provisional live results bounded; finalized-video analysis remains
     authoritative.

2. **Analysis review UI**
   - Support recorded-file review and Live Capture mode.
   - Show preview, progress, capture health, provisional overlays, and results.
   - Permit correction and recomputation.
   - Hand finalized cyclic-run recordings directly into authoritative analysis.

3. **Quality hardening**
   - Audit keyboard/focus order, accessible names, contrast, scaling, bounded
     cancellation, error surfacing, and every resource cleanup path.
   - Add missing regression coverage before changing behavior.

4. **Windows and Linux packaging**
   - Produce repeatable packages and smoke-test them.
   - Record FFmpeg/native prerequisites or bundling policy and license notices.

5. **Documentation handoff**
   - Complete operator, maintainer, schema, troubleshooting, packaging, and
     hardware-acceptance documentation.

### Externally blocked

Physical 3840x2160 at 60 fps certification remains blocked until representative
Windows DirectShow and native Linux V4L2 rigs are available and the owner
supplies:

- run duration and soak margin;
- dropped/duplicated-frame limits;
- startup and preview-latency limits;
- minimum provisional-analysis rate;
- CPU, GPU, memory, thermal, storage, and free-space limits;
- recording codec, quality, bitrate, and output-size expectations;
- FFmpeg bundle-versus-prerequisite redistribution policy.

Synthetic FFmpeg evidence fixes the software direction but is not physical
hardware certification.

## Non-negotiable decisions

- Deliver one Python desktop application for Windows and Linux.
- Use PySide6, Qt Widgets, and PyQtGraph; no QML, JavaScript, Designer files, or
  standalone QSS.
- Preserve `ui -> application -> domain`; infrastructure implements core
  interfaces, and domain/application remain Qt-free.
- Use `uv`, `pyproject.toml`, the `src/` layout, and `pytest`.
- Keep hardware disconnected at import and startup. Discovery and connection
  require explicit operator action.
- Instrument Console is the production shell. Experiment Studio remains a
  development comparison only.
- FFmpeg owns authoritative recording and the one physical camera input.
  OpenCV owns offline vision and lower-rate processing, not authoritative 4K60
  recording.
- Recording is on by default for cyclic runs and must be proven active before
  `CMD:START`.
- Keep manual base, tip, and ROI selection. Marker automation remains advisory,
  explainable, and correctable.
- Use versioned, atomic, collision-safe artifacts with workspace-relative paths.
- Do not add a backend API, NI-DAQmx adapter, firmware changes, or an ML model in
  this effort.
- Do not claim physical 4K60 support until the hardware matrix passes.

## Core invariants to preserve

### Camera

- One FFmpeg process owns one physical input.
- `video.partial.mkv` is authoritative while recording.
- Preview is a low-cost pipe drained continuously into a bounded latest-frame
  channel.
- `ffprobe` verifies output before promotion to `video.mkv`.
- Rejected and faulted captures retain the partial file.
- The first parsed video stream is the input negotiation; output/preview stream
  messages must not overwrite it.

### Serial and calibration

- Known commands include `CMD:SET CYCLES`, `CMD:SET ON`, `CMD:SET OFF`,
  `CMD:START`, `CMD:STOP`, `CMD:CAL_ON`, and `CMD:CAL_OFF`.
- Firmware field order and ACK semantics remain unconfirmed.
- The observed legacy third telemetry field is available only through the
  explicit `legacy-field-3-unconfirmed` profile.
- Uncorrelated duplicate ACKs must skip already-satisfied commands.
- Calibration freshness is sequence based.
- `CAL_OFF` is sent after success, timeout, cancellation, and faults.
- Hardware capture never blocks the Qt thread.

### Run lifecycle

- Camera readiness is proven before controller setup and `CMD:START`.
- Command receipts must be `SENT` or `ACKNOWLEDGED`.
- Telemetry is persisted and flushed before UI decimation.
- End-run markers finalize cleanly without redundant `CMD:STOP`.
- Watchdog duration is derived from cycles, on/off durations, and grace.
- Error frames, camera faults, operator stop, startup failure, and normal
  completion all converge on one finalizer.
- Global Stop and command writes are coordinated so a late `CMD:START` cannot
  escape cleanup.
- Finalizer state resets for every run generation.

### Geometry and marker suggestions

- Video open/probe/decode must stay off the GUI thread and remain cancellable.
- Crop/overlay coordinates must use the same exact integer transform.
- Compatible loaded geometry should survive video attachment.
- Representative-frame selection must validate and round-trip.
- Input tools respond only to intended mouse buttons and clear drag state.
- Over-reported frame counts clamp at EOF.
- Legacy ROI width/height must be positive.
- Automatic candidates never silently become authoritative. Acceptance is an
  explicit operator action, and later manual correction clears suggestion
  provenance.

## Restart sequence

1. Run `uv sync`.
2. Run `uv run pytest`.
3. Read this document, `AGENTS.md`, and ADRs 0001 through 0005.
4. Inspect `ui/production.py`, `ui/shells/instrument_console.py`,
   `ui/views/production_run.py`, and production bootstrap tests.
5. Repair the full production composition before starting analysis work.
6. Implement the analysis pipeline and its tests before the analysis UI.
7. Run quality hardening, then packaging and documentation.
8. Dispatch independent final reviewers only after all non-hardware work is
   integrated.
9. Implement actionable reviewer findings and rerun the complete verification
   matrix.

## Suggested sub-agent plan

Do not reuse one agent across overlapping ownership areas. Give each agent the
files, constraints, acceptance evidence, and known defects in its initial
prompt.

### Production integration

- **Primary:** GPT-5.6 Terra.
- **Scope:** full Instrument Console production composition and end-to-end
  composition tests.
- **Do not let it replace the selected shell or introduce demo state.**
- **Independent QA:** Claude Sonnet 5 for operator flow, Qt lifecycle, and
  accessibility; GPT-5.6 Terra or Opus 4.8 for run-state correctness.

### Analysis

- **Pipeline primary:** GPT-5.6 Terra, with notebook/fixture context and no Qt
  ownership.
- **UI primary:** Claude Sonnet 5 after pipeline contracts are stable.
- **QA:** separate Terra reviewer for frame-zero, timestamps, missing/stale
  detection, cancellation, export, and live/finalized authority boundaries.

### Hardening and release

- **Accessibility/lifecycle QA:** Claude Sonnet 5.
- **Packaging:** GPT-5.6 Terra, separately for Windows and Linux if file
  ownership can remain disjoint.
- **Final architecture/correctness reviewers:** at least one GPT-5.6 Terra and
  one Claude Opus 4.8. Reviewers remain read-only and report only actionable,
  high-confidence issues.

### Agent operating rules

- Keep one owner per file group.
- Create tests and documentation with behavior changes.
- Run targeted tests before the full suite.
- Never use hardware or external FFmpeg tests as default-suite requirements.
- Never silently reinterpret unknown firmware fields or ACK behavior.
- Preserve manual operator paths while adding automation.
- Stop an agent after its scoped acceptance criteria pass; do not let it wander
  into the next todo.

## Previous fleet ledger

The stopped implementation session used these scoped agents:

- Research and architecture: `scientific-ui-research`,
  `architecture-writer`, `capture-investigator`.
- Foundation: `fixture-builder`, `project-scaffolder`, `domain-implementer`,
  `artifact-implementer`, `ui-foundation-builder`.
- Foundation QA: `scaffold-qa`, `core-contract-qa`.
- Prototypes and selection: `prototype-page-builder`,
  `instrument-console-builder`, `experiment-studio-builder`,
  `prototype-ux-evaluator`, `prototype-tech-evaluator`,
  `production-shell-selector`.
- Presenter/page integration: `presenter-state-architect`,
  `workflow-page-modularizer`.
- Workflows: `workspace-implementer`, `calibration-implementer`,
  `geometry-implementer`, `serial-implementer`, `camera-implementer`,
  `run-lifecycle-implementer`, `marker-suggestions-implementer`.
- Independent review: `core-integration-qa`,
  `camera-lifecycle-reviewer`, `run-lifecycle-reviewer`,
  `geometry-workflow-reviewer`.

The marker agent's completed handoff reported:

- new Qt/OpenCV-free marker application contracts and scoring workflow;
- a sole OpenCV detector adapter with dual-hue HSV masking;
- geometry suggestion provenance and acceptance/correction behavior;
- a cancellable threaded Qt suggestion view embedded in geometry setup;
- synthetic video, application, infrastructure, pipeline, UI, and import
  boundary coverage;
- 460 passing default tests and one deselection.

The runtime continued to display that agent as running after its completed
handoff even though repository file timestamps stopped changing. Treat the
committed tree and this handoff as authoritative, not the stale runtime label.

## Verification

Run from the repository root:

```bash
uv sync
uv run pytest
uv run soft-actuator-testing --no-gui
uv build
```

Targeted marker and geometry verification:

```bash
uv run pytest \
  tests/application/test_marker_suggestion.py \
  tests/application/test_marker_suggestion_pipeline.py \
  tests/application/test_video_geometry_workflow.py \
  tests/infrastructure/test_red_marker_detector.py \
  tests/ui/test_marker_suggestion.py \
  tests/ui/test_video_geometry.py \
  tests/ui/test_workflow_pages.py \
  tests/test_import_boundaries.py
```

Production composition acceptance must eventually add a test that proves:

1. no hardware is opened during construction;
2. a workspace can be created or opened in the production UI;
3. real calibration and geometry snapshots feed readiness;
4. camera proof precedes serial start;
5. the run finalizes artifacts exactly once on every terminal path;
6. the finalized recording becomes available to analysis;
7. window close performs bounded cleanup.

## Resume prompt

Use the following as the starting instruction for the next implementation
session:

> Continue the unified SoftActuatorTesting implementation from
> `docs/continuation-plan.md`. Preserve `AGENTS.md` and ADR 0001-0005. First
> replace the incomplete three-tab production composition with the selected
> full Instrument Console, using real-but-disconnected workspace, serial,
> calibration, camera, geometry/marker, readiness, run, analysis-handoff, and
> settings services. Add end-to-end composition tests proving no startup
> hardware side effects and real readiness propagation. Then complete the
> analysis pipeline/UI, quality hardening, packaging, documentation, and final
> independent reviews in the recorded order. Keep physical 4K60 certification
> blocked until its explicit hardware matrix and thresholds are supplied.
