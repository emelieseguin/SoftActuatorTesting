# Legacy operator guide (`old-files`)

This directory contains the original, stand-alone serial GUIs and offline video
analysis. It is not the current application. **This legacy code uses serial
I/O; it does not use NI-DAQmx.** The firmware implementation is not in this
repository, so the protocol details below are the strings the Python source
expects, not a guarantee about a particular controller.

## Quick start

From the repository root:

```bash
uv sync --project old-files --all-extras --locked
cd old-files
uv run python PressureCalibration.py       # make calibration.json first
uv run python DataCollection-V2.py        # run the rig; optionally record video
uv run python VideoConfig.py              # mark geometry on the recorded video
uv run jupyter notebook PneumaticActuatorAnalysis-V1.ipynb
```

The recommended sequence is **calibration → data collection/camera →
video-geometry configuration → notebook analysis**. Run calibration again when
the pressure sensor, plumbing, electronics, or measurement range changes; for a
repeat run with unchanged hardware, load the previously validated calibration.
Calibration is effectively required for a successful Data Collection CSV:
without a model, the GUI can display raw volts, but its current CSV formatter
can fail when it tries to format a missing pressure value.

If already in `old-files`, the equivalent setup is:

```bash
uv sync --all-extras --locked
```

From the repository root, a command can also be run without changing directory
(outputs then use the root as their working directory):

```bash
uv run --project old-files --directory old-files python DataCollection-V2.py
```

## What each piece does

| File | Purpose | Operator result |
|---|---|---|
| `PressureCalibration.py` | Reads serial voltage, pairs it with known pressure, and fits a linear or quadratic pressure model. | A user-selected calibration JSON. |
| `DataCollection-V2.py` | Controls cycles over serial, displays raw/calibrated telemetry, and optionally records a camera with FFmpeg. | A `runs/run_<timestamp>/` directory containing `data.csv` and, if selected, `video.mkv`. |
| `VideoConfig.py` | Opens one video frame and lets the operator mark actuator geometry. | `<video-stem>_config.json` beside the selected video. |
| `PneumaticActuatorAnalysis-V1.ipynb` | Detects the red actuator tip in the configured ROI and calculates a signed acute angle per frame. | A selected-folder `analysis_<timestamp>.csv` and an annotated preview window. |

## Prerequisites

### Software

