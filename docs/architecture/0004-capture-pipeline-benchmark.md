# ADR 0004: 4K60 capture-pipeline benchmark and record+preview design

**Status:** Accepted for implementation, hardware acceptance pending.
Synthetic/capability probes select the implementation architecture.
**Production 4K60 hardware acceptance is not claimed.**
**Date:** 2026-07-11.
**Evidence captured:** 2026-07-11 16:19-16:40 EDT.
**Related todos:** completed `camera-spike` implementation-selection spike and
blocked `hardware-4k60-validation` representative-hardware acceptance.

**Implementation record (2026-07-11):** `camera-integration` implements this
accepted architecture in Qt-free application capture services,
`infrastructure/ffmpeg.py`, the single-owner `infrastructure/camera.py` worker,
and a separately composable presenter-backed camera panel. Default tests use
scripted processes/devices; the opt-in `external_ffmpeg` test uses only a
synthetic source. See
[`camera-capture-implementation.md`](camera-capture-implementation.md).
This records the implemented boundary without changing the decision or claiming
representative 4K60 hardware acceptance.

## Context

The legacy `FFmpegRecorder` (see
[`../initial-implementation/data-collection.md`](../initial-implementation/data-collection.md))
is a single, hard-coded, Windows-only pipeline:

- input: `-f dshow -i video=<selected DirectShow name>`;
- encode: `mjpeg` input, `nv12`, `h264_qsv`, `veryfast`,
  `global_quality=23`, `-rtbufsize 512M`;
- defaults: `3840x2160 @ 60 fps` requested from `send_start()` (unused
  constructor defaults are `1920x1080 @ 30 fps`);
- `ffmpeg` must be on `PATH`; camera detection also shells out to
  `ffmpeg -list_devices true -f dshow -i dummy` and parses stderr.

This path was introduced after earlier OpenCV capture problems, but it has no
Linux path, capability probe, sustained-throughput evidence, dropped-frame
reporting, simultaneous preview, or safe finalization. `stop()` calls
`terminate()` and blocks on `wait()` on the GUI thread (see
[`0003-concurrency-and-run-finalization.md`](0003-concurrency-and-run-finalization.md)).
The rewrite treats sustained 3840x2160 at 60 fps as a target requirement, not
an optional stretch goal, on both Windows and Linux.

## Decision

### Implementation selection (accepted; hardware profile provisional)

Use a separately invoked, version-probed FFmpeg process as the provisional
authoritative recorder baseline on both platforms. Open the camera once and
fan out inside that process:

1. a priority full-rate branch encodes/muxes to `video.partial.mkv`;
2. a decimated `960x540 @ 10 fps` branch feeds preview and provisional
   analysis through a continuously drained pipe;
3. the pipe drainer replaces one latest-frame slot instead of queueing
   frames; UI and analysis consumers may skip stale preview frames and can
   never own or block the recording process;
4. finalized analysis of the recorded video remains authoritative.

An FFmpeg filter graph can still block if nobody drains its preview pipe.
Therefore the drainer is part of recorder ownership, starts before readiness
can be reported, and has a watchdog. A slow UI/analysis consumer is detached
from that drainer by bounded latest-value channels. The implementation must
include a deterministic slow-consumer test required by ADR 0003.

Do **not** select OpenCV `VideoCapture`/`VideoWriter` as the authoritative
recorder from this evidence. The measurable Linux wheel probe below could not
open an H.264 writer, and its MJPEG decode/write plus preview path achieved
only 28.663 fps (0.478x real time). OpenCV remains suitable for
preview/provisional and offline analysis: decoding the same file and
processing every sixth frame achieved 272.152 input fps (4.536x real time).
It can be reconsidered as a recorder only if future representative Windows
and Linux hardware evidence passes every acceptance gate and justifies
changing this decision.

This implementation architecture is accepted so `camera-integration` can
proceed; it is **not** production 4K60 camera acceptance. The concrete
platform encoder and camera input format remain provisional runtime profiles
selected by a real encode probe. Listing an encoder is insufficient: this
host listed QSV, NVENC, and VAAPI, while only NVENC was usable.

### Encoder and input-profile rules

- Enumerate and record the camera's actual modes before capture. Reject
  startup if the negotiated width, height, rate, or pixel format differs from
  the approved profile.
