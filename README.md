# SoftActuatorTesting

**Last updated:** 2026-07-13

SoftActuatorTesting is a Python/PySide6 desktop application for calibrated
soft-actuator experiments. The production **Instrument Console** implements
workspace-managed artifacts, serial and calibration workflows, FFmpeg-backed
recording/preview, video geometry and advisory marker suggestions, cyclic-run
finalization, and recorded-video angle analysis with review, correction, and
versioned export. Hardware remains disconnected until an operator explicitly
discovers or connects it.

The default production Analysis page is the real `AnalysisPage`, not a
handoff-only placeholder: it accepts finalized recordings, performs
authoritative offline analysis, and offers a clearly labeled provisional live
preview. As of 2026-07-13, the documented default hardware-free test suite
passes without failures; external-FFmpeg and physical-hardware tests remain
opt-in. See [test and release](docs/test-and-release.md) for the dated
verification record and release matrix.

## Start from source

Requires Python 3.10–3.13 and [`uv`](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/emelieseguin/SoftActuatorTesting.git
cd SoftActuatorTesting
uv sync
uv run pytest
```

Safe checks do not open a window, serial port, or camera:

```bash
uv run soft-actuator-testing --no-gui
uv run soft-actuator-testing --smoke-imports
```

Launch the real, disconnected production application:

```bash
uv run soft-actuator-testing
```

Deterministic demo services and the rejected comparison shell remain available
for training/development only:

```bash
uv run soft-actuator-testing --mode demo
uv run soft-actuator-testing --prototype experiment-studio
```

Use `uv run soft-actuator-testing --help` for the complete CLI contract.

## FFmpeg and physical 4K60 status

FFmpeg and FFprobe are external prerequisites; they are not included in source
or desktop bundles. Install a matching pair on `PATH`, or set
`SOFT_ACTUATOR_FFMPEG` to the FFmpeg executable with `ffprobe` adjacent or on
`PATH`. Without them, the application stays usable with camera capture
disconnected and shows an actionable diagnostic.

The software requests 3840×2160 at 60 fps and has synthetic/software coverage,
but **physical 4K60 is not certified**. Representative native Windows
DirectShow and Linux V4L2 evidence plus owner-approved acceptance thresholds
are still required. See the [hardware acceptance procedure](docs/hardware-4k60-acceptance.md).

## Native desktop packages

PyInstaller creates one-directory native bundles. Build and smoke each bundle
on its target OS—never cross-build:

```bash
# Native Linux
uv run python tools/package_desktop.py --platform linux
uv run python tools/smoke_desktop.py --platform linux

# Native Windows PowerShell
uv run python tools/package_desktop.py --platform windows
uv run python tools/smoke_desktop.py --platform windows
```

Configuration-only dry runs are safe on any host:

```bash
uv run python tools/package_desktop.py --platform linux --dry-run
uv run python tools/package_desktop.py --platform windows --dry-run
```

A Linux bundle has been built and hardware-free-smoked natively. No Windows
binary has been built or executed; a Windows release remains unverified until
the native Windows commands succeed. Bundle, FFmpeg, and license obligations
are detailed in [desktop packaging](docs/architecture/desktop-packaging.md) and
[test and release](docs/test-and-release.md).

## Documentation

- [Operator guide](docs/operator-guide.md) and [troubleshooting](docs/troubleshooting.md)
- [Maintainer guide](docs/maintainer-guide.md) and [artifact schemas](docs/artifact-schemas.md)
- [Test and release guide](docs/test-and-release.md) and
  [hardware acceptance](docs/hardware-4k60-acceptance.md)
- [Architecture records](docs/architecture/README.md), [legacy inventory](docs/initial-implementation/README.md),
  and the [software handoff](docs/continuation-plan.md)

The project is licensed under [GPLv3](LICENSE). Runtime dependency and
redistribution notes are in [dependency licenses](docs/architecture/dependency-licenses.md).
