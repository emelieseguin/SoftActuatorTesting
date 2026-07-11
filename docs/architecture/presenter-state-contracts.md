# Application presenter-state contracts

**Status:** Implemented mandatory ADR 0005 gate.
**Date:** 2026-07-11.
**Related todo:** `presenter-state-integration`.

## Boundary

`application/presentation.py` owns the Qt-free presentation boundary. All
state crossing into a UI is an immutable (`frozen=True`) dataclass composed of
plain Python/domain values:

- `WorkspaceSnapshot`
- `DeviceConnectionsSnapshot`
- `CalibrationSnapshot`
- `GeometrySnapshot`
- `ReadinessSnapshot`
- `RunPresenterSnapshot`
- `AnalysisSnapshot`
- `SettingsSnapshot`
- `NotificationSnapshot` and `FaultSnapshot`
- aggregate `ApplicationSnapshot`

Binary preview pixels cross as immutable `bytes` in
`PreviewFrameSnapshot`; live mutable camera frames and Qt images do not.
`StateStore[ApplicationSnapshot]` is the one current-state and subscription
seam. It suppresses equal publications and returns an idempotent
`Subscription`. `PresenterSession` pairs that state source with the typed
`ApplicationCommandDispatcher` boundary.

Qt views use `ui.presenters.bind_view`. It weakly holds bound render methods,
disposes on `QObject.destroyed`, checks the wrapped C++ object is still valid,
and therefore cannot invoke a stale destroyed view.

## Command families

Commands are immutable dataclasses grouped by typed aliases:

- workspace: `SelectWorkspace`, `CreateWorkspace`
- devices: `ConnectDevices`, `DisconnectDevices`, `RequestDiagnostics`
- calibration: `CollectCalibrationSamples`, `FitCalibration`
- geometry: `SetManualGeometry`, `DetectMarker`
- readiness: `ConfigureExperiment`, `EvaluateReadiness`
- run: `BeginRun`, `ConfirmRunStarted`, `CompleteRun`, `RequestRunStop`,
  `ConfirmRunStopped`, `GlobalStop`, `ReportRunFault`, `ReportRunTimeout`
- analysis: `ChooseAnalysisSource`, `SetAnalysisMode`, `RunAnalysis`
- settings/notifications: `ApplySettings`, `DismissNotification`

`WorkflowController` is a deterministic synchronous controller over injected
application service protocols. It is the demo implementation, not a real
workspace, serial, camera, calibration, geometry, recording, or analysis
service. Later adapters may replace it while preserving `PresenterSession`.

## Authoritative run and Stop semantics

`RunPresenterSnapshot.lifecycle` is the sole UI run source. Pages and shells
never infer run state from button labels, `PageScenario`, or local stage sets.
`STARTING` and `STOPPING` are explicit because start/stop requests and their
confirmations are separate commands.

| Situation | `GlobalStop` result |
| --- | --- |
| Disconnected, idle, ready, fault, or clean/stopped completion | Acknowledged idempotent no-op; existing lifecycle/outcome is not rewritten |
| Starting or running | Request stop, finalize once as `ABORTED`, publish acknowledgement |
| Stopping with no outcome | Finalize once as `ABORTED` |
| Already completed as aborted | Acknowledged duplicate; no lifecycle change |
| Cleanup timeout/worker fault | `ReportRunTimeout`/`ReportRunFault` finalize an active/stopping run as `FAULTED` and publish diagnostics |
| Device disconnect during an active run | Finalize as `FAULTED`, disconnect, retain the outcome text/fault diagnostic |

`CompleteRun` is ordinary controller-declared `CLEAN` completion and is never
called by Global Stop. `RequestRunStop`/`ConfirmRunStopped` represent an
ordinary operator stop (`STOPPED`). None of these presenter outcomes claims
that physical hardware reached a safe state; hardware safety evidence belongs
to later device-integration work.

## UI integration

Shared pages dispatch typed commands and render only their aggregate snapshot
projection. Instrument Console uses the same subscription for fixed run/fault
chrome, Global Stop enablement, status/file context, telemetry, readiness
guidance, recommended next action, and collapsed diagnostics. `PageScenario`
remains only an explicit prototype visual fixture and cannot enable Start/Stop
or complete workflow stages.

Experiment Studio remains a rejected development prototype, but now shares
the presenter, derives completion/readiness from it, and dispatches the same
emergency-abort command.

## Verification

Pure application tests cover snapshot immutability, subscriptions, command
propagation, readiness, disconnect/reconnect/fault, starting/stopping and
duplicate Stop, ordinary completion versus abort, and timeout finalization.
`pytest-qt` tests cover cross-page consistency, fixed Console chrome and
guidance, state propagation, and stale subscription disposal.