- Prefer camera-native H.264 stream copy if and only if the approved camera
  supplies a suitable 4K60 H.264 stream and preview decoding remains stable.
  Otherwise benchmark camera MJPEG decode plus H.264 encode, then raw input
  plus H.264 encode.
- Probe candidate hardware encoders with a short real encode at application
  startup/configuration time. Do not infer usability from `ffmpeg -encoders`.
- Keep `libx264 -preset ultrafast` as a measurable fallback, subject to target
  CPU/thermal limits. Probe NVENC, QSV, and VAAPI only where the native host,
  driver, FFmpeg build, and device nodes support them.
- Use Matroska during capture. Write `video.partial.mkv`, finalize and verify
  it with `ffprobe`, then atomically promote it to `video.mkv`. Preserve an
  invalid/unfinalized partial file for diagnosis rather than presenting it as
  a completed recording.

## Dated host inventory

The probe host was Ubuntu 24.04.3 under WSL2
(`6.6.87.2-microsoft-standard-WSL2`), not a representative native Linux
capture host.

| Item | Observed value |
| --- | --- |
| CPU | 13th Gen Intel Core i9-13900H, 20 logical CPUs exposed to WSL |
| Memory | 31 GiB total; 25 GiB available at inventory time |
| Repository filesystem | WSL virtual ext4, 1007 GiB total / 802 GiB available |
| Linux video devices | no `/dev/video*`, `/dev/media*`, or `/sys/class/video4linux`; `v4l2-ctl` not installed |
| Windows camera enumeration | PnP reported `Integrated Webcam` and `Integrated IR Webcam`; neither was opened, mode-probed, or treated as representative 4K60 hardware |
| Windows FFmpeg | no `ffmpeg.exe` found on Windows `PATH`; DirectShow could not be probed |
| GPUs | Windows reported Intel Iris Xe and NVIDIA RTX 4070 Laptop GPU; WSL exposed `/dev/dxg` and `nvidia-smi`, but no `/dev/dri` |

Inventory commands:

```bash
date --iso-8601=seconds
uname -a
cat /etc/os-release
lscpu
free -h
df -hT .
lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,MODEL
ls -l /dev/video* /dev/media*
test -d /sys/class/video4linux && find /sys/class/video4linux -maxdepth 2
command -v v4l2-ctl
ls -ld /dev/dri /dev/dxg
nvidia-smi -L
cmd.exe /d /c "where ffmpeg 2>nul"
powershell.exe -NoLogo -NoProfile -Command \
  "Get-PnpDevice -PresentOnly | Where-Object { \$_.Class -in @('Camera','Image') } | Select-Object Class,FriendlyName,Status,InstanceId; Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,AdapterRAM"
```

The missing glob paths printed `none` in the actual inventory wrapper rather
than being passed literally to `ls`.

## FFmpeg and OpenCV capability evidence

### FFmpeg

The installed FFmpeg was Ubuntu `6.1.1-3ubuntu5`, built with GCC 13.2. Its
configuration included `--enable-gpl`, `--enable-libx264`, `--enable-libvpl`,
`--enable-libdrm`, and the NVENC/QSV/VAAPI encoders. The Linux build included
the `video4linux2,v4l2` input device and did not include DirectShow.
Relevant listed encoders were `libx264`, `h264_nvenc`, `h264_qsv`,
`h264_vaapi`, `h264_v4l2m2m`, software MJPEG, `mjpeg_qsv`, and
`mjpeg_vaapi`; listing did not establish that the corresponding device was
usable.

```bash
ffmpeg -hide_banner -version
ffmpeg -hide_banner -devices
ffmpeg -hide_banner -hwaccels
ffmpeg -hide_banner -encoders
ffmpeg -hide_banner -h demuxer=v4l2
ffmpeg -L
```

Actual runtime probes:

```bash
ffmpeg -hide_banner -loglevel error \
  -f lavfi -i 'color=size=640x480:rate=30:duration=1' \
  -c:v h264_nvenc -f null -

ffmpeg -hide_banner -loglevel error \
  -f lavfi -i 'color=size=640x480:rate=30:duration=1' \
  -c:v h264_qsv -f null -

ffmpeg -hide_banner -loglevel error \
  -vaapi_device /dev/dri/renderD128 \
  -f lavfi -i 'color=size=640x480:rate=30:duration=1' \
  -vf 'format=nv12,hwupload' -c:v h264_vaapi -f null -
```

