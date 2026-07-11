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
  atomic promotion after ffprobe, repeated Stop, timeout escalation, disconnect,
  close, and deterministic drainer cleanup;
- Qt-free timed capture coordination plus immutable presenter snapshots;
- a reusable headless Qt camera panel rendering status, health, preview, and
  commands without composing it into Connections, Live Run, or Analysis.

An `external_ffmpeg` test is opt-in and uses only FFmpeg's synthetic `lavfi`
source. Physical DirectShow/V4L2 acceptance remains under the separately gated
`hardware` marker.

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
  descriptions. A readable partial is promoted only after startup proof and a
  fault-free capture; rejected or faulted captures retain the partial for
  diagnostics.
- `ui/widgets/camera_panel.py` renders the dedicated presenter and
  `VideoCanvas`; it owns no camera or subprocess resource. Window close waits
  for bounded finalization rather than abandoning a daemon cleanup worker.

Shared application presentation contracts and workflow pages are intentionally
unchanged. Later composition may embed the panel without moving process
ownership into `connections_diagnostics.py`, `live_run.py`, or `analysis.py`.

## Acceptance boundary

The code enforces the target dimensions and rate, but no camera pixel format,
encoder, timeout, bitrate, thermal limit, or package policy is accepted without
the native Windows/Linux evidence listed in ADR 0004. Synthetic tests verify
software behavior only; they do not complete `hardware-4k60-validation`.
