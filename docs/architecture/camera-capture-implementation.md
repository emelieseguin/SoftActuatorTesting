# Camera capture implementation boundary

**Status:** Implemented in software; representative hardware acceptance blocked.
**Date:** 2026-07-11.
**Related:** [ADR 0004](0004-capture-pipeline-benchmark.md),
`camera-integration`, and blocked `hardware-4k60-validation`.

## Test plan

Before integration, the default hardware-free suite will cover:

- FFmpeg/FFprobe discovery, version/capability probing, absent-tool diagnostics,
  DirectShow/V4L2 device and exact 3840x2160@60 profile commands, negotiated
  profile rejection, malformed progress, and runtime encoder fallback;
- one scripted process feeding recording progress and complete preview frames,
  including startup failure, slow preview consumption, partial-file retention,
  atomic promotion only after `q` exits cleanly, startup proof, drainer joins,
  and ffprobe verification; repeated Stop, timeout escalation, disconnect,
  close, cyclic-completion reasons, and deterministic drainer cleanup;
- Qt-free timed capture coordination plus immutable presenter snapshots;
- a reusable headless Qt camera panel rendering status, health, preview, and
  commands, including collision-safe standalone Connections reservations;
- conservative storage refusal, runtime encode selection, and DirectShow/V4L2
  mode parsing/unsupported-mode diagnostics without physical hardware,
  including genuine FFmpeg V4L2 format-list output that omits frame rates and
  must remain non-blocking until negotiated startup proof.

An `external_ffmpeg` test is opt-in and uses only FFmpeg's synthetic `lavfi`
source. Physical DirectShow/V4L2 acceptance remains under the separately gated
`hardware` marker.

### Capture-manifest evidence — 2026-07-13

The run-controller/artifact-store tests must prove that a terminal cyclic
`run.json` preserves the complete typed `CaptureResult` and `CaptureHealth`
facts available to the controller. Coverage includes clean promotion, an
unclean retained partial, recording-disabled/unavailable capture, startup
failure, controller completion and operator stop, omitted optional evidence,
workspace-relative paths, command redaction, old V1 manifests without this
additive area, and duplicate finalization. No test treats unavailable
FFmpeg/FFprobe fields as successful evidence: they remain explicit `null` or
`unknown` values.

## Ownership boundary

- `application/camera_capture.py` owns capture-facing immutable state, the
  bounded latest-frame channel, timed coordination, and the camera-panel
  presenter. It is Qt-free and knows no FFmpeg command details.
- `infrastructure/ffmpeg.py` owns executable discovery, capability and actual
  encode probes, platform command construction, progress/format parsing,
  storage estimates, and ffprobe verification.
- `infrastructure/camera.py` is the single process owner. It opens one physical
  input, continuously drains preview/progress, proves startup, and routes every
  stop reason through one finalizer. Negotiation is taken from the first input
  stream and cannot be replaced by later record or downscaled-preview output
  descriptions. A readable partial is promoted only after startup proof, a
  cooperative `q`/zero-exit shutdown, joined drainers, and fault-free ffprobe
  verification; completion is determined from those facts rather than a
  reason-string allowlist. Rejected, escalated, or faulted captures retain the
  partial for diagnostics.
- `ui/widgets/camera_panel.py` renders the dedicated presenter and
  `VideoCanvas`; it owns no camera or subprocess resource. Window close waits
  for bounded finalization rather than abandoning a daemon cleanup worker.

Shared application presentation contracts and workflow pages are intentionally
unchanged. Later composition may embed the panel without moving process
ownership into `connections_diagnostics.py`, `live_run.py`, or `analysis.py`.

## Standalone Connections capture policy (2026-07-13)

Connections is a diagnostic capture surface, not a managed cyclic run. Each
Start reserves a new `<workspace>/runs/standalone-capture-<uuid>/` directory
before invoking FFmpeg; it never writes to a shared
`<workspace>/runs/video.mkv`. The directory contains a small
`capture-status.json` with the reservation identifier, state, video/partial
names, terminal reason, error, and typed shutdown evidence. This status is
deliberately independent of the run manifest, so cyclic capture serialization
does not make Connections depend on artifact-store internals. Cyclic `run.json`
now serializes its own typed
`CaptureResult`/`CaptureHealth` evidence, but does not link this standalone
status file because no typed result field represents that relationship.

Before every capture, production applies a conservative policy of 600 seconds
at 100 MiB/s plus a 1 GiB reserve (or the operator-configured panel duration)
and refuses to start when free space cannot satisfy it. The policy is a
preflight estimate, not a size or bitrate guarantee.

Refresh probes the selected DirectShow/V4L2 device's advertised modes. A
reported mode that does not include exact 3840×2160@60 is explicitly
unsupported; no capture starts. FFmpeg's V4L2 `-list_formats all` output may
list only format/resolution and omit frame rates. That incomplete evidence is a
visible non-blocking warning—not an invented 60 fps mode—and startup's
negotiated-profile proof remains the gate for that device. At capture start, a
short FFmpeg encode probe selects a usable NVENC/QSV/VAAPI/software H.264
encoder, with `libx264` only as the final tested fallback. These are host
capability checks, not physical 4K60 validation.

## Acceptance boundary

The code enforces the target dimensions and rate, but no camera pixel format,
encoder, timeout, bitrate, thermal limit, or package policy is accepted without
the native Windows/Linux evidence listed in ADR 0004. Synthetic tests verify
software behavior only; they do not complete `hardware-4k60-validation`.