- NVENC opened successfully.
- QSV failed with `Error creating a MFX session: -9`.
- VAAPI failed because `/dev/dri/renderD128` and a VA display were absent.

The Debian package reported 2,468 KiB installed size for the FFmpeg package,
but `/usr/bin/ffmpeg` dynamically referenced 215 library entries. That package
size is not a portable bundle-size estimate.

### OpenCV

System Python 3.12.3 had no `cv2` module. To make the planned dependency
measurable without modifying the concurrently created root project or any
lockfile, the probe used an isolated uv environment:

```bash
uv run --isolated --no-project --with 'opencv-python==5.0.0.93' python - <<'PY'
import cv2
print(cv2.__version__)
print(cv2.getBuildInformation())
registry = cv2.videoio_registry
for backend in registry.getBackends():
    print(
        registry.getBackendName(backend),
        registry.hasBackend(backend),
        registry.isBackendBuiltIn(backend),
    )
PY
```

Package `opencv-python==5.0.0.93` exposed `cv2.__version__ == 5.0.0`.
Its built-in video I/O had FFmpeg 62.28.101 and V4L2 enabled. GStreamer and
Intel MFX were not available. The installed `cv2` package tree occupied
74,865,557 bytes (71.4 MiB) in that isolated environment. OpenCV is already
needed for analysis, so using it for provisional preview adds no new Python
dependency, but it does not remove the need for a controlled external FFmpeg
recorder.

At 3840x2160 at 60 fps, OpenCV's FFmpeg writer:

- failed to open `avc1` and `H264`; the bundled backend attempted unavailable
  `h264_v4l2m2m`;
- opened MJPEG and FFV1 writers.

The writer probe was:

```bash
OPENCV_VIDEOIO_DEBUG=1 \
uv run --isolated --no-project --with 'opencv-python==5.0.0.93' python - <<'PY'
from pathlib import Path
import cv2
import numpy as np

root = Path('.camera-spike-work')
frame = np.zeros((2160, 3840, 3), dtype=np.uint8)
for name, fourcc in [('avc1', 'avc1'), ('h264', 'H264'),
                     ('mjpg', 'MJPG'), ('ffv1', 'FFV1')]:
    path = root / f'opencv-writer-{name}.mkv'
    writer = cv2.VideoWriter(
        str(path), cv2.CAP_FFMPEG, cv2.VideoWriter_fourcc(*fourcc),
        60.0, (3840, 2160),
    )
    print(
        f'{fourcc}: opened={writer.isOpened()}, '
        f'backend={writer.getBackendName() if writer.isOpened() else None}'
    )
    if writer.isOpened():
        writer.write(frame)
    writer.release()
PY
```

## Safe synthetic-source throughput probes

No probe opened a camera. `lavfi testsrc2` and a generated recording were the
only sources. These results test this host's generation/decode, filtering,
encoding, muxing, and storage paths; they do not test USB transport, camera
drivers, DirectShow/V4L2 timestamps, negotiated camera modes, camera-originated
drops, a visible Qt preview, target thermals, or representative image entropy.

The preview proxy decimated to 10 fps, scaled to 960x540, ran `signalstats`,
and emitted one `framecrc` per processed frame. It exercises fan-out and
full-frame provisional work but not UI presentation.

### Results

| Probe | Record / preview frames | Throughput | CPU / maximum RSS | Output |
| --- | ---: | ---: | ---: | ---: |
| FFmpeg libx264 record only, 10 s content, unthrottled | 600 / n/a | 3.33x; 3.04 s wall | 587%; 1,430,812 KiB | 135,900,426 B; 108.720 Mbit/s |
| FFmpeg libx264 plus preview proxy, 10 s content, unthrottled | 600 / 100 | 3.46x; 2.94 s wall | 608%; 1,453,728 KiB | 135,900,426 B; 108.720 Mbit/s |
| FFmpeg NVENC plus preview proxy, 10 s content, unthrottled | 600 / 100 | 1.35x; 7.63 s wall | 64%; 460,068 KiB | 104,147,776 B; 83.318 Mbit/s |
| FFmpeg libx264 plus preview proxy, 30 s real-time-throttled | 1,800 / 300 | 1.02x; 29.54 s wall | 141%; 1,454,072 KiB | 407,774,882 B; 108.740 Mbit/s |
| OpenCV FFmpeg decode + MJPEG write + every-sixth-frame analysis | 600 / 100 | 28.663 fps; 0.478x | 152%; 650,856 KiB | 204,232,835 B; 163.386 Mbit/s |
| OpenCV FFmpeg decode + every-sixth-frame analysis, no writer | 600 / 100 | 272.152 fps; 4.536x | 632%; 577,040 KiB | none |

