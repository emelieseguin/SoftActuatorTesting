# Unified application final software handoff

**Handoff date:** 2026-07-13
**Purpose:** durable context after completion of the software implementation.

## Final status

All non-hardware tracked work is complete: the selected Instrument Console is
the production shell; real-but-disconnected workspace, serial, calibration,
camera, geometry/marker, readiness, run, analysis, and settings workflows are
integrated; data/resource/accessibility hardening, packaging tooling, and
operator/maintainer documentation are complete.

Production uses the real `AnalysisPage(production_mode=True)`, bound to the
active workspace. A finalized run video is handed to it for an explicit
recorded-file selection; it never auto-starts analysis or hardware. Completed
recorded-video analysis is authoritative and exportable; live camera analysis
is visibly provisional and non-exportable. See
[production composition](architecture/production-instrument-console-composition.md)
and [analysis review](architecture/analysis-review-ui.md).

The default test selection excludes `hardware` and `external_ffmpeg`. The
fact-checked release record dated 2026-07-13 reports a passing default suite,
safe CLI checks, lock check, build, and packaging dry runs. Read the
[test and release guide](test-and-release.md) for dated results rather than
treating a test count here as a permanent claim.

## Remaining external hardware-validation decisions

Only `hardware-4k60-validation` remains blocked. Do not claim a Windows binary
has run or that physical 3840×2160@60 capture is certified. Before pass/fail
acceptance, the owner must provide:

1. representative native Windows DirectShow and native Linux V4L2 rigs;
2. approved nominal run duration, soak duration, and repetitions;
3. allowed dropped/duplicate-frame counts, output timing/frame-count variance,
   startup-proof time, preview latency, and provisional-analysis rate/staleness;
4. CPU, GPU, memory, process-count, storage/free-space, thermal, and throttling
   limits;
5. approved codec, quality, bitrate, and output-size expectations; and
6. the FFmpeg bundle-versus-external-prerequisite redistribution and license
   decision.

Execute the matrix and retain its evidence using
[hardware 4K60 acceptance](hardware-4k60-acceptance.md). Synthetic FFmpeg
coverage, probes, and a Linux package smoke are software evidence only.

## Non-negotiable invariants

- One Python desktop application for Windows and Linux: PySide6, Qt Widgets,
  PyQtGraph, `uv`, `src/`, and `pytest`; no QML, Designer files, JavaScript, or
  second package manager.
- Keep `ui -> application -> domain`; infrastructure implements Qt-free
  application/domain interfaces. Startup and imports do not open hardware.
- Instrument Console is the production shell. Experiment Studio is a
  fake-service development comparison only.
- One FFmpeg process owns one physical camera input. It records
  `video.partial.mkv`, continuously drains a bounded preview, verifies with
  `ffprobe`, then promotes only startup-proven, cooperatively stopped,
  fully-drained clean output to `video.mkv`; failures retain partial evidence.
  Standalone Connections captures reserve unique workspace `runs/` directories
  and retain a minimal status file; their typed capture evidence awaits the
  later run-manifest wiring rather than duplicating it here.
- Camera startup proof precedes `CMD:START`. Persist telemetry before UI
  decimation. Every run terminal path converges on one generation-aware,
  idempotent finalizer.
- Firmware ACK and field semantics remain unconfirmed. The
  `legacy-field-3-unconfirmed` profile is diagnostic, not scientific truth.
- Geometry base/tip/ROI stay manual and versioned. Marker suggestions require
  explicit acceptance; later manual edits clear suggestion provenance.
- Artifacts are versioned, collision-safe, atomic, and workspace-relative.
  Finalized analysis alone is authoritative/exportable; provisional analysis
  is not.
- Global Stop is software control, not a physical emergency interlock.

## Verification and release commands

Run from repository root:

```bash
uv lock --check
uv sync
uv run pytest
uv run soft-actuator-testing --no-gui
uv run soft-actuator-testing --smoke-imports
uv build
uv run python tools/package_desktop.py --platform linux --dry-run
uv run python tools/package_desktop.py --platform windows --dry-run
```

For a release, build and smoke on each matching native platform:

```bash
# Linux
uv run python tools/package_desktop.py --platform linux
uv run python tools/smoke_desktop.py --platform linux

# Windows PowerShell, on a native Windows runner
uv run python tools/package_desktop.py --platform windows
uv run python tools/smoke_desktop.py --platform windows
```

The bundle deliberately excludes FFmpeg/FFprobe; install a matching external
pair on `PATH` or set `SOFT_ACTUATOR_FFMPEG`. Recheck notices and redistribution
obligations in [desktop packaging](architecture/desktop-packaging.md) and
[dependency licenses](architecture/dependency-licenses.md) for each release.

## Future extensions and explicit non-goals

NI-DAQmx, firmware changes, a backend/network API, an ML model, alternate
camera ownership, and a bundled FFmpeg distribution are not part of this
handoff. Propose each separately with a Qt-free protocol seam, explicit
resource ownership/cancellation, artifact provenance, licensing review, safety
analysis, and hardware evidence. Do not use legacy scripts as an operational
path; they are compatibility evidence only.

## Historical implementation ledger

The completed implementation fleet was partitioned into: research/architecture;
fixtures/scaffold/domain/artifacts/UI foundation; prototype construction and
selection; presenter/module integration; workspace, calibration, geometry,
serial, camera, run, marker, and analysis workflows; independent correctness
review; accessibility/resource/data hardening; packaging; and documentation.
The named historical agents and earlier intermediate test counts were useful
execution records, but the repository, architecture index, and dated
verification summaries are now authoritative.

For day-to-day operation start with the [operator guide](operator-guide.md);
for maintenance start with the [maintainer guide](maintainer-guide.md) and
[architecture index](architecture/README.md).
