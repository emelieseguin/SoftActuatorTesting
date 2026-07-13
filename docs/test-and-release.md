# Test and release guide

## Test commands

Run from the repository root after `uv sync`.

```bash
uv run pytest
uv run pytest -o addopts='' --collect-only -m "external_ffmpeg"
uv run pytest -o addopts='' --collect-only -m "hardware" || [ $? -eq 5 ]
uv run pytest -o addopts='' -m "external_ffmpeg"
uv run pytest -o addopts='' -m "hardware"
```

`pyproject.toml` configures the default suite as
`-m 'not hardware and not external_ffmpeg'`. Override that configured
selection with `-o addopts=''` when deliberately selecting a marker. Thus
plain `uv run pytest` requires neither a camera nor externally installed
FFmpeg. The `external_ffmpeg` marker exercises a synthetic source only; it
requires a matching `ffmpeg`/`ffprobe` pair. The `hardware` marker is reserved
for an approved physical rig and is never a release-suite default. At this
snapshot no test is marked `hardware`, so its collection has pytest's normal
exit 5; the collection command accepts that outcome but it is not acceptance
evidence.

Useful targeted commands:

```bash
uv run pytest tests/domain tests/application tests/infrastructure
uv run pytest tests/application/test_run_controller.py tests/infrastructure/test_camera.py
uv run pytest tests/application/test_analysis_pipeline.py tests/infrastructure/test_analysis_pipeline_video.py
uv run pytest tests/infrastructure/test_artifact_store.py tests/infrastructure/test_workspace_settings.py tests/application/test_workspace.py
uv run pytest tests/ui/test_production_composition.py tests/ui/test_production_run_page.py tests/ui/test_analysis_review.py
uv run pytest tests/test_import_boundaries.py tests/test_package.py tests/test_desktop_packaging.py
uv run soft-actuator-testing --no-gui
uv run soft-actuator-testing --smoke-imports
uv build
```

Expected conditions are zero test failures for the selected suite, normal
default-marker deselection of external/hardware tests, and exit status zero
for the two safe CLI checks and build. `--no-gui` must say that no hardware was
initialized. `--smoke-imports` may import Qt, OpenCV, PyQtGraph, and production
composition, but must not construct a window, native dialog, or hardware
connection.

### Headless UI and fixtures

UI tests use `pytest-qt`; use an offscreen Qt platform in headless CI when the
runner does not provide a display:

```bash
QT_QPA_PLATFORM=offscreen uv run pytest tests/ui
```

Tests inject fakes/scripted processes rather than real ports/cameras. Key
fixtures are under `tests/fixtures/`: sanitized calibration, geometry,
pressure, angle, and serial compatibility inputs; deterministic synthetic
OpenCV videos; and marker-suggestion frame scenarios. Generate only when
needed with the commands in `tests/fixtures/README.md`; generated videos are
synthetic, not lab evidence.

Before modifying a behavior, run its nearest targeted suite; then run the
default suite. For artifacts, cover field validation, atomic failure cleanup,
path escape, collision refusal, legacy import/export, and prior-V1 analysis
header compatibility. For lifecycle changes, cover startup proof before
`CMD:START`, every terminal path, duplicate finalization, and bounded cleanup.

## Packaging

The project uses Hatchling for Python distributions and PyInstaller (dev
dependency) for native one-directory desktop bundles. Do not commit generated
`build/` or `dist/` binaries.

```bash
# Python package artifacts
uv build

# Linux: build and execute on native Linux (release bundle plus the
# packaging-only UI-smoke helper; smoke_desktop.py runs both)
uv run python tools/package_desktop.py --platform linux
uv run python tools/package_desktop.py --platform linux --component ui-smoke
uv run python tools/smoke_desktop.py --platform linux

# Configuration-only inspection, safe on any host
uv run python tools/package_desktop.py --platform linux --dry-run
uv run python tools/package_desktop.py --platform windows --dry-run
uv run python tools/package_desktop.py --platform linux --component ui-smoke --dry-run
uv run python tools/package_desktop.py --platform windows --component ui-smoke --dry-run
```