All four FFmpeg recording probes reported zero duplicate and zero dropped
output frames. `ffprobe -count_frames` confirmed the record counts, and the
CRC line count confirmed preview counts. The slightly faster short fan-out
result than record-only is run-to-run noise; it is evidence of no measurable
penalty in this one short synthetic run, not evidence that fan-out is free.

The OpenCV recording probe decoded the FFmpeg-generated H.264 file before
writing MJPEG, so it is intentionally a conservative decode/write proxy and
not a direct comparison to the `lavfi` FFmpeg input. It establishes that the
only available lossy OpenCV writer path did not meet 60 fps on this host.

### Exact FFmpeg commands

Record-only:

```bash
/usr/bin/time -v -o .camera-spike-work/x264-record-only-time.txt \
ffmpeg -hide_banner -loglevel warning -stats_period 1 -y \
  -f lavfi -i 'testsrc2=size=3840x2160:rate=60:duration=10' \
  -map 0:v -c:v libx264 -preset ultrafast -crf 23 \
  -pix_fmt yuv420p -fps_mode cfr \
  .camera-spike-work/x264-record-only.mkv \
  -progress .camera-spike-work/x264-record-only-progress.txt -nostats
```

libx264 fan-out:

```bash
/usr/bin/time -v -o .camera-spike-work/x264-time.txt \
ffmpeg -hide_banner -loglevel warning -stats_period 1 -y \
  -f lavfi -i 'testsrc2=size=3840x2160:rate=60:duration=10' \
  -filter_complex \
  '[0:v]split=2[record][preview];[preview]fps=10,scale=960:540:flags=fast_bilinear,signalstats[previewout]' \
  -map '[record]' -c:v libx264 -preset ultrafast -crf 23 \
  -pix_fmt yuv420p -fps_mode cfr .camera-spike-work/x264-record.mkv \
  -map '[previewout]' -c:v rawvideo -pix_fmt yuv420p -fps_mode cfr \
  -f framecrc .camera-spike-work/x264-preview.crc \
  -progress .camera-spike-work/x264-progress.txt -nostats
```

NVENC used the same input and filter graph:

```bash
/usr/bin/time -v -o .camera-spike-work/nvenc-time.txt \
ffmpeg -hide_banner -loglevel warning -stats_period 1 -y \
  -f lavfi -i 'testsrc2=size=3840x2160:rate=60:duration=10' \
  -filter_complex \
  '[0:v]split=2[record][preview];[preview]fps=10,scale=960:540:flags=fast_bilinear,signalstats[previewout]' \
  -map '[record]' -c:v h264_nvenc -preset p4 -tune hq \
  -rc vbr -cq 23 -b:v 0 -pix_fmt yuv420p -fps_mode cfr \
  .camera-spike-work/nvenc-record.mkv \
  -map '[previewout]' -c:v rawvideo -pix_fmt yuv420p -fps_mode cfr \
  -f framecrc .camera-spike-work/nvenc-preview.crc \
  -progress .camera-spike-work/nvenc-progress.txt -nostats
```

The 30-second real-time proxy used the libx264 fan-out command with `-re`
immediately before `-f lavfi` and `duration=30`. Verification used:

```bash
ffprobe -v error -count_frames -select_streams v:0 \
  -show_entries stream=codec_name,pix_fmt,width,height,avg_frame_rate,nb_read_frames \
  -show_entries format=duration,size,bit_rate \
  -of default=noprint_wrappers=1 .camera-spike-work/realtime-record.mkv
grep -c '^0,' .camera-spike-work/realtime-preview.crc
tail -15 .camera-spike-work/realtime-progress.txt
```

### Exact OpenCV throughput logic

The input was the verified 600-frame
`.camera-spike-work/x264-record-only.mkv` produced above:

