# SoftActuatorTesting: initial implementation analysis

**Status:** legacy workflow inventory and rewrite starting point (2026-07-11).
**Scope:** the repository currently contains legacy, operator-facing scripts and
one notebook under [`old-files/`](../../old-files/). It does **not** yet contain
the proposed `src/` rewrite.

## Executive summary

The legacy software supports a soft pneumatic-actuator experiment in four
separate, interactive steps:

1. calibrate a voltage-bearing pressure signal against known pressure;
2. command an attached controller and collect its serial telemetry while
   optionally recording a camera;
3. mark actuator geometry on the resulting video; and
4. extract an actuator-angle time series from the video.

The current hardware/data path is **serial based**, not NI based:
`pyserial` opens one operator-selected port at 115200 baud, firmware is expected
to accept text commands and emit text telemetry, and the main GUI writes its
CSV only when firmware text markers arrive. There are **no** `nidaqmx` imports,
NI device names, NI channels, or NI-DAQmx API calls in any legacy artifact.
NI-DAQmx is a researched **rewrite option**, not an implemented dependency or
current acquisition path. See [NI-DAQmx integration](nidaqmx-integration.md).

The intended replacement is a reliable, testable Python application under
`src/` that separates: hardware acquisition/control, calibration, run lifecycle
and persistence, camera management, and offline analysis. It should preserve
useful legacy artifact contracts during migration while explicitly versioning
new ones.

## Scope and artifact inventory

| Artifact | Current role | Main inputs/outputs |
| --- | --- | --- |
| [`DataCollection-V2.py`](../../old-files/DataCollection-V2.py) | Tk main test GUI | Serial commands/telemetry; calibration JSON; optional FFmpeg video; run CSV |
| [`PressureCalibration.py`](../../old-files/PressureCalibration.py) | Tk calibration GUI | Serial text and operator-entered kPa; calibration JSON |
| [`VideoConfig.py`](../../old-files/VideoConfig.py) | First-frame geometry annotation | Existing video; `<video>_config.json` |
| [`PneumaticActuatorAnalysis-V1.ipynb`](../../old-files/PneumaticActuatorAnalysis-V1.ipynb) | Interactive video-angle extraction | Video + config JSON; `analysis_<timestamp>.csv` |
| [`pyproject.toml`](../../old-files/pyproject.toml) and [`uv.lock`](../../old-files/uv.lock) | Reproducible legacy environment | Base, plotting, and notebook dependency sets |
| [`old-files/README.md`](../../old-files/README.md) | Legacy environment/operator command reference | Installation and non-hardware checks |

Detailed component findings are deliberately kept separate:

- [Data collection analysis](data-collection.md)
- [Calibration and video analysis](calibration-and-video.md)
- [Notebook analysis](notebook-analysis.md)
- [NI-DAQmx integration guide](nidaqmx-integration.md)
- [Legacy environment README](../../old-files/README.md)

## Current end-to-end architecture and data flow

```text
Operator inputs
  ├─ known pressure (kPa), serial port ──> PressureCalibration GUI
  │                                         └─ calibration JSON
  ├─ serial port, cycles, ON/OFF seconds,
  │  calibration JSON, optional camera ──> DataCollection GUI
  │                                         ├─ serial CMD:* text to controller
  │                                         ├─ controller text telemetry
  │                                         ├─ runs/run_<timestamp>/data.csv
  │                                         └─ optional video.mkv (FFmpeg)
  └─ video + mouse/key annotation ───────> VideoConfig
                                            └─ <video>_config.json

video.mkv (or another selected video) + config JSON ──> notebook
                                                       └─ analysis_<timestamp>.csv
```

### Acquisition/control path

`DataCollection-V2.py` enumerates OS serial ports and opens the selected one
with `pyserial` at **115200 baud** and a **0.5 s** timeout. A daemon
`SerialReader` thread calls `readline()`, decodes UTF-8 with replacement, and
puts text into a queue; the Tk thread drains that queue every 100 ms.

The operator enters total cycles and ON/OFF durations in seconds. **Set Params**
converts durations to integer milliseconds and writes newline-terminated UTF-8
commands:

