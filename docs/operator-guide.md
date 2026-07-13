# Operator guide — SoftActuatorTesting Instrument Console

**Audience:** the person running the desktop application to calibrate,
connect, and operate a soft-actuator test rig, then review results.
**Scope:** operating the application as built today. Where a capability is
incomplete, disconnected from production, or blocked on hardware evidence,
this guide says so explicitly instead of describing a future or aspirational
behavior.

This guide does not describe firmware, wiring, or lab safety procedures for
the physical rig itself — only the desktop application.

## 1. What the application is (and is not)

The application is one Python/PySide6 (Qt) desktop program named
**Instrument Console**. It is not a web app and has no server component.

- It never opens a serial port, enumerates cameras, or otherwise touches
  hardware at startup or at window construction. Every connection is an
  explicit operator action (Refresh / Connect / Start).
- Its "software Stop" (Global Stop) commands the application's own run
  lifecycle and asks the controller to stop. **It is not a physical
  emergency-stop interlock.** It cannot guarantee the rig itself is in a
  physically safe state — see [Section 9](#9-safety-limitations).
- Physical recording at 3840×2160 (4K) at 60 fps is implemented in software
  but **not certified on real hardware** — see
  [Section 11](#11-physical-4k60-recording-status).

## 2. Installing

### 2.1 From source (any supported platform)

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.10–3.13.

```bash
git clone https://github.com/emelieseguin/SoftActuatorTesting.git
cd SoftActuatorTesting
uv sync
uv run pytest
```

`uv sync` reproduces the managed environment (PySide6/Qt, OpenCV, PyQtGraph,
pyserial). `uv run pytest` runs the full hardware-free, external-FFmpeg-free
default test suite; it should pass with no failures before you rely on the
build.

Launch the application from source with:

```bash
uv run soft-actuator-testing
```

### 2.2 Packaged builds — honest platform status

Native, one-directory PyInstaller bundles are built **per platform, on that
platform** — a Linux bundle is never cross-built on Windows or vice versa.

| Platform | Status |
| --- | --- |
| **Linux** | Built and hardware-free-smoke-tested on a native Linux host. This is the only platform with a verified packaged build as of this writing. |
| **Windows** | Build and smoke commands are documented and the configuration can be dry-run-inspected from any host, but **no Windows executable has been produced or run**. Do not treat Windows packaging as verified until it has been built and smoked on an actual Windows runner. |

Build a bundle yourself:

```bash
# Linux (run on a Linux host)
uv run python tools/package_desktop.py --platform linux
uv run python tools/smoke_desktop.py --platform linux

# Windows PowerShell (run on a Windows host)
uv run python tools/package_desktop.py --platform windows
uv run python tools/smoke_desktop.py --platform windows
```

The resulting bundle is written to `dist/desktop/<platform>/SoftActuatorTesting/`
and contains the application, Python runtime, Qt/PySide6, OpenCV, PyQtGraph,
project metadata, and a `licenses/` folder. It deliberately does **not**
include tests, legacy environments, or FFmpeg (see
[Section 10](#10-ffmpeg-is-an-external-prerequisite)).

## 3. Starting the application safely

```bash
uv run soft-actuator-testing --help
```

```
usage: soft-actuator-testing [-h] [--no-gui] [--smoke-imports]
                             [--mode {production,demo}] [--prototype SHELL]
                             [--version]
```

| Flag | Effect |
| --- | --- |
| *(none)* | Opens the **production** Instrument Console. Real, disconnected workspace/serial/camera/calibration/geometry services are constructed; nothing is opened until you act. |
| `--no-gui` | Prints an installation/status line and exits. Opens no window, no hardware. Use this to sanity-check an install. |
| `--smoke-imports` | Verifies the packaged Qt plugin path, OpenCV, and PyQtGraph resources import correctly. Opens no window, dialog, camera, or serial port. |
| `--mode demo` | Opens the same Instrument Console shell wired to **deterministic fake services** (no real hardware adapters exist at all). Useful for training or exploring the UI without a rig. |
| `--prototype experiment-studio` | Opens the **rejected** Experiment Studio shell for development comparison only; it is not the shipped production shell and also always uses fake demo services. |
| `--version` | Prints the installed version. |

Both `--mode demo` and `--prototype experiment-studio` are always
demo/fake-service compositions. **An explicit `--mode production` combined
with `--prototype` is rejected outright** — the CLI exits with status code
`2` and an actionable error message on stderr; it never silently launches
either shell. This is the same unambiguous precedence enforced a second time
by `create_application_window()` for any other (non-CLI) caller, so the
production/prototype conflict cannot be resolved silently regardless of
entry point. Only running with no `--mode`/`--prototype` flags (or explicit
`--mode production` alone) opens the real, disconnected production
composition described in the rest of this guide.

The window title bar and a status-strip label always say **"Production"** or
**"Demo"** so you can tell at a glance which one is open, and the status bar
states that hardware is disconnected until you act.

## 4. The Instrument Console shell

The production shell has:

- a **left navigation rail** with one button per destination (see below),
  each with an `Ctrl+1`…`Ctrl+8` shortcut in registration order (Workspace,
  Connections, Calibration, Geometry/Marker Setup, Readiness, Live Run,
  Analysis, Settings);
- a **persistent top status strip** showing Connection, Calibration, Camera,
  Storage, Run, and Fault indicators, plus the always-visible **⏹ STOP**
  button;
- a **central workspace** showing the currently selected destination page;
- four dockable panels (Telemetry plot, Run Control, Event Log, File/Context)
  that can be moved, floated, or tabbed like any Qt dock.

None of the status indicators communicate state by color alone — each pairs a
semantic color with a text label (e.g. "Ready", "Fault"), consistent with the
project's no-color-only-meaning accessibility rule.

### Global Stop

- Button: **⏹ STOP** in the top status strip.
- Menu action / keyboard shortcut: **Ctrl+Shift+S**, active application-wide.
- Effect in production mode: immediately asks the run coordinator to abort
  the active run. This routes through the same single, idempotent finalizer
  used by every other stop path (clean completion, operator Stop, fault,
  timeout, or window close), so it cannot double-finalize or leave an
  artifact half-written.
- The Stop button/action are only enabled while a run is `starting`,
  `running`, or `stopping`.
- **This is a software command, not a physical interlock.** See
  [Section 9](#9-safety-limitations).

### Layout save/restore

The **View** menu (and `Ctrl+Shift+L`/`Ctrl+Shift+R`) lets you save and
restore the arrangement of the four dockable panels (Telemetry, Run Control,
Event Log, File/Context):

- **Save layout** writes the current dock/toolbar arrangement to a small,
  versioned JSON file in the OS-standard per-user configuration location
  (`~/.config/soft-actuator-testing/console-layout.json` on Linux;
  `%APPDATA%\SoftActuatorTesting\console-layout.json` on Windows), so it
  survives across separate runs of the application — not just within the
  current session.
- **Restore layout** re-applies the last saved arrangement. If no layout was
  ever saved, or the saved file is missing, unreadable, or was written by an
  incompatible schema version, restore is a safe no-op: it leaves the
  current on-screen layout unchanged and reports this in the status bar and
  event log rather than raising an error.
- Both actions only ever replay previously captured window geometry/dock
  state. **Neither action ever contacts a device, opens a port, starts a
  camera, or moves/hides the Global Stop control** — restoring a layout
  cannot change hardware/run state.
- This is a distinct feature from the separate **in-memory-only** demo
  layout save/restore available when the application is started with
  `--mode demo`; the demo affordance is never written to disk and does not
  persist across restarts.

## 5. Complete operator workflow

Work through the destinations in navigation order. Each is a full page; nothing
here is a demo placeholder unless explicitly marked below.

### 5.1 Workspace

A **workspace** is a directory holding one or more versioned artifact
documents (workspace/calibration/geometry/run/analysis JSON plus CSVs),
written through the application's atomic, collision-safe artifact store. A
workspace is **portable**: all stored references are workspace-relative, so
moving/copying the folder and reopening it elsewhere works, and reopening does
not depend on the directory you launched the app from.

Actions on the Workspace page:

- **Choose storage root** — pick the writable parent folder used for new
  workspaces and camera output.
- **Create workspace** — creates a new workspace using the "New name" field
  under the chosen storage root.
- **Open workspace** — opens an existing workspace directory (also supported
  by dragging a folder onto the page).
- **Open individual files** — an explicit **read-only** mode for opening one
  or more standalone artifact JSON files without treating them as a workspace.
  The button supports selecting multiple files at once (via the standard
  multi-select file dialog, fully operable by keyboard alone — Tab to the
  button, activate it, then use the OS file dialog's own multi-select
  keyboard controls), with the same deterministic, order-preserving,
  no-workspace-required import behavior as dragging multiple files onto the
  page.
- **Save workspace** — writes a new workspace document (never overwrites an
  existing one).
- **Close workspace** — clears the active workspace.
- A **Recent** dropdown remembers previously opened workspaces across
  restarts (persisted to the OS configuration location, not the current
  directory).

No workspace = no artifact store. Until a workspace is open, calibration,
geometry, and run artifacts have nowhere to save, and run readiness will
report the workspace requirement as unmet.

**While a run is starting, running, or stopping**, workspace-changing
commands (create, open, close, choosing a new storage root, opening
individual files) are rejected with an on-screen message; retry after the run
finalizes.

### 5.2 Connections — serial and camera

**Serial (top of page):**

1. **Refresh ports** — lists serial ports using the OS port enumeration; an
   empty list means no ports were found (see
   [troubleshooting](troubleshooting.md#no-serial-ports-listed)).
2. Choose/enter the **Port**, **Baud** (default 115200), and **Read timeout
   (ms)** (default 500).
3. **Connect serial** / **Disconnect serial**.
4. A profile note states the active parser profile and whether its field
   mapping is unconfirmed (see
   [Section 9](#9-safety-limitations) and
   [troubleshooting](troubleshooting.md#unknown-protocol--field-3-uncertain)).
5. **Send legacy start / Send legacy stop / Enable legacy calibration /
   Disable legacy calibration** send raw `CMD:START` / `CMD:STOP` /
   `CMD:CAL_ON` / `CMD:CAL_OFF` text directly to the connected device. **These
   are manual diagnostic commands, not the managed cyclic-run path** — they
   do not reserve artifacts, do not check readiness, and do not prove camera
   capture first. Prefer the Readiness → Live Run flow in
   [Section 5.5](#55-readiness) /
   [5.6](#56-live-run-and-cyclic-operation) for an actual experiment.
6. A read-only diagnostic log shows recent frames/events.

**Camera (bottom of page):** only appears if FFmpeg/FFprobe were discoverable
at composition time (see [Section 10](#10-ffmpeg-is-an-external-prerequisite));
otherwise the page shows: *"Camera capture unavailable: install FFmpeg/FFprobe
or turn recording off."*

1. **Refresh cameras** — enumerates DirectShow device names (Windows) or
   `/dev/video*` nodes (Linux). It does not run automatically; you must click
   it (unlike the demo/prototype shells).
2. Choose a **Camera** device and (optionally) a **Duration** — leave at
   "Until stopped" to record/preview indefinitely.
3. **Start capture** / **Stop capture**. Start is disabled until a workspace
   is open and a device is selected. Each Start creates a unique
   `runs/standalone-capture-.../` directory, so it never overwrites another
   Connections recording. Its `capture-status.json` records the final
   state/reason and preserves a partial file when capture is not clean.
4. A live preview and a capture-health line (phase, dropped-frame count,
   negotiated profile) update continuously.

Camera capture uses FFmpeg to own the one physical input and prove startup
(negotiated profile, progress, growing output file, preview frame) before it
is considered usable; OpenCV is used only for later offline analysis, never
for the authoritative 4K recording.

Refresh also probes the camera's advertised DirectShow/V4L2 modes. A camera
that reports frame-rate-capable modes but no exact 3840×2160@60 mode is shown
as unsupported and cannot start. FFmpeg V4L2 format listings can omit frame
rates; that is shown as a warning and the requested/negotiated profile at
startup remains the actual gate. Before capture, the application checks free
space using the selected duration (or a conservative ten-minute policy) plus a
reserve; free-space refusal is intentional. A completed `video.mkv` exists
only after FFmpeg exited cooperatively, its pipes drained, and FFprobe verified
the partial; otherwise inspect the retained `video.partial.mkv` and status
file.

### 5.3 Calibration

1. Enter a **Known pressure (kPa)**.
2. **Request fresh sample** — sends `CMD:CAL_ON`, waits for the next
   telemetry row newer than the request, then always sends `CMD:CAL_OFF`
   (on success, timeout, cancellation, or fault). **Cancel capture** aborts an
   in-flight request.
3. **Record sample** — adds the captured (pressure, voltage) pair to the
   samples table (editable pressure/voltage/sequence columns; **Remove
   selected**, **Clear samples**, **Undo** are available).
4. Choose a **Model** (Linear or Quadratic) and click **Fit calibration**.
   The fit summary reports R², RMSE, and adequacy; warnings explain exactly
   why a fit was rejected (see
   [troubleshooting](troubleshooting.md#calibration-fit-is-rejected)).
   The sample/fit and residual plots render immediately.
5. Persist the fit: **Save versioned** / **Load versioned** (workspace
   artifacts) or **Import legacy JSON** / **Export legacy JSON** for the
   older flat format.

A calibration only satisfies run readiness once it is fitted **and**
`is_adequate` (R² and RMSE within the configured policy — default minimum
R² 0.98). The demo-only **"Load presenter demo samples"** button is hidden in
production mode; production always captures real samples over serial.

### 5.4 Video Geometry / Marker Setup

1. **Choose video…** to load a prerecorded `.avi`/`.mp4`/`.mov`/`.mkv` file
   (or **Close video**).
2. Scrub with **First/Previous/Next/Last frame** buttons, the on-canvas
   **Left/Right arrow keys** (step one frame) and **Home/End** (jump to
   first/last frame), or **Use as representative frame** to record the frame
   used for authoring.
3. **Zoom in/out**, **Pan left/right/up/down**, **Fit to frame**, **Reset
   view** control the preview only; they do not change geometry.
4. Choose a placement tool (**No placement tool / Place base point / Place
   tip point / Draw ROI**) and click on the canvas, or edit the **Base point
   (x, y)**, **Tip point (x, y)**, and **ROI (x, y, w, h)** fields/nudge
   arrows directly. **Clear tip point** removes the tip only.
5. **Undo / Redo / Reset selections** manage the manual-edit history.
6. **Save versioned / Load versioned / Import legacy JSON / Export legacy
   JSON** persist the geometry the same way calibration does.

**Guided marker suggestions (below the manual editor):** an *advisory*
detector, never automatic:

1. Adjust HSV/morphology/scoring thresholds if needed, then **Apply
   thresholds**.
2. **Detect marker candidates** runs a bounded, cancellable scan (dual-hue red
   detection) of the frame currently on screen; **Cancel detection** stops it.
3. Review the ranked candidate table (rank, confidence, tip point,
   human-readable reasons) and the detection **mask preview**. The status line
   explicitly states **"No detection"**, **"Ambiguous"** (two top candidates
   too close in confidence — see
   [troubleshooting](troubleshooting.md#geometry--marker-no-detection-ambiguity-manual-fallback)),
   or **"Resolved"**.
4. Select a candidate row and click **Accept selected candidate** to apply it
   as the tip point — exactly like a manual tip placement. It is never
   applied automatically.
5. Manually correcting an accepted tip afterward (drag/nudge/spinbox) reverts
   its provenance back to manual — a corrected point is never mistaken for an
   unreviewed automatic suggestion.
6. If you scrub to a different frame after a scan, acceptance is disabled and
   the status line tells you the candidates are stale for the new frame; rerun
   detection.

Complete geometry (base, tip, and ROI) is required for run readiness and for
analysis.

### 5.5 Readiness

Configure the experiment and check whether a cyclic run can start:

- **Experiment** name, **Cycles**, **On (ms)**, **Off (ms)**.
- **Record video** (checked by default) — unchecking it skips the camera
  device/profile/service requirements while keeping the serial, workspace,
  calibration, geometry, and storage requirements.
- The page shows the currently **Selected camera** (from the Connections
  page) and a **Check readiness** button (also re-evaluated automatically).
- The readiness indicator and detail text list every unmet requirement in
  plain language — see
  [troubleshooting](troubleshooting.md#run-readiness-will-not-turn-green) for
  the exact set of checks and how to satisfy each one.

Recording, when enabled, always targets the fixed **3840×2160 @ 60 fps**
profile; readiness fails if a different profile is negotiated.

### 5.6 Live Run and cyclic operation

- **Start run** is enabled only once readiness passes and no run is already
  active.
- **Stop run** is enabled only while a run is starting/running/stopping.
- A live pressure plot and a diagnostic detail line show run progress.
- Internally, the controller proves camera capture (negotiated profile,
  progress, growing file, preview) **before** sending any serial command, then
  sends exactly:

  ```text
  CMD:SET CYCLES <n>
  CMD:SET ON <milliseconds>
  CMD:SET OFF <milliseconds>
  CMD:START
  ```

  It does not wait for or claim an acknowledgement for this sequence — the
  unconfirmed legacy protocol does not establish ACK semantics for it (see
  [Section 9](#9-safety-limitations)). A firmware end-of-run marker completes
  the run cleanly without a redundant `CMD:STOP`.
- An expected-duration watchdog (`cycles × (on + off)` plus a configured
  grace period), any error frame, or a camera fault will fault and finalize
  the run automatically.
- Telemetry is durably written (flushed and fsynced) before it reaches the
  UI, so what you see on screen is never more current — or more authoritative
  — than what has already been saved.
- If a calibration becomes unavailable mid-run, subsequent rows are recorded
  with an empty `pressure_kPa` (explicit raw-only) rather than a fabricated or
  dropped value.

### 5.7 Fault recovery

Any of: an operator Stop, Global Stop, a fault, a watchdog timeout, or
closing the window routes through the **same single, idempotent finalizer**.
Whichever happens first wins; a late `CMD:START` cannot slip through after
Stop has already been issued. Partial output (including a partial video) is
always preserved, never deleted, for diagnostics. After the run finalizes
(clean, stopped, aborted, or faulted), the Readiness page and Start button
return to normal — press **Check readiness** and correct anything reported
before starting again.

The terminal `runs/<run-id>/run.json` is the durable capture record. Its
additive `capture` area records the requested target, available startup and
negotiation proof, progress/drop counters, preview counters, terminal
cleanup/promotion facts, FFprobe readability, and relative partial/final
paths. A retained partial is intentionally represented as such rather than as
a successful recording. Fields that the software did not receive (for example
an FFprobe duration or preview timestamp) are `null`; do not treat them as
zero. Any recorded FFmpeg command is sanitized so it does not retain secrets,
private environment substitutions, external paths, or URLs.

### 5.8 Finalized-video handoff to Analysis

When a cyclic run finalizes with a recorded video, the Analysis destination
receives it automatically. Its **Finalized recording handoff** section shows
the recording's path with the message *"Finalized recording is ready for
authoritative analysis handoff."* and enables the **Use as recorded-file
source** button. If the run had no recording (disabled, unavailable, or never
finalized), it instead shows *"No finalized video is available from this run."*
and leaves that button disabled. Before any run finalizes, the section reads
*"Waiting for a finalized recording from a production run."*

Clicking **Use as recorded-file source** loads that finalized recording as the
recorded-file analysis source (exactly as if you had picked it with **Choose
video**). It does **not** automatically start an analysis run, start the
camera, or export anything — you still complete the recorded-file workflow in
[Section 5.9](#59-recorded-file-and-live-provisional-analysis-review) yourself.

The default production build launched by `uv run soft-actuator-testing` (no
flags) instantiates the real analysis review UI (`AnalysisPage`, constructed
with `production_mode=True` and `workspace_output_only=True`): authoritative
recorded-file analysis, a provisional shared-camera live preview, review with
corrections and export, workspace-bound artifact output, and this
finalized-video handoff. It is **not** a handoff-only display.

### 5.9 Recorded-file and live provisional analysis review

*(Available in the default production build as well as via `--mode demo` and
the `--prototype experiment-studio` shell.)*

**Recorded-file analysis** (the *"Recorded-file analysis (finalized video)"*
section):

1. Choose a video — via **Choose video**, or via the finalized-recording
   handoff's **Use as recorded-file source** button ([Section
   5.8](#58-finalized-video-handoff-to-analysis)) — enter a **Geometry artifact
   ID** and click **Load geometry**. In production the output is bound to the
   open workspace (**Choose output location** is disabled and the Output line
   reads *"Workspace output: …"*), so **Run analysis** is disabled until a
   video, a loaded geometry artifact, and an open workspace are all present.
2. Each processed frame updates the frame preview, a progress bar, and a
   results table row (detection state, confidence, reasons) in real time.
   Frame zero is measured and shown, never skipped.
3. **Cancel analysis** stops an in-flight run early. The run's status is always
   shown as exactly one of:
   - **COMPLETED** — authoritative; may be exported.
   - **CANCELLED** — a non-authoritative partial result; cannot be exported.
   - **TRUNCATED** — a non-authoritative partial result following a read/decode
     failure; cannot be exported.
4. Select a results row to review/correct it: enter a new point and **Apply
   correction**, or **Clear marker point** to mark it `missing`. Either
   recomputes a brand-new result (the original is never mutated); the results
   table's **Corrected** column shows `yes` (or `no`) as text, not color.
5. **Export results** is enabled only for a COMPLETED, authoritative result
   with an output location (the open workspace) available; each export writes a
   new versioned results/manifest artifact pair and never overwrites a prior
   export. If the chosen video is outside the workspace, export first copies it
   unchanged to a collision-safe `video/analysis-imports/` location and records
   that portable workspace-relative copy. A manifest write failure rolls back
   the new results artifact rather than leaving a results-only export.

**Live Capture (provisional only):** shows a live, camera-fed preview scored
against the same detector, always labeled **"Provisional (live) — not
authoritative"** and **"preview-derived"**. Full-resolution base/tip/ROI
geometry is deterministically transformed into the displayed preview's pixels;
the saved geometry is never changed. The production 960×540 proxy uses an
explicit stretch transform (rather than hidden letterboxing/cropping), so its
candidate and overlay coordinates describe the displayed pixels. It is a
one-slot channel: a result published before the
previous one is read is dropped, and the dropped count is shown
(`dropped-stale=N`). A provisional result never appears in the results table
and can never be exported — only a completed recorded-file/finalized-video run
is authoritative.

### 5.10 Settings / Help

In production mode, this page is a static reminder: hardware stays
disconnected until you use Refresh/Connect, and a workspace must be chosen
before capturing artifacts or running a cycle. (The interactive profile/demo
density controls and help text seen in `--mode demo` are demo-only and are
replaced by this static page in production.)

## 6. Where output files live

Everything under an open workspace directory:

| Artifact | Path (relative to the workspace) |
| --- | --- |
| Workspace document | `artifacts/workspace/<id>.json` |
| Calibration | `artifacts/calibration/<id>.json` |
| Geometry | `artifacts/geometry/<id>.json` |
| Run manifest / pressure | `runs/<run-id>/run.json`, `runs/<run-id>/pressure.csv` |
| Analysis manifest / results | `analysis/<analysis-id>/analysis.json`, `analysis/<analysis-id>/angles.csv` |
| Recorded video | Under `runs/<run-id>/` alongside the run manifest, referenced by a workspace-relative path in `run.json` |

All references are workspace-relative, so a workspace directory can be moved
or copied elsewhere (including onto removable storage) and reopened without
edits — this is what "portable workspace" means in this application. Writes
are atomic (temp file + fsync + replace, plus a directory fsync on
non-Windows systems); a workspace document is never silently overwritten —
**Save workspace** always creates a new versioned document.

Persistent app preferences (recent workspaces, chosen storage root) live in
the OS configuration location, not the workspace or the current directory:
Windows `%APPDATA%/SoftActuatorTesting/workspace-settings.json`; Linux/Unix
`$XDG_CONFIG_HOME/soft-actuator-testing/` (or `~/.config/soft-actuator-testing/`).

## 7. Visual state semantics

- Status indicators (Connection, Calibration, Camera, Storage, Run, Fault)
  use a semantic color **and** a plain-text state — never color alone.
- Run/Fault semantics: **Fault** state → error color; **Completed** with an
  aborted/faulted outcome → error color; **Completed** cleanly, or **Ready** →
  success color; connecting/starting/running/stopping → informational color;
  anything else → neutral.
- The results table's **Corrected** column shows the literal text `yes` (or
  `no`), not a colored cell, to mark corrected analysis rows.
- The window title bar and a status-strip label always say "Production" or
  "Demo" so the active mode is never ambiguous.

## 8. Accessibility and keyboard operation

- Every interactive control has an accessible name/description; no control
  relies on a mouse-only gesture without a keyboard alternative.
- Video-geometry scrubbing/nudging: **Left/Right arrows** step one frame,
  **Home/End** jump to first/last frame; every mouse-driven placement tool
  (base/tip/ROI) has an equivalent numeric spin box and nudge-arrow buttons.
- Navigation between destinations: `Ctrl+1` through `Ctrl+8` (in the order
  Workspace, Connections, Calibration, Geometry, Readiness, Live Run,
  Analysis, Settings).
- Global Stop: `Ctrl+Shift+S`, application-wide.
- Layout save/restore: `Ctrl+Shift+L` / `Ctrl+Shift+R` (see
  [Layout save/restore](#layout-saverestore)).
- Docked panels (Telemetry, Run Control, Event Log, File/Context) are
  standard Qt dock widgets — movable, floatable, and tabbable with the
  keyboard/mouse like any other Qt dock.
- "Open individual files" supports keyboard-driven multi-file selection with
  the same import behavior as multi-file drag/drop (see
  [Section 5.1](#51-workspace)).

Automated, CI-enforced evidence for keyboard focus traversal, shortcut
activation, and representative 100/150/200% scaling is described in
`docs/architecture/quality-ui-accessibility.md` and exercised by
`tests/ui/test_instrument_console.py`. Platform-specific manual evidence
(real screen readers, real OS-level DPI scaling, and a representative
operator study) is tracked separately — and, as of this writing, is still
**pending** — in
[`docs/architecture/manual-accessibility-dpi-evidence-matrix.md`](architecture/manual-accessibility-dpi-evidence-matrix.md).

## 9. Safety limitations

Read this section before relying on the application around a physical rig.

- **Global Stop is a software command to this application's run controller,
  not a physical emergency-stop interlock.** It asks the coordinator to abort
  and finalize; it cannot guarantee the actuator, controller, or camera are
  in a physically safe state. Maintain and use an independent physical
  emergency stop for the rig itself.
- **The cyclic-run command sequence (`CMD:SET CYCLES`/`CMD:SET ON`/`CMD:SET
  OFF`/`CMD:START`) does not wait for or claim an acknowledgement.** The
  firmware's acknowledgement and field-order semantics are unconfirmed; a
  "sent" command receipt means the bytes were written to the serial port, not
  that the controller executed it. Verify actual rig behavior independently
  before trusting a Ready/Running indicator as ground truth.
- **The "Send legacy start/stop" and "Enable/Disable legacy calibration"
  buttons on the Connections page bypass run readiness entirely** — they send
  raw commands with no camera-proof-first ordering, no artifact reservation,
  and no watchdog. Do not use them as a substitute for the Readiness → Live
  Run flow when running an actual experiment.
- **Marker/geometry automation is advisory only.** No suggested marker point
  is ever applied without an explicit operator "Accept" action, and it can be
  corrected at any time.
- **Physical 3840×2160@60 recording is not certified** — see
  [Section 11](#11-physical-4k60-recording-status).

## 10. FFmpeg is an external prerequisite

FFmpeg/FFprobe are **not bundled** with the application (Linux or Windows
build). Install a matching `ffmpeg`/`ffprobe` pair and either put both on
`PATH` or set the `SOFT_ACTUATOR_FFMPEG` environment variable to the FFmpeg
executable (with a matching `ffprobe` alongside it or on `PATH`).

If FFmpeg/FFprobe cannot be found at startup, this is treated as a **normal
disconnected-camera state**, not a crash:

- the Connections page's camera section is replaced with: *"Camera capture
  unavailable: install FFmpeg/FFprobe or turn recording off."*
- the rest of the application (workspace, serial, calibration, geometry,
  readiness with recording turned off) remains fully usable.

This policy exists partly to avoid silently redistributing an FFmpeg build
whose codec configuration could carry GPL source-offer obligations — see
`docs/architecture/desktop-packaging.md` (packaging/licensing detail; owned
by the packaging documentation, not this guide).

## 11. Physical 4K60 recording status

**Software support for 3840×2160 at 60 fps capture is implemented and is
covered by synthetic, hardware-free tests. It is explicitly *not* certified
on physical hardware.** Certification remains blocked until representative
native Windows (DirectShow) and native Linux (V4L2) rigs are available and an
owner supplies run-duration/soak, dropped/duplicate-frame limits,
startup/preview-latency limits, minimum provisional-analysis rate,
resource/thermal/storage limits, recording codec/quality expectations, and the
FFmpeg bundling-versus-prerequisite policy. Passing synthetic/software tests
is evidence for the software design; it is not physical certification and
must not be described as such to a rig operator or safety reviewer.

## 12. Getting help

- `uv run soft-actuator-testing --help` — CLI flags.
- [`troubleshooting.md`](troubleshooting.md) — symptom-driven fixes for
  serial, calibration, camera/FFmpeg, workspace/storage, geometry/marker,
  and analysis problems, diagnostics to collect, and when to stop rather
  than continue a run.
- The in-app **Event Log** dock and each page's status/diagnostic text are
  the first place to look; most pages surface the exact failure reason
  inline rather than only in a log.

## Verification Summary

_Fact-check pass dated 2026-07-13, after the production analysis integration._
Every quantitative, naming, behavioral, command, UI-label, workflow, platform,
artifact, safety, and status claim in this guide was checked against the
current source, tests, packaging tooling, and CLI `--help`/`--version` output.

**Claim counts**

| Category | Extracted | Verified accurate | Corrected |
| --- | --- | --- | --- |
| Quantitative (defaults, profiles, thresholds) | 9 | 9 | 0 |
| Naming (app/shells/pages/paths/env vars) | 12 | 12 | 0 |
| Behavioral (workflow/finalizer/analysis logic) | 22 | 20 | 2 |
| Command (serial `CMD:*` sequences) | 5 | 5 | 0 |
| UI-label (buttons, status strings, columns) | 17 | 14 | 3 |
| Workflow (compositions, handoff, readiness) | 7 | 6 | 1 |
| Platform (Linux/Windows packaging status) | 3 | 3 | 0 |
| Artifact (workspace-relative output paths) | 6 | 6 | 0 |
| Safety (Global Stop, unconfirmed protocol) | 5 | 5 | 0 |
| Status (4K60 certification) | 2 | 2 | 0 |
| **Total** | **88** | **82** | **6** |

**Corrections made**

1. **§5.8 (major)** — Removed the stale claim that the default production
   Analysis destination is "a handoff display only" and that the analysis
   review UI is "wired only into `--mode demo` … and `--prototype
   experiment-studio`, not into the default production composition." The
   production composition now instantiates the real
   `AnalysisPage(production_mode=True, workspace_output_only=True)`
   (`src/soft_actuator_testing/ui/production.py:198-206`). Documented the
   **Use as recorded-file source** handoff button and clarified it does not
   automatically start a run, start the camera, or export (verified by
   `tests/ui/test_production_composition.py:225-236`).
2. **§5.9** — Removed the "Available today via `--mode demo` /
   `--prototype experiment-studio`" restriction; the review UI ships in the
   default production build.
3. **§5.9** — Corrected the output-location step: in production the output is
   bound to the open workspace (**Choose output location** is disabled and the
   Output line reads *"Workspace output: …"*), and **Run analysis** requires a
   video, a loaded geometry artifact, and an open workspace
   (`analysis.py:355-357,585-614,641-651`).
4. **§5.9** — Corrected button labels: **Cancel** → **Cancel analysis**,
   **Export** → **Export results** (`analysis.py:379,456`).
5. **§5.9** — Corrected "corrected rows show `Corrected = yes`" to the results
   table's **Corrected** column showing `yes`/`no` as text
   (`analysis.py:416,789`; `tests/ui/test_analysis_review.py:275`).
6. **§7** — Corrected the same `Corrected = yes` literal claim to the
   **Corrected** column value.

**Unverifiable claims (retained, stated honestly)**

- The absence of a produced/run Windows executable (§2.2) is an environmental
  negative that cannot be positively confirmed from this repository; the guide
  already frames Windows packaging as unverified.
- Physical 3840×2160@60 hardware certification (§11) is asserted as *not*
  certified — a negative status claim consistent with
  `docs/hardware-4k60-acceptance.md` and the software-only `TARGET_4K60`
  support in `src/soft_actuator_testing/application/camera_capture.py:41`.