```bash
/usr/bin/time -v -o .camera-spike-work/opencv-time.txt \
env OPENCV_VIDEOIO_DEBUG=0 \
uv run --isolated --no-project --with 'opencv-python==5.0.0.93' python - <<'PY'
from time import perf_counter
import cv2

cap = cv2.VideoCapture(
    '.camera-spike-work/x264-record-only.mkv', cv2.CAP_FFMPEG
)
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps = cap.get(cv2.CAP_PROP_FPS)
writer = cv2.VideoWriter(
    '.camera-spike-work/opencv-mjpg-record.mkv',
    cv2.CAP_FFMPEG,
    cv2.VideoWriter_fourcc(*'MJPG'),
    fps,
    (width, height),
)
if not cap.isOpened() or not writer.isOpened():
    raise SystemExit('capture or writer did not open')

frames = preview_frames = red_pixels = 0
started = perf_counter()
while True:
    ok, frame = cap.read()
    if not ok:
        break
    writer.write(frame)
    frames += 1
    if frames % 6 == 0:
        preview = cv2.resize(
            frame, (960, 540), interpolation=cv2.INTER_LINEAR
        )
        mask = cv2.inRange(preview, (0, 0, 96), (160, 160, 255))
        red_pixels += cv2.countNonZero(mask)
        preview_frames += 1
elapsed = perf_counter() - started
writer.release()
cap.release()
print(
    f'frames={frames} preview_frames={preview_frames} '
    f'elapsed_s={elapsed:.6f} throughput_fps={frames / elapsed:.3f} '
    f'realtime_x={frames / elapsed / fps:.3f} '
    f'red_pixel_accumulator={red_pixels}'
)
PY
```

The preview-only probe was the same loop without `VideoWriter` creation,
`writer.write(frame)`, and `writer.release()`.

## Startup proof, accounting, and finalization

### Startup proof

`STARTING -> READY` must require all of the following before a controller
cycle command is allowed:

1. the FFmpeg process remains alive;
2. its input log confirms the approved negotiated mode;
3. parsed `-progress` data reports `frame >= 1` and increasing
   `out_time_us`;
4. `video.partial.mkv` exists and its byte count has increased;
5. the preview drainer has received at least one complete frame.

A synthetic Python `subprocess.Popen` harness parsed
`-progress pipe:1` every 0.1 seconds and sampled the partial file. On this
host, conditions 1, 3, and 4 became true after 0.612 seconds at progress frame
33, `out_time_us=1000000`, and 5,242,880 output bytes. The probe then sent
`q\n`; FFmpeg exited 0 and `ffprobe` read 68 finalized frames over 1.134
seconds. The preview branch was active but sent to the null muxer in this
latency probe, so condition 5 was **not** latency-measured and remains required
for the application/hardware test.

The FFmpeg portion of that exact probe was:

```bash
ffmpeg -hide_banner -loglevel warning -stats_period 0.1 -y -re \
  -f lavfi -i 'testsrc2=size=3840x2160:rate=60:duration=10' \
  -filter_complex \
  '[0:v]split=2[record][preview];[preview]fps=10,scale=960:540:flags=fast_bilinear,signalstats[previewout]' \
  -map '[record]' -c:v libx264 -preset ultrafast -crf 23 \
  -pix_fmt yuv420p -fps_mode cfr \
  .camera-spike-work/startup-record.partial.mkv \
  -map '[previewout]' -f null - -progress pipe:1 -nostats
```

The service must impose an approved startup timeout. Failure of any condition
stops/finalizes FFmpeg and prevents `CMD:START`; it must not silently fall
back to a lower camera mode.

### Frame and drop accounting

Persist these values in the run manifest and expose them in capture health:

- requested and negotiated input device, backend, size, rate, pixel format,
  encoder, encoder settings, and FFmpeg version/build configuration;
- monotonic process start, first input/preview/output proof times, final
  output PTS, output file bytes, `frame`, `fps`, `speed`, `dup_frames`, and
  `drop_frames` from FFmpeg progress;
- all FFmpeg buffer-overrun, corrupt-buffer, timestamp-discontinuity, device
  disconnect, and encoder warnings;
- preview frames produced, consumed, replaced as stale, and maximum frame age;
- finalized `ffprobe -count_frames` packet/frame count and duration versus the
  expected count from negotiated rate and output PTS.

