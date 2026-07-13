# Architecture decision and implementation records

**Status:** current index (2026-07-13).
**Scope:** the unified `src/` desktop application. The selected Instrument
Console, production analysis integration, reliability hardening, and native
packaging work are implemented in software. Physical 4K60 acceptance remains a
separate, externally blocked activity; this index does not certify it.

For the durable handoff, remaining hardware decisions, and verification
commands, see the [continuation plan](../continuation-plan.md). For historical
legacy behavior, see the [legacy inventory](../initial-implementation/README.md).

## Foundational decisions

| Document | Purpose | Status |
| --- | --- | --- |
| [ADR 0001 — UI framework and Qt boundaries](0001-ui-framework-and-qt-boundaries.md) | PySide6/Qt Widgets/PyQtGraph and Qt-free core boundary | Accepted |
| [ADR 0002 — artifact versioning and legacy compatibility](0002-artifact-versioning-and-legacy-compatibility.md) | Versioned artifacts and migration policy | Accepted; implemented |
| [ADR 0003 — concurrency and run finalization](0003-concurrency-and-run-finalization.md) | Ownership, threading, and idempotent cleanup | Accepted; implemented |
| [ADR 0004 — capture pipeline benchmark](0004-capture-pipeline-benchmark.md) | FFmpeg record/preview direction and 4K60 evidence boundary | Accepted for implementation; physical acceptance pending |
| [ADR 0005 — shell evaluation](0005-ui-shell-evaluation.md) | Instrument Console selection | Accepted; production composition implemented |
| [Dependency licenses](dependency-licenses.md) | Runtime and redistribution obligations | Informational; recheck at release |
| [Default vs. hardware-gated test plan](test-plan.md) | Test boundary and evidence strategy | Accepted; implemented by later records |

## Implemented workflows and production composition

| Document | Purpose | Status |
| --- | --- | --- |
| [UI foundation](ui-foundation.md) | Shared accessible controls, tokens, wrappers, state binding, and demo seams | Implemented |
| [Presenter state contracts](presenter-state-contracts.md) | Immutable snapshots, typed commands, and authoritative Stop semantics | Implemented |
| [Shared workflow pages](shared-workflow-pages.md) | Shared prototype workflow-page foundation | Implemented |
| [Workflow page module ownership](workflow-page-module-ownership.md) | Stable per-workflow module boundaries | Implemented |
| [Workspace lifecycle](workspace-lifecycle.md) | Workspace creation, restoration, and portable artifacts | Implemented |
| [Calibration workflow test plan](calibration-workflow-test-plan.md) | Capture, fitting, cancellation, and persistence | Implemented |
| [Serial protocol and test plan](serial-protocol-and-test-plan.md) | Single-owner serial lifecycle and explicit legacy uncertainty | Implemented |
| [Camera capture implementation](camera-capture-implementation.md) | FFmpeg discovery, proof, preview, and finalization | Implemented in software; hardware acceptance blocked |
| [Run lifecycle implementation](run-lifecycle-implementation.md) | Readiness, ordering, telemetry, and one finalizer | Implemented |
| [Video geometry workflow](video-geometry-workflow.md) | Cancellable manual geometry authoring and persistence | Implemented |
| [Marker suggestions](marker-suggestions.md) | Advisory explainable red-marker detection | Implemented |
| [Analysis pipeline](analysis-pipeline.md) | Finalized-video frame processing, authority, corrections, and export | Implemented |
| [Analysis review UI](analysis-review-ui.md) | Recorded-file review, provisional live view, and handoff | Implemented |
| [Production Instrument Console composition](production-instrument-console-composition.md) | Real disconnected services and workspace-bound analysis | Implemented |

## Hardening and packaging

| Document | Purpose | Status |
| --- | --- | --- |
| [Data-integrity hardening](quality-data-integrity.md) | Validation, persistence durability, paths, and legacy compatibility | Implemented |
| [Resource-lifecycle hardening](quality-resource-lifecycle.md) | Cancellation, ownership, cleanup, and stale-result handling | Implemented |
| [UI accessibility hardening](quality-ui-accessibility.md) | Keyboard/accessibility, timer lifecycle, and state clarity | Implemented |
| [Desktop packaging](desktop-packaging.md) | Native PyInstaller bundles, FFmpeg policy, notices, and smoke plan | Linux native build/smoke verified; Windows execution pending native runner |

## Dependency direction

```text
ui -> application -> domain
infrastructure -> application/domain interfaces
domain -> Python standard library and numerical primitives only
```

New hardware adapters or external services must preserve this direction and the
explicit connection/lifecycle ownership described in the
[maintainer guide](../maintainer-guide.md).