```text
CMD:SET CYCLES <cycles>
CMD:SET ON <milliseconds>
CMD:SET OFF <milliseconds>
CMD:START
CMD:STOP
```

The controller firmware is not in this repository. Its command acknowledgement,
exact telemetry schema, marker timing, actuator/valve wiring, and all safety
behavior are therefore unknown. The main GUI assumes comma-separated telemetry
and attempts to parse `parts[2]` as volts, although its source comment says
`time,voltage,pressureRaw,...`; this is an unresolved protocol inconsistency.

At **Start Run**, the GUI creates `runs/run_YYYYmmdd_HHMMSS/` relative to its
current working directory. If a camera name was selected, it launches FFmpeg
before sending `CMD:START`. It does not require a connected serial device or
prove that the camera opened first. The run CSV is not opened at Start; it is
opened only when incoming text begins with `--- new run ---`. An incoming line
beginning with `--- end run ---` closes the CSV and stops the FFmpeg recorder.
Thus the legacy run lifecycle depends on controller-emitted markers.

### Calibration path

`PressureCalibration.py` independently opens a serial port with the same
115200/0.5 s settings. It takes the first regex-matched numeric token from each
received line as the latest voltage. An operator enters the corresponding known
pressure in **kPa** and records samples as `(known_kPa, volts)`.

The tool can request a sample by sending `CMD:CAL_ON`, waiting 300 ms, and
sending `CMD:CAL_OFF`; there is no acknowledgement, buffer flush, or proof that
the captured value is new. It fits either:

- linear: `pressure_kPa = a * volts + b`
- quadratic: `pressure_kPa = a * volts² + b * volts + c`

and saves a user-selected JSON file. The data-collection GUI loads `model` and
its `coeffs`; it ignores the saved sample list.

### Camera/video path

Selecting and “connecting” a camera in the main GUI only saves a DirectShow
device **name** in GUI state. Camera discovery and recording call `ffmpeg`
through the Windows DirectShow input (`dshow`); recording requests
3840×2160 at 60 fps with MJPEG input and Intel Quick Sync H.264 (`h264_qsv`)
output to `video.mkv`. This is a Windows/DirectShow/Intel-QSV-oriented path,
not a portable camera abstraction.

`VideoConfig.py` does not acquire from a camera. It asks the operator to choose
an existing `.mp4`, `.avi`, `.mov`, or `.mkv`, reads its first frame, and
records clicks for an actuator base, actuator tip, and rectangular ROI.

### Offline notebook path

The notebook uses dialogs to select a video, a config JSON, and an output
directory. It reads the video—not the main GUI's pressure CSV—then detects
red blobs inside the annotated ROI, selects the farthest valid centroid from
the base, smooths accepted points over five detections, and computes a signed
acute angle. It writes a separate per-frame angle CSV. It neither reads nor
synchronizes with the pressure data CSV.

## Current implementation boundaries vs. NI-DAQmx plan

| Area | Current verified behavior | Planned/recommended rewrite direction |
| --- | --- | --- |
| Pressure input | Text from a serial controller, parsed in GUI code | A replaceable pressure-sample source; an NI adapter may return voltage samples |
| Actuation | Text commands sent to firmware | Keep controller initially, or separately decide on NI digital/analog/counter outputs |
| Calibration | Project JSON and Python volts-to-kPa formula | Retain project-level calibration initially for auditability and migration compatibility |
| Camera sync | Software order: start FFmpeg, then send serial start | Keep software mode initially; add hardware triggering only after camera/PFI capabilities are known |
| NI-DAQmx | Not installed or called by legacy scripts | Add `nidaqmx` to the rewrite only after hardware/driver/channel decisions |