FFmpeg's `drop_frames=0` covers only drops visible to its output sync logic.
It does not prove a camera/driver never lost a frame before FFmpeg observed
it. Representative tests must also use available camera sequence metadata or
native driver counters and retain the FFmpeg input warnings.

### Clean finalization

Normal stop sends `q\n` to FFmpeg stdin, drains the preview pipe, and waits
off the GUI thread. If it exceeds the approved timeout, Linux sends SIGINT and
Windows sends `CTRL_BREAK_EVENT` to a process group, then waits again. Forced
kill is last resort and must mark the recording unclean. Every path runs
`ffprobe`; only a readable file is promoted from `.partial.mkv`.

Normal-stop proxy:

```bash
(sleep 5; printf 'q\n') | ffmpeg -hide_banner -loglevel warning \
  -stats_period 1 -y -re \
  -f lavfi -i 'testsrc2=size=3840x2160:rate=60:duration=60' \
  -filter_complex \
  '[0:v]split=2[record][preview];[preview]fps=10,scale=960:540:flags=fast_bilinear,signalstats[previewout]' \
  -map '[record]' -c:v libx264 -preset ultrafast -crf 23 \
  -pix_fmt yuv420p -fps_mode cfr \
  .camera-spike-work/graceful-record.partial.mkv \
  -map '[previewout]' -c:v rawvideo -pix_fmt yuv420p -fps_mode cfr \
  -f framecrc .camera-spike-work/graceful-preview.crc \
  -progress .camera-spike-work/graceful-progress.txt -nostats
```

It exited 0. `ffprobe` read 334 frames, 5.567 seconds, and 75,678,448 bytes;
the preview branch produced 56 frames.

The SIGINT proxy wrapped the same command with:

```bash
timeout --preserve-status --signal=INT --kill-after=5s 5s ffmpeg ...
```

FFmpeg returned 255 after handling the signal, but wrote a readable Matroska
file with 329 frames, 5.484 seconds, and 74,557,708 bytes; the preview branch
produced 55 frames. The finalizer must interpret the signal status in context
and use `ffprobe`, not exit code alone, to report artifact readability.

These tests support normal-stop and cooperative interruption design only.
Camera disconnect, controller fault, application crash, power loss, and a
forced process kill remain representative-hardware/fault-injection tests.
No claim of clean finalization for those untested cases is made.

## CPU, memory, storage, thermal, and packaging implications

- The real-time-throttled libx264 proxy used about 1.41 CPU cores on average
  but reached about 1.39 GiB maximum RSS. Its unthrottled capacity used about
  six cores. Camera decode and real image entropy may materially change both.
- NVENC reduced observed CPU and maximum RSS to 64% and about 449 MiB, but was
  only 1.35x in this WSL test. GPU utilization, VRAM, power, and temperature
  were not captured, so no thermal selection follows.
- The synthetic libx264 output was about 13.59 MB/s (48.9 GB/hour);
  NVENC was about 10.41 MB/s (37.5 GB/hour); OpenCV MJPEG was about
  20.42 MB/s (73.5 GB/hour). Test-pattern compressibility makes these capacity
  estimates, not retention requirements.
- Raw 8-bit 4:2:0 at 4K60 is approximately 746 MB/s; packed 8-bit 4:2:2 is
  approximately 995 MB/s. The approved camera's native format is therefore a
  blocking input for bus, decode, and memory-bandwidth analysis.
- A 512 MiB synchronized sequential write proxy on the WSL ext4 volume:

  ```bash
  dd if=/dev/zero of=.camera-spike-work/storage-probe.bin \
    bs=16M count=32 conv=fdatasync status=progress
  ```

  reported 1.6 GB/s. It only establishes ample headroom for these synthetic
  outputs on this virtual volume; it is not a target-disk acceptance result.
- No sustained thermal/soak run was possible because the maximum approved run
  duration and representative native hosts are unspecified.
- Do not use OpenCV's private bundled FFmpeg as the recording executable.
  Package or require a separately version-pinned `ffmpeg`/`ffprobe` pair per
  platform and save `ffmpeg -version` plus `-buildconf` with diagnostics.
  The observed Ubuntu build is GPL-enabled because it includes libx264.
  Bundling any build requires its corresponding notices and source/license
  compliance; package size must be measured from the actual selected Windows
  and Linux distributions, not this dynamically linked Ubuntu package.

