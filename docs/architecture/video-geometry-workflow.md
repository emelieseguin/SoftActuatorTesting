# Video geometry workflow

**Status:** Implemented.
**Date:** 2026-07-11.
**Related todo:** `geometry-workflow`.

## Ownership

This todo owns:

- `application/video_geometry_workflow.py` — the Qt-free, OpenCV-free manual
  geometry authoring engine (video lifecycle, undo/redo, zoom/pan/fit view
  transform, coordinate-transform helpers, and versioned/legacy persistence).
- `infrastructure/video_file_reader.py` — the only module that imports `cv2`
  for this workflow; a replaceable adapter implementing the workflow's
  `VideoFrameSource`/`OpenVideoFile` protocols.
- `ui/views/video_geometry.py` — a bare, embeddable `QWidget`
  (`VideoGeometryView`) that renders/edits the workflow's snapshot, following
  the same composable pattern as `ui/views/home_workspace.py` and
  `ui/views/connections.py`.
- The geometry-authoring section embedded in
  `ui/views/workflows/video_geometry_marker_setup.py` (the page's pre-existing
  demo "Set manual geometry"/"Detect marker automatically" buttons and their
  presenter dispatch are untouched; automatic marker detection remains a
  separate, later todo).

`application/presentation.py` (the shared demo/stub presenter) is explicitly
not modified: `WorkflowController` there is documented as a demo stand-in, not
a real geometry service, so this workflow is a fully independent seam that the
host page composes alongside the existing demo controls.

## Design decisions

- **No fabricated defaults.** Loading a new video, closing a video, or
  resetting selections clears the draft base/tip/ROI rather than filling in
  placeholder values (directly addressing the legacy `VideoConfig.py` defect
  of silently writing `(0, 0, 100, 100)` when a selection was missing — see
  `docs/initial-implementation/calibration-and-video.md`). Saving/exporting
  requires a base point, a tip point, and an ROI to all be present; the
  workflow raises `GeometryError` instead of writing an incomplete/invalid
  document.
- **Drag-direction normalization.** `NormalizedRoi.from_corners` (already in
  `domain/geometry.py`) is used for ROI selection so a reversed drag (bottom
  → top, right → left) always produces a valid axis-aligned rectangle,
  fixing the other legacy-tool defect where reverse-order ROI corners were
  written to disk unmodified.
- **Zoom/pan without image resizing.** `ViewTransform.visible_rect()` computes
  a crop rectangle of the current frame rather than resizing pixel data; the
  view cropped to that rectangle is handed to the shared `VideoCanvas`, whose
  own aspect-preserving `scaled()` call in `paintEvent` produces the zoom
  effect. `video_canvas.py` itself is unmodified.
- **Coordinate transforms are pure functions.** `frame_to_widget_point`/
  `widget_point_to_frame` replicate `VideoCanvas.paintEvent`'s
  `KeepAspectRatio`-centered scaling formula, scoped to the current visible
  crop, so mouse-based placement and its round-trip math are testable without
  Qt.
- **"Required selections" for saving = base + tip + ROI.** `domain.geometry`
  treats the tip point as optional, but `infrastructure.artifact_store`'s
  existing `_validate_geometry` already requires a tip point for versioned
  save. This workflow's own completeness gate (base, tip, and ROI all
  present) is a workflow-level policy — consistent with the legacy audit's
  P0 recommendation — not a change to the domain contract.
- **`source_video_name` instead of `video_path`.** `artifact_store.py`
  auto-converts any `*_path`/`video_path` payload key to a workspace-relative
  path on save. Source videos are not required to live inside the workspace,
  so the persisted payload stores only the video's file name for reference.
- **Cancellation.** `VideoProbeCancelled` is a local exception (not a new
  `domain.errors.ErrorCode`) raised when a caller-supplied
  `CancellationToken` aborts an `open()`/metadata probe — used by
  `OpenCvVideoFileReader`'s manual frame-count-scanning fallback for
  containers that under-report `CAP_PROP_FRAME_COUNT`.
- **Mouse placement is an addition, not a requirement.** All base/tip/ROI
  edits are available via keyboard-operable numeric spin boxes and nudge
  buttons first; `VideoGeometryView` additionally installs an event filter on
  its `VideoCanvas` instance (rather than modifying the shared widget) so a
  mouse click/drag can also place a point or draw an ROI, using the same
  coordinate-transform helpers.
- **Default adapter.** `VideoGeometryView` defaults to
  `VideoGeometryWorkflow(OpenCvVideoFileReader())` when no workflow is
  injected — unlike hardware-dependent services elsewhere in this codebase,
  reading a prerecorded file has no live-hardware dependency, so wiring the
  real adapter by default is safe for both the demo page and any future
  composition root. Tests always inject `FakeVideoFrameSource` instead.

## Test plan

- **Application (`tests/application/test_video_geometry_workflow.py`):**
  frame-zero and representative-frame selection, safe metadata probing (no
  state corruption on missing registration/cancellation), video-replacement
  handle cleanup, frame scrubbing/clamping, reverse-drag ROI normalization,
  out-of-bounds rejection for base/tip/ROI, keyboard-nudge deltas and
  bounds-clamping, undo/redo/reset (including redo-stack invalidation),
  overlay visibility, zoom/pan/fit/reset bounds, coordinate-transform
  round-trips (with/without zoom and pan), versioned save completeness and
  round-trip, mismatched-frame-size rejection on load, and legacy
  import/export using the fixtures in `tests/fixtures/geometry/`.
- **Infrastructure (`tests/infrastructure/test_video_file_reader.py`):**
  exercises the real `tests/fixtures/video/synthetic-red-marker.avi` fixture
  (192×128, 3 frames, 10 fps) for metadata probing, frame reads, out-of-range
  rejection, close-then-read failing closed, and the cancellable manual
  frame-count scan.
- **UI (`tests/ui/test_video_geometry.py`):** hardware-free, using
  `FakeVideoFrameSource`/`FakeFilePicker`/a temporary `ArtifactFileStore` —
  video load/close, frame scrubbing and representative-frame buttons,
  zoom/pan/fit/reset, numeric field edits (including an invalid-value error
  path), keyboard nudge buttons, undo/redo/reset, overlay toggle, mouse
  click/drag placement (base, tip, reversed ROI drag), save/load/import/export
  (including the completeness gate and a missing-store guard), and
  handle cleanup on close/teardown.
- **Page wiring:** `tests/ui/test_workflow_pages.py::test_geometry_supports_manual_and_automatic_marker_setup`
  and the parametrized `test_every_page_builds_accessibly_without_shell_navigation`
  continue to pass unmodified, proving the embedded widget does not disturb
  the existing demo presenter flow or page factory contract.

Run `uv run pytest tests/application/test_video_geometry_workflow.py
tests/infrastructure/test_video_file_reader.py tests/ui/test_video_geometry.py
tests/ui/test_workflow_pages.py`, then the full `uv run pytest` suite.