The NI path requires both the Python binding and the native NI-DAQmx driver.
`uv sync` alone cannot make a host hardware-ready. The detailed guide includes
the authoritative sources, a voltage-input example, timing choices, task
lifecycle guidance, and migration boundary:
[NI-DAQmx integration](nidaqmx-integration.md#sources).

## Configuration prerequisites

### Verified in code or packaging

- Python is declared as `>=3.9,<3.14` by the legacy package.
- Base dependencies are `numpy`, `opencv-python`, and `pyserial`.
- The `plotting` extra provides `matplotlib`; the `notebook` extra provides
  `ipykernel` and `notebook`.
- The serial GUIs use Tkinter and require an OS-recognized serial port.
- The data GUI requires an `ffmpeg` executable on `PATH` to enumerate or record
  its optional DirectShow camera.
- `VideoConfig.py` and the notebook require a local GUI session, Tk support,
  and OpenCV display support. They work on existing files, not live cameras.
- The legacy code contains no NI-DAQmx dependency, import, device, or channel.

### Lab-specific details that remain unknown

These must be recorded before deploying a rewrite; they are not discoverable
from this repository:

- controller model, firmware version/source, serial message schema, and
  command acknowledgement semantics;
- pressure-sensor model, output type/range, scaling, calibration procedure,
  safe pressure limits, and pneumatic/valve interlocks;
- camera model, supported modes, lighting/marker arrangement, and whether it
  accepts an external trigger;
- host OS, serial driver, FFmpeg build, DirectShow compatibility, and
  Intel-QSV driver/GPU availability;
- any NI device model, device name, physical channel, terminal configuration,
  voltage range, sample rate, trigger line, driver version, and safe output
  state.

For a future NI host, install the native driver using NI's official download
and verify the device in NI MAX or NI Hardware Configuration Utility before
adding application logic. The exact compatibility matrix is hardware and
OS-specific; see the [official NI download page](https://www.ni.com/downloads/)
and [NI Python API documentation](https://nidaqmx-python.readthedocs.io/).

## Install and safe non-hardware verification

Run legacy commands from `old-files/`; filenames contain hyphens and are not
Python module names or console entry points.

```bash
cd old-files

# Full legacy environment, including optional plot and notebook support
uv sync --all-extras --locked

# Or choose exactly one smaller environment:
uv sync --locked
uv sync --locked --extra plotting
uv sync --locked --extra notebook
```

The following checks are safe for a machine without experiment hardware. They
do not execute `__main__`, open a serial port, start FFmpeg, access a camera,
or open Tk/OpenCV windows. `uv build` creates a local `dist/` build artifact,
which may be removed afterward if it is not wanted.

```bash
cd old-files
uv lock --check
uv sync --all-extras --locked
uv build
uv run python -m py_compile DataCollection-V2.py PressureCalibration.py VideoConfig.py
uv run python - <<'PY'
import importlib.util
from pathlib import Path

for filename in ("DataCollection-V2.py", "PressureCalibration.py", "VideoConfig.py"):
    spec = importlib.util.spec_from_file_location(
        Path(filename).stem.replace("-", "_"), filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print(f"Imported {filename}")
PY
uv run python - <<'PY'
import nbformat
from pathlib import Path

notebook = nbformat.read(Path("PneumaticActuatorAnalysis-V1.ipynb"), as_version=4)
nbformat.validate(notebook)
print(f"Notebook is valid nbformat {notebook.nbformat}.{notebook.nbformat_minor}.")
PY
```

These checks verify packaging, syntax, imports, and notebook structure only.
They do not validate a serial controller, sensor values, FFmpeg/Quick Sync,
camera capture, calibration accuracy, pneumatic safety, or the notebook's
interactive image-processing behavior.

## Legacy operator workflow

Use this order only on an authorized, physically safe test rig with a trained
operator. It describes current behavior; it is **not** a safety procedure or
an endorsement of the legacy implementation.

1. Confirm physical rig safety and the lab-specific controller/camera/driver
   setup outside the software. Do not infer limits or interlocks from code.
2. If a calibration is needed, run:
   `uv run python PressureCalibration.py`.
   Select the serial port, collect known-kPa/voltage pairs, fit linear or
   quadratic, and save JSON. A quadratic fit requires the `numpy` base
   dependency; live calibration plotting needs the `plotting` extra.
3. Run `uv run python DataCollection-V2.py`. Select and connect the controller
   serial port, then load the calibration JSON. Without calibration, the
   current CSV path can fail when it tries to format `None` as pressure.
4. Enter cycles and ON/OFF seconds; press **Set Params** and confirm the
   controller behavior independently because the UI does not parse a command
   acknowledgement.
5. If recording video, select a discovered DirectShow device and press
   **Connect Camera**. This does not prove capture works. Ensure FFmpeg,
   DirectShow, QSV, and the requested 4K60 mode are actually usable.
6. Press **Start Run**. Watch serial console output. The expected firmware
   markers are `--- new run ---` (CSV opens) and `--- end run ---` (CSV/video
   close). **Stop Run** only sends `CMD:STOP`; wait for the end marker.
7. For offline geometry, run `uv run python VideoConfig.py`, choose the video,
   press `b` then click base, `t` then click tip, `a` then click top-left,
   press `a` again then click bottom-right, and press `s` to save/preview.
8. Run `uv run jupyter notebook PneumaticActuatorAnalysis-V1.ipynb`. Execute
   path selection, set `exclusion_radius`, then execute the analysis cell.
   Select the video, its config JSON, and an output directory. Watch `Analysis`
   and press `q` to stop early.

Do not close or disconnect the main GUI expecting it to finish an active run:
legacy shutdown does not close an active CSV or stop an active FFmpeg process.

## Inputs, protocols, persisted outputs, and units

### Inputs and protocol assumptions

| Producer | Input | Current format/units |
| --- | --- | --- |
| Calibration GUI | Serial telemetry | Arbitrary text; first regex-matched numeric token is treated as volts (V) |
| Calibration GUI | Known reference | Operator numeric entry in kPa |
| Data GUI | Serial telemetry | Comma-separated text assumed; code attempts the third field as volts (V) |
| Data GUI | Test settings | Integer cycles; ON/OFF entered in s, transmitted as integer ms |
| Video config | Geometry | Mouse-selected pixel coordinates on first frame |
| Notebook | Video/config/output folder | Runtime dialogs; video types `.mp4`, `.avi`, `.mov`, `.mkv` |

No repository source defines a definitive firmware frame schema or validates
that the calibration GUI and data GUI identify the same voltage field. That is
a rewrite prerequisite, not an established protocol.

### Files and schemas

| File/output | Location/name | Schema and notes |
| --- | --- | --- |
| Calibration JSON | User-chosen path | `{"model": {"type": "linear"|"quadratic", "coeffs": [...]}, "samples": [[known_kPa, volts], ...]}`; tuples serialize as arrays |
| Run directory | `runs/run_YYYYmmdd_HHMMSS/` | Relative to the data GUI working directory; timestamps can collide within a second |
| Pressure CSV | `<run-dir>/data.csv` | Header exactly `time_s,volts,pressure_kPa`; time is local wall-clock elapsed seconds from new-run marker, voltage is V, pressure is kPa |
| Camera video | `<run-dir>/video.mkv` | Created only when a selected camera is present at start; FFmpeg output, no embedded run metadata contract |
| Video config | `<video_basename>_config.json` beside selected video | `angle_base_point: {x,y}`, `angle_tip_point: {x,y}`, `actuator_roi: {x,y,w,h}`; all coordinates/pixel sizes are pixels |
| Notebook angle CSV | Chosen folder, `analysis_YYYYmmdd_HHMMSS.csv` | Header exactly `Frame,ActuatorAngle_deg`; frame index is unitless and angle is degrees; missing initial detections write `nan` |

The notebook accepts ROI JSON either as `x,y,w,h` or
`top_left,bottom_right`; `VideoConfig.py` writes the former. The notebook
requires base/ROI and ignores `angle_tip_point`. It consumes the first video
frame only for window sizing, so its CSV begins with frame number 1 for the
second frame read. It does not record FPS-derived timestamps, source paths,
pressure, or a detection-quality flag, and saves no annotated video.

### Live visual outputs

- Calibration: live voltage label, optional volts-versus-known-kPa scatter/fit
  plot, samples list, and model label.
- Data collection: console, raw voltage label, optional calibrated pressure
  label and pressure plot. The 2,000-point plot uses time relative to its first
  plotted sample. The displayed cycle-left, run-time, and sample-rate fields
  are created but not updated.
- Video configuration: `Frame` overlay and a post-save `Preview` window.
- Notebook: `Analysis` OpenCV window showing ROI/base/tip overlays; no saved
  visualization or plot.

## Lifecycle, errors, and safety-relevant behavior

### Current resource behavior

- Both serial GUIs use daemon reader threads and stop/close serial on normal
  UI shutdown, but neither joins the reader thread.
- The data GUI starts recurring queue polling every 100 ms and attempts to
  cancel that callback during shutdown. It has no centralized run finalizer.
- The data CSV opens only on a new-run marker and is not explicitly flushed for
  each row. It closes only on the end-run marker.
- FFmpeg is terminated and waited on synchronously in the Tk thread when an
  end-run marker arrives; a hung process can freeze the GUI.
- Disconnecting the data GUI's camera only clears the selected name; it does
  not stop an already active recorder.
- `VideoConfig.py` releases its one-frame `VideoCapture` and destroys windows;
  a JSON write failure is not caught.
- The notebook closes its CSV, releases video capture, and destroys windows in
  its normal frame-loop `finally` block, but failures before that protected
  region have weaker cleanup.

### Confirmed constraints and defects

Do not treat the following as hypothetical rewrite requirements; they are
verified limitations of the current code:

1. Main-GUI run state (`run_active`, `run_file`, `current_run_dir`, and
   `run_start_time_wall`) is not initialized in `App.__init__`; telemetry
   before a new-run marker can stop queue processing with `AttributeError`.
2. The main parser checks for at least two CSV fields but accesses field three.
3. An uncalibrated data row formats `pressure=None` with `:.6f`, raising
   `TypeError`.
4. Manual stop, serial disconnect, and window close do not directly close a
   run CSV or stop camera capture; end-marker delivery is required.
5. `gui_start_recording()`/`gui_stop_recording()` are orphaned alternate
   recording methods. The reachable camera “connect” operation is only GUI
   state, not capture validation.
6. ON/OFF values lack positive/range/finite validation; values such as `nan`
   can fail outside the intended input-error handler. There are no software
   interlocks, pressure limits, or actuator safety checks before `CMD:START`.
7. Calibration can misidentify a timestamp or other unrelated number as volts;
   its 300 ms request path can capture stale telemetry, and a two-sample
   quadratic fit is permitted.
8. Video config can save missing geometry as empty/defaulted values, can retain
   module-global state across in-process reruns, and does not normalize
   reverse-order ROI clicks.
9. The notebook's config-cancel path calls the nonexistent
   `filedialog.messagebox`; it discards the first analyzed video frame, retains
   a stale tip buffer across misses, can overwrite same-second output names,
   and relies on a simple red-blob/farthest-centroid heuristic.
10. Broad exception handling hides failures in several GUI/plotting paths.
    In both serial GUIs, unexpected line-handler exceptions can stop scheduled
    queue processing.

## Prioritized rewrite roadmap

All items below are recommendations, not behavior already delivered.

### Phase 0 — establish safety and interfaces

1. Obtain and version-control (without secrets) the approved controller
   protocol, pressure limits, wiring/sensor specification, operator safety
   procedure, and expected safe state.
2. Define typed, schema-versioned calibration, video-config, run-metadata, and
   telemetry contracts. Specify missing values, units, validation, and
   compatibility policy.
3. Create testable `src/` modules for calibration, protocol parsing, run state,
   persistence, and configuration validation before building a new GUI.
4. Implement one idempotent run finalizer that closes/flushed files, stops the
   recorder, stops/cleans hardware, records clean/aborted status, and is called
   for stop, close, exception, and Ctrl+C.

### Phase 1 — stabilize serial migration

1. Implement a serial adapter with explicit frame types, acknowledgement and
   timeout handling, parser errors, bounded queues, and single ownership of
   serial resources.
2. Preserve volts and kPa in a documented run schema, but make calibrated and
   raw-only capture intentional rather than allowing `None` formatting.
3. Validate finite positive duration/cycle inputs and enforce configuration
   limits approved for the rig.
4. Replace GUI-owned FFmpeg lifecycle with a camera adapter that reports
   startup failure and stops with timeout/error reporting.
5. Make analysis batchable/testable; normalize ROI, validate all geometry, use
   a detection-quality flag, and record timestamps/provenance.

### Phase 2 — introduce NI-DAQmx behind an adapter

1. Add `nidaqmx` through `uv` only in the new application environment after
   verifying a native driver and physical/simulated device.
2. Define a `PressureSampleSource`-style interface that yields monotonic
   timestamps plus voltage samples. Keep calibration, CSV policy, camera, and
   GUI outside that adapter.
3. Implement the serial source and an NI analog-voltage source with the same
   logical output shape; compare results side by side for known inputs.
4. Externalize device/channel, input range, terminal configuration, sample
   rate, buffer size, timeout, trigger, and safe-state settings. Use one owner
   for each NI task and close tasks deterministically.

### Phase 3 — timing, data quality, and production readiness

1. Select continuous versus finite hardware-timed acquisition from measured
   scientific requirements; use buffered reads outside the UI thread.
2. Decide whether actuation remains firmware controlled or moves to NI
   output/counter channels, with safe idle behavior explicitly configured.
3. Add a documented software-sync mode and, if hardware allows it, a
   trigger/signal-routing mode with persisted synchronization metadata.
4. Add provenance, collision-resistant run IDs, durable logs, recovery policy,
   and a migration tool for legacy JSON/CSV artifacts.

## Verification and testing strategy

| Level | What to verify | Hardware requirement |
| --- | --- | --- |
| Packaging/static | Lock consistency, build, Python compilation, import safety, notebook validation | None |
| Unit | Calibration formulas/arity, protocol frames, state transitions, config validation, paths, CSV rows, detector behavior on saved frames | None |
| Adapter integration | Serial fake and deterministic pressure-source fake; failure/timeout/finalizer paths | None |
| NI adapter | Device lookup, task/timing configuration, reads, error translation, cleanup | NI simulated device where supported |
| Rig acceptance | Wiring/polarity/range/noise, limits/interlocks, controller protocol, real camera mode, timing latency, output safe state | Authorized real rig |
| Regression | Known videos/configs with expected angles and quality flags; schema compatibility fixtures | Existing files; GUI not required |

The initial non-hardware commands above are the current minimum baseline.
Future tests should run with `uv run pytest` from the rewrite root and should
not require actual hardware by default. Real hardware tests must be explicit,
isolated, and gated by the lab safety process.

## Decisions required before implementation

1. What is the authoritative firmware protocol, including field order, units,
   message versioning, acknowledgements, run markers, and abort semantics?
2. What pressure sensor, wiring, physical range, calibration reference, and
   safe pressure/actuation limits apply?
3. Will valve control remain on the microcontroller, migrate to NI outputs, or
   support both? What must every error/shutdown state drive outputs to?
4. Which NI hardware, OS, native-driver version, channel names, terminal
   configuration, voltage range, sample rate, and trigger terminals are
   approved?
5. What temporal precision is required between pressure, actuator control, and
   video? Can the camera accept/generate hardware triggers?
6. Is preserving the legacy CSV/JSON schema mandatory, or can a versioned,
   provenance-rich format replace it?
7. What red marker/lighting/camera geometry is reliable enough for angle
   extraction, and what uncertainty/quality reporting is required?
8. Which desktop OS/camera encoder combinations must be supported, and is the
   current DirectShow/QSV constraint acceptable?

## Authoritative references

- Legacy package and commands: [`old-files/README.md`](../../old-files/README.md)
  and [`old-files/pyproject.toml`](../../old-files/pyproject.toml)
- Component facts: [data collection](data-collection.md),
  [calibration/video](calibration-and-video.md), and
  [notebook](notebook-analysis.md)
- NI planning and source links: [NI-DAQmx integration guide](nidaqmx-integration.md)
- NI Python binding: <https://github.com/ni/nidaqmx-python>
- NI Python API documentation: <https://nidaqmx-python.readthedocs.io/>
- NI driver downloads: <https://www.ni.com/downloads/>

## Verification summary

This entry point was reconciled against all tracked legacy artifacts in
`old-files/`—the three Python scripts, notebook, package metadata, lockfile,
and legacy README—and the four detailed implementation analyses. File paths,
legacy commands, package extras, schema names, serial settings, and the
serial-versus-NI boundary were checked against those sources. Lab hardware,
firmware, wiring, and safety limits are explicitly marked unknown rather than
presented as verified behavior.
