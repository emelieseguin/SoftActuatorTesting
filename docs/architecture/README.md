# Architecture decision records

**Status:** index (2026-07-11).
**Scope:** this folder records the architecture and process decisions for the
unified `src/` rewrite approved in the *Unified SoftActuatorTesting UI
Implementation Plan* (plan date 2026-07-11). It implements the plan's Phase 0
`architecture-record` todo: "Add dated docs for UI framework, dependency
boundaries, artifact versioning, concurrency, the 4K60 capture benchmark,
visual evaluation, and the required test plan. Record runtime dependency
licenses."

The rewrite now includes the scaffold plus workspace, calibration, serial,
camera, geometry/marker, and core run-lifecycle implementations. These
documents record both the decisions made before implementation and the
subsequent design/test evidence required by `AGENTS.md`. For the legacy-code
inventory under [`old-files/`](../../old-files/), see
[`../initial-implementation/README.md`](../initial-implementation/README.md).
For the remaining work and restart sequence, see
[`../continuation-plan.md`](../continuation-plan.md).

## Documents

| Document | Decision | Status |
| --- | --- | --- |
| [`0001-ui-framework-and-qt-boundaries.md`](0001-ui-framework-and-qt-boundaries.md) | PySide6/Qt Widgets/PyQtGraph choice, Qt-free domain/application boundary, Python-only source/style construction | Accepted |
| [`0002-artifact-versioning-and-legacy-compatibility.md`](0002-artifact-versioning-and-legacy-compatibility.md) | Versioned artifacts and legacy import/export compatibility policy | Accepted |
| [`0003-concurrency-and-run-finalization.md`](0003-concurrency-and-run-finalization.md) | Resource ownership, threading rules, and idempotent run finalization | Accepted |
| [`0004-capture-pipeline-benchmark.md`](0004-capture-pipeline-benchmark.md) | FFmpeg-baseline / OpenCV-eligible 4K60 Windows/Linux capture benchmark and record+preview design | Accepted for implementation, hardware acceptance pending |
| [`0005-ui-shell-evaluation.md`](0005-ui-shell-evaluation.md) | Instrument Console selected from the two-shell evaluation; retained Studio ideas and mandatory presenter/safety work recorded | Accepted — production readiness work pending |
| [`dependency-licenses.md`](dependency-licenses.md) | Runtime dependency license/redistribution implications | Informational |
| [`test-plan.md`](test-plan.md) | Default non-hardware suite vs. hardware-gated suite | Accepted (execution starts in later phases) |
| [`ui-foundation.md`](ui-foundation.md) | `ui/` foundation module layout: theme tokens, accessible controls, plot/video wrappers, state binding, demo services | Informational — implemented (`ui-foundation`, Phase 1) |
| [`presenter-state-contracts.md`](presenter-state-contracts.md) | Immutable aggregate UI state, typed command families, subscription disposal, and authoritative Stop semantics | Implemented mandatory ADR 0005 gate |
| [`workflow-page-module-ownership.md`](workflow-page-module-ownership.md) | Independently owned workflow-view modules, shared page base, and compatibility imports | Implemented |
| [`serial-protocol-and-test-plan.md`](serial-protocol-and-test-plan.md) | Single-owner serial lifecycle, explicit unconfirmed legacy mapping, bounded reader queue, and hardware-free protocol tests | Implemented |
| [`camera-capture-implementation.md`](camera-capture-implementation.md) | FFmpeg discovery/probes, platform commands, single-input record/preview worker, capture presenter/panel, and gated tests | Implemented in software; representative hardware acceptance blocked |
| [`run-lifecycle-implementation.md`](run-lifecycle-implementation.md) | Qt-free cyclic-run readiness, durable telemetry, command ordering, and unified finalization | Implemented (hardware-free composition) |
| [`video-geometry-workflow.md`](video-geometry-workflow.md) | Qt-free manual video geometry authoring (load/scrub/zoom/pan, base/tip/ROI editing, undo/redo, versioned/legacy persistence) and its replaceable OpenCV reader adapter | Implemented |
| [`marker-suggestions.md`](marker-suggestions.md) | Explainable dual-hue HSV marker candidates, ranking, cancellation, staleness, operator acceptance/correction, and provenance | Implemented |
| [`marker-suggestions.md`](marker-suggestions.md) | Qt-free guided red-marker suggestion engine (dual-hue HSV scoring/ranking/ambiguity/staleness), its replaceable OpenCV detector adapter, and the bounded-cancellable UI widget that complements manual geometry authoring | Implemented |

## Status legend

- **Accepted** — the decision is fixed for this rewrite and should not be
  revisited without a new dated record superseding it.
- **Accepted for implementation, hardware acceptance pending** — available
  evidence fixes the implementation direction without claiming the target
  physical rig has passed its hardware acceptance gates.
- **Proposed** — the plan/criteria are fixed, but the concrete outcome depends
  on evidence (a benchmark or a prototype comparison) that does not exist yet.
  The document will gain a dated "Outcome" addendum, or be superseded by a new
  numbered record, once that evidence exists.
- **Blocked** — useful non-hardware evidence exists, but a required decision
  cannot be completed until the document's named hardware or threshold inputs
  are supplied.
- **Informational** — reference material that must be kept current as
  dependencies change, rather than a one-time decision.

## Dependency direction (summary)

```text
ui           -> application -> domain
infrastructure -> application/domain interfaces
domain       -> Python standard library and numerical primitives only
```

See [`0001-ui-framework-and-qt-boundaries.md`](0001-ui-framework-and-qt-boundaries.md)
for the full rationale and enforcement plan.

## Relationship to later phases

The remaining phases are full production-shell composition, angle analysis,
quality hardening, packaging, and handoff. Add or update dated documents here
as those phases land, keeping documentation and behavior changes together.
Physical 4K60 acceptance remains a separate hardware-gated activity.
