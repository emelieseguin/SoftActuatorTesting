# Legacy `old-files` environment

This is a reproducible environment for the original scripts and notebook, not
a refactored application. Its metadata permits Python 3.9 through 3.13.

## Install

From this directory, install the complete legacy environment:

```bash
uv sync --all-extras --locked
```

For a smaller environment, use exactly one of these commands instead:

```bash
uv sync --locked
uv sync --locked --extra plotting
uv sync --locked --extra notebook
```

The base environment provides `pyserial` for the two serial GUIs and
`opencv-python` plus `numpy` for `VideoConfig.py` and the analysis notebook.
The `plotting` extra enables the optional live plots in both serial GUIs.
The `notebook` extra provides Jupyter and its Python kernel. `matplotlib` is
optional in the source; the notebook itself does not import it.

## Run

Run these files from `old-files`:

```bash
uv run python DataCollection-V2.py
uv run python PressureCalibration.py
uv run python VideoConfig.py
uv run jupyter notebook PneumaticActuatorAnalysis-V1.ipynb
```

The hyphenated filenames are legacy filenames, so they cannot be used with
`python -m` and intentionally have no console-script entry points. The wheel
preserves the legacy artifacts for installation metadata, but the supported
operator commands above run the source files directly.

## Hardware, native software, and GUI requirements

- `DataCollection-V2.py` and `PressureCalibration.py` use `pyserial` at
  115200 baud and require the appropriate OS driver for the attached serial
  device.
- `DataCollection-V2.py` uses an `ffmpeg` executable on `PATH` for its
  optional camera path. That command is hard-coded for Windows DirectShow
  (`dshow`) and Intel Quick Sync H.264 (`h264_qsv`), so camera capture also
  requires a compatible Windows capture stack, FFmpeg build, and Intel driver.
- `VideoConfig.py` and the notebook operate on existing video files, but open
  Tk and OpenCV windows. They require a local desktop session, Tk support in
  the Python installation (often a separate OS package on Linux), and OpenCV
  GUI/native display libraries.
- These files contain no `nidaqmx` import or NI-DAQmx API usage. Do not add the
  `nidaqmx` package or NI-DAQmx native driver for this legacy implementation;
  its only device I/O is serial and the optional FFmpeg camera subprocess.

## Non-hardware verification

The following commands install/build/parse/import only. They do not invoke the
scripts' `__main__` blocks, open a serial port, start FFmpeg, access a camera,
or open a GUI window.

```bash
uv lock --check
uv sync --all-extras --locked
uv build
uv run python -m py_compile DataCollection-V2.py PressureCalibration.py VideoConfig.py
uv run python - <<'PY'
import importlib.util
from pathlib import Path

for filename in ("DataCollection-V2.py", "PressureCalibration.py", "VideoConfig.py"):
    spec = importlib.util.spec_from_file_location(Path(filename).stem.replace("-", "_"), filename)
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
