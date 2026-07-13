# Run lifecycle implementation

**Status:** Implemented (hardware-free composition).
**Date:** 2026-07-11.
**Related todo:** `run-lifecycle`.

`application.run_controller.RunController` is the Qt-free production
coordinator. Constructing it with no collaborators opens neither a serial port
nor a camera. A production composition injects `SerialController`,
`CameraCaptureService`, and `ArtifactFileStore`; views continue to consume
immutable presenter snapshots and may call `start_async` rather than block a
GUI thread.

`ui.production.create_production_composition` is the installed application's
explicit production composition root. It constructs no physical adapter by
default; an operator/integration layer supplies configured collaborators and
then explicitly connects them. Its Console binding polls immutable coordinator
snapshots on the Qt event loop, and production Global Stop/window close route
to the coordinator finalizer rather than a demo lifecycle.

The production root creates a disconnected `SerialAdapter` with
`PySerialTransportFactory` and the explicit unconfirmed legacy profile, a
native `QtFilePicker`, persistent workspace preferences, and a
`SerialCalibrationSampleSource`.  It binds an `ArtifactFileStore` only after a
workspace opens. FFmpeg discovery is executable-only; camera device discovery
remains an operator Refresh action, and unavailable FFmpeg leaves a clear
record-video-off path.

## Start gate and command behavior

Readiness rejects an empty experiment name, non-positive cycles/timing,
missing/inadequate calibration, missing geometry, disconnected or unprofiled
serial, unwritable/undersized storage, missing camera selection, and a profile
other than 3840x2160@60. Cyclic recording defaults to enabled.
The readiness page exposes the default-on **Record video** toggle; disabling it
skips camera selection/profile/service gates while retaining serial, workspace,
calibration, geometry, and durable pressure requirements.

The coordinator reserves durable artifacts and proves capture readiness
(negotiated profile, preview, output progress, and a growing output file) before
issuing legacy commands. The only command sequence is:

```text
CMD:SET CYCLES <n>
CMD:SET ON <milliseconds>
CMD:SET OFF <milliseconds>
CMD:START
```

It deliberately does not wait for or claim an ACK: the unconfirmed legacy
profile does not establish acknowledgement semantics.

Every command receipt is nevertheless checked: missing receipts and
`WRITE_FAILED`/non-sent states fault startup. A per-run generation/cancellation
gate is checked after artifact/camera acquisition and before each legacy
command, so Global Stop cannot allow a late `CMD:START`. Firmware end-run
markers complete cleanly without a redundant `CMD:STOP`; error frames, capture
faults/results, and an expected-duration watchdog (`cycles * (on + off)` plus
configured grace) use the same fault finalizer.

## Artifacts and finalization

`ArtifactFileStore.begin_run_artifacts` reserves a collision-safe run directory
and writes `pressure.csv` rows with flush plus `fsync` before UI decimation.
The V1 columns remain `schema_version,artifact_id,time_s,volts,pressure_kPa`;
the versioned manifest records units, decoded-telemetry provenance, calibration
raw-only behavior, command/profile metadata, completion, diagnostics, and
portable links to pressure and finalized video.
If a calibration becomes unavailable after a validated run starts, the
coordinator records subsequent rows with an empty `pressure_kPa` and a
raw-only diagnostic; it never drops voltage telemetry or fabricates pressure.

One idempotent finalizer serves clean completion, operator/global stop,
timeouts, controller/camera faults, startup exceptions, and window close. It
cancels the telemetry worker, attempts `CMD:STOP` once, stops capture, closes
and manifests artifacts, and reports every cleanup failure without deleting
partial output. A cleanup failure becomes `faulted` except an explicit
emergency abort remains `aborted`.

The manifest now embeds immutable calibration/geometry snapshots, serial and
platform diagnostics, capture negotiation/encoder/frame/drop health, and
workspace-portable output references. Header-only pressure CSVs are valid for
aborted/faulted runs that receive no telemetry.

Each terminal `run.json` now also contains additive `capture` evidence. It
captures every available typed `CaptureResult`/`CaptureHealth` fact (requested
target, device/encoder, sanitized FFmpeg metadata, proof/negotiation,
progress/preview, terminal cleanup, ffprobe readability, paths, and
promotion). It uses `null` for unavailable optional facts rather than
fabricating values, preserves partial paths only when workspace-relative, and
does not link the separate standalone Connections status file. The manifest is
written once by the shared finalizer; the capture result is cached before
manifest assembly so a camera that has already finalized cannot lose terminal
evidence.

## Test plan and evidence

`tests/application/test_run_controller.py` uses only fakes. It verifies camera
proof before `CMD:START`, exact ordering, default recording, all readiness
failures, durable telemetry versus bounded UI telemetry, explicit raw-only
pressure, duplicate finalization, every terminal route, close during a run,
and partial cleanup failures. Real device acceptance remains hardware-gated.
