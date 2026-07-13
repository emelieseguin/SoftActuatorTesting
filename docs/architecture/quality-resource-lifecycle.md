# Resource lifecycle hardening test plan

**Date:** 2026-07-13  
**Scope:** Non-UI camera, serial, calibration, geometry/video, marker, and
finalized-analysis services.

## Regression plan

- Camera process cleanup must produce one terminal result even when a process
  escalation operation fails; finalization remains bounded and preserves the
  partial recording for diagnosis.
- Capture-health sampling must atomically preserve concurrent warnings, phase,
  and clean-state updates.
- Serial disconnect must not report success or permit a replacement reader when
  the existing reader misses its bounded join. Pending ACK waits must wake and
  report the closed transport rather than timing out silently.
- Calibration capture must reject a failed `CAL_ON`/`CAL_OFF`, preserve
  cancellation/timeout behavior when both commands succeed, and only accept
  telemetry observed after streaming was enabled.
- Video open/probe must observe cancellation before opening, during probing,
  and before returning a handle; failed replacement-handle cleanup must not
  silently corrupt the current geometry state.
- Marker request sequencing/settings snapshots must be safe for concurrent
  background scans, so a late result is always stale.
- Finalized-video results that are cancelled or truncated are explicitly
  non-authoritative and cannot be exported as persistent analysis artifacts.

All regressions use injected processes, transports, clocks, and in-memory
frames. The default suite does not require a camera, serial device, or external
FFmpeg executable.

## 2026-07-13 implementation evidence

The serial owner now refuses reconnect after a timed-out reader join and wakes
pending ACK waits with an explicit failed receipt. Camera drainer joins share
one cleanup deadline, and failed termination still produces a fault result.
Calibration verifies both streaming-command receipts and drains pre-command
telemetry before accepting a sample. Video probes check cancellation at every
open/probe boundary; geometry replacement retains its current handle when old
cleanup fails. Marker state is lock-protected, and non-authoritative analysis
prefixes are rejected by the artifact exporter.

Targeted lifecycle regressions passed (**130 tests**); the full default suite
passed (**536 tests, 1 deselected**).
