# Notebook analysis: `old-files/PneumaticActuatorAnalysis-V1.ipynb`

This document analyzes the legacy notebook in place without modifying it.

Related context:

- [Old notebook](../../old-files/PneumaticActuatorAnalysis-V1.ipynb)
- [Video configuration helper](../../old-files/VideoConfig.py)
- [Serial/data collection GUI](../../old-files/DataCollection-V2.py)

Primary source ranges: path selection cells [`PneumaticActuatorAnalysis-V1.ipynb:22-119`](../../old-files/PneumaticActuatorAnalysis-V1.ipynb#L22-L119), analysis cell [`:156-389`](../../old-files/PneumaticActuatorAnalysis-V1.ipynb#L156-L389), stored config-print cell [`:392-411`](../../old-files/PneumaticActuatorAnalysis-V1.ipynb#L392-L411).

## Executive summary

**Fact:** The notebook is a one-off video-analysis workflow that loads a video, loads a per-video JSON config, detects a red actuator tip in each frame, computes an angle from a manually chosen base point, and writes a per-frame CSV.

**Inference:** Its practical goal is to turn an experiment recording from a soft pneumatic actuator test into a time series of actuator angle measurements.

**Fact:** The notebook is not a polished exploratory notebook; it mixes launch/setup cells, helper definitions, and immediate execution, and it depends on interactive GUI dialogs and OpenCV display windows.

---

## Notebook metadata

**Visible facts**

- Format: Jupyter notebook `nbformat=4`, `nbformat_minor=5`.
- Top-level metadata has exactly `kernelspec = {display_name: "Python 3", language: "python", name: "python3"}` and `language_info` with codemirror `{name: "ipython", version: 3}`, file extension `.py`, MIME type `text/x-python`, language/name `python`, nbconvert exporter `python`, Pygments lexer `ipython3`, and version `3.11.9`.
- Cell metadata: empty on every cell.
- Stored outputs: only two code cells have outputs, both `stdout` text; no images, rich tables, or error tracebacks are stored.

---

## Concise cell / stage inventory

| Cell | ID / execution count | Type | Role / stored output |
|---|---|---|---|
| 0 | `2de2febe` / 7 | code | No-op launcher artifact: `#!python VideoSelectionHelper.py` |
| 1 | `e3d1ac9a` / — | markdown | Section title: “Set Paths for Video and Configuration Files” |
| 2 | `9490b76a` / — | markdown | Hard-coded example paths for video/config/CSV folder (not executable in this cell type) |
| 3 | `274f9f54` / 8 | code | Defines `get_paths()` GUI selector for video, config JSON, and CSV output folder |
| 4 | `080ba84d` / 9 | code | Calls `get_paths()` and assigns `VIDEO_PATH`, `CONFIG_PATH`, `CSV_PATH` |
| 5 | `8a17c4ac` / 10 | code | Sets `MAX_PRESSURE` and `exclusion_radius` |
| 6 | `069a8ca4` / 11 | code | Defines a window-resize helper (later shadowed) |
| 7 | `af3162c7` / — | markdown | Section title: “Analysis” |
| 8 | `49284623` / 12 | code | Imports analysis libraries, defines helpers, and runs `main()`; stored stdout has base/ROI/export-complete lines |
| 9 | `68ca0e50` / 13 | code | Loads and prints the selected JSON config; stored stdout is a Python `pprint` dict representation |

---

## What the notebook does

### 1) Setup and path selection

**Fact:** Cell 3 defines `get_paths(default_video=None, default_config=None, default_csv_folder=None)` using `tkinter` dialogs.
The optional defaults are used only to choose each dialog's initial directory; they do not bypass selection or provide fallback paths.

- Required selections:
  - one video file
  - one JSON config file
  - one output folder for CSV export
- Allowed video extensions in the dialog: `.mp4`, `.avi`, `.mov`, `.mkv`
- Config dialog accepts `.json`
- Output file name is generated as `analysis_YYYYmmdd_HHMMSS.csv`
- The selector hides a Tk root, marks it topmost, destroys it before returning, and exits with `sys.exit(...)` if video, config, or output-folder selection is abandoned.

**Fact:** Cell 4 immediately calls `get_paths()`, so the notebook’s actual runtime paths come from the GUI, not from the hard-coded strings in cell 2.

**Fact:** The hard-coded values in cell 2 are Windows OneDrive paths under `C:\Users\evely\OneDrive\Documents\2025-2026\PneumaticActuators\...`.

**Fact:** The stored output from cell 8 shows a different machine/user path in the saved CSV location: `C:\Users\ewatt015\OneDrive\Documents\2025-2026\PneumaticActuators\Code\analysis_20251211_181835.csv`.

**Inference:** The notebook was copied between machines or edited across runs, and the hard-coded path cell is stale.

**Confirmed defect:** If the required config dialog is cancelled, cell 3 calls `filedialog.messagebox.askyesno(...)`; `messagebox` is not imported from `tkinter` and is not provided by the `filedialog` module, so the retry/exit path is likely to raise `AttributeError` instead of showing the confirmation dialog.

### 2) Analysis parameters

**Fact:** Cell 5 defines:

- `MAX_PRESSURE = 700  # KpA`
- `exclusion_radius = 60  # pixels`

**Fact:** `MAX_PRESSURE` is never referenced anywhere else in the notebook.

**Fact:** The unit comment says `KpA`, which is likely intended to be `kPa`.

### 3) Core image-processing pipeline

**Fact:** Cell 8 imports `cv2`, `json`, `math`, `numpy`, `csv`, `os`, and `deque`.

**Fact:** The notebook defines these helper functions:

- `load_config(path)` → JSON load
- `signed_acute_angle(base, tip)` → angle in approximately `[-90, 90]` degrees: it folds the `atan2` result to an acute magnitude and makes it positive when the tip's image `y` is below the base, negative when above
- `compute_angle(p1, p2)` → raw `atan2` angle in degrees
- `normalize_angle(angle)` → wrap angle to `[0, 360)`
- `find_actuator_tip(...)` → detect the farthest valid red blob centroid from the base
- `resize_window_preserve_aspect(...)` → OpenCV window sizing helper

**Fact:** `compute_angle()` and `normalize_angle()` are not used by the main pipeline.

**Fact:** `resize_window_preserve_aspect()` is defined twice:

- cell 6: defaults `max_width=2560`, `max_height=1080`
- cell 8: defaults `max_width=1000`, `max_height=700`

The later definition shadows the earlier one.

**Fact:** `main()` requires these globals to exist before execution:

- `CONFIG_PATH`
- `VIDEO_PATH`
- `CSV_PATH`
- `exclusion_radius`

**Fact:** `main()` reads the config JSON and requires:

- `angle_base_point` as a dict with `x` and `y`
- `actuator_roi` as a dict

**Fact:** `actuator_roi` may be expressed as either:

- `top_left` + `bottom_right`, or
- `x`, `y`, `w`, `h`

**Fact:** The code converts the used base/ROI coordinates to integers and rejects non-positive ROI width/height; the unused config tip point is never converted.
For the `top_left`/`bottom_right` form it does not apply `abs(...)`; reversed corners are rejected, whereas the `x,y,w,h` form accepts any integer origin but requires positive `w` and `h`.

### 4) Frame-by-frame analysis

**Fact:** The pipeline:

1. Opens the video with `cv2.VideoCapture(VIDEO_PATH)`.
2. Reads one frame before the loop and uses it only to size the window.
3. Enters a loop that reads the remaining frames.
4. Crops the actuator ROI from each frame with bounds clipping (the clipped origin is used when converting local centroids back to full-frame coordinates).
5. Converts the ROI to HSV.
6. Thresholds red using two hue ranges:
   - `[0,120,120]` to `[10,255,255]`
   - `[170,120,120]` to `[180,255,255]`
7. Applies morphological open and close with a `3x3` kernel.
8. Finds external contours.
9. Filters contours by area `> 50`.
10. Computes each contour centroid from image moments.
11. Picks the centroid farthest from the base point.
12. Rejects tips closer than `exclusion_radius` pixels to the base.
13. Smooths accepted detections by averaging the last 5 coordinates in a `deque(maxlen=5)`.
14. Computes an angle with `signed_acute_angle()`.
15. Draws overlays on the full frame.
16. Writes a CSV row for every processed frame.

**Fact:** Before the first accepted detection, a frame with no valid tip writes `NaN` via `float("nan")`. After the buffer has any detection, frames with no new detection continue using the old buffer and therefore write a stale/smoothed angle rather than `NaN`.

**Fact:** The CSV header is exactly:

- `Frame`
- `ActuatorAngle_deg`

**Fact:** The code flushes the CSV file every 100 frames and closes it in a `finally` block.

**Fact:** The OpenCV display window is named `Analysis`.
When the buffer is empty, the base is still drawn and no angle text is shown; after any accepted detection, the retained buffer makes later misses look like normal `Actuator Angle` overlays. No annotated video is saved.

### 5) Stored outputs

**Fact:** Cell 8 stored one stdout stream with these exact lines:

- `Base: (1254, 702)`
- `Actuator ROI: x=496, y=430, w=1016, h=1150`
- `CSV export complete: C:\Users\ewatt015\OneDrive\Documents\2025-2026\PneumaticActuators\Code\analysis_20251211_181835.csv`

**Fact:** Cell 9 printed the config as a Python dict representation (single quotes), not serialized JSON:

```text
{'actuator_roi': {'h': 1150, 'w': 1016, 'x': 496, 'y': 430},
 'angle_base_point': {'x': 1254, 'y': 702},
 'angle_tip_point': {'x': 696, 'y': 876}}
```

**Fact:** The notebook does not store the generated CSV inside the repository.

**Fact:** The notebook stores no plot images and no processed frame snapshots.

---

## Expected data acquisition context

**Inference:** This notebook appears to sit downstream of a separate capture/configuration step for a soft pneumatic actuator experiment.

**Supporting facts:**

- The repository contains a dedicated video-configuration helper, [VideoConfig.py](../../old-files/VideoConfig.py), which saves a JSON config beside a video.
- The config shape matches the notebook’s requirements (`angle_base_point`, `angle_tip_point`, `actuator_roi`).
- The hard-coded sample paths refer to `PneumaticActuators/RibbedActuators/PrintQualityTest/...`, which looks like a structured experiment series.
- The notebook works on a video file rather than live acquisition.

**Inference:** The video likely shows an actuator with a red marker or red feature near the tip, and the config was created by clicking a base point and an ROI on a representative frame.

**Unknown:** The exact camera setup, lighting, frame rate, and physical meaning of the red feature are not documented in this notebook.

---

## Environment and package assumptions

**Fact:** The notebook requires:

- Python 3.11-compatible Jupyter environment
- `tkinter` GUI support
- `opencv-python` (`cv2`)
- `numpy`
- standard library modules: `json`, `math`, `csv`, `os`, `sys`, `datetime`, `collections.deque`

**Fact:** `matplotlib` is not used in this notebook.

**Inference:** This notebook expects a desktop GUI session. It is not headless-safe because it opens file dialogs and an OpenCV display window.

**Inference:** On some Linux distributions, `tkinter` may be missing even if Python is installed.

---

## Inputs, formats, columns, units, and naming conventions

### Inputs

**Video**

- File type: any file accepted by the dialog, but the prompt emphasizes `.mp4`, `.avi`, `.mov`, `.mkv`
- Path source: user-selected at runtime
- The notebook never reads metadata such as FPS or timestamps

**Config JSON**

- Required keys: `angle_base_point`, `actuator_roi`
- Required shapes at runtime: `angle_base_point` must be a dict containing numeric-convertible `x` and `y`; `actuator_roi` must be a dict containing either `top_left`/`bottom_right` point dicts or `x`, `y`, `w`, `h`.
- Optional/legacy key present in printed config: `angle_tip_point`
- Coordinate units: pixels
- For `top_left`/`bottom_right`, width and height are direct differences and must be positive; for `x,y,w,h`, `w` and `h` must be positive. The notebook does not validate that coordinates lie inside the video before clipping the crop.

**Output CSV**

- Naming convention: `analysis_YYYYmmdd_HHMMSS.csv`
- Before opening the CSV, `main()` creates its parent directory with `exist_ok=True`; an empty parent component resolves to the current directory.
- Column units:
  - `Frame`: frame index, unitless
  - `ActuatorAngle_deg`: degrees
- Missing values: `nan`
- Rows are written with `csv.writer`; there is no time/FPS column, pressure column, source-video path, config path, or detector-quality flag.

### Naming conventions

**Fact:** The config helper in [VideoConfig.py](../../old-files/VideoConfig.py) saves `*_config.json` beside the source video.

**Fact:** The notebook itself does not enforce the sidecar naming convention, but the printed config and hard-coded path structure imply that convention.

---

## State / order dependencies

**Fact:** The notebook is order-sensitive.

- Cell 4 must run before cell 8 because it defines the path globals.
- Cell 5 must run before cell 8 because `exclusion_radius` is required.
- Cell 8 defines `load_config()`, `signed_acute_angle()`, `find_actuator_tip()`, `main()`, and the second `resize_window_preserve_aspect()`.
- Cell 9 depends on cell 8 because it calls `load_config()`.
- Cell 8 has an `if __name__ == "__main__": main()` guard; in a normal notebook kernel this commonly evaluates true, so executing that cell both defines the functions and immediately starts analysis.

**Fact:** Cell 2 is superseded by cell 4 and is effectively dead unless the notebook is manually edited or run piecemeal.

**Fact:** The first frame of the video is consumed before the analysis loop and is never written to CSV.

**Fact:** The CSV frame numbers start at 1 for the second frame read from the video, not frame 0 of the original file.

**Inference:** If the notebook is rerun in a different cell order, it is easy to get missing globals or stale helper definitions.

---

## Numerical and data-quality assumptions

**Fact:** The tip detector assumes the relevant visual marker is red in HSV space and that the marker occupies at least 50 pixels of contour area.

**Fact:** The detector uses the farthest valid red centroid from the base as the tip candidate.

**Fact:** The `exclusion_radius` gate rejects detections too close to the base point.

**Fact:** Tip smoothing averages the most recent 5 accepted coordinates.

**Inference:** The angle calculation is a heuristic, not a fully geometric actuator model. `signed_acute_angle()` uses the tip’s vertical position relative to the base to assign sign, which may be unstable for certain actuator orientations.

**Inference:** Because the notebook does not use `angle_tip_point`, the config’s tip point is probably legacy metadata rather than a live control input.

**Unknown:** Whether the `angle_base_point` refers to a fixed hinge, a calibration point, or a manually chosen reference on the actuator is not documented here.

---

## Errors, obsolete APIs, and correctness risks

### High-confidence issues

- **High:** Error handling is partial and UI-bound.
   - `main()` raises `RuntimeError` for missing required globals, an unreadable first frame, or failure to open the CSV; CSV row failures are only printed and processing continues.
   - The normal frame loop releases the capture, closes the CSV, and destroys OpenCV windows in `finally`, but failures before that `try` block do not receive the same cleanup guarantee.

1. **Bug: incorrect messagebox access in `get_paths()`.**
   - The code calls `filedialog.messagebox.askyesno(...)`.
   - `messagebox` is not imported, and it is not a documented attribute of `tkinter.filedialog`.
   - This path is likely to fail when the config dialog is cancelled.

2. **Bug / data loss risk: the first frame is discarded.**
   - The notebook reads one frame before the loop and never analyzes it.
   - This silently shifts frame indexing and drops data.

3. **Bug / ambiguity: the notebook defines but never uses `angle_tip_point`.**
   - The config contains a tip point, but the analysis ignores it.
   - This suggests stale or incomplete design.

4. **Bug / maintainability issue: duplicate helper definition.**
   - `resize_window_preserve_aspect()` is defined twice with different defaults.
   - This is easy to misunderstand and makes cell-order execution fragile.

5. **Bug / dead code: `MAX_PRESSURE` is unused.**
   - The constant implies pressure-based analysis, but the notebook never measures pressure.

6. **Bug / stale-data risk: the tip buffer is not cleared on a missed detection.**
   - Once one accepted tip is in `tip_buffer`, a later frame with no valid red contour still computes an angle from the old buffer.
   - The CSV therefore reports a held/smoothed angle rather than `nan`, and the display labels it as the last angle; this is not equivalent to a missing tip.

7. **Data-loss risk: timestamp-only output naming can collide.**
   - `get_paths()` uses `analysis_%Y%m%d_%H%M%S.csv` and `main()` opens that path in write mode.
   - Two runs selected in the same folder during one second can overwrite the earlier file.

### Medium-confidence risks

8. **Heuristic tip detection may choose the wrong object.**
   - Any red blob larger than 50 pixels can be selected if it is farthest from the base.
   - Reflections, markers, labels, or background red objects can distort results.

9. **Angle sign rule is simplistic.**
   - Sign is derived from whether the tip is above or below the base, not from full actuator orientation.

10. **UI-only execution model is fragile.**
   - The notebook requires a desktop session and a user present to select files.
   - It is not automation-friendly or batch-friendly.

11. **The notebook mixes configuration, analysis, and execution in one file.**
   - This makes reruns and reuse harder.

12. **The hard-coded path cell is stale.**
    - It points to a different user account than the stored execution output.

---

## Reproducibility / rerun instructions that can be inferred

**Fact:** To rerun the notebook as intended:

1. Execute the path-selection cell(s) so `VIDEO_PATH`, `CONFIG_PATH`, and `CSV_PATH` are set.
2. Ensure `exclusion_radius` is defined.
3. Run the analysis cell so the helper functions and `main()` are defined.
4. Let the file dialogs select a video, a JSON config, and an output folder.
5. Watch the OpenCV window and press `q` to stop early if needed.

**Fact:** The saved CSV filename depends on the wall clock time at selection time.
Because the name has one-second resolution and the file is opened with `"w"`, two selections in the same output folder and second can target the same path.

**Inference:** Exact reruns are not fully reproducible unless the selected paths, input video, and timestamp are recorded elsewhere.

**Unknown:** The notebook does not document the camera settings, so rerunning on a different capture setup may not produce comparable angles.

---

## Prioritized improvements for a rewrite

### P0 — correctness / must-fix

- Replace the incorrect `filedialog.messagebox.askyesno` call with a valid `tkinter.messagebox` import and usage.
- Remove the discarded-first-frame behavior or document it explicitly and make frame indexing consistent.
- Make the config schema explicit and validate it in one place.
- Treat a missed detection as missing (or record an explicit held-value flag); do not silently reuse `tip_buffer` forever.

### P1 — data quality / reliability

- Use the configured `angle_tip_point` or remove it from the config format.
- Replace the “farthest red centroid” heuristic with a more robust tip-localization method.
- Record frame timestamps or FPS-derived time in the CSV.
- Add explicit units and provenance metadata to the CSV output.
- Normalize ROI coordinates consistently with `VideoConfig.py`, and reject clicks/configs that are outside the frame or use fabricated defaults.
- Use collision-resistant output names or refuse to overwrite an existing CSV.
- Stop shadowing `resize_window_preserve_aspect()` with duplicate definitions.

### P2 — maintainability / rewrite hygiene

- Move the analysis code into `/src` and keep the notebook as a thin driver or example.
- Split config loading, detection, angle computation, and export into testable functions.
- Add a small regression test corpus with representative frames and expected angles.
- Remove dead code (`MAX_PRESSURE`, unused helpers, stale path cell).
- Replace interactive-only path selection with CLI arguments or a config file path.

---

## Bottom line

**Fact:** The notebook is a legacy, interactive, per-video actuator-angle extraction script wrapped in notebook form.

**Inference:** It is useful as an artifact of the original workflow, but it should be rewritten into a testable Python module before it can be considered robust or reproducible.
