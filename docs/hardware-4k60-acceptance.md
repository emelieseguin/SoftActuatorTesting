# 4K60 physical acceptance procedure

## Status and scope

This is an evidence procedure, **not certification**. Current software tests
prove scripted and synthetic FFmpeg behavior only. Physical 3840×2160 at
60 fps remains unaccepted until the owner supplies thresholds and representative
rig evidence for both native Windows DirectShow and native Linux V4L2.

The tested design is one FFmpeg-owned camera input, `video.partial.mkv` during
capture, a 960×540@10 preview branch drained into a one-slot channel, and
`ffprobe` verification before promotion to `video.mkv`. Do not describe a
passing synthetic source, encoder listing, or capability probe as camera
acceptance.

## Required rigs and preflight

Run the matrix on separate representative native hosts, not WSL:

| Rig | Required evidence before a run |
| --- | --- |
| Windows | DirectShow camera name, driver/version, cable/port, native FFmpeg/ffprobe build/configuration, GPU driver, encoder probe outcome, storage volume/health |
| Linux | V4L2 `/dev/video*` node, camera mode listing, kernel/driver, native FFmpeg/ffprobe build/configuration, GPU/accelerator driver, encoder probe outcome, storage volume/health |

For each rig, retain OS/build, application version and commit, Python/package
versions, FFmpeg version/build configuration, selected encoder and command,
camera model/firmware, requested and negotiated profile, power/thermal setup,
free storage, and test operator/date. Verify that the camera exposes and
negotiates 3840×2160@60 with the intended input pixel format before a cyclic
run. Record encoder probes; an encoder shown by `ffmpeg -encoders` is not proof
that it works.

Before physical testing:

1. Review lab safety, actuator limits, controller/firmware protocol facts, and
   an emergency-stop procedure. The software Global Stop outcome does not prove
   physical safe state.
2. Select a writable workspace and record free bytes; configure a realistic
   estimated storage requirement so storage preflight runs.
3. Start production without hardware action and prove no port/camera was opened.
   Then explicitly discover/connect devices.
4. Confirm calibration is adequate, geometry is complete, and the serial profile
   is approved. `legacy-field-3-unconfirmed` remains a diagnostic uncertainty,
   not firmware validation.
5. Run a short capture only to verify startup proof: negotiated profile,
   progress, growing partial recording, and preview frame must all occur before
   `CMD:START`.

## Owner decisions required before pass/fail

The following thresholds are intentionally blank. The owner must approve values
per rig and workload before acceptance; no source code establishes them.

| Metric | Owner-approved threshold | Observed | Pass? |
| --- | --- | --- | --- |
| Nominal run duration and soak duration / repetitions |  |  |  |
| Negotiated width, height, FPS, pixel format/codec |  |  |  |
| Startup-proof completion time |  |  |  |
| Preview first-frame and steady-state latency |  |  |  |
| Recorder dropped and duplicate frames |  |  |  |
| Output frame count/timing variance |  |  |  |
| Minimum provisional-analysis update rate / max stale age |  |  |  |
| Authoritative final-analysis completion/rate/quality |  |  |  |
| CPU, GPU, memory, process count |  |  |  |
| Storage throughput, final size, minimum free-space margin |  |  |  |
| Camera/host/encoder thermal limits and throttling |  |  |  |
| Allowed warnings, reconnects, cleanup time |  |  |  |

## Execution and decision points

1. **Baseline.** Capture a cold-start short run and a normal cyclic run. Check
   that recording proof precedes start, serial settings precede `CMD:START`,
   preview remains responsive, and telemetry is durably recorded.
2. **Sustained run.** Execute the owner-approved run duration at real 4K60
   motion/scene complexity, then repeat/soak for the owner-approved margin.
   Do not use a static synthetic source as the only load.
3. **Observe continuously.** Capture health exposes FFmpeg frame, output FPS,
   speed, output bytes, duplicate/drop counters, negotiated profile, preview
   produced/consumed/replaced counts and maximum age. Collect host CPU/GPU,
   memory, disk throughput/free space, and temperatures at a documented cadence.
4. **Verify recording.** Ensure clean runs promote `video.partial.mkv` to
   `video.mkv`; run `ffprobe`/frame-count verification and compare profile,
   duration and frame accounting against the threshold. Preserve partial files
   from failures rather than renaming them as final output.
