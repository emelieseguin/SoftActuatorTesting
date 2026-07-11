# Data Collection V2 analysis (2026-07-11)

Source: [`old-files/DataCollection-V2.py`](../../old-files/DataCollection-V2.py)

Related calibration source: [`old-files/PressureCalibration.py`](../../old-files/PressureCalibration.py)

Key source ranges: protocol/constants [`DataCollection-V2.py:36-42`](../../old-files/DataCollection-V2.py#L36-L42), recorder [`:93-142`](../../old-files/DataCollection-V2.py#L93-L142), start/stop lifecycle [`:355-397`](../../old-files/DataCollection-V2.py#L355-L397), telemetry and run markers [`:458-542`](../../old-files/DataCollection-V2.py#L458-L542), shutdown [`:581-602`](../../old-files/DataCollection-V2.py#L581-L602).

Legend:
- **Fact** = directly visible in code.
- **Inference** = deduced from code behavior or naming.
- **Unknown** = not established by the repository.

## 1) Software goal

- **Fact:** `DataCollection-V2.py` is a Tkinter desktop GUI titled **“Main Test GUI”** that:
  - connects to a serial device,
  - sends cycle/start/stop commands,
  - loads a calibration JSON file,
  - shows live raw voltage and calibrated pressure,
  - optionally plots pressure live,
  - optionally records a camera feed through `ffmpeg`.
- **Fact:** The file docstring says it should be saved as `main_gui.py` and run with `python main_gui.py`, but the legacy file currently lives in `old-files/`.
- **Inference:** This is an operator-facing control panel for a soft-actuator pneumatic test rig, not an analysis script.

## 2) End-to-end control and data flow

1. **Operator selects a serial port** from the combobox populated by `serial.tools.list_ports.comports()`; the first detected port is preselected when any exist.
2. **Operator connects serial**; a background `SerialReader` thread starts reading `readline()` continuously.
3. **Operator may load calibration JSON** from the separate calibration tool.
4. **Operator enters cycle count and ON/OFF durations** and clicks **Set Params**.
   - GUI sends three commands:
     - `CMD:SET CYCLES <cycles>`
     - `CMD:SET ON <on_ms>`
     - `CMD:SET OFF <off_ms>`
5. **Operator may select a camera** from the `ffmpeg`/DirectShow device list and click **Connect Camera**. This only records the selected device name in GUI state; it does not open the camera.
6. **Operator clicks Start Run**.
   - A timestamped run folder is created under `runs/`.
   - If a camera was selected, `ffmpeg` starts recording to `runs/run_<timestamp>/video.mkv`.
   - Only after the recorder start call does the GUI send `CMD:START` over serial; it does not first verify that serial is connected or that `ffmpeg` opened the device.
7. **Incoming serial lines are queued** by the reader thread and polled by the GUI every 100 ms.
8. **Each serial line is parsed**:
   - split on commas,
   - if the line has at least two fields, the third field (`parts[2]`) is attempted as the voltage (the guard is off by one; see defects below),
   - calibration is applied if loaded,
   - raw/calibrated labels update,
   - the pressure plot appends a point only when calibration produced a pressure.
9. **Run file creation is event-driven**:
   - when a line starts with `--- new run ---`, the GUI opens `runs/run_<timestamp>/data.csv` and writes a header,
   - subsequent data rows append to that file,
   - when a line starts with `--- end run ---`, the CSV is closed and the video recorder is stopped.

## 3) Functions / classes and responsibilities

### `SerialReader`

- **Fact:** Thread subclass that continuously calls `self.ser.readline()`.
- **Fact:** Decodes bytes as UTF-8 with replacement and pushes `(timestamp, line)` into the global `line_q`.
- **Fact:** Any serial read exception is converted into a sentinel line like `__ERROR__ Serial read error: ...`.

### `apply_calibration_model(model, raw)`

- **Fact:** Applies either:
  - linear: `a * raw + b`
  - quadratic: `a * raw * raw + b * raw + c`
- **Fact:** Returns the raw value unchanged if the model is missing, unsupported, malformed, or an exception occurs.

### `FFmpegRecorder`

- **Fact:** Builds and launches an `ffmpeg` process for Windows DirectShow capture. The constructor argument is called `index`, but the selected value is the camera **name** returned by device enumeration, so the input is `video=<selected DirectShow name>`.
- **Fact:** Uses these recording defaults in `start()`:
  - input: `-f dshow -i video=<selected DirectShow name>`
  - capture size: whatever was passed to the constructor
  - framerate: whatever was passed to the constructor
  - codec path: MJPEG input, QuickSync H.264 output
  - output path: whatever was passed to the constructor
- **Fact:** `stop()` calls `terminate()` then `wait()`, and returns the output path.

### `App`

- **Fact:** Owns all UI widgets and application state.
- **Fact:** Important methods:
  - `build_ui()` — constructs controls and labels.
  - `send_set_params()` — validates cycle/ON/OFF values and sends serial commands.
  - `send_start()` / `send_stop()` — start/stop the test run command stream.
  - `detect_cameras()` — shells out to `ffmpeg` to enumerate DirectShow video devices.
  - `connect_serial()` — connect/disconnect serial.
  - `load_calibration_file()` — load calibration JSON.
  - `process_serial_queue()` — GUI polling loop for queued serial lines.
  - `handle_serial_line()` — parse one line, update UI, write CSV, react to run markers.
  - `schedule_plot_update()` / `_update_plot()` — live plot refresh.
  - `shutdown()` — close serial and destroy the window.

## 4) Hardware and software dependencies

- **Fact:** Python stdlib dependencies: `tkinter`, `threading`, `subprocess`, `queue`, `time`, `json`, `os`, `collections.deque`, `datetime`.
- **Fact:** Third-party dependency: `pyserial`.
- **Fact:** Optional dependency: `matplotlib` (TkAgg backend).
- **Fact:** `ffmpeg` must exist on `PATH` for camera detection and recording.
- **Fact:** The recording command uses `dshow` and `h264_qsv`, so the capture path is Windows/DirectShow/Intel-QuickSync oriented.
- **Fact:** The calibration file format comes from [`old-files/PressureCalibration.py`](../../old-files/PressureCalibration.py).
- **Inference:** The serial device is likely an Arduino-class controller because the UI expects simple ASCII commands and CSV-like telemetry.
- **Unknown:** The exact firmware implementation is not present in this repository.

## 5) Hard-coded and runtime configuration

| Area | Value / behavior | Notes |
|---|---|---|
| Serial baud rate | `115200` | `BAUDRATE` global |
| Serial timeout | `0.5` s | `SERIAL_TIMEOUT` global |
| Plot buffer size | `2000` points | `PLOT_MAX_POINTS` |
| Plot refresh interval | `200` ms | `PLOT_UPDATE_MS` |
| Default cycles | `20` | UI prefill |
| Default ON time | `6` s | UI prefill |
| Default OFF time | `5` s | UI prefill |
| Cycle-entry widget bounds | `1..10000` | `Spinbox` display bounds; `send_set_params()` only rejects values below 1, so typed values above 10000 are not actually rejected |
| ON/OFF command units | milliseconds | UI converts seconds to `int(seconds * 1000)`; no positive/range check is performed |
| Run output root | `runs/` | Relative to current working directory |
| Run folder format | `runs/run_%Y%m%d_%H%M%S/` | Used by `send_start()` |
| Video file name | `video.mkv` | Only when `send_start()` sees a selected camera |
| CSV file name | `data.csv` | Created on `--- new run ---` |
| CSV header | `time_s,volts,pressure_kPa` | Written once per run |
| Console timestamp | `%H:%M:%S` | `console_insert()` |
| Camera detect command | `ffmpeg -list_devices true -f dshow -i dummy` | Parses `(video)` entries from stderr |
| Camera recording defaults | `3840x2160 @ 60 fps` | Passed from `send_start()` |
| Unused recorder defaults | `1920x1080 @ 30 fps` | Constructor defaults in `FFmpegRecorder` |
| ffmpeg capture/encode settings | `-hide_banner`, `-loglevel warning`, `-rtbufsize 512M`, `mjpeg` input, `nv12`, `h264_qsv`, `veryfast`, `global_quality=23` | Hard-coded in `FFmpegRecorder.start()` |
| Calibration model types | `linear`, `quadratic` | `apply_calibration_model()` |

### Calibration JSON schema

- **Fact:** `load_calibration_file()` requires the file to contain a top-level `model` object with a `coeffs` field.
- **Fact:** It does not validate model type, coefficient count, numeric coefficient values, or the optional `samples` field; unsupported/malformed models later fall back to the raw value in `apply_calibration_model()`.
- **Fact:** `PressureCalibration.py` saves:
  - `model`: `{"type": "linear"|"quadratic", "coeffs": [...]}`,
  - `samples`: list of `(known_kPa, volts)` pairs.
- **Fact:** `DataCollection-V2.py` ignores `samples` when loading; it only keeps `model`.

## 6) Expected invocation and operator workflow

- **Fact:** The file can be run directly with `python old-files/DataCollection-V2.py` today; the docstring suggests the intended final name was `main_gui.py`.
- **Expected operator flow:**
  1. Open GUI.
  2. Select serial port and connect.
  3. Optionally load calibration JSON.
  4. Enter cycles, ON, OFF values.
  5. Click **Set Params**.
  6. Select a camera device if video capture is desired.
  7. Click **Connect Camera**.
  8. Click **Start Run**.
  9. Watch console, pressure label, and plot.
  10. Click **Stop Run** when done.
  11. Wait for the device to emit `--- end run ---` so CSV/video are closed; closing the window or disconnecting does not perform this run-resource cleanup.

- **Inference:** The GUI depends on the device firmware to emit the run markers; the Stop button alone does not close files. The source does not define marker timing, acknowledgement, or whether a marker is emitted after an aborted cycle.

## 7) Outputs

### Console

- **Fact:** A Tk text widget logs every received line with a local wall-clock timestamp prefix.
- **Fact:** Serial errors appear as `__ERROR__ ...` lines.

### Live GUI state

- **Fact:** Raw voltage label shows `<value> V`.
- **Fact:** Calibrated label shows `<value> kPa` when calibration is enabled.
- **Fact:** Live plot shows pressure versus elapsed time since the first plotted sample.
- **Fact:** The plot retains at most 2,000 calibrated points; it has no raw-voltage series and is empty when calibration is disabled.
- **Fact:** Cycle-left, run-time, and sample-rate labels are created but never updated by the legacy code.

### Data files

- **Fact:** `runs/run_<timestamp>/data.csv`
  - Header: `time_s,volts,pressure_kPa`
  - Each row is written from `handle_serial_line()` using:
    - elapsed time from `run_start_time_wall`,
    - parsed voltage,
    - calibrated pressure.
- **Fact:** `runs/run_<timestamp>/video.mkv` is created if a camera is selected when `Start Run` is pressed.
- **Fact:** A separate, unused method `gui_start_recording()` would instead write `runs/run_<timestamp>.mkv`; it is not wired to any button.
- **Fact:** The CSV is opened only after the new-run marker and is not flushed per row; the current source closes it only on the end-run marker.

## 8) Assumptions and coupling

- **Fact:** The code assumes serial telemetry is comma-separated.
- **Fact:** It assumes the **third** field is the voltage.
- **Fact:** Reader lines are stripped of surrounding whitespace after UTF-8 decoding with replacement; the GUI polls the global queue every 100 ms, and run-marker matching is case-insensitive (`line.lower().startswith(...)`).
- **Inference:** The comment `time,voltage,pressureRaw,...` is inconsistent with the extraction code, so the real device format is ambiguous.
- **Fact:** The serial command protocol is hard-coded to newline-terminated UTF-8 ASCII messages:
  - `CMD:SET CYCLES`
  - `CMD:SET ON`
  - `CMD:SET OFF`
  - `CMD:START`
  - `CMD:STOP`
- **Fact:** There is no response/acknowledgement parser for commands. Telemetry is treated as a separate comma-separated text stream.
- **Fact:** Camera selection is tied to `ffmpeg` DirectShow device names, not an abstraction layer.
- **Fact:** Relative paths (`runs/`) depend on the current working directory.
- **Inference:** This GUI is tightly coupled to a specific lab workflow, specific firmware, and a Windows capture stack.

## 9) Error handling and safety concerns

- **Fact:** Serial read exceptions are caught in the reader thread and surfaced as a sentinel line.
- **Fact:** `send_serial()` warns if no serial device is connected.
- **Fact:** Calibration file load validates only `model` and `coeffs`.
- **Fact:** Most UI helper methods swallow exceptions with bare `except`, which hides failures.
- **Fact:** `FFmpegRecorder.start()` has no local error handling; process launch failures may bubble out.
- **Fact:** `FFmpegRecorder.stop()` blocks on `wait()` in the GUI thread.
- **Fact:** `shutdown()` cancels the serial-poll callback, stops the reader, closes serial, and destroys the Tk window, but does not close an open run CSV or stop an active `ffmpeg` process.
- **Fact:** Reader threads are stopped but never joined; serial disconnect closes the port after requesting the daemon thread to stop.
- **Fact:** `process_serial_queue()` catches only `queue.Empty`; exceptions from `handle_serial_line()` escape the scheduled callback and stop future queue polling.
- **Inference:** There are no interlocks, limits, or hardware safety checks before sending `CMD:START`.
- **Inference:** If the underlying hardware misbehaves, the GUI has little protection beyond showing messages.

## 10) Known bugs and fragile behavior

1. **Uninitialized run state can crash telemetry processing**
   - **Fact:** `self.run_active`, `self.run_file`, `self.current_run_dir`, and `self.run_start_time_wall` are not initialized in `__init__`.
   - **Impact:** The first valid data line before `--- new run ---` can raise `AttributeError` in `handle_serial_line()` and stop queue processing.

2. **CSV parsing check is off by one**
   - **Fact:** `handle_serial_line()` checks `if len(parts) >= 2:` but then accesses `parts[2]`.
   - **Impact:** Lines with only two CSV fields are mishandled; the guard does not match the access.

3. **CSV write fails when calibration is disabled**
   - **Fact:** `pressure` may remain `None`, but the code writes `f"{pressure:.6f}"`.
   - **Impact:** A run with no calibration loaded can raise `TypeError` when writing `data.csv`.

4. **Manual stop does not directly close outputs**
   - **Fact:** `send_stop()` only sends `CMD:STOP`.
   - **Impact:** CSV/video closure still depends on the device emitting `--- end run ---`.

5. **Dead / orphaned camera methods**
   - **Fact:** `gui_start_recording()` and `gui_stop_recording()` are defined but not bound to buttons.
   - **Impact:** There are two competing recording flows, but only one is reachable.

6. **Camera status is not real connection validation**
   - **Fact:** `connect_camera()` only stores the selected name and updates the label.
   - **Impact:** The UI can claim “connected” even if `ffmpeg` cannot actually open that device.

7. **Potential GUI freeze on recorder shutdown**
   - **Fact:** `FFmpegRecorder.stop()` waits synchronously.
   - **Impact:** If `ffmpeg` hangs, the GUI thread can stall.

8. **Documentation drift**
   - **Fact:** The top docstring mentions `opencv-python` and `numpy`, but this file does not import or use them.
   - **Impact:** The embedded usage note is stale and may mislead operators or maintainers.

9. **Shutdown/disconnect can leak run resources**
   - **Fact:** `shutdown()` and serial disconnect do not call the end-run cleanup path; `send_stop()` sends only the firmware command.
   - **Impact:** An active `data.csv` can remain unclosed and an active `ffmpeg` process can continue if the firmware never emits `--- end run ---`.

10. **Camera discovery and start failures are weakly reported**
    - **Fact:** `detect_cameras()` catches all exceptions and returns an empty list; `FFmpegRecorder.start()` does not catch `Popen` failures, and `send_start()` does not require serial connectivity before starting video.
    - **Impact:** Missing `ffmpeg`, malformed device-enumeration output, or a failed camera process can leave misleading UI state or abort the start callback.

11. **Run-status widgets are dead state**
    - **Fact:** `sample_count` is reset on a new-run marker but never incremented, and the `Cycles left`, `Run time`, and `Sample rate` labels are never updated.
    - **Impact:** Operators cannot rely on those status fields to judge progress.

12. **Parameter validation can leak exceptions**
    - **Fact:** `send_set_params()` converts ON/OFF text to floats inside its `try`, but converts them to integer milliseconds afterward; non-finite values such as `nan` can fail outside that handler, and negative/zero durations are accepted.
    - **Impact:** A malformed operator entry can abort the Tk callback instead of showing the intended validation dialog.

13. **Disconnecting the camera does not stop an active recorder**
    - **Fact:** `disconnect_camera()` only clears `selected_camera` and changes the label.
    - **Impact:** It can leave an already-started `_ffmpeg` process running until an end-run marker or external cleanup.

## 11) Prioritized rewrite recommendations

### P0 — make the run state safe

- **Fix `__init__` state initialization** in `App` (`run_active`, `run_file`, `current_run_dir`, `run_start_time_wall`, `selected_camera`, `_ffmpeg`).
- **Guard CSV writes** so missing calibration writes a safe value (blank, `NA`, or raw-only schema).
- **Make the parser guard match the data access** (`len(parts) >= 3` before `parts[2]`).

### P1 — formalize the device protocol

- **Extract serial protocol handling** into a separate module with explicit message parsing and validation.
- **Document or version the firmware protocol** for `CMD:*` commands and run markers.
- **Replace “magic string” run markers** with a structured message format if firmware can be changed.

### P1 — make recording lifecycle explicit

- **Remove the duplicate unused recording path** (`gui_start_recording()` / `gui_stop_recording()`) or wire it to the UI.
- **Close CSV/video in one central place** during stop and shutdown, not only on `--- end run ---`.
- **Avoid blocking `wait()` on the main thread**; stop recorder asynchronously or with timeout handling.

### P2 — reduce hard-coded environment coupling

- **Externalize path and camera settings** (`runs/`, resolution, fps, encoder choice).
- **Make DirectShow/QuickSync optional** or document the Windows-only requirement clearly.
- **Use a validated config object** for serial, camera, and run parameters.

### P2 — improve maintainability and observability

- **Replace bare `except` blocks** with logged, user-visible errors where practical.
- **Add tests** for calibration application, serial line parsing, and output-path generation after the code is refactored into testable units.
- **Update the stale docstring** so it matches the actual file name and dependencies.
- **Define status semantics**: either implement or remove the currently non-functional cycle/time/rate labels.
