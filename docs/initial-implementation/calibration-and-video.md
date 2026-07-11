# Legacy calibration and video tooling analysis

Scope: [`old-files/PressureCalibration.py`](../../old-files/PressureCalibration.py), [`old-files/VideoConfig.py`](../../old-files/VideoConfig.py), and the directly linked consumers in [`old-files/DataCollection-V2.py`](../../old-files/DataCollection-V2.py) and [`old-files/PneumaticActuatorAnalysis-V1.ipynb`](../../old-files/PneumaticActuatorAnalysis-V1.ipynb).

Key source ranges: calibration reader/fits [`PressureCalibration.py:41-81`](../../old-files/PressureCalibration.py#L41-L81), calibration UI and serial parsing [`:83-279`](../../old-files/PressureCalibration.py#L83-L279), fit/persistence/cleanup [`:302-447`](../../old-files/PressureCalibration.py#L302-L447); video interaction/config write [`VideoConfig.py:11-151`](../../old-files/VideoConfig.py#L11-L151).

Legend:
- **Code fact** = visible directly in source.
- **Inference** = derived from source behavior or downstream use.
- **Unknown** = not specified by the code.

## System view

- **`PressureCalibration.py`** collects `(known pressure kPa, measured volts)` samples over serial, fits a calibration model, and saves JSON for later use.
- **`VideoConfig.py`** annotates a selected video frame with a base point, tip point, and actuator ROI, then saves a per-video JSON config.
- **`DataCollection-V2.py`** is intended to load calibration JSON, convert live volts to pressure, record CSV, and optionally record camera video; as written, its uncalibrated CSV path raises when formatting `pressure=None`.
- **`PneumaticActuatorAnalysis-V1.ipynb`** consumes the video config JSON and expects the geometry fields written by `VideoConfig.py`.
- **Cross-cutting project fact:** None of these legacy files imports `nidaqmx` or names an NI device/channel; the observed acquisition boundary is serial plus an external `ffmpeg` camera path, not NI-DAQmx.

## 1) `old-files/PressureCalibration.py`

### Goal

- **Code fact:** This is a standalone Tkinter calibration GUI. The header says it is meant to record known pressure vs volts samples, fit a linear or quadratic model, and save calibration JSON.
- **Inference:** It is intended to be the offline “calibration authoring” step for the live data-collection GUI.

### Callable surface and execution flow

- **`SerialReader(threading.Thread)`**
  - Reads `ser.readline()` in a daemon thread.
  - Decodes each line and pushes `(timestamp, text)` into a global queue.
  - Pushes `__ERROR__ ...` messages on read failure.
- **`linear_fit(xs, ys)`**
  - Computes simple least-squares line fit `y = a*x + b`.
  - Returns `None` if fewer than 2 points or if all `x` values are identical.
- **`CalibrationApp`**
  - Builds the full GUI in `__init__`.
  - Polls the serial queue every 100 ms.
  - Supports connect/disconnect, sample capture, sample request, fit, save, load, and shutdown.
- **`__main__`**
  - Starts a Tk root, instantiates `CalibrationApp`, and installs `WM_DELETE_WINDOW` cleanup.

### Hardware/software dependencies

- **Code fact:** Uses `tkinter`, `pyserial`, `serial.tools.list_ports`.
- **Code fact:** Optional `matplotlib` plotting (`TkAgg`) and optional `numpy` for quadratic fit.
- **Inference:** Hardware is a serial device that emits voltage-bearing lines; the code does not name a DAQ channel, ADC board, or sensor model.

### Required and hard-coded configuration

- **Code fact:** Serial opens at **115200 baud** with **0.5 s timeout**.
- **Code fact:** Serial queue is processed every **100 ms**.
- **Code fact:** `request_sample()` sends `CMD:CAL_ON\n`, waits **300 ms**, then sends `CMD:CAL_OFF\n`.
- **Code fact:** The 300 ms callback reads the most recently parsed value; it does not wait for a device acknowledgement or prove that a new sample arrived after `CAL_ON`.
- **Code fact:** If the known-pressure field is filled, the request callback appends automatically; if it is blank, it only reports the captured volts and leaves saving to `Record Sample`.
- **Code fact:** Fit modes are exactly `"linear"` and `"quadratic"`.
- **Code fact:** The fit combobox defaults to `"linear"`.
- **Code fact:** Linear model is stored as `pressure_kPa = a * volts + b`.
- **Code fact:** Quadratic model is stored as `pressure_kPa = a * volts^2 + b * volts + c`.
- **Code fact:** Sample UI label and list use units **kPa** for known pressure and **V** for raw voltage.
- **Code fact:** When matplotlib is available, the calibration plot is a `6×3` inch Tk-embedded figure with volts (V) on x and known pressure (kPa) on y; it redraws scatter samples and, when present, a 201-point fitted curve.
- **Unknown:** No sensor scaling, channel name, or physical input range is defined.

### Inputs

- Serial port selection from the detected port list.
- Manual entry of known pressure in kPa.
- Incoming serial text lines.
- Optional firmware support for the `CAL_ON`/`CAL_OFF` request commands; this tool has no acknowledgement parser.

### Outputs

- In-memory `samples` list of `(known_kPa, volts)`.
- In-memory `model` dict: `{"type": ..., "coeffs": [...]}`.
- Saved calibration JSON containing `model` and `samples`.
- The save dialog lets the operator choose the JSON path; there is no fixed output directory or filename convention.
- Message-box feedback for connect, sample, fit, save, and load actions.
- Live label updates for current volts and fitted model.

### Integration with data collection

- **Code fact:** The saved calibration JSON is consumed by `DataCollection-V2.py`.
- **Code fact:** `DataCollection-V2.py` only requires `model.coeffs` and `model.type`; it ignores saved samples.
- **Code fact:** `DataCollection-V2.py` does not validate coefficient arity or numeric values, and a saved “samples only” file is rejected because its `model` is absent.
- **Inference:** The on-disk schema is the intended contract between calibration authoring and live recording, but it is not versioned or schema-validated.

### Operator workflow

1. Pick a serial port and connect.
2. Watch live voltage update as lines arrive.
3. Type a known pressure in kPa.
4. Record a sample manually, or use `Request Sample` to send `CAL_ON/CAL_OFF` and capture the current reading.
5. Remove selected samples or clear all if needed, then repeat until enough points exist.
6. Fit a linear or quadratic model (linear is the default).
7. Save calibration JSON, or load an existing JSON to replace the in-memory model/samples.

### Persisted and console outputs

- **Code fact:** Saved JSON uses `json.dump(..., indent=2)`.
- **Code fact:** JSON contains the model and all samples; tuples become JSON arrays.
- **Code fact:** There is no file log or CSV export from this tool.
- **Code fact:** The commented title update suggests an intention to echo the last line in the window title, but it is disabled.
- **Inference:** Feedback is mostly modal dialogs plus a single live label; there is no audit trail of capture events.

### Assumptions

- Incoming serial lines contain a numeric voltage somewhere in the text.
- The first regex match in a line is the intended measurement; because the regex is not field-aware, a timestamp, error code, or unrelated number can be selected (and a leading sign on an integer is not preserved by the alternation).
- The attached firmware understands `CMD:CAL_ON` and `CMD:CAL_OFF`, or at least ignores them safely.
- Measured volts are the independent variable and known pressure is the dependent variable.

### Error handling, cleanup, safety, portability

- **Code fact:** Serial open errors show a message box and reset `self.ser = None`.
- **Code fact:** `shutdown()` stops the reader, closes the serial port, and destroys the window.
- **Code fact:** `plot_model()` swallows all exceptions.
- **Code fact:** `save_calibration()` and `load_calibration()` catch generic exceptions and show message boxes.
- **Code fact:** `load_calibration()` accepts whatever `model` and `samples` values are present without schema validation; malformed values may only fail later in plotting or downstream use.
- **Code fact:** The 100 ms queue loop catches `queue.Empty` but not arbitrary exceptions from line handling, so an unexpected UI/parse error can stop subsequent polling.
- **Inference:** The reader thread is not joined, so shutdown relies on the serial timeout to let the daemon thread exit.
- **Inference:** Because line parsing uses a regex on arbitrary text, unrelated serial output can be misread as voltage.
- **Unknown:** There is no explicit upper bound, interlock, or sanity check on voltage/pressure values.
- **Fact:** `record_sample()` accepts any numeric known pressure, including values outside a physical range, and can record the last streamed voltage without proving it corresponds to the currently entered pressure.
- **Portability concern:** Requires a GUI desktop and a serial device; not headless-friendly.

### Confirmed defects / fragile behavior

- **High:** The parser uses `re.search(...)` and accepts the first number in any line, so timestamps or unrelated numeric text can be mistaken for voltage.
- **High:** `request_sample()` captures `self.last_raw_voltage` after a fixed 300 ms delay, so it can save stale data if the device stream is slow or jittery.
- **Medium:** Errors queued as `__ERROR__ ...` are not explicitly surfaced; they are passed through the same parser path as normal lines.
- **Medium:** Quadratic fitting depends on `numpy`, but there is no validation that the point set is numerically well-conditioned.
- **Medium:** The shared `len(self.samples) < 2` gate also permits a quadratic fit with only two samples; `numpy.polyfit(..., 2)` may be rank-deficient and emits coefficients without a domain-specific adequacy check.
- **Low:** The tool can save “samples only” with no model, creating JSON that downstream code may not use meaningfully.

### Recommendations

1. **P0 — define and validate a serial frame schema.** Parse explicit fields instead of “first float anywhere”; reject malformed lines and surface queue errors.
2. **P1 — make sample capture deterministic.** Add a buffer flush / sequence marker / acknowledgment before recording a sample instead of using the current 300 ms heuristic.
3. **P1 — validate saved calibration JSON.** Check that `model.type` and `model.coeffs` match the expected arity before saving and loading.
4. **P2 — join or manage the reader thread explicitly.** Avoid relying on daemon-thread teardown.
5. **P2 — replace silent exception swallowing in plotting with logged or visible errors.**

## 2) `old-files/VideoConfig.py`

### Goal

- **Code fact:** This is an interactive OpenCV/Tk tool for marking a single video frame with geometry used by downstream analysis.
- **Code fact:** The header and console text indicate the tool is meant to save a JSON config next to the chosen video.
- **Inference:** It is a one-time annotation utility rather than a live video-processing app.

### Callable surface and execution flow

- **`click_event(event, x, y, flags, param)`**
  - Handles left-clicks.
  - Writes base point, tip point, or ROI corner coordinates into module globals.
- **`choose_video()`**
  - Opens a Tk file dialog limited to `*.mp4 *.avi *.mov *.mkv`.
- **`resize_window_preserve_aspect(...)`**
  - Sizes the OpenCV window to fit within 1000×700 while preserving aspect ratio.
- **`main()`**
  - Chooses a video.
  - Opens it with `cv2.VideoCapture`.
  - Reads only the first frame.
  - Lets the operator set base, tip, and ROI via hotkeys and clicks.
  - Saves JSON and shows a preview.
- **`__main__`**
  - Calls `main()`.

### Hardware/software dependencies

- **Code fact:** Uses `cv2` (OpenCV), `json`, `os`, and `tkinter.filedialog`.
- **Inference:** Requires a GUI desktop environment and an OpenCV build with HighGUI support.
- **Unknown:** No camera capture hardware is used here; the tool works on an existing video file only.

### Required and hard-coded configuration

- **Code fact:** Video file picker accepts `mp4`, `avi`, `mov`, and `mkv`.
- **Code fact:** The initial display uses only the first frame of the selected video.
- **Code fact:** Window sizing caps at **1000×700** pixels.
- **Code fact:** Hotkeys are fixed:
  - `b` = actuator base
  - `t` = actuator tip
  - `a` = actuator ROI corner selection
  - `s` = save
  - `q` = quit without saving
- **Code fact:** The interactive loop polls keys with `cv2.waitKey(1)`; after saving, the preview blocks on `cv2.waitKey(0)` until any key is pressed.
- **Code fact:** Saved JSON file is written as `<video_basename>_config.json`.
- **Code fact:** ROI values are stored in **pixels**.
- **Code fact:** ROI width/height are computed with `abs(...)`, but `x` and `y` remain the first (TL) click; reverse-order clicks therefore produce a positive size with an origin that may not bound the intended rectangle.
- **Unknown:** There is no explicit calibration of image scale or physical units.

### Inputs

- A user-selected video file.
- Mouse clicks on the displayed frame.
- Hotkey presses.

### Outputs

- JSON config beside the video file.
- Interactive `Frame` window with point/ROI overlays, followed after save by a `Preview` window that redraws the ROI only.
- Console prints for instructions, selection feedback, save path, and config contents.

### Integration with downstream analysis

- **Code fact:** The notebook [`PneumaticActuatorAnalysis-V1.ipynb`](../../old-files/PneumaticActuatorAnalysis-V1.ipynb) reads this config.
- **Code fact:** The notebook requires `angle_base_point` and `actuator_roi`.
- **Code fact:** The notebook accepts `actuator_roi` in either `top_left/bottom_right` or `x,y,w,h` form; this tool writes `x,y,w,h`.
- **Code fact:** The notebook uses `exclusion_radius = 60` pixels and example absolute Windows paths for `VIDEO_PATH`, `CONFIG_PATH`, and `CSV_PATH`.
- **Inference:** The config file is part of the analysis contract; missing or malformed geometry will break the notebook.

### Operator workflow

1. Choose the experiment video.
2. Press `b` and click the actuator base.
3. Press `t` and click the actuator tip.
4. Press `a` and click the ROI top-left, then press `a` again and click the ROI bottom-right.
5. Press `s` to save the config and see a preview.

### Persisted and console outputs

- **Code fact:** Saves JSON with:
  - `angle_base_point: {x, y}`
  - `angle_tip_point: {x, y}`
  - `actuator_roi: {x, y, w, h}`
- **Code fact:** Prints the saved path and full JSON to stdout.
- **Code fact:** Shows a preview window before exit.
- **Inference:** There is no separate validation report or audit log.

### Assumptions

- The first frame is representative enough to annotate all required geometry.
- The user knows which point is the base and which is the tip.
- The ROI can be approximated by a single axis-aligned rectangle.

### Error handling, cleanup, safety, portability

- **Code fact:** If no video is selected, the tool exits cleanly.
- **Code fact:** If the first frame cannot be read, it prints an error and exits.
- **Code fact:** `cv2.destroyAllWindows()` is called on quit and after save.
- **Code fact:** The Tk root created for the file dialog is destroyed immediately after selection.
- **Inference:** There is no file-write error handling around `open(out_json, "w")`.
- **Inference:** Missing base/tip clicks are silently serialized as `{}`. Missing ROI corners are substituted independently (`x/y` default to `0`, the missing opposite corner defaults to `100`), so no selections produce a `0,0,100,100` ROI and partial selections produce other fabricated coordinates.
- **Portability concern:** The tool depends on local GUI capability and OpenCV window support.

### Confirmed defects / fragile behavior

- **High:** The tool does not validate that both `angle_base_point` and `angle_tip_point` were actually selected before saving.
- **High:** Incomplete ROI selection falls back to independently defaulted coordinates (all missing gives `x=0,y=0,w=100,h=100`), which can silently produce wrong configs.
- **High:** Reverse-order ROI clicks are not normalized: `w`/`h` use absolute differences but `x`/`y` stay at the first click, so the notebook may crop a different rectangle than the preview suggests.
- **Medium:** The tool does not clear global `points`/`rois`, so reruns in the same process can inherit stale state.
- **Medium:** There is no explicit “reset selection” action.
- **Medium:** The instructions say “click top-left then bottom-right,” but the code does not auto-advance after the first ROI click; the operator must press `a` again to switch the active corner.
- **Medium:** The active action remains selected after a click, so additional clicks can overwrite the current base, tip, or ROI corner without a confirmation step.
- **Low:** All output is stdout + windows; there is no structured validation of the resulting JSON.

### Recommendations

1. **P0 — validate required geometry before saving.** Refuse to write JSON unless base, tip, and both ROI corners are present.
2. **P1 — normalize and reset ROI state.** Provide a reset action and avoid stale globals between runs.
3. **P1 — persist an explicit schema version.** This would make downstream validation and migration safer.
4. **P2 — surface missing/invalid selections in the UI before save.**
5. **P2 — consider writing both ROI forms (`x,y,w,h` and `top_left/bottom_right`) if downstream consumers vary.**

## 3) Direct consumers and integration details

### `old-files/DataCollection-V2.py`

- **Code fact:** Loads calibration JSON with `load_calibration_file()`, requires `model` and `coeffs`, and enables calibration only after a valid file is loaded.
- **Code fact:** Uses the same linear/quadratic formulas as `PressureCalibration.py`.
- **Code fact:** Writes CSV rows as `time_s,volts,pressure_kPa`.
- **Code fact:** Starts a new run on the serial marker `--- new run ---` and ends it on `--- end run ---`.
- **Code fact:** `send_start()` creates `runs/run_<timestamp>/`, optionally starts camera recording, and sends `CMD:START`.
- **Code fact:** `send_stop()` only sends `CMD:STOP`; camera/file closure actually happens when the firmware emits the end-run marker.
- **Code fact:** With calibration disabled, the live UI can display raw volts, but a valid run data row formats `pressure=None` with `:.6f` and raises `TypeError`; the apparent raw-only workflow is therefore not currently safe.
- **Code fact:** Camera discovery uses `ffmpeg -list_devices true -f dshow -i dummy`.
- **Code fact:** Recording is hard-coded to **3840×2160 at 60 fps**, using `mjpeg` input, `nv12` pixel format, and `h264_qsv` encoding with `global_quality=23`.
- **Code fact:** The combobox stores camera device names, not numeric indexes.
- **High-risk defect:** `handle_serial_line()` checks `len(parts) >= 2` but reads `parts[2]`; this is an off-by-one bug and can drop or misparse short lines.
- **Inference:** This file is Windows/Intel-QSV oriented because it depends on DirectShow and `h264_qsv`.
- **Unknown:** The expected serial CSV field order is not consistently documented; the comment says `time,voltage,pressureRaw,...`, but the code reads the third field.
- **Unknown:** The calibration tool instead extracts the first regex number from arbitrary text, so the two GUIs do not establish one shared telemetry schema.

### `old-files/PneumaticActuatorAnalysis-V1.ipynb`

- **Code fact:** Requires `CONFIG_PATH`, `VIDEO_PATH`, `CSV_PATH`, and `exclusion_radius`.
- **Code fact:** Checks presence/type of `angle_base_point` and `actuator_roi`, converts their coordinates to integers, and rejects non-positive ROI dimensions; it does not validate `angle_tip_point`.
- **Code fact:** Accepts `actuator_roi` in either `top_left/bottom_right` or `x,y,w,h` shape.
- **Code fact:** Uses `math.hypot(...)` and an `exclusion_radius` of **60 pixels** to filter candidate tips.
- **Code fact:** The notebook examples use hard-coded Windows absolute paths.
- **Code fact:** The notebook consumes only video geometry and does not consume the pressure CSV produced by the data-collection GUI.
- **Inference:** `VideoConfig.py` is meant to produce exactly the config schema that this notebook consumes.

## 4) Cross-cutting unknowns

- No DAQ channel names, hardware pin maps, or sensor part numbers are present in these files.
- The firmware-side serial protocol is only partially inferred.
- There is no JSON schema/versioning for calibration or video config files.
- There is no automated validation that calibration output and video config output remain compatible with the downstream notebook.

## 5) Priority summary

1. **P0:** Fix serial parsing and schema validation (`PressureCalibration.py` + `DataCollection-V2.py`).
2. **P0:** Validate required geometry in `VideoConfig.py` before save.
3. **P1:** Add explicit resource/thread cleanup and better error reporting.
4. **P1:** Add schema/version metadata to both JSON outputs.
5. **P2:** Remove hard-coded recording assumptions (4K60, QSV, DirectShow) from the data-collection path.
