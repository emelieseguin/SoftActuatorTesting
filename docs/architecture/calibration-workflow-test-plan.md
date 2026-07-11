# Calibration workflow test plan

**Date:** 2026-07-11

The calibration domain tests cover finite/ranged known-pressure validation,
minimum distinct inputs, rank/conditioning rejection, residuals, fit metrics,
and quadratic adequacy.  Service tests use a deterministic structured sample
source to prove a requested capture is newer than its baseline sequence,
exercise record/edit/remove/clear/undo, and round-trip versioned and legacy
artifacts without hardware.  `pytest-qt` tests exercise editable/sortable
tables, visible residual plots, and actionable validation feedback using only
the fake source and fake file picker.

## Capture boundedness correction — 2026-07-11

Real capture is bridged from `SerialController.poll()` and its
`SerialAdapter`-owned reader/parser, never from raw serial text or `readline`.
Before `CAL_ON`, queued decoded telemetry is drained to establish the sequence
baseline. The bridge polls the controller queue only until a configurable
monotonic deadline, checks an explicit cancellation token between polls, and
always sends `CAL_OFF` in `finally` on success, timeout, cancellation, or
adapter fault.

`CalibrationPage` runs only sources marked as hardware/blocking in a bounded
`QThread`; fake sources remain synchronous for compact deterministic tests.
The page disables duplicate requests, offers Cancel, receives completion on the
GUI thread, and joins a cancelled worker during page teardown. Tests cover
bridge timeout/cancellation/release and Qt responsiveness, duplicate rejection,
cancel status, and worker cleanup.
