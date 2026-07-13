# Maintainer guide

## Restart safely

From the repository root, run `uv sync`, then `uv run pytest`. Read
`AGENTS.md`, `docs/continuation-plan.md`, and ADRs 0001–0005 before changing
behavior. Do not treat the legacy programs in `old-files/` as an operational
implementation: they are compatibility evidence only.

The normal entry point is `soft-actuator-testing`. `--no-gui` returns before
importing the UI and reports that no hardware was initialized; `--smoke-imports`
checks packaged GUI/runtime imports without creating a window. Production
composition is constructed by `ui.production.create_production_composition()`.
It creates disconnected services; it does not enumerate, open, or connect a
serial port or camera. Discovery and connection remain explicit operator
actions. Preserve this no-startup-hardware invariant in every new adapter,
presenter, and package smoke test.

## Boundaries and ownership

| Layer | Owns | Must not own |
| --- | --- | --- |
| `domain/` | Immutable validation models, calibration maths, geometry, analysis rows, run-state transitions and error types | Qt, device/file I/O, process/thread ownership |
| `application/` | Qt-free workflows, protocols, immutable snapshots, cancellation, readiness, run orchestration | Qt widgets, raw serial reads, direct FFmpeg/OpenCV ownership |
| `infrastructure/` | Artifact filesystem implementation, legacy adapters, serial reader/parser, FFmpeg process, OpenCV file reader/detector, preferences | UI state or implicit hardware startup |
| `ui/` | PySide6 widgets, timers, view binding and presentation | Scientific truth, artifact writes, device protocol parsing |

The permitted direction is `ui -> application -> domain`; infrastructure
implements application/domain seams. `tests/test_import_boundaries.py` guards
the Qt/PyQtGraph-free core. Keep new camera, serial, NI, or network
implementations behind application-owned protocols (`application/services.py`)
and inject them into workflows. Do not add QML, Designer files, JavaScript, or
a second package manager.

`InstrumentConsoleWindow` is the normal production shell. Its persistent
status strip, Global Stop, navigation, and docks are shell concerns; the
workspace, connection, calibration, geometry, readiness, live-run, analysis
handoff, and settings pages are supplied by production composition. Experiment
Studio is an explicit development comparison, not another production workflow.

### State and presenter flow

`application.presentation.StateStore` publishes immutable snapshots and returns
idempotently disposable subscriptions. Views render snapshots and dispatch
typed commands; they do not become a second source of run or readiness truth.
Qt worker results must be marshalled to the GUI thread before changing a
widget. The production console polls the thread-owned run snapshot on a Qt
timer; workers never mutate widgets directly.

Keep ordinary completion, requested stop, global abort, timeout, and fault
distinct:

* `CompleteRun` is clean completion.
* `RequestRunStop` then `ConfirmRunStopped` is an ordinary stopped outcome.
* `GlobalStop` is idempotent and produces an aborted active run; inactive or
  duplicate calls are acknowledged no-ops.
* timeout and device/worker faults produce faulted completion.

## Threading, cancellation, and resources

One infrastructure component owns each physical resource. `SerialAdapter`
owns its transport and reader thread. `FfmpegCaptureBackend` owns one FFmpeg
process, its preview/progress drainers, and its finalizer. No view opens,
reads, or closes those resources.

Blocking serial polling, calibration capture, prerecorded-video probe/decode,
marker/analysis work, FFmpeg waits, and file-heavy operations must stay off the
Qt thread. Use explicit cancellation tokens/events, bounded waits, immutable
results, and fault reporting. `LatestFrameChannel` and
`ProvisionalAnalysisChannel` each have one slot: producers replace stale work
rather than block recording. Persisted telemetry is not decimated; only the
UI projection is bounded.

Always dispose state subscriptions and stop timers on page/window close. A
serial disconnect joins its non-daemon reader with a bounded timeout. Video
geometry closes the open file handle before replacing it. Calibration capture
always attempts `CAL_OFF`, including success, cancellation, timeout, and
failure.

## Serial and calibration policy

Firmware field order and ACK semantics are not authoritative in this
repository. The default `ParserProfile` has no telemetry mapping and no ACK
support. The only observed legacy mapping is the explicitly named
`legacy-field-3-unconfirmed` profile (`timestamp_seconds=0`, `volts=2`).
Never infer a mapping or claim command acknowledgement from an unconfirmed
profile. A configured ACK-capable profile correlates receipts and treats late
or duplicate ACKs without satisfying an already-completed command.