On a native Windows runner, not Linux/WSL:

```powershell
uv sync
uv run python tools/package_desktop.py --platform windows
uv run python tools/package_desktop.py --platform windows --component ui-smoke
uv run python tools/smoke_desktop.py --platform windows
```

PyInstaller refuses cross-builds because it packages host-native extensions and
Qt assets. The Windows dry-run is not a Windows executable test; no Windows
release is supported until the Windows commands pass on a native runner. The
smoke helper runs the produced native executable with `--no-gui`, `--version`,
and `--smoke-imports` under `QT_QPA_PLATFORM=offscreen`; each must exit zero
within its 30-second timeout. It then runs a second, packaging-only executable
(`SoftActuatorTestingUiSmoke`, built by `--component ui-smoke`) that constructs
and closes the real production Instrument Console composition under the same
offscreen platform, with an injected inert camera backend (no FFmpeg/hardware
discovery) and the production default lazy serial adapter (never opens a
port); it must exit 0 within the same 30-second bound, print its success
message, and report `demo_module_imported=False` and
`serial_status=DISCONNECTED`. Pass `--skip-ui-smoke` to `smoke_desktop.py` to
run only the pre-existing import/version checks (for example if only the main
bundle was rebuilt). This project's Windows path is documented and
dry-run-verified only from this Linux environment; native Windows execution of
both the main smoke and the UI-smoke check must be run on a native Windows
runner before release, never claimed from Linux.

### FFmpeg and notices

FFmpeg/FFprobe are external prerequisites and are not bundled. Operators put a
matching pair on `PATH`, or set `SOFT_ACTUATOR_FFMPEG` to FFmpeg with
`ffprobe` adjacent or on `PATH`. Missing tools are a disconnected-camera
diagnostic, not a startup failure.

The bundle copies the project license, the dependency-license record, a
generated `THIRD_PARTY_NOTICES.txt`, and available runtime distribution license
files. In the current one-directory layout these are distributed below
`_internal/licenses/`; retain that directory. The notice identifies bundled
runtime distribution versions and declared license expressions. It states that
PyInstaller is build-time-only and FFmpeg/FFprobe are external. Recheck
`docs/architecture/dependency-licenses.md`, exact installed metadata, Qt/PySide
LGPL notices/dynamic-linking obligations, and any future FFmpeg redistribution
terms at release time.

## Release checklist

1. Update version metadata in `pyproject.toml` and package version expectation
   in `tools/smoke_desktop.py` together; rebuild the lock only through `uv`.
2. Run `uv lock --check`, `uv sync`, the default suite, safe CLI checks, and
   `uv build`.
3. Run desktop packaging tests and both platform dry-runs, including the
   `ui-smoke` component.
4. Build and smoke Linux natively (both the `app` and `ui-smoke` components);
   retain command output, bundle version, and license/notices inventory. The
   UI-smoke check must report the real production console constructed and
   closed with no demo import and no hardware discovery/connection.
5. Build and smoke Windows natively (both components); do not substitute a
   Linux dry-run.
6. Confirm generated binaries/intermediates are ignored and uncommitted.
7. Confirm FFmpeg remains external, or complete a separate redistribution,
   source/license, update, and hardware-acceptance review before bundling it.
8. Review artifact/schema compatibility and release notes; record unresolved
   physical-rig acceptance as a limitation, not a certification claim.

## Verification Summary

Fact-checked against the current working tree on 2026-07-13, then re-verified
after adding the packaged-UI-construction smoke check. Every command in this
guide was executed to confirm it behaves as documented.

- **Claims checked:** 28
- **Confirmed:** 28
- **Corrected:** 0
- **Unverifiable:** 0

Command validation (all run from the repository root):

- `pyproject.toml` sets `addopts = "-m 'not hardware and not external_ffmpeg'"`
  and declares both markers (`pyproject.toml`).