5. **Verify analysis.** Hand the finalized video and matching geometry into
   offline analysis. Confirm it processes frame zero with measured FPS,
   classifies missing/ambiguous frames explicitly, and exports only completed
   authoritative results. Live/provisional overlays are not the acceptance
   measurement.
6. **Exercise terminal paths** on an approved safe rig: normal end marker,
   ordinary stop, Global Stop, camera/serial fault or disconnect where safe,
   watchdog timeout simulation, and application close. For each, verify one
   manifest, closed/fsynced pressure output, no duplicate stop write, bounded
   cleanup, and retained diagnostic partial recording where promotion fails.

Stop and block the campaign if the profile is not negotiated exactly, startup
proof fails, capture faults/FFmpeg exits, storage preflight fails, counters or
latency breach an owner-approved limit, thermal throttling occurs, telemetry
or manifest integrity is lost, a cleanup timeout leaves a resource active, or
any lab-safety/firmware uncertainty is unresolved. Diagnose, change one
controlled variable, and repeat; do not average a failure away.

## Evidence template

Copy this template once per run and attach the referenced workspace artifacts.

```text
Run ID / workspace:
Date, operator, host OS/native platform:
Application version + commit:
Camera model/firmware; connection/cable:
Driver/kernel + GPU driver:
FFmpeg/ffprobe version, build configuration, encoder probe and selected encoder:
Serial profile and firmware evidence:
Calibration ID/adequacy; geometry ID:
Requested profile / negotiated profile:
Run duration / soak iteration / scene or actuator load:
Startup proof elapsed; preview first-frame / steady latency:
FFmpeg frame, drop, duplicate, speed, output bytes:
ffprobe codec/pixel format/dimensions/FPS/frame count/duration:
Provisional channel produced/consumed/replaced/max age:
Final analysis ID/completion/authoritative frame count/missing/ambiguous/manual counts:
CPU/GPU/RAM; storage throughput/free margin; thermal observations:
Warnings/faults/reconnects and cleanup duration:
Artifacts retained: run.json, pressure.csv, video.mkv or video.partial.mkv,
  analysis.json, angles.csv, FFmpeg/serial/application logs, host metrics:
Owner thresholds used:
Result (pass/fail/blocked) and owner sign-off:
```

## Verification Summary

Fact-checked against the current working tree on 2026-07-13. Verification of the
software-behavior claims that underpin this procedure; the acceptance thresholds
are intentionally owner-supplied and out of scope for code verification.

- **Claims checked:** 18
- **Confirmed:** 18
- **Corrected:** 0
- **Unverifiable (by design):** the blank owner-threshold table and the physical
  4K60 rig evidence — these require a native Windows DirectShow and native Linux
  V4L2 rig and are deliberately uncertified.

Representative confirmations:

- 4K60 is not certified: no source code establishes acceptance thresholds; only
  scripted/synthetic FFmpeg behavior is tested (`tests/infrastructure/test_ffmpeg.py`,
  `test_external_ffmpeg.py`; `CameraInputProfile.verify_target`,
  `ffmpeg.py:140-153`).
- One FFmpeg-owned input, `video.partial.mkv` during capture, a 960×540@10
  one-slot preview channel, and ffprobe verification before promotion to
  `video.mkv` (`infrastructure/camera.py:141-243,540-609`).
- Platform inputs `dshow` (Windows) / `v4l2` (Linux) (`camera.py:96-99`).
- Startup proof (negotiated 3840×2160@60 profile, ≥1 progress frame, advancing
  output time, growing partial file, ≥1 preview frame) is required before
  `CMD:START`, which the run controller enforces (`camera.py:377-396`,
  `run_controller.py:135-136,287-320`).
- Capture health exposes frame, fps, speed, output_bytes, duplicate/dropped
  frames, negotiated profile, and preview produced/consumed/replaced/max-age
  stats (`application/camera_capture.py:116-180`).
- `legacy-field-3-unconfirmed` is an explicit diagnostic, not firmware
  validation (`serial_adapter.py:115-122`).
- Clean runs promote the partial file and retain it on failure; finalized
  offline analysis processes frame zero with measured FPS and exports only
  completed authoritative results (`camera.py:571-600`,
  `analysis_pipeline.py:64-86,231`).

