# NI-DAQmx integration guide for the planned rewrite

Research date: **2026-07-11**

Legend:
- **Official fact** — directly supported by NI's published `nidaqmx` repository, Read the Docs API docs, or PyPI metadata.
- **Project fact** — directly visible in this repository's legacy files.
- **Recommendation** — proposed rewrite direction for this project.
- **Unknown** — not established by the sources reviewed.

## 1) What `nidaqmx-python` is, and what the native NI-DAQmx driver still does

- **Official fact:** [`nidaqmx`](https://github.com/ni/nidaqmx-python) is NI's Python API for NI DAQ hardware, implemented as an object-oriented wrapper around the **NI-DAQmx C API** using Python [`ctypes`](https://docs.python.org/3/library/ctypes.html).
- **Official fact:** Running `nidaqmx` still requires the native [NI-DAQmx driver](https://www.ni.com/downloads/). The package can even launch driver installation with `python -m nidaqmx installdriver`.
- **Official fact:** NI's [`System`](https://nidaqmx-python.readthedocs.io/en/latest/system.html) API exposes installed devices, the installed driver version, and MAX-managed tasks/scales/global channels.
- **Implication from official architecture:** `nidaqmx-python` is **not** a standalone hardware driver. The native NI-DAQmx installation remains responsible for device discovery, hardware communication, buffering/timing/triggering/routing implemented by the NI driver stack, and integration with NI MAX / NI Hardware Configuration Utility.
- **Recommendation:** Treat `nidaqmx-python` as the Python binding layer at the hardware boundary, not as the place to hide project logic such as calibration policy, run naming, or camera coordination.

## 2) Verifiable support snapshot as of 2026-07-11

- **Official fact:** The latest published PyPI release visible on the research date is [`nidaqmx` 1.5.0](https://pypi.org/project/nidaqmx/), uploaded on **2026-04-23**.
- **Official fact:** The upstream repository's current `pyproject.toml` declares `requires-python = '>=3.9,<4.0'` and classifiers for **CPython 3.9-3.14** plus **PyPy**.
- **Official fact:** The published docs state `nidaqmx` supports **CPython 3.9+** and **PyPy3 for non-gRPC use cases**.
- **Official fact:** The published docs state `nidaqmx` supports **Windows and Linux where the NI-DAQmx driver is supported**; NI's hardware/OS compatibility matrix is the authority for exact device/OS combinations.
- **Official fact:** The published docs state `nidaqmx` supports **all versions of NI-DAQmx**, while warning that some functions may be unavailable with older drivers.
- **Recommendation:** For this repository, target a modern CPython version already compatible with project tooling and pin `nidaqmx` in `pyproject.toml` via `uv`, but confirm the chosen Python version against the actual lab machine, NI driver version, and camera stack before committing to a deployment image.
- **Unknown:** The exact NI hardware model(s), host OS, and installed NI-DAQmx driver version for this project are not recorded in this repository today.

## 3) Installation and hardware/driver prerequisites

### Official prerequisites

- **Official fact:** NI says to install the Python package with `pip install nidaqmx` or `pip install nidaqmx[grpc]` for gRPC use cases.
- **Official fact:** NI says the machine must have **NI-DAQmx installed** and at least one **physical or simulated DAQ device** available.
- **Official fact:** NI says device names and configuration can be verified in **NI MAX** or **NI Hardware Configuration Utility**.

### Project-specific packaging recommendation

- **Recommendation:** Because this repository standardizes on `uv`, the rewrite should manage the dependency with `uv add nidaqmx` and run code with `uv run ...`, even though NI's upstream install examples use `pip`.
- **Recommendation:** Keep the NI driver as an external system prerequisite documented in onboarding docs; do not assume `uv sync` alone makes the machine hardware-ready.

### Practical prerequisites for this project

- **Recommendation:** Before writing application code, verify all of the following on the target machine:
  1. NI-DAQmx driver installs successfully.
  2. The DAQ device appears in NI MAX / Hardware Configuration Utility.
  3. The intended pressure sensor wiring and signal type are known.
  4. The intended device/channel names are known.
  5. If camera synchronization matters, available PFI/counter/digital terminals are identified.

## 4) Core NI-DAQmx concepts that matter for this rewrite

### Tasks

- **Official fact:** A [`Task`](https://nidaqmx-python.readthedocs.io/en/latest/task.html) is a collection of one or more virtual channels plus timing, triggering, and related properties.
- **Recommendation:** In this project, the clean mental model is likely:
  - one **pressure acquisition task**,
  - optionally one **actuation output task** if NI hardware eventually replaces firmware-driven valve control,
  - optionally one **trigger/synchronization task** if camera or other devices need hardware timing.

### Channel configuration

- **Official fact:** NI's docs describe virtual channels as software entities that combine a physical channel with configuration such as range, terminal configuration, and custom scaling.
- **Official fact:** Physical channel names follow NI naming such as `Dev1/ai0`, `Dev2/ao5`, or `cDAQ1Mod4/ai0`.
- **Official fact:** The generated API includes `add_ai_voltage_chan(...)` with `terminal_config`, `min_val`, `max_val`, `units`, and `custom_scale_name` parameters.
- **Official fact:** The generated API also includes pressure-specific bridge helpers such as `add_ai_pressure_bridge_two_point_lin_chan(...)`.
- **Recommendation:** Start the rewrite with a **plain voltage input channel** unless the actual sensor is confirmed to be a supported bridge/excitation sensor and the team deliberately wants NI-DAQmx-level scaling.

### Terminal configuration

- **Official fact:** `nidaqmx.constants.TerminalConfiguration` includes `DEFAULT`, `DIFF`, `NRSE`, `PSEUDO_DIFF`, and `RSE`.
- **Recommendation:** Terminal configuration must be externalized to config and chosen from the actual wiring diagram and device manual. Do **not** hard-code `DIFF`/`RSE` until the hardware is known.
- **Unknown:** The correct terminal configuration for this test rig is not recorded anywhere in the legacy files.

### Timing and sample modes

- **Official fact:** NI distinguishes **software timing** from **hardware timing**, and states hardware timing is faster and more accurate than a software loop.
- **Official fact:** `nidaqmx.constants.AcquisitionType` includes `FINITE`, `CONTINUOUS`, and `HW_TIMED_SINGLE_POINT`.
- **Official fact:** `READ_ALL_AVAILABLE = -1` and `WAIT_INFINITELY = -1.0` are library constants.
- **Recommendation:**
  - Use **on-demand / software-timed single reads** only for very simple calibration or diagnostics.
  - Use **continuous hardware-timed acquisition** for live monitoring during an experiment.
  - Use **finite acquisition** for bounded capture windows when the run duration is known in advance.

### Reads and writes

- **Official fact:** `Task.read()` is dynamic and returns data shaped to the task/channel count and sample count requested.
- **Official fact:** `Task.write()` similarly supports dynamic writes for output tasks.
- **Official fact:** NI's examples show:
  - single-sample AI reads with `task.read()`;
  - continuous AI with `cfg_samp_clk_timing(..., sample_mode=AcquisitionType.CONTINUOUS)`;
  - single-sample AO writes with `task.write(1.1)`.
- **Recommendation:** For this project, pressure acquisition should expose a stable data shape regardless of whether the backend uses single-point reads or buffered reads.

### Stream readers and writers

- **Official fact:** [`nidaqmx.stream_readers`](https://nidaqmx-python.readthedocs.io/en/latest/stream_readers.html) and [`nidaqmx.stream_writers`](https://nidaqmx-python.readthedocs.io/en/latest/stream_writers.html) provide preallocated-array APIs for performance.
- **Official fact:** NI specifically documents preallocated arrays as valuable for **continuous acquisition scenarios**.
- **Recommendation:** If the rewrite adopts buffered continuous sampling, prefer `AnalogSingleChannelReader`/`AnalogMultiChannelReader` instead of repeated `task.read()` calls in a GUI loop.

### Constants and enums

- **Official fact:** The library exposes enums for acquisition modes, edges, line grouping, terminal configuration, output behavior, pressure units, and many sensor-specific settings.
- **Recommendation:** Keep project config values as strings/numbers in files, but convert them at the hardware boundary into `nidaqmx.constants.*` enums.

### Errors and warnings

- **Official fact:** [`nidaqmx.errors`](https://nidaqmx-python.readthedocs.io/en/latest/errors.html) defines:
  - `DaqError`
  - `DaqReadError`
  - `DaqWriteError`
  - `DaqWarning`
  - `DaqResourceWarning`
- **Official fact:** `DaqReadError` and `DaqWriteError` include partial sample counts.
- **Recommendation:** Catch `DaqError` at the adapter boundary, translate it into project-level error messages, and preserve the original error code in logs or persisted run metadata.

### Resource cleanup

- **Official fact:** NI examples consistently use `with nidaqmx.Task() as task:`.
- **Official fact:** `DaqResourceWarning` exists specifically for resource-usage problems such as leaked tasks.
- **Recommendation:** The rewrite should create tasks in context managers where possible, or otherwise guarantee `stop()`/`close()` in `finally` blocks.

## 5) Small accurate code examples tailored to this project

### Single pressure-voltage read for a calibration or wiring check

```python
import nidaqmx

with nidaqmx.Task() as task:
    task.ai_channels.add_ai_voltage_chan("Dev1/ai0", min_val=0.0, max_val=5.0)
    volts = task.read()
    print(f"Pressure sensor voltage: {volts:.6f} V")
```

- **Official fact:** This mirrors NI's published software-timed voltage acquisition examples.
- **Recommendation:** Use this style only for one-off diagnostics or calibration helpers, not for the main experiment loop.

### Continuous acquisition better aligned with live experiment monitoring

```python
import numpy as np
import nidaqmx
from nidaqmx.constants import AcquisitionType
from nidaqmx.stream_readers import AnalogSingleChannelReader

buffer = np.zeros(500, dtype=np.float64)

with nidaqmx.Task() as task:
    task.ai_channels.add_ai_voltage_chan("Dev1/ai0", min_val=0.0, max_val=5.0)
    task.timing.cfg_samp_clk_timing(1000.0, sample_mode=AcquisitionType.CONTINUOUS)

    reader = AnalogSingleChannelReader(task.in_stream)
    task.start()

    samples_read = reader.read_many_sample(buffer, number_of_samples_per_channel=len(buffer), timeout=5.0)
    print(f"Read {samples_read} samples; first sample = {buffer[0]:.6f} V")

    task.stop()
```

- **Official fact:** NI documents preallocated stream readers as useful for continuous acquisition.
- **Recommendation:** In the rewrite, the adapter should return timestamps plus sample arrays, and calibration-to-kPa should happen above this layer.

### Hardware-triggerable acquisition boundary for synchronization work

```python
import nidaqmx
from nidaqmx.constants import AcquisitionType

with nidaqmx.Task() as task:
    task.ai_channels.add_ai_voltage_chan("Dev1/ai0")
    task.timing.cfg_samp_clk_timing(1000.0, sample_mode=AcquisitionType.CONTINUOUS)
    task.triggers.start_trigger.cfg_dig_edge_start_trig("/Dev1/PFI0")
    task.start()
    # read in application loop
```

- **Official fact:** NI's examples include continuous voltage acquisition started by a digital edge trigger on `/Dev1/PFI0`.
- **Recommendation:** If the camera or experiment controller can emit/accept hardware triggers, prefer this approach over trying to align only software timestamps.

## 6) Precise inventory of the current legacy implementation

## Bottom line first

- **Project fact:** The legacy implementation uses **zero `nidaqmx` imports** and **zero NI device/channel names**.
- **Project fact:** The current pressure path is built around a **serial device at 115200 baud**, not NI-DAQmx.
- **Recommendation:** The rewrite should treat NI-DAQmx integration as a **backend substitution and protocol simplification**, not as a small line-for-line port.

### `old-files/DataCollection-V2.py`

- **Project fact:** Opens a serial port using `pyserial` at `115200` baud and `0.5 s` timeout.
- **Project fact:** Sends ASCII commands:
  - `CMD:SET CYCLES <n>`
  - `CMD:SET ON <ms>`
  - `CMD:SET OFF <ms>`
  - `CMD:START`
  - `CMD:STOP`
- **Project fact:** Expects incoming serial lines and tries to parse the **third CSV field** (`parts[2]`) as the raw voltage.
- **Project fact:** Loads a calibration JSON and computes `pressure_kPa` in Python before writing `time_s,volts,pressure_kPa` to CSV.
- **Project fact:** Uses serial markers `--- new run ---` and `--- end run ---` to open/close run outputs.
- **Project fact:** Camera recording is started/stopped in software and currently depends on `ffmpeg` + DirectShow, not any NI signal.
- **Device/channel assumption actually present today:** one serial device provides run control and a single voltage-like telemetry stream; no NI AI/AO/DI/DO channel mapping exists in code.

### `old-files/PressureCalibration.py`

- **Project fact:** Opens a serial port at `115200` baud and `0.5 s` timeout.
- **Project fact:** Extracts the **first numeric token anywhere in a serial line** as the latest raw voltage.
- **Project fact:** Optionally sends `CMD:CAL_ON` and `CMD:CAL_OFF` before capturing a sample.
- **Project fact:** Fits either a linear or quadratic model and saves JSON with `model` and `samples`.
- **Device/channel assumption actually present today:** one serial device emits voltage-bearing text; no NI channel name, range, or terminal configuration is known.

### `old-files/VideoConfig.py`

- **Project fact:** Has no DAQ interaction; it creates video-side ROI/base/tip configuration only.
- **Rewrite relevance:** none for NI integration except that run metadata and filenames should stay compatible with downstream analysis.

### `old-files/PneumaticActuatorAnalysis-V1.ipynb`

- **Project fact:** Has no DAQ interaction.
- **Project fact:** Defines `MAX_PRESSURE = 700  # KpA`, but that constant is unused in the notebook.
- **Rewrite relevance:** the notebook currently consumes video/config/CSV artifacts, so any data format changes in the NI rewrite should be deliberate and versioned.

## 7) What should replace the current hard-coded assumptions

The current scripts hard-code serial behavior rather than DAQ settings. A DAQ-backed rewrite should externalize at least:

- **Recommendation:** `device_name` (for example `Dev1` or `cDAQ1Mod1`)
- **Recommendation:** `pressure_input_channel` (for example `Dev1/ai0`)
- **Recommendation:** `pressure_input_min_volts` / `pressure_input_max_volts`
- **Recommendation:** `terminal_configuration` (`DIFF`, `RSE`, `NRSE`, etc.)
- **Recommendation:** `sample_rate_hz`
- **Recommendation:** `sample_mode` (`finite` vs `continuous`)
- **Recommendation:** `samples_per_read`
- **Recommendation:** `read_timeout_s`
- **Recommendation:** calibration file path or calibration coefficients
- **Recommendation:** optional trigger line names (for example start trigger input/output)
- **Recommendation:** optional valve-control output channel(s) if NI hardware will drive actuation
- **Recommendation:** safe output state on shutdown/error
- **Recommendation:** run storage root and metadata schema version

## 8) Recommended integration boundary for the rewrite

### Minimal boundary that matches the current workflow

- **Recommendation:** Introduce a small hardware adapter interface whose first responsibility is **pressure-voltage acquisition only**.
- **Recommendation:** Keep these responsibilities **outside** the NI adapter:
  - calibration model selection and volts→kPa conversion,
  - experiment naming and file layout,
  - GUI state,
  - camera process management,
  - analysis-specific CSV schema decisions.

A practical boundary is:

```python
class PressureSampleSource:
    def open(self) -> None: ...
    def start(self) -> None: ...
    def read_available(self) -> tuple[float, list[float]]: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...
```

Where the tuple means `(monotonic_timestamp_s, voltage_samples)`.

- **Recommendation:** Keep a separate optional interface for actuation outputs if that scope is added later.
- **Recommendation:** Do not force camera control, DAQ, calibration, and CSV writing into one large class like the legacy GUI currently does.

### Why this boundary fits the current code

- **Project fact:** The legacy app's true hardware dependency for pressure is "something that eventually yields volts."
- **Project fact:** Calibration is already a separate concern encoded on disk as JSON.
- **Recommendation:** Preserve that separation in the rewrite so the system can support:
  - a serial backend during migration,
  - a `nidaqmx` backend once hardware is wired,
  - test fakes in unit tests.

## 9) Pressure calibration and unit conversion responsibilities

- **Project fact:** The current calibration tool models `pressure_kPa` as a function of **raw volts**.
- **Project fact:** The live GUI currently writes both `volts` and `pressure_kPa` to CSV.
- **Official fact:** NI virtual channels can include scaling/custom scaling, and the API also exposes pressure-specific channel helpers.
- **Recommendation:** For the first rewrite, keep the NI boundary returning **volts**, and keep **volts→kPa** conversion in project code.
- **Why:**
  1. it preserves compatibility with the current calibration JSON,
  2. it keeps calibration auditable in the repository/domain model,
  3. it avoids prematurely baking a sensor-specific NI scaling choice into the driver layer.
- **Recommendation:** Only move scaling into NI-DAQmx channel configuration later if the team standardizes on a specific transducer type and wants driver-level physical units.
- **Unknown:** The actual sensor technology is not documented here. It may be simple voltage output, current output, bridge-based, or something else.

## 10) Continuous vs finite acquisition in this project

### Continuous acquisition

- **Official fact:** `AcquisitionType.CONTINUOUS` acquires until stopped.
- **Recommendation:** Prefer continuous acquisition for the operator GUI and live plot, because it best matches the current "watch values while the run is active" behavior.
- **Recommendation:** If used, continuously drain the buffer with a stream reader and a fixed chunk size. Do not block the UI thread on one-sample reads.
- **Risk:** Continuous mode requires explicit buffer management, stop handling, and timestamp policy.

### Finite acquisition

- **Official fact:** `AcquisitionType.FINITE` acquires a fixed number of samples.
- **Recommendation:** Use finite acquisition for bounded calibration captures, scripted test bursts, or validation runs where the expected duration is known.
- **Risk:** Finite mode is awkward for a user-driven run that may stop early or continue longer than predicted.

### On-demand / software-timed reads

- **Official fact:** NI distinguishes these from hardware-timed acquisition and states software timing is less accurate.
- **Recommendation:** Keep on-demand reads for hardware checks and perhaps a very simple calibration tool, but not for experiment-grade time alignment.

## 11) Synchronization with camera and experiment control

- **Project fact:** The legacy GUI currently starts camera recording in software, then sends `CMD:START` over serial.
- **Project fact:** That means current synchronization is software/event-order based, not hardware-trigger based.
- **Official fact:** `Task.export_signals.export_signal(...)` and `System.connect_terms(...)` exist for routing clocks/triggers/signals across terminals and tasks.
- **Official fact:** NI examples show digital-edge start triggering on PFI lines and synchronized multi-task acquisition on the same device.

### Recommended synchronization strategy

1. **Recommendation:** Preserve a software-only start/stop path first so the rewrite can ship without waiting for perfect synchronization hardware.
2. **Recommendation:** If the camera or a companion controller can use hardware triggers, add a second path where NI hardware provides or consumes a start trigger.
3. **Recommendation:** Persist explicit run-start metadata indicating whether sync was:
   - software only,
   - shared digital start trigger,
   - shared sample clock / routed signal.
4. **Recommendation:** If camera sync becomes important, identify available PFI/counter lines early; this decision affects channel allocation.

- **Unknown:** Whether the camera stack can accept external trigger input is not documented in this repository.
- **Unknown:** Whether actuation timing will remain on a microcontroller or move onto NI digital/counter outputs is still open.

## 12) Error handling, shutdown, fail-safe behavior, and concurrency

### Error handling

- **Official fact:** NI exposes distinct read/write/general exceptions and warnings.
- **Recommendation:** The adapter should convert `DaqError`/`DaqReadError`/`DaqWriteError` into project-level failures with:
  - human-readable message,
  - NI error code,
  - operation context (`open`, `start`, `read`, `write`, `stop`).
- **Recommendation:** Persist an "aborted/error" run state if acquisition fails after a run starts.

### Shutdown and fail-safe behavior

- **Recommendation:** On any stop path (normal stop, GUI close, exception, Ctrl+C):
  1. stop accepting new UI commands,
  2. drive outputs to a safe state if outputs exist,
  3. stop acquisition tasks,
  4. close tasks,
  5. flush/close CSV or other run artifacts,
  6. stop camera recording,
  7. mark whether the run ended cleanly.
- **Official fact:** NI exposes AO idle/power-up output behavior enums such as `ZERO_VOLTS`, `HIGH_IMPEDANCE`, and `MAINTAIN_EXISTING_VALUE`.
- **Recommendation:** If NI analog output is ever used for valve/pressure control, safe idle behavior must be an explicit config decision, not a default left undocumented.

### Concurrency / thread-safety

- **Official fact:** NI's examples show callbacks and continuous loops, but the reviewed official docs do **not** provide a clear blanket thread-safety guarantee for sharing one `Task` across threads.
- **Recommendation:** Assume **single-owner task access** unless verified otherwise with official NI guidance for the exact API path being used.
- **Recommendation:** If a GUI is used, own the task in one worker thread/process and send decoded samples to the UI through a queue.
- **Recommendation:** Do not have multiple threads call `read()`/`write()` on the same task concurrently.

## 13) Testing options

### Tests that should not require NI hardware

- **Recommendation:** Unit-test the layers above the DAQ boundary with mocks/fakes:
  - calibration application,
  - volts→kPa conversion,
  - run state transitions,
  - CSV formatting,
  - configuration validation,
  - start/stop/error handling.
- **Recommendation:** Add a fake `PressureSampleSource` that returns deterministic sample chunks.

### Simulated-device testing

- **Official fact:** NI states both **physical and simulated devices** are supported.
- **Recommendation:** Use NI simulated devices for adapter/integration tests whenever the intended channel types are supported by simulation on the lab OS.
- **Recommendation:** Tests using simulated devices should verify:
  - device/channel lookup,
  - task creation,
  - timing configuration,
  - start/stop lifecycle,
  - basic read path shape and error translation.

### Tests that still require real hardware

- **Recommendation:** Reserve real-hardware tests for questions simulation cannot answer reliably:
  - actual pressure sensor wiring and polarity,
  - correct terminal configuration,
  - voltage range and clipping behavior,
  - electrical noise at the chosen sample rate,
  - end-to-end camera/actuation synchronization latency,
  - safe-state behavior of real outputs.

## 14) Incremental migration path from the current scripts

1. **Recommendation:** Preserve the current calibration JSON contract (`model`, `samples`) initially.
2. **Recommendation:** Extract pure calibration and CSV-writing logic from the rewrite into testable modules under `src/`.
3. **Recommendation:** Define the hardware adapter interface before replacing any hardware backend.
4. **Recommendation:** Implement a serial-backed adapter first if that helps stabilize the rewrite around current behavior.
5. **Recommendation:** Add a `nidaqmx` pressure-input adapter that produces the same logical output shape as the serial adapter.
6. **Recommendation:** Validate side-by-side that both backends produce the same volts→kPa results for the same sensor input.
7. **Recommendation:** Only then decide whether run control also migrates from firmware commands to NI digital/analog/counter outputs.
8. **Recommendation:** Leave video analysis inputs/outputs stable unless there is a deliberate schema/version update.

## 15) Known risks and open decisions

- **Unknown:** Exact NI device model(s) and installed NI-DAQmx driver version.
- **Unknown:** Pressure sensor type, voltage/current/bridge behavior, and required excitation.
- **Unknown:** Correct physical channel name(s) and terminal configuration.
- **Unknown:** Whether valve actuation remains on a microcontroller or moves to NI hardware.
- **Unknown:** Whether the camera can participate in hardware trigger synchronization.
- **Unknown:** Required acquisition rate for scientifically acceptable pressure traces.
- **Risk:** The legacy code contains inconsistent assumptions about where the "voltage" field appears in serial telemetry; migration should not blindly encode those assumptions into the new design.
- **Risk:** Using NI driver-level pressure scaling too early could hide calibration-policy decisions that are currently explicit and reviewable.
- **Risk:** A software-only camera sync path may remain materially less accurate than a hardware-triggered one even after the DAQ migration.

## 16) Recommended first implementation decision set

If the rewrite had to start before the remaining hardware questions are answered, the lowest-risk choices are:

- **Recommendation:** use `nidaqmx` only for **single-channel analog voltage input** at first;
- **Recommendation:** keep calibration in project code and continue storing both volts and kPa;
- **Recommendation:** externalize all device/channel/range/timing settings;
- **Recommendation:** run acquisition in a worker thread with one task owner;
- **Recommendation:** keep camera start/stop software-driven initially, but design config so a trigger line can be added later.

That path matches the real legacy behavior most closely while still creating a durable boundary for future NI-specific growth.

## Sources

Official upstream pages used in this research:

1. https://github.com/ni/nidaqmx-python
2. https://raw.githubusercontent.com/ni/nidaqmx-python/master/README.rst
3. https://raw.githubusercontent.com/ni/nidaqmx-python/master/pyproject.toml
4. https://pypi.org/project/nidaqmx/
5. https://pypi.org/pypi/nidaqmx/json
6. https://nidaqmx-python.readthedocs.io/en/latest/
7. https://nidaqmx-python.readthedocs.io/en/latest/task.html
8. https://nidaqmx-python.readthedocs.io/en/latest/system.html
9. https://nidaqmx-python.readthedocs.io/en/latest/constants.html
10. https://nidaqmx-python.readthedocs.io/en/latest/stream_readers.html
11. https://nidaqmx-python.readthedocs.io/en/latest/stream_writers.html
12. https://nidaqmx-python.readthedocs.io/en/latest/errors.html
13. https://raw.githubusercontent.com/ni/nidaqmx-python/master/generated/nidaqmx/constants.py
14. https://raw.githubusercontent.com/ni/nidaqmx-python/master/generated/nidaqmx/task/collections/_ai_channel_collection.py
15. https://raw.githubusercontent.com/ni/nidaqmx-python/master/examples/analog_in/voltage_sample.py
16. https://raw.githubusercontent.com/ni/nidaqmx-python/master/examples/analog_in/cont_voltage_acq_int_clk.py
17. https://raw.githubusercontent.com/ni/nidaqmx-python/master/examples/analog_in/cont_voltage_acq_int_clk_dig_start.py
18. https://raw.githubusercontent.com/ni/nidaqmx-python/master/examples/analog_out/voltage_update.py
19. https://raw.githubusercontent.com/ni/nidaqmx-python/master/examples/synchronization/multi_function/cont_ai_di_acq.py

Project files inspected for the legacy mapping:

- [`old-files/DataCollection-V2.py`](../../old-files/DataCollection-V2.py)
- [`old-files/PressureCalibration.py`](../../old-files/PressureCalibration.py)
- [`old-files/VideoConfig.py`](../../old-files/VideoConfig.py)
- [`old-files/PneumaticActuatorAnalysis-V1.ipynb`](../../old-files/PneumaticActuatorAnalysis-V1.ipynb)
- [`old-files/README.md`](../../old-files/README.md)