- Python `>=3.9,<3.14`, [uv](https://docs.astral.sh/uv/), and this repository.
- The locked environment in `pyproject.toml`: `numpy`, `opencv-python`, and
  `pyserial`.
- Optional `plotting` extra: `matplotlib` for both GUI plots.
- Optional `notebook` extra: Jupyter Notebook and `ipykernel`.
- A desktop session with Tk support and OpenCV GUI support. On Linux, Tk and
  native display libraries may need to be installed by the OS.
- `ffmpeg` on `PATH` only when using the Data Collection camera path. FFmpeg
  is not installed by this `pyproject.toml`.

### Hardware / OS

- A serial-connected controller/sensor and its OS driver; the code opens the
  selected port at **115200 baud**, timeout **0.5 s**.
- A safe, pressure-rated actuator test setup. This software provides no
  interlocks, pressure limits, emergency stop, or command acknowledgements.
- For live camera capture: **Windows + FFmpeg DirectShow (`dshow`) + an Intel
  Quick Sync-capable driver/device**. The source requests `3840x2160` at
  `60 fps`, MJPEG input, and `h264_qsv` output. This is the current camera
  constraint; a camera may be omitted and an existing video may be analyzed.

## Serial protocol and firmware expectations

All commands are UTF-8 text with a trailing newline:

| UI action | Bytes sent |
|---|---|
| Set cycle count | `CMD:SET CYCLES <cycles>` |
| Set ON duration | `CMD:SET ON <milliseconds>` |
| Set OFF duration | `CMD:SET OFF <milliseconds>` |
| Start | `CMD:START` |
| Stop | `CMD:STOP` |
| Calibration “Request Sample” | `CMD:CAL_ON`, then `CMD:CAL_OFF` after 300 ms |

The calibration request commands are optional hooks in the source; use them
only if the firmware implements them. Otherwise leave the controller streaming
and use **Record Sample**.

Data Collection treats incoming lines as comma-separated text and attempts the
**third field** (`parts[2]`) as volts. Firmware is expected to emit these
case-insensitive, line-start markers:

```text
--- new run ---
--- end run ---
```

There is no acknowledgement parser. `--- new run ---` opens the CSV; the end
marker closes the CSV and stops FFmpeg. The exact telemetry format, marker
timing, and controller behavior are not verified here.

## Component instructions

### 1. `PressureCalibration.py`

```bash
uv run python PressureCalibration.py
```

1. Select a serial port (use **Refresh** if needed) and click **Connect**.
2. For each known pressure, enter **Known pressure kPa** and click
   **Record Sample** while a live voltage is shown. Use at least two distinct
   voltage values for a linear fit; collect more points across the intended
   range.
3. Optionally use **Request Sample** (see the firmware warning above).
4. Select `linear` or `quadratic`, click **Fit Model**, then **Save Calibration**.
5. Save a JSON file and load that file in Data Collection. A “samples only”
   file has no usable model for Data Collection.

The JSON contains `model` (`type` plus coefficients) and `samples` (known kPa,
volts). `numpy` is used for the quadratic fit; `matplotlib` only adds the
calibration plot.

### 2. `DataCollection-V2.py`

```bash
uv run python DataCollection-V2.py
```

Before starting a run:

1. Choose the serial port and click **Connect**.
2. Click **Load Calibration JSON** and select a JSON containing
   `model.coeffs`.
3. Enter **Total Cycles** (default `20`), **ON (s)** (default `6`), and
   **OFF (s)** (default `5`); click **Set Params**. Durations are converted to
   integer milliseconds.
4. For video, select a detected DirectShow camera and click **Connect Camera**.
   This only records its name; it does not test the camera until Start Run.
5. Click **Start Run**, monitor the console/raw-volts/pressure displays, and
   click **Stop Run** when appropriate. Wait for the controller’s end marker.

`runs/` is relative to the directory from which the command runs. A run creates
`runs/run_YYYYMMDD_HHMMSS/data.csv` with header
`time_s,volts,pressure_kPa`; selecting a camera also creates
`video.mkv` in that directory. The Stop button sends `CMD:STOP` but does not
itself close the files. Closing the GUI or disconnecting can leave the CSV or
FFmpeg process unfinished if the end marker is never received. The displayed
cycle/time/sample-rate status fields are not updated by this legacy source.

### 3. `VideoConfig.py`

```bash
uv run python VideoConfig.py
```

Choose an existing `.mp4`, `.avi`, `.mov`, or `.mkv`. In the OpenCV window,
press a key before each click:

| Action | Input |
|---|---|
| Actuator base | `b`, then click the base |
| Actuator tip reference | `t`, then click the tip |
| Actuator ROI | `a`, click top-left, press `a` again, click bottom-right |
| Save | `s` |
| Quit without saving | `q` |

After saving, dismiss the preview with any key. The JSON stores
`angle_base_point`, `angle_tip_point`, and an `actuator_roi` with `x`, `y`, `w`,
and `h`. The notebook requires the base point and a positive ROI; it does not
use the saved tip point for detection.

### 4. `PneumaticActuatorAnalysis-V1.ipynb`

```bash
uv run jupyter notebook PneumaticActuatorAnalysis-V1.ipynb
```

Run the cells in order. The path examples in the notebook are old Windows
paths; cell 4 opens dialogs and replaces them. Select (1) the experiment video,
(2) its required geometry config JSON, and (3) a folder for the analysis CSV.
Then run the analysis cell. Press `q` in the **Analysis** window to stop early;
end-of-video also closes it. The output is
`analysis_YYYYMMDD_HHMMSS.csv` with `Frame,ActuatorAngle_deg`.

Detection is intentionally narrow: it thresholds red pixels in the configured
ROI, ignores candidates within 60 pixels of the base, smooths over five
detections, and writes `NaN` when no tip is found. `MAX_PRESSURE = 700` is
defined but is not used. Check the overlay and CSV for missed detections; this
notebook does not read the serial pressure CSV or calculate pressure.

## Verification without hardware

Run from `old-files` after syncing. These commands do not invoke a GUI
`__main__`, open a port, start FFmpeg, access a camera, or open a video window:

```bash
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

## Common failures

| Symptom | Action |
|---|---|
| `uv` or a Python module is missing | Install uv, then rerun `uv sync --all-extras --locked` from this directory. Do not use the stale `pip install` text in the source docstrings. |
| Tk/OpenCV window will not start | Use a local desktop session and install the OS Tk/display packages; headless sessions cannot operate these tools. |
| No serial ports / open fails | Check cable, device driver, permissions, and whether another program owns the port; click **Refresh**, select the actual port, and confirm 115200 baud. |
| Raw volts stay blank | Confirm the controller is powered and streaming newline-terminated text with a numeric third comma-separated field. |
| `pressure_kPa` is blank or the run crashes while writing CSV | Load a valid calibration JSON with `model` and `coeffs` before starting; a samples-only JSON is insufficient. |
| CSV/video never finishes | Wait for `--- end run ---`; Stop alone is not cleanup. If the marker is absent, stop the controller/FFmpeg using the lab’s approved procedure before closing the GUI. |
| No camera list / FFmpeg error | Confirm `ffmpeg` is on `PATH` and use the Windows DirectShow device name. `h264_qsv` also requires compatible Intel hardware/driver; omit camera capture if unavailable. |
| VideoConfig cannot read a frame | Check the file path/codec and OpenCV support; try a known-readable video. |
| Notebook produces many `NaN` angles | Recheck base/ROI coordinates, lighting, and the fixed red threshold; inspect the annotated window. |

## Limitations and safety

This is unmaintained, tightly coupled lab code. It has broad exception
handling, incomplete status displays, no command acknowledgements or hardware
interlocks, and known lifecycle dependence on firmware markers. Verify the
pressure range, relief path, fittings, actuator restraint, emergency-stop
procedure, serial target, and camera framing before applying pressure. Keep an
operator present; never treat a successful GUI launch as proof that the rig is
safe or that a run is recording correctly.

## Further reading

- [Initial implementation overview](../docs/initial-implementation/README.md)
- [Data Collection details](../docs/initial-implementation/data-collection.md)
- [Calibration and video details](../docs/initial-implementation/calibration-and-video.md)
- [Notebook analysis details](../docs/initial-implementation/notebook-analysis.md)
- [NI-DAQmx integration notes](../docs/initial-implementation/nidaqmx-integration.md)
