# Troubleshooting — SoftActuatorTesting Instrument Console

Symptom-driven fixes for operating the desktop application. Pair this with
[`operator-guide.md`](operator-guide.md) for the full workflow. Every message
quoted below is taken from the current implementation; if what you see is
worded differently, treat this document as a starting point, not a literal
string match.

## Contents

1. [No serial ports listed](#no-serial-ports-listed)
2. [Unknown protocol / field 3 uncertain](#unknown-protocol--field-3-uncertain)
3. [Command "sent" but nothing happens / ACK timeout](#command-sent-but-nothing-happens--ack-timeout)
4. [Calibration capture won't complete](#calibration-capture-wont-complete)
5. [Calibration fit is rejected](#calibration-fit-is-rejected)
6. [FFmpeg missing / no camera modes / camera unavailable](#ffmpeg-missing--no-camera-modes--camera-unavailable)
7. [Recording proof failure ("camera startup failed")](#recording-proof-failure-camera-startup-failed)
8. [Only a partial video file exists](#only-a-partial-video-file-exists)
9. [Workspace / path / storage / full-disk problems](#workspace--path--storage--full-disk-problems)
10. [Geometry / marker: no detection, ambiguity, manual fallback](#geometry--marker-no-detection-ambiguity-manual-fallback)
11. [Analysis result is CANCELLED, TRUNCATED, missing, or ambiguous](#analysis-result-is-cancelled-truncated-missing-or-ambiguous)
12. [Export is refused](#export-is-refused)
13. [Run readiness will not turn green](#run-readiness-will-not-turn-green)
14. [Cleanup and restart](#cleanup-and-restart)
15. [Diagnostics to collect before asking for help](#diagnostics-to-collect-before-asking-for-help)
16. [When *not* to continue a run](#when-not-to-continue-a-run)

---

## No serial ports listed

**Symptom:** clicking **Refresh ports** on the Connections page leaves the
Port dropdown empty.

The application enumerates ports the same way the OS reports them (pyserial's
port scan); an empty list means the OS reports no ports, not an application
bug.

- Confirm the USB/serial adapter is physically connected and the OS itself
  sees it (Device Manager on Windows; `ls /dev/tty*` or `dmesg` on Linux).
- On Linux, confirm your user has permission to open the device node
  (commonly the `dialout` group) — a permissions problem often surfaces later
  as a **Connect serial** failure rather than a missing port.
- If you just plugged the adapter in, click **Refresh ports** again; nothing
  is polled automatically.
- If a port disappears while connected, that's a physical/driver
  disconnection — disconnect in the app, reseat the cable, then reconnect.

## Unknown protocol / field 3 uncertain

**Symptom:** the Connections page's profile note says the parser profile is
unconfirmed, or telemetry seems to include a third field you don't fully
trust.

This is expected and by design, not a bug: the firmware's serial protocol
(field order, units, and acknowledgement semantics) has never been confirmed
from firmware source. The legacy transcript shows the old GUI attempting to
read a third CSV field as volts, but its own field-count check was incorrect
and its own code comment disagreed with that assumption. Consequently:

- The default parser configures **no** telemetry fields at all — it will not
  silently guess a meaning for unknown data.
- The `legacy-field-3-unconfirmed` parser profile is an **explicit, opt-in**
  configuration, not a default or an authoritative schema. Only enable/rely on
  it if you have independently confirmed what that field means for your rig.
- Do not treat any value derived from this field as calibrated pressure
  without independent verification.
- This uncertainty is unrelated to whether commands are being sent — see the
  next section for send/acknowledgement problems.

## Command "sent" but nothing happens / ACK timeout

**Symptom:** a calibration capture (or another correlated command) reports a
timeout, e.g. *"no fresh voltage sample arrived within N seconds"*, or a
command receipt shows a timed-out acknowledgement with detail *"No matching
acknowledgement arrived before timeout."*

- A `SENT` receipt only means the bytes were written to the serial port — not
  that the controller executed the command. Acknowledgements are opt-in per
  parser profile; a profile with no reliable correlation identifier does not
  wait for one at all.
- **The cyclic-run start sequence (`CMD:SET CYCLES`/`SET ON`/`SET OFF`/
  `CMD:START`) never waits for or claims an acknowledgement** — this is
  intentional, not a defect, because the unconfirmed legacy profile does not
  establish ACK semantics for that sequence. A "Ready"/"Running" indicator
  reflects the software state machine, not confirmed firmware execution.
- For calibration capture specifically: confirm the device is actually
  connected (Connection status = success/green) and streaming telemetry
  (watch the Connections diagnostic log for `CMD:CAL_ON` followed by
  telemetry frames) before requesting a sample. `CAL_OFF` is always sent
  afterward regardless of success, timeout, or cancellation — you do not need
  to send it manually.
- If a command receipt is `WRITE_FAILED` rather than `SENT`/`ACKNOWLEDGED`,
  that is a transport-level failure (e.g. the port was closed/disconnected
  mid-command) — reconnect and retry.
- A late/duplicate acknowledgement for an already-timed-out command is
  reported as a **late acknowledgement**, not treated as confirming a newer,
  different request — you may safely ignore an old late-ACK diagnostic line
  as long as the current operation already completed or timed out on its own.

## Calibration capture won't complete

**Symptom:** **Request fresh sample** never finishes, or fails immediately.

- *"capture baseline is no longer current"* — you (or a concurrent process)
  issued a second capture request before the first one's baseline was
  established. Wait for the active request to finish or press **Cancel
  capture**, then request again.
- *"no fresh voltage sample arrived within N seconds"* — see the ACK-timeout
  section above; confirm connection and telemetry streaming.
- *"`CMD:CAL_ON` was not sent successfully…"* (or `CAL_OFF`) — the streaming
  command itself failed at the transport level; check the serial connection.
- Capture cancellation is always safe: **Cancel capture** stops the wait and
  `CMD:CAL_OFF` is still sent to leave the device in a known state.

## Calibration fit is rejected

**Symptom:** **Fit calibration** shows a warning instead of a fit summary.

The fit-quality messages are specific about what to fix:

| Message (paraphrased) | What it means | Fix |
| --- | --- | --- |
| "*fitting requires at least N samples*" | Not enough recorded samples for the chosen model (2 for linear, 3 for quadratic) | Record more samples |
| "*fitting requires at least N distinct voltages*" | All/most samples share (nearly) the same measured voltage | Capture samples across a wider range of known pressures so voltage varies |
| "*calibration samples are poorly conditioned for this fit*" | Sample voltages are numerically too close together for a stable fit | Spread distinct voltage samples farther apart, or use a lower-order (linear) model |
| "*calibration samples do not support a full-rank fit*" | The sample set is degenerate for the chosen model | Add samples with genuinely different voltage/pressure pairs |
| "*R² X is below the required Y*" | The fit doesn't explain enough of the variance (default minimum R² is 0.98) | Recheck sample quality/labeling, capture more samples, or try a different model |
| "*RMSE X kPa exceeds Y kPa*" (only if a maximum RMSE policy is configured) | Residual error too large | Same as above |

A rejected fit is never silently accepted — `is_adequate` must be true (no
warnings) before the calibration will satisfy run readiness. Reviewing the
residual plot next to the samples table often shows which single sample is
the outlier worth removing or recapturing.

## FFmpeg missing / no camera modes / camera unavailable

**Symptom:** the Connections page shows *"Camera capture unavailable: install
FFmpeg/FFprobe or turn recording off."* instead of camera controls.

FFmpeg and FFprobe are **external prerequisites** — never bundled with the
application (source or packaged build).

1. Install a matching `ffmpeg`/`ffprobe` pair for your OS.
2. Either put both executables on `PATH`, or set the `SOFT_ACTUATOR_FFMPEG`
   environment variable to the FFmpeg executable path (with a matching
   `ffprobe` next to it, or also on `PATH`).
3. Restart the application — FFmpeg discovery only happens when the
   production composition is constructed at startup.

**Symptom:** FFmpeg is installed, but **Refresh cameras** reports *"No
cameras found."* or *"Camera discovery failed."*

- On Windows, device discovery lists DirectShow video device names; on Linux
  it lists `/dev/video*` nodes. If your camera does not appear under the OS's
  own camera list (Device Manager / `v4l2-ctl --list-devices`), the
  application will not see it either — this is an OS/driver problem, not an
  application bug.
- *"Camera discovery failed."* indicates the discovery command itself raised
  an error (e.g. FFmpeg became unavailable after startup, or a transient OS
  error) — retry **Refresh cameras**; if it persists, check the FFmpeg
  install again.
- Recording always targets the fixed 3840×2160 @ 60 fps profile; a camera
  that cannot negotiate that exact profile will fail readiness or startup
  proof even if it is otherwise detected — see the next section.

## Recording proof failure ("camera startup failed")

**Symptom:** camera capture reports a startup failure, or run readiness fails
even though a camera is selected.

The application requires **proof** that capture is actually working
(negotiated profile, encoder progress, a growing output file, and at least
one preview frame) before it will send `CMD:START` for a cyclic run. If that
proof does not arrive:

- Confirm the selected camera can actually deliver 3840×2160 @ 60 fps with the
  input pixel format FFmpeg expects — many USB cameras cannot, especially at
  60 fps, and the profile is not currently operator-configurable to a lower
  resolution/rate.
- Confirm no other application (or a second instance of this one) already has
  the camera open — one FFmpeg process owns one physical input.
- Check available encoder support (`ffmpeg -encoders`); note that an encoder
  merely being *listed* is not proof it can actually encode on this machine —
  a runtime encode probe failure will be reported as a separate, more specific
  error.
- Any capture that starts but then faults **preserves the partial recording
  file** rather than deleting it — see the next section before assuming
  nothing was captured.
- This remains software-verified only; physical 4K60 acceptance on real
  camera hardware is a separate, currently unmet requirement (see the
  operator guide's [Section 11](operator-guide.md#11-physical-4k60-recording-status)).

## Only a partial video file exists

**Symptom:** after a run or a manual capture, you find a partial video rather
than a finalized one.

This is expected behavior in several situations, not silent data loss:

- The authoritative output file is written *while recording*, then verified
  with `ffprobe` and **promoted** to its final name only after a clean,
  fault-free capture. A **rejected or faulted** capture deliberately
  **retains the partial file** for diagnostics rather than deleting it — look
  for it in the run's output folder even after a faulted run.
- If the run/capture faulted (watchdog timeout, camera fault, error frame,
  Global Stop), the recording never promotes; check the run manifest's
  `completion`/diagnostic/`cleanup_errors` fields (or the on-screen fault
  detail) for the specific reason.
- A partial file is still useful evidence: open it directly with the Video
  Geometry page's **Choose video…** picker to inspect what was actually
  captured before the fault.

## Workspace / path / storage / full-disk problems

**Symptom:** *"workspace is not writable: …"* or a run/capture refuses to
start with a storage message.

- **"workspace is not writable"** — the chosen workspace/storage root could
  not accept a small write-and-fsync probe file. Choose a different, writable
  folder (permissions, read-only mount, or a removed/ejected drive are common
  causes).
- **"workspace has N free bytes but run requires M"** — free space at the
  workspace's volume is below the run's estimated storage requirement. Free
  disk space, choose a workspace on a larger volume, or reduce the requested
  capture duration/size before starting.
- **"path escapes the workspace"** — an artifact or a legacy-import reference
  tried to point outside the workspace directory (e.g. via `..`); this is
  rejected to keep workspaces portable and self-contained. Re-select a file
  that is actually inside (or import into) the workspace.
- **"Configured workspace does not match artifact storage"** — an internal
  consistency check; typically resolved by closing and reopening the intended
  workspace rather than switching storage roots mid-session.
- **Workspace changes rejected while a run is active** — create/open/close/
  storage-root/individual-file actions are intentionally blocked while a run
  is starting, running, or stopping. Wait for the run to finalize (or Stop
  it), then retry.
- Individual-file mode (**Open individual files**) is read-only by design —
  if you need to save new artifacts, open or create an actual workspace
  instead.

## Geometry / marker: no detection, ambiguity, manual fallback

**Symptom:** **Detect marker candidates** reports **"No detection"**.

The status line always states this plainly (e.g. *"No detection: …"*) rather
than leaving an empty table with no explanation.

- Confirm the ROI actually contains the marker in the frame currently on
  screen — detection only scans inside the configured ROI.
- Try loosening thresholds: widen the hue/saturation/value ranges, lower
  **Minimum area (px)** or **Minimum circularity**, or reduce the **Base-point
  exclusion radius** if the marker sits close to the base point. Click
  **Apply thresholds** and detect again.
- **Fall back to manual placement at any time** — automation is advisory
  only. Select **Place tip point** and click directly on the marker in the
  canvas, or type exact coordinates into the Tip point (x, y) fields.

**Symptom:** the result is **"Ambiguous"**.

- This means the top two candidates' confidence scores are within the
  configured **Ambiguity margin** (default 0.05) — the application is
  deliberately refusing to silently pick one for you. Both candidates (and
  the mask preview) are still shown so you can make an informed manual
  choice.
- Either select the correct candidate row yourself and **Accept selected
  candidate**, or ignore both and place the tip manually.
- If ambiguity persists across frames, consider narrowing the ROI to exclude
  a decoy region, or raising the **Ambiguity margin**/tightening thresholds
  slightly so a clear best candidate can emerge — but always verify visually
  before accepting.

**Symptom:** acceptance is disabled after a detection completed.

- You likely scrubbed to a different frame after the scan finished — the
  status line will say the candidates are stale for the new frame index.
  Rerun detection on the frame currently shown; a stale result for a
  different frame can never be accepted.

## Analysis result is CANCELLED, TRUNCATED, missing, or ambiguous

*(This section applies to the full recorded-file/live analysis review UI. It
is wired into the default production build (`uv run soft-actuator-testing`) as
well as `--mode demo` and `--prototype experiment-studio` — see the operator
guide's
[Section 5.8](operator-guide.md#58-finalized-video-handoff-to-analysis) and
[Section 5.9](operator-guide.md#59-recorded-file-and-live-provisional-analysis-review).)*

- **CANCELLED** — you clicked **Cancel analysis** during a run. The partial
  result up to that point is kept and shown, but it is explicitly
  non-authoritative and the **Export results** button stays disabled for it.
  Rerun analysis from the start if you need a complete, exportable result.
- **TRUNCATED** — the pipeline hit a video read/decode failure partway
  through (for example, an over-reported frame count or a corrupt frame) and
  stopped at the last verified frame. Also non-authoritative and not
  exportable. Check that the source video file is not itself corrupted or
  still being written (e.g. a partial recording — see
  [above](#only-a-partial-video-file-exists)); re-run against the finalized,
  fully-written video once available.
- A per-frame result of **missing** means no marker was resolved for that
  frame (no tip point, no angle) — this is reported explicitly rather than
  reusing the previous frame's point or silently interpolating.
- A per-frame result of **ambiguous** means detection found competing
  candidates too close in confidence to auto-resolve for that frame,
  mirroring the geometry page's ambiguity behavior above.
- For either **missing** or **ambiguous** rows you disagree with, select the
  row, enter/confirm a point, and click **Apply correction** (or **Clear
  marker point** to explicitly reset it to missing). This recomputes a new,
  independent result — the original run's result object is never mutated, so
  you can always compare before/after.
- A **live capture** panel result is always prefixed **"Provisional (live) —
  not authoritative"** and can never appear in the results table or be
  exported — this is intentional, not a bug; only a completed recorded-file
  run is authoritative.

## Export is refused

**Symptom:** clicking **Export results** does nothing, or the button is disabled with
a message like *"Run an analysis before exporting results."* or *"Choose an
output location before exporting."*

Export is only enabled once **both** conditions hold:

1. the current result's completion is **COMPLETED** (authoritative) — a
   CANCELLED or TRUNCATED result can never be exported, by design; and
2. an output location — the open workspace / artifact store — is available.

If you need to export, first satisfy whichever of the two is missing, then
retry. Every successful export writes a **new** versioned results/manifest
artifact pair — re-running, re-correcting, and re-exporting never overwrites
an earlier exported artifact, so you can always find a previous export intact.

## Run readiness will not turn green

**Symptom:** the Readiness page shows a warning state and lists one or more
unmet requirements.

Readiness checks (each reported individually, in plain language) include:

- Experiment name is non-empty.
- Cycles, On (ms), and Off (ms) are all positive integers.
- A **validated, adequate** calibration is loaded (see
  [Calibration fit is rejected](#calibration-fit-is-rejected) if it fails
  adequacy).
- **Complete** video geometry (base, tip, and ROI) is set.
- If **Record video** is checked: a camera device is selected, and its
  negotiated profile is exactly 3840×2160 @ 60 fps.
- Estimated storage capacity is non-negative and the timeout grace period is
  a finite, non-negative duration (these are internal defaults; a message
  here usually indicates a configuration/build problem, not something to
  hand-tune).
- The serial controller is configured, **connected**, and has a telemetry
  profile assigned (not `unconfigured`) — an unconfirmed profile is allowed
  but is called out as a diagnostic note, not a blocking failure.
- Writable artifact storage is available and an **open workspace** matches
  it (see [Workspace / path / storage](#workspace--path--storage--full-disk-problems)
  above).
- If recording is enabled, a camera capture service must actually exist (i.e.
  FFmpeg was discoverable) — see
  [FFmpeg missing](#ffmpeg-missing--no-camera-modes--camera-unavailable).

Uncheck **Record video** if you only need pressure telemetry and want to skip
every camera-related requirement above — the serial, workspace, calibration,
and geometry requirements still apply.

## Cleanup and restart

- **Window close during an active run** is handled the same way as an
  explicit Stop: it routes through the single idempotent finalizer, attempts
  `CMD:STOP` once, stops capture, and closes/manifests artifacts before the
  window actually closes. You do not need to Stop manually before closing,
  but doing so first is fine and lets you see the finalization result on
  screen.
- If the application appears to hang on close, wait briefly — camera/serial
  cleanup is bounded (a few seconds) but is not instantaneous, especially
  while a capture is being verified with `ffprobe`.
- To restart after any fault: relaunch (`uv run soft-actuator-testing` or the
  packaged executable), reopen the same workspace from **Recent**, and use
  **Check readiness** before starting a new run. Nothing persists mid-run
  state across a restart — a faulted run's artifacts remain on disk exactly
  as the finalizer left them.
- If serial or camera state seems stuck (e.g. **Disconnect serial** does
  nothing visible), close and reopen the affected page's underlying
  connection explicitly rather than assuming a background reconnect will
  happen — nothing reconnects automatically.

## Diagnostics to collect before asking for help

Before reporting a problem, gather:

1. **Exact on-screen message(s)** from the relevant page's status/diagnostic
   label (most pages state the specific failure inline) — a screenshot or
   copied text is more useful than a paraphrase.
2. The **Event Log** dock's recent entries (right-click/tab to it if hidden
   behind another dock).
3. For serial issues: the Connections page's diagnostic log, the configured
   port/baud/timeout, and whether the `legacy-field-3-unconfirmed` profile is
   active.
4. For camera/recording issues: the FFmpeg/FFprobe version (`ffmpeg
   -version`), the selected camera device name/node, and whether the failure
   happened at discovery, startup proof, or mid-recording.
5. For a faulted run: the run's `run.json` manifest (`completion`, `reason`,
   `warnings`, `cleanup_errors`) and whether a partial video/pressure CSV
   exists in that run's folder.
6. For analysis problems: the source video path, the geometry artifact ID
   used, and whether the result was COMPLETED/CANCELLED/TRUNCATED.
7. Application version (`uv run soft-actuator-testing --version`) and
   platform (Linux/Windows, source or packaged build).

## When *not* to continue a run

Stop (Global Stop, then investigate) rather than continuing if you observe
any of the following — do not simply retry or ignore them:

- **Readiness will not turn green** for a requirement you cannot explain —
  starting anyway is not possible (Start stays disabled), but do not work
  around it by disabling checks you don't understand (e.g. turning off
  recording just to bypass a camera problem you haven't diagnosed).
- **The serial connection shows Fault** or repeated write failures — a
  physically or electrically unreliable connection can produce inconsistent
  command delivery; fix the connection before running an experiment whose
  data you intend to trust.
- **A camera fault or dropped-frame count is climbing** during a run — the
  recording may not be usable for later analysis; stop, diagnose (cabling,
  USB bandwidth, thermal throttling), and restart cleanly rather than
  continuing to accumulate an unusable file.
- **You are relying on the unconfirmed `legacy-field-3-unconfirmed` field**
  for a safety-relevant decision — this field's meaning has never been
  confirmed from firmware; do not treat it as ground truth for anything
  safety-relevant.
- **You need certified 4K60 physical performance** (e.g. for a safety case or
  formal acceptance) — that certification does not exist yet; do not present
  a passing software/synthetic test as physical hardware evidence.
- **The physical rig's own emergency-stop has not been exercised/verified**
  before the session — this application's Global Stop is a software command
  only and is not a substitute for it.

## Verification Summary

_Fact-check pass dated 2026-07-13, after the production analysis integration._
Every quoted message, UI label, default value, and behavioral claim was checked
against the current source and tests.

**Claim counts**

| Category | Extracted | Verified accurate | Corrected |
| --- | --- | --- | --- |
| Quoted error/status strings | 24 | 24 | 0 |
| UI labels (buttons/controls) | 12 | 10 | 2 |
| Quantitative defaults (R² 0.98, margin 0.05, 4K60) | 5 | 5 | 0 |
| Behavioral (finalizer, proof-first, corrections) | 14 | 14 | 0 |
| Workflow/wiring (analysis availability) | 3 | 2 | 1 |
| Command sequences (`CMD:*`) | 3 | 3 | 0 |
| Status (4K60 certification) | 2 | 2 | 0 |
| **Total** | **63** | **60** | **3** |

**Corrections made**

1. **"Analysis result is CANCELLED, TRUNCATED, missing, or ambiguous"** — The
   section note previously said the review UI is "currently reachable via
   `--mode demo` or `--prototype experiment-studio`" with a "production wiring
   status" caveat. It is now wired into the default production build
   (`uv run soft-actuator-testing`) as well; the note and its links were
   updated (`src/soft_actuator_testing/ui/production.py:198-206`;
   `tests/ui/test_production_composition.py:151-236`).
2. **Label fixes** — **Cancel** → **Cancel analysis** and **Export** →
   **Export results** to match the actual button labels
   (`src/soft_actuator_testing/ui/views/workflows/analysis.py:379,456`).
3. **"Export is refused"** — Reworded condition 2 so the output location is
   described as *available* (the open workspace/artifact store) rather than
   *selected*, reflecting the workspace-bound production output
   (`analysis.py:585-614`).

**Verified-accurate exact strings (spot list)**

`"Camera capture unavailable: install FFmpeg/FFprobe or turn recording off."`,
`"No cameras found."`, `"Camera discovery failed."`, `"workspace is not
writable: …"`, `"workspace has N free bytes but run requires M"`, `"path
escapes the workspace"`, `"Configured workspace does not match artifact
storage."`, `"Run an analysis before exporting results."`, `"Choose an output
location before exporting."`, calibration fit rejections (`R² … below the
required …`, `RMSE … kPa exceeds … kPa`, `… requires at least N samples`,
`… requires at least N distinct voltages`, `poorly conditioned`, `full-rank`),
and `CMD:CAL_ON`/`CMD:CAL_OFF`/`CMD:SET CYCLES|ON|OFF`/`CMD:START`/`CMD:STOP`.

**Unverifiable claims (retained, stated honestly)**

- Physical 4K60 acceptance is asserted as an unmet requirement / software-only
  verification — a negative status claim consistent with the operator guide's
  §11 and `docs/hardware-4k60-acceptance.md`.
