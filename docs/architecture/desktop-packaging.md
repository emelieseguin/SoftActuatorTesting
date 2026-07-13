# Desktop packaging architecture and test plan

**Status:** Implemented for native Linux builds; Windows build execution is
platform-gated.  
**Date:** 2026-07-13.  
**Related todo:** `platform-packaging`, `packaged-ui-smoke`.

## Decision

Use PyInstaller, installed only in the `dev` dependency group through `uv`, to
produce a native, one-directory application bundle from the `src/` package. A
minimal documented `tools/frozen_entrypoint.py` imports its normal bootstrap so
package-relative imports are preserved in the frozen executable. The same
documented Python build helper supplies the Linux and Windows configurations; it
refuses to cross-build because PyInstaller packages native Python extensions and
Qt libraries for its host platform.

The bundle name is `SoftActuatorTesting` and is written below
`dist/desktop/<platform>/`. It contains the application executable, Python
runtime, PySide6/Qt plugins, OpenCV, PyQtGraph, package metadata, and a
`licenses/` directory. Build intermediates are below `build/desktop/`; both
locations are ignored and no binary is committed. Tests, legacy environments,
and development-only modules are explicitly excluded.

The executable remains a console executable so `--no-gui` and `--version`
produce observable output. Its normal launch opens the production Instrument
Console without importing demo services. The production composition constructs
disconnected services only; it does not select, enumerate, open, or connect
hardware until an operator acts.

### Packaged production-console smoke (`--component ui-smoke`)

`--no-gui`/`--smoke-imports` prove the frozen bundle's imports and packaged Qt
resources are present, but neither one ever constructs the production
`InstrumentConsoleWindow`. `tools/package_desktop.py --component ui-smoke`
closes that gap by building a **second**, packaging-only executable
(`SoftActuatorTestingUiSmoke`) from `tools/packaging_ui_smoke.py`, a standalone
entry point that is never imported through `soft_actuator_testing.bootstrap`
and never changes the normal CLI's argument surface or startup semantics.

That entry point:

- forces the `offscreen` Qt platform plugin before any Qt import;
- calls the same public
  `soft_actuator_testing.ui.production.create_production_composition()` the
  normal production launch uses, injecting an inert, in-process
  `CameraCaptureBackend` so construction never runs `FfmpegTools.discover()`
  (no FFmpeg/camera discovery) -- the serial seam keeps the production
  default, a lazy adapter that is constructed but never opens a port;
- redirects workspace preferences to a caller-supplied path so the run never
  touches a real operator's configuration directory;
- asserts the composition never imports `soft_actuator_testing.ui.demo` and
  that the serial snapshot stays `DISCONNECTED`;
- briefly shows the window, pumps a bounded Qt event loop (a `QTimer`
  guarantees `QApplication.exec()` returns even if nothing else quits it),
  then closes and synchronously deletes the window before exiting 0.

`tools/smoke_desktop.py --platform <platform>` runs this second executable
(bounded by the same 30-second subprocess timeout as the other checks) after
the existing `--no-gui`/`--version`/`--smoke-imports` checks, and fails the
whole smoke run if the executable exits non-zero, does not report success, or
reports that the demo module was imported or that serial left its
disconnected default. Pass `--skip-ui-smoke` to run only the import/version
checks (for example while only the main bundle has been rebuilt).

Because this second executable is a packaging-only test aid and not a
distributed release artifact, its build configuration carries no license data
files or third-party notice of its own; only the main `SoftActuatorTesting`
bundle is released to operators.

### Application paths

Persistent preferences use the operating-system configuration location:

- Windows: `%APPDATA%/SoftActuatorTesting/workspace-settings.json`;
- Linux and other Unix platforms: `$XDG_CONFIG_HOME/soft-actuator-testing/`
  (or `~/.config/soft-actuator-testing/`).

Workspaces and recorded artifacts are selected explicitly by the operator and
stay workspace-relative. Packaging writes no runtime output relative to the
current working directory.

### FFmpeg policy

FFmpeg and FFprobe are **external prerequisites**, not bundled application
assets. Operators must install a supported matching pair, place both on
`PATH`, or set `SOFT_ACTUATOR_FFMPEG` to the FFmpeg executable (with matching
`ffprobe` adjacent or on `PATH`). This avoids silently redistributing a build
whose codec configuration may impose GPL obligations, and permits camera
drivers/accelerators appropriate to each native host. Missing tools are a
normal disconnected camera state: the Connections page remains usable and
shows the actionable discovery diagnostic; no camera is opened at startup.