Known legacy command text is `CMD:SET CYCLES`, `CMD:SET ON`, `CMD:SET OFF`,
`CMD:START`, `CMD:STOP`, `CMD:CAL_ON`, and `CMD:CAL_OFF`; this is not a
firmware specification. Run startup accepts only `sent` or `acknowledged`
receipts. Under the legacy profile it sends the three settings and then start
without an ACK wait.

Calibration capture drains queued telemetry, sets a sequence baseline after
`CAL_ON`, and accepts only a later voltage sample. A stale sample cannot be
recorded. Fitting requires two distinct voltages for linear or three for
quadratic; its default quality policy requires R² >= 0.98 and rejects a
condition number above `1e8`. Fit adequacy, residuals, input domain, and
extrapolation warnings are data, not hidden UI policy. A cyclic run requires
an adequate calibration, but an active run whose calibration becomes
unavailable persists later pressure cells as blank raw-only values.

## Capture, run, geometry, and analysis

FFmpeg is the authoritative recorder and owns the single physical input.
`FfmpegCaptureBackend` requests 3840×2160@60 using the platform input
(`dshow` on Windows, `v4l2` on Linux), records `video.partial.mkv`, and fans
out a 960×540@10 RGB preview pipe. It starts separate preview/progress
drainers; the latest-frame channel prevents a slow preview or provisional
analysis from applying backpressure to recording. Startup proof requires the
negotiated target profile, at least one progress frame, advancing output time,
a growing partial file, and at least one drained preview frame.

On shutdown, FFmpeg receives `q`, then bounded interrupt/kill escalation if
needed. Promotion to `video.mkv` requires a startup-proven capture, readable
ffprobe result, zero-exit cooperative shutdown, and joined preview/progress
drainers; it must not depend on a stop-reason allowlist, because a cyclic
controller supplies its own terminal reason. Unreadable, rejected, escalated,
and faulted captures retain `video.partial.mkv` for diagnosis. The typed
`CaptureResult.evidence` records startup proof, cooperative exit code, drainer
state, verification, promotion, and escalation. `RunController` persists the
complete available typed capture record under additive `payload.capture` in
the terminal V1 run manifest, caching any already-finalized result before
assembly. Keep unavailable values as `null`; do not infer ffprobe
duration/frame/stream data or preview timestamp/rate from unrelated facts.
The command serializer must redact secrets, environment substitutions, URLs,
and external paths; partial/final references must be workspace-relative. The
initial negotiated input line is retained; output/preview stream messages must
not replace it.

Connections capture is standalone diagnostics. It reserves one unique
workspace-owned `runs/standalone-capture-<uuid>/` directory and writes its
small `capture-status.json`; do not point it at a shared `runs/video.mkv` or
make it depend on `ArtifactFileStore` internals. Production applies the
`CaptureStoragePolicy` before each capture (configured duration or a
conservative ten-minute 100 MiB/s estimate plus 1 GiB reserve), probes
advertised DirectShow/V4L2 modes on Refresh, and runs the real encoder probe at
explicit capture start. FFmpeg V4L2 format listings may omit frame rates; keep
that as a visible non-blocking warning and let the negotiated startup profile
remain the actual target gate. A listed encoder or reported mode is not
hardware acceptance evidence. Keep the unsupported-mode/error state visible
for frame-rate-capable probes that reject the target, and do not claim physical
4K60 validation without the separate hardware gate.

`RunController` reserves artifacts, proves camera readiness **before**
`CMD:START`, durably writes each telemetry row before UI decimation, and starts
a watchdog calculated as `cycles * (on_ms + off_ms) / 1000 + grace`. End-run
markers finalize cleanly without a redundant `CMD:STOP`. Startup failure,
operator stop, Global Stop, timeout, serial/camera fault, and window close
all converge on `finalize()`. That method is generation-aware and idempotent:
it stops workers/watchdog, optionally sends stop once, finalizes capture,
closes/fsyncs pressure output, writes one manifest, and records cleanup errors.
Existing V1 manifests without the additive capture area must remain loadable
unchanged. Standalone Connections `capture-status.json` remains independent:
do not create a run-manifest linkage unless a public typed capture-result
contract supplies it.

