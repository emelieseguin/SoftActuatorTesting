# Runtime dependency license and redistribution implications

**Status:** Informational — update this table whenever a dependency is added,
removed, or upgraded via `uv add`/`uv remove`.
**Date:** 2026-07-11 (license data verified against PyPI package metadata on
this date; re-verify at the time each dependency is actually added, since
upstream licensing can change).
**Related todo:** `architecture-record` (Phase 0) in the approved Unified
SoftActuatorTesting UI Implementation Plan: "Record runtime dependency
licenses."

The project's own license is GNU GPLv3 (see [`../../LICENSE`](../../LICENSE)).

## Already-present legacy runtime dependencies

These are declared today in
[`old-files/pyproject.toml`](../../old-files/pyproject.toml) and are expected
to carry forward into the rewrite's `pyproject.toml` when `project-scaffold`
creates it.

| Package | License | Notes |
| --- | --- | --- |
| `numpy` | BSD-3-Clause (plus a few bundled components under 0BSD/MIT/Zlib/CC0-1.0) | Permissive; no redistribution conflict with GPLv3. |
| `opencv-python` | Apache-2.0 | Permissive; compatible with GPLv3 combination. Note some OpenCV third-party components have their own licenses if non-default build options/contrib modules are used. |
| `pyserial` | BSD (BSD-3-Clause-style) | Permissive. |
| `matplotlib` (optional `plotting` extra) | Custom BSD-style "Matplotlib License" (PSF-derived); a few bundled fonts/assets carry their own licenses (e.g. OFL-1.1, BaKoMa Fonts Licence, FTL/GPL-2.0-or-later for bundled FreeType-linked components) | Permissive overall; if matplotlib is retained only for legacy/notebook parity, confirm no bundled font is redistributed under a restrictive term when packaging. |
| `ipykernel` / `notebook` (optional `notebook` extra) | BSD-3-Clause | Only relevant while the legacy Jupyter workflow is kept for import/reference. |

## Planned additions for the rewrite

None of these are added to any dependency manifest yet — `project-scaffold`
(Phase 1) is responsible for the actual `uv add` calls. This table records
license expectations ahead of time, per the plan's decision to record
license implications for every runtime dependency.

| Package | License | Notes |
| --- | --- | --- |
| `PySide6` (Qt Widgets, Qt Multimedia) | `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` (per PyPI metadata); The Qt Company also sells a commercial Qt license | Default open-source terms are LGPLv3. LGPLv3 permits a GPLv3 application (this project's license) to dynamically link against it without imposing GPL on Qt itself, **provided Qt is linked dynamically** (the default for PySide6 wheels) so end users retain their LGPL right to relink a modified Qt. Do not statically link Qt into a packaged binary without re-checking this. Packaged installers must include the LGPLv3 notice for Qt. |
| `PyQtGraph` | MIT | Permissive; no redistribution conflict. |
| `pytest` | MIT | Dev/test-only dependency; not distributed with the application. |
| `pytest-qt` | MIT | Dev/test-only dependency; not distributed with the application. |
| FFmpeg (external executable, invoked via `subprocess`, **not** a Python package dependency) | Depends on build configuration: LGPL-2.1-or-later for a "free"/non-GPL-component build; GPL-2.0-or-later/GPL-3.0-or-later if built with GPL-only components (for example `libx264`) | The legacy path already assumes an external `ffmpeg` binary. Because the rewrite still invokes `ffmpeg` as a subprocess rather than linking it, dynamic invocation avoids most linking concerns, but **packaging/redistribution of any bundled `ffmpeg` binary** must record the exact build configuration used and ship the matching license text. If a bundled build includes GPL-only codecs, the packaged distribution must comply with GPL terms for that binary, not just LGPL terms. |

## Action items

- Re-verify each license at the moment a dependency is actually added via
  `uv add` in `project-scaffold`, since upstream licensing/build
  configuration can change between this planning record and implementation.
- Record the exact `ffmpeg` build/version and its license configuration used
  in the Windows/Linux capture benchmark (see
  [`0004-capture-pipeline-benchmark.md`](0004-capture-pipeline-benchmark.md))
  once that benchmark selects the packaged `ffmpeg` build.
- If NI-DAQmx is ever added under a later, separate plan (explicitly out of
  scope for this rewrite), record the `nidaqmx` Python binding's license and
  the proprietary NI driver's redistribution terms at that time — see
  [`../initial-implementation/nidaqmx-integration.md`](../initial-implementation/nidaqmx-integration.md)
  for prior research on that binding.
