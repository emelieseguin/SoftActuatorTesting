# Runtime dependency license and redistribution implications

**Status:** Informational — update this table whenever a dependency is added,
removed, or upgraded via `uv add`/`uv remove`.
**Date:** 2026-07-13 (runtime and packaging metadata re-verified in the managed
environment on this date; re-verify at release time, since upstream licensing
can change).
**Related todo:** `architecture-record` (Phase 0) in the approved Unified
SoftActuatorTesting UI Implementation Plan: "Record runtime dependency
licenses."

The project's own license is GNU GPLv3 (see [`../../LICENSE`](../../LICENSE)).

## Current runtime dependencies

These are declared in [`../../pyproject.toml`](../../pyproject.toml). The
native packaging helper copies available license files and a generated
third-party notice into every bundle; release owners must review the exact
installed wheel versions.

| Package | License | Notes |
| --- | --- | --- |
| `numpy` | BSD-3-Clause (plus a few bundled components under 0BSD/MIT/Zlib/CC0-1.0) | Permissive; no redistribution conflict with GPLv3. |
| `opencv-python` | Apache-2.0 | Permissive; compatible with GPLv3 combination. Note some OpenCV third-party components have their own licenses if non-default build options/contrib modules are used. |
| `pyserial` | BSD (BSD-3-Clause-style) | Permissive. |
| `PySide6` (Qt Widgets, Qt Multimedia) | `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` (per PyPI metadata); The Qt Company also sells a commercial Qt license | Default open-source terms are LGPLv3. LGPLv3 permits a GPLv3 application (this project's license) to dynamically link against it without imposing GPL on Qt itself, **provided Qt is linked dynamically** (the default for PySide6 wheels) so end users retain their LGPL right to relink a modified Qt. Do not statically link Qt into a packaged binary without re-checking this. Packaged installers must include the LGPLv3 notice for Qt. |
| `PyQtGraph` | MIT | Permissive; no redistribution conflict. |
| `pytest` | MIT | Dev/test-only dependency; not distributed with the application. |
| `pytest-qt` | MIT | Dev/test-only dependency; not distributed with the application. |
| FFmpeg (external executable, invoked via `subprocess`, **not** a Python package dependency) | Depends on build configuration: LGPL-2.1-or-later for a "free"/non-GPL-component build; GPL-2.0-or-later/GPL-3.0-or-later if built with GPL-only components (for example `libx264`) | **Not bundled.** Operators install a supported matching `ffmpeg`/`ffprobe` pair. Dynamic invocation avoids redistributing this binary; a future bundled build must record its exact configuration and ship matching notices/source-offer obligations. A build with GPL-only codecs requires GPL-compliant redistribution of that binary. |

## Build dependency

| Package | License | Notes |
| --- | --- | --- |
| `PyInstaller` (dev dependency) | GPL-2.0-or-later with a special exception for distributing the generated application/bootloader | It is installed with `uv add --dev`, not imported by the application. The native bundle carries its bootloader, so retain the applicable PyInstaller notice and exception in release records. It does not make the test tooling part of the application runtime. |

## Action items

- Re-verify current wheel licenses and copied notices for every release.
- Retain the Qt/PySide LGPL notice and dynamic-relinking information from the
  exact packaged wheel before publishing an installer.
- Record the exact FFmpeg build/version and license configuration if the
  external-prerequisite policy is ever changed.
- If NI-DAQmx is ever added under a later, separate plan (explicitly out of
  scope for this rewrite), record the `nidaqmx` Python binding's license and
  the proprietary NI driver's redistribution terms at that time — see
  [`../initial-implementation/nidaqmx-integration.md`](../initial-implementation/nidaqmx-integration.md)
  for prior research on that binding.