Before changing this policy, record the exact FFmpeg binaries, build
configuration, codec licenses, source-offer obligations, update path, and
Windows/Linux hardware evidence. In particular, the current fallback requests
`libx264`, so bundling a build containing it requires GPL-compliant
redistribution of that binary.

### License material

Every bundle includes the project GPLv3 license, the dependency-license record,
and a generated third-party notice listing the bundled runtime distribution
versions and declared license expressions. It also copies installed license
files when distributions provide them (including NumPy, OpenCV, and
PyQtGraph). Each copied file retains its normalized, distribution-relative
path below `licenses/third-party/<distribution>/`, so same-named notices from
different wheel subdirectories cannot overwrite one another and no absolute
build-environment path is embedded. In PyInstaller's current one-directory
layout, these files are under `_internal/licenses/`; preserve that directory
when distributing the bundle. PySide6/Qt remains dynamically loaded; the release owner must
retain LGPLv3 relinking rights and include the applicable Qt/PySide notices
from the exact wheel before publishing an installer. FFmpeg is not included and
its license is therefore not redistributed by this bundle.

## Commands

Reproduce dependencies first:

```bash
uv sync
```

Build natively on Linux (both the release bundle and the packaging-only
UI-smoke helper, then run the full packaged smoke including production-console
construction):

```bash
uv run python tools/package_desktop.py --platform linux
uv run python tools/package_desktop.py --platform linux --component ui-smoke
uv run python tools/smoke_desktop.py --platform linux
```

Build and smoke on a Windows runner with the repository checkout and Windows
`uv` installed:

```powershell
uv sync
uv run python tools/package_desktop.py --platform windows
uv run python tools/package_desktop.py --platform windows --component ui-smoke
uv run python tools/smoke_desktop.py --platform windows
```

The Windows configuration can be inspected on any host without producing an
artifact:

```bash
uv run python tools/package_desktop.py --platform windows --dry-run
uv run python tools/package_desktop.py --platform windows --component ui-smoke --dry-run
```

## Test plan and evidence

1. Configuration tests run both platform dry-runs, for both the `app` and
   `ui-smoke` components, and assert the entry module, deterministic output
   paths, native-host guard, Qt/OpenCV/PyQtGraph collection, project metadata,
   license assets, duplicate NumPy/OpenCV license basenames with distinct
   destinations, explicit exclusions, and that the `ui-smoke` component (a
   packaging-only test aid) carries no license data files of its own.
2. Bootstrap tests run `--no-gui`, `--version`, and the no-dialog
   `--smoke-imports` path. The latter imports production composition and checks
   Qt plugin, OpenCV, and PyQtGraph resource discovery without constructing a
   window or touching hardware.
3. `tests/test_desktop_packaging.py` also runs `tools/packaging_ui_smoke.py`
   directly (in-process and as a subprocess) and asserts the returned/printed
   evidence: the demo module is never imported, the serial seam stays
   `DISCONNECTED`, and (via a monkeypatched `FfmpegTools.discover` that raises
   if called) that no FFmpeg/camera discovery happens.
4. Native artifact smoke runs the generated `SoftActuatorTesting` executable
   with `--no-gui`/`--version`/`--smoke-imports`, then runs the generated
   `SoftActuatorTestingUiSmoke` executable, which constructs and closes the
   real production Instrument Console under offscreen Qt with no hardware and
   must exit 0 within its bounded subprocess timeout. None of these open a GUI
   window visibly or a native dialog.
5. This Linux environment builds and executes both Linux artifacts. Windows
   execution is intentionally not claimed from Linux; run the stated Windows
   commands, including the `ui-smoke` component, on a Windows runner before
   release. A suitable Windows CI/runner step is:

   ```powershell
   uv sync
   uv run python tools/package_desktop.py --platform windows
   uv run python tools/package_desktop.py --platform windows --component ui-smoke
   uv run python tools/smoke_desktop.py --platform windows
   ```

The default `pytest` suite never needs external FFmpeg or hardware. Physical
camera/4K60 acceptance remains governed by the separate `hardware` marker and
the capture architecture records.