Geometry is frame-bounded: ROI right/bottom edges are exclusive, reverse
corners normalize, and base/tip/ROI must be complete to save. The same
integer/aspect-preserving transform must be used for crop, overlay, and input.
Loading/probing/decoding is cancellable and off the GUI thread. Marker
suggestions are advisory only: operator acceptance is explicit and persisted;
manual tip editing clears suggestion provenance.

Finalized-file analysis uses measured FPS and processes frame zero. A
completed finalized-video pass is the only authoritative and exportable result.
Cancelled or truncated passes are coherent non-authoritative frame-zero
prefixes. Missing and ambiguous rows have no selected point or angle; manual
corrections return a new immutable result. Live previews are explicitly
provisional and non-persistable. Angle is the signed image-coordinate
`atan2(dy, dx)` from base to tip, independent of calibration.

## Future extension points and non-goals

Add NI-DAQ or a network API only in a separately approved effort. Neither an
NI-DAQmx adapter nor a backend/network service is part of this application.
Such an adapter must be injected behind a Qt-free protocol, must preserve
explicit connection/lifecycle ownership and artifact provenance, and must add
licensing, safety, cancellation, and hardware acceptance evidence. Do not
replace the serial uncertainty policy, bypass the artifact store, or introduce
an API that makes hardware start implicitly.

## Verification Summary

Fact-checked against the current working tree on 2026-07-13. This is a
factual verification of code claims only, not a re-review.

- **Claims checked:** 36
- **Confirmed:** 36
- **Corrected:** 0
- **Unverifiable:** 0

Representative confirmations:

- Entry point `soft-actuator-testing` maps to `bootstrap:main`; `--no-gui`
  returns before the UI import and reports "no hardware was initialized"; and
  `--smoke-imports` imports cv2/pyqtgraph/PySide6/production without a window
  (`bootstrap.py:16-80`; verified by running both CLI flags, exit 0).
- `create_production_composition()` (`ui/production.py:168`) and
  `InstrumentConsoleWindow` (`ui/shells/instrument_console.py`, aliased at
  `ui/production.py:350`) exist as described.
- `application.presentation.StateStore` publishes immutable snapshots and
  returns idempotently disposable subscriptions (`presentation.py:360-402`).
- `SerialAdapter` reader thread is `daemon=False`, joined with a bounded
  timeout (`serial_adapter.py:342,370`); `FfmpegCaptureBackend` owns one process
  plus preview/progress drainers and a single finalizer (`camera.py:129-375`);
  `LatestFrameChannel`/`ProvisionalAnalysisChannel` are one-slot
  (`camera_capture.py:126`, `analysis_pipeline.py:140`).
- Default `ParserProfile` has empty telemetry mapping and
  `acknowledgements_supported=False`; `legacy-field-3-unconfirmed` maps
  `timestamp_seconds=0, volts=2` (`serial_adapter.py:87-122`). Command text and
  `sent`/`acknowledged`-only receipt policy confirmed
  (`serial_controller.py:274-286`, `run_controller.py:307-320,576-584`).
- Calibration fit requires degree+1 distinct voltages (2 linear / 3 quadratic),
  `minimum_r_squared=0.98`, `maximum_condition_number=1.0e8`
  (`calibration.py:139-141,191-226`).
- Capture requests 3840×2160@60 via `dshow`/`v4l2`, records `video.partial.mkv`,
  fans out a 960×540@10 rgb24 preview, proves the 5-part startup criterion,
  escalates `q`→interrupt→kill, and requires ffprobe before promoting to
  `video.mkv` (`camera.py:141-243,377-620`, `ffmpeg.py:140-153,338-417`,
  `application/camera_capture.py:41`).
- Watchdog = `cycles * (on_ms + off_ms) / 1000 + grace`
  (`run_controller.py:587`); `finalize()` is idempotent, sends stop at most once,
  writes one manifest (`run_controller.py:422-522`).
- Angle is `degrees(atan2(dy, dx))` from base to tip (`analysis.py:132-139`);
  finalized analysis processes frame zero with measured FPS
  (`analysis_pipeline.py:231`). `tests/test_import_boundaries.py` bars
  PySide6/pyqtgraph/cv2 from `domain`/`application`. `AGENTS.md`,
  `docs/continuation-plan.md`, and ADRs 0001–0005 all exist.