## Native input paths to validate

These are command templates for the next hardware run, not commands executed
or accepted on this host.

### Windows DirectShow

```powershell
ffmpeg.exe -hide_banner -list_devices true -f dshow -i dummy
ffmpeg.exe -hide_banner -f dshow -list_options true `
  -i video="APPROVED_CAMERA_NAME"
ffmpeg.exe -hide_banner -loglevel info -stats_period 1 `
  -rtbufsize 512M -thread_queue_size 512 -f dshow `
  -video_size 3840x2160 -framerate 60 -vcodec mjpeg `
  -i video="APPROVED_CAMERA_NAME" <approved-output-and-preview-arguments>
```

Test QSV, NVENC, and libx264 separately on the native Windows target. The
legacy `h264_qsv` setting is not a default until its runtime encode probe
passes.

### Linux V4L2

```bash
v4l2-ctl --device=/dev/video0 --list-formats-ext
ffmpeg -hide_banner -f v4l2 -list_formats all -i /dev/video0
ffmpeg -hide_banner -loglevel info -stats_period 1 \
  -thread_queue_size 512 -f v4l2 -input_format mjpeg \
  -video_size 3840x2160 -framerate 60 -timestamps default \
  -i /dev/video0 <approved-output-and-preview-arguments>
```

Run this on native Linux with real `/dev/video*` and `/dev/dri` access, not
WSL. Test VAAPI, QSV, NVENC, and libx264 only where the actual device and
driver support them.

## Acceptance gates for the representative run

The selected per-platform pipeline must:

- sustain measured 3840x2160 at 60 fps for at least the longest approved
  cyclic run plus its acceptance margin;
- record while a responsive preview is visible and provisional marker
  processing is enabled;
- satisfy every startup-proof condition before the cycle command;
- report the requested/negotiated profile and complete frame/drop accounting;
- remain stable with an artificially slow UI and analysis consumer;
- produce a readable finalized artifact after normal stop, startup timeout,
  controller fault, camera disconnect, application close, and the approved
  recoverable interruption cases;
- stay within approved CPU/GPU, memory, storage, free-space, thermal, preview
  latency, and package-size limits.

## Deferred production-hardware acceptance

`camera-spike` is **done** because the implementation architecture and current
host evidence are complete. The separate `hardware-4k60-validation` todo is
**blocked**. Accepting a Windows/Linux production profile requires:

1. approved representative 4K60 camera make/model(s), connection type, and
   supported 3840x2160@60 pixel formats on each OS;
2. native Windows and native Linux target machines, drivers, and the exact
   FFmpeg distributions proposed for packaging;
3. maximum cyclic-run duration, soak margin, allowed recorded drops/duplicates,
   startup timeout, preview frame rate/latency, and provisional-analysis rate;
4. recording codec/quality or bitrate limits and acceptable output size;
5. CPU, GPU, memory, thermal, sustained-storage, minimum-free-space, and
   package-size thresholds;
6. a packaging policy: bundle the pinned FFmpeg builds or require and verify a
   separately installed executable.

The implementation decision is to keep FFmpeg as the provisional
authoritative baseline and keep OpenCV in preview/offline analysis roles.
Once the missing inputs are supplied, run the native DirectShow and V4L2
matrix and choose the simplest passing encoder per OS. Future hardware
evidence may change the backend decision; this WSL synthetic result must not
be extrapolated into a production hardware-success claim.

## Consequences

- Capture implementation remains behind the project-owned camera/recorder
  protocol; UI and application layers do not depend on FFmpeg process details.
- `camera-integration` can implement the interface, FFmpeg baseline,
  readiness state, accounting, partial-file policy, and fake/synthetic tests
  without waiting on representative hardware. It does not thereby accept a
  production 4K60 profile.
- `hardware-4k60-validation` follows both `camera-spike` and
  `camera-integration`; `camera-integration` does not depend on that blocked
  validation todo.
- Hardware tests remain excluded from the default `uv run pytest` suite as
  required by [`test-plan.md`](test-plan.md).
- This ADR must be updated with the representative command logs and final
  per-platform profiles, or superseded by a dated ADR, when hardware
  acceptance completes.