- Default suite: `602 passed, 1 deselected` (offscreen), zero failures.
- `tests/test_desktop_packaging.py` alone: `10 passed`, covering both platform
  dry-runs for the `app` and `ui-smoke` components, an in-process construction
  check (with a monkeypatched `FfmpegTools.discover` that raises if called),
  a dedicated regression-proof check that the demo-import guard actually
  rejects a simulated demo-import regression (not a vacuous pass), and a
  subprocess CLI evidence check.
- Test-order independence: the demo-import guard evicts any already-cached
  `soft_actuator_testing.ui.demo*` modules from `sys.modules` before
  constructing the production composition (`tests/test_desktop_packaging.py::
  _evict_demo_module_cache`), so the assertion stays strict even when
  `tests/application/test_presentation.py` and the other demo-mode test
  modules that import `ui.demo` at collection time run first in the same
  session. Verified by running those five demo-importing test files directly
  ahead of `tests/test_desktop_packaging.py` in one `pytest` invocation
  (`110 passed`), and by repeated full-suite runs (latest:
  `602 passed, 1 deselected`).
- `pytest -o addopts='' --collect-only -m hardware` collects 0 and exits 5;
  `-m external_ffmpeg` collects exactly 1 test
  (`tests/infrastructure/test_external_ffmpeg.py`) and exits 0. No test carries
  the `hardware` marker at this snapshot.
- `soft-actuator-testing --no-gui` prints "no hardware was initialized" (exit 0);
  `--smoke-imports` reports packaged imports available (exit 0);
  `--version` prints `0.1.0`.
- `uv lock --check`, `uv build` (produced sdist + wheel), and both
  `package_desktop.py --dry-run` platforms (both `app` and `ui-smoke`
  components) all exit 0.
- Every targeted test path listed exists; `tests/fixtures/README.md` and the
  calibration/geometry/pressure/angle/serial/video fixture folders are present.
- Native Linux build+smoke of both components: `package_desktop.py --platform
  linux` and `--platform linux --component ui-smoke` each exit 0 and produce
  `dist/desktop/linux/SoftActuatorTesting/` and
  `dist/desktop/linux/SoftActuatorTestingUiSmoke/`; `smoke_desktop.py
  --platform linux` exits 0 and its output includes "Packaged production
  Instrument Console constructed and closed without hardware.",
  `demo_module_imported=False`, and `serial_status=DISCONNECTED`.

Source confirmations:

- Hatchling build backend + PyInstaller dev dependency + `--onedir`; cross-build
  refusal on non-native host (`tools/package_desktop.py:159,187-207`).
- `smoke_desktop.py` runs the native executable with `--no-gui`/`--version`
  (`0.1.0`)/`--smoke-imports` under `QT_QPA_PLATFORM=offscreen`, `timeout=30`
  (`tools/smoke_desktop.py:23-60`), then runs the packaging-only
  `SoftActuatorTestingUiSmoke` executable and asserts its success evidence
  (`tools/smoke_desktop.py::_run_ui_smoke`).
- `tools/packaging_ui_smoke.py::construct_and_close_production_console` calls
  the real `soft_actuator_testing.ui.production.create_production_composition`
  with an inert, in-process camera backend (no `FfmpegTools.discover()`) and
  the production default lazy serial adapter, asserts the demo module was not
  imported and serial stayed `DISCONNECTED`, then closes and synchronously
  deletes the window via `shiboken6.delete` before returning.
- Bundle copies LICENSE, `dependency-licenses.md`, generated
  `THIRD_PARTY_NOTICES.txt`, and runtime distribution license files under
  `licenses/` (runtime `_internal/licenses/`); the notice states PyInstaller is
  build-time-only and FFmpeg/FFprobe are external
  (`tools/package_desktop.py:108-133`). `SOFT_ACTUATOR_FFMPEG` override and PATH
  fallback confirmed (`infrastructure/ffmpeg.py:48-58`). The packaging-only
  `ui-smoke` component intentionally carries no license data files of its own
  (it is a test aid, not a distributed release artifact).

Not statically verifiable (require a physical/native runner, as the guide
already states): the actual native Windows PyInstaller builds (both
components) and their smoke runs, including the Windows `ui-smoke` check.
These have only been dry-run-verified from Linux and must be run on a native
Windows runner before release.
