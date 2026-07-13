# Analysis review UI architecture and test plan

**Date:** 2026-07-13

This documents the real `AnalysisPage` (`ui/views/workflows/analysis.py`)
built on the already-completed Qt-free `application/analysis_pipeline.py`
contracts. It replaces the previous demo-only placeholder with a
production-capable recorded-file review surface, live-capture provisional
preview, correction/recompute, export, and a finalized-video handoff group,
while keeping the original demo widgets intact for prototype compatibility.

## Scope and layering

* `domain`/`application` remain Qt-free; this page is the only place that
  imports Qt, `pyqtgraph` (via `PlotCanvas`), and (by default construction
  only) the OpenCV adapters `OpenCvVideoFileReader`/`OpenCvRedMarkerFrameDetector`
  — consistent with the existing precedent in `ui/views/marker_suggestion.py`
  and `ui/production.py` (ADR 0001).
* `ui.production.create_production_composition()` now constructs this page as
  `AnalysisPage(production_mode=True)`, with an explicit OpenCV
  reader/detector-backed `AnalysisPipeline` and the existing shared
  `CameraPanelPresenter`. The former `ProductionAnalysisHandoffPage` stub is
  not instantiated. The page's `.source`, `.status`, `.finalized_video`, and
  `.receive_finalization(result)` handoff contract remains stable.
* Production supplies analysis output only through the currently bound
  workspace `ArtifactFileStore`; the generic output picker is disabled there.
  A workspace change synchronously updates the run, calibration, geometry, and
  analysis stores. Standalone/demo use retains the existing selectable output
  location behavior.
* Two small additive changes were made to `application/analysis_pipeline.py`
  (no behavior change for existing callers, all with default values so
  existing tests were unaffected):
  * `AnalysisPipeline.analyze(..., on_progress=None)` — an optional
    per-frame callback used to render live progress/preview without changing
    what is analyzed or returned.
  * `OperatorCorrection.point` widened from `PixelPoint` to `PixelPoint | None`
    — `None` explicitly means "clear this frame's marker back to `missing`"
    (the reviewer's "clear marker point" action), which was previously
    impossible to express through the correction API.
  * A new standalone `analyze_frame(...)` function reuses the pipeline's
    shared per-frame scoring for the Live Capture preview, without altering
    or refactoring the existing `analyze()` loop.

## Recorded-file analysis

The operator chooses a video, an output location (an `ArtifactFileStore`
directory), and a geometry artifact ID to load (parsed with a small
self-contained payload parser mirroring
`VideoGeometryWorkflow.load_document`'s field parsing, intentionally without
its open-video mismatch check, which does not apply before a video is read).
`run_recorded_analysis()` validates all three inputs are present before starting a
`_AnalysisRunThread` (a `QThread` wrapping `AnalysisPipeline.analyze()` with a
cancellable `AnalysisCancellation` token). The public `run_analysis()` method is
a thin dispatcher: in demo (non-production) mode it preserves the original
prototype `run_demo_analysis()` behavior expected by
`instrument_console.py`'s/`experiment_studio.py`'s guided-walkthrough flows;
in production mode it delegates to `run_recorded_analysis()`. The
"Run analysis" button in the recorded-file group always calls
`run_recorded_analysis()` directly, regardless of mode. Each processed frame emits a Qt
`progress` signal (row, frame, frame_count) marshaled onto the GUI thread,
which appends a results-table row, updates the visible frame preview
(`VideoCanvas`), the detection-state/confidence/reasons label, and the
progress bar. `run_status_label` always states the run's explicit completion:
`COMPLETED` (authoritative, exportable), `CANCELLED`, or `TRUNCATED` (both
non-authoritative and not exportable), matching
`AnalysisRunResult.authoritative`'s invariant.

## Review and correction

Selecting a results-table row populates the correction X/Y spin boxes (ranged
to the loaded geometry's frame size) with an existing point (or leaves them
where they were for a missing/ambiguous row). "Apply correction" builds an
`OperatorCorrection(row, PixelPoint(x, y), reason)`; "Clear marker point"
builds `OperatorCorrection(row, None, reason)`. Both call
`AnalysisPipeline.recompute()`, which returns a brand-new `AnalysisRunResult`
— the prior result object is never mutated (Python dataclasses here are
frozen besides). Corrected rows show `Corrected = yes` in the table (a text
value, not a color, per the no-color-only-meaning accessibility requirement).
`export_results()` refuses (with an explicit status message, not a crash) to
export unless the current result is authoritative (`COMPLETED`) and an output
location is chosen; each export call creates a new versioned
`analysis_results`/`analysis_manifest` artifact pair via
`AnalysisArtifactExporter`, so re-running/re-correcting/re-exporting never
overwrites or mutates a previously exported artifact.

When the selected source is outside the workspace, export imports a
collision-safe UUID-named copy under `video/analysis-imports/` before manifest
publication, preserving the external source unchanged and recording the
workspace-relative copy.  Results and manifest are published as a rollback-safe
pair: a pre-publication failure on the manifest removes the new results file.

### Export provenance is snapshotted, never read live

**2026-07-13 fix:** `export_results()` originally read `self._video_path` and
`self._geometry_artifact_id` — the mutable, live UI selections — when building
the exported manifest's `source_video`/`geometry_artifact_id` fields. Because
those selections can change after a run completes (choosing a new video,
accepting a new finalized-video handoff, or loading a different geometry
artifact for a future run), an export triggered after such a change would
silently mislabel the already-completed, authoritative result with the
*wrong* provenance.

The fix has two parts:

- `source_video` is exported from `AnalysisRunResult.source_video`, which is
  part of the frozen, immutable result returned by `AnalysisPipeline.analyze()`
  and therefore can never drift from what was actually analyzed.
- `geometry_artifact_id` has no equivalent field on `AnalysisRunResult` (it is
  a UI/artifact-store concept, not a pipeline concept), so the page now
  snapshots it explicitly: `_pending_run_geometry_artifact_id` is captured
  from the live selection the instant `run_recorded_analysis()` starts a run,
  and is only adopted into `_current_result_geometry_artifact_id` (the value
  `export_results()` actually reads) inside `_on_run_succeeded()`, i.e. once
  that specific run's result is adopted as `_current_result`. A run that fails
  outright (`_on_run_failed()`) never adopts its pending snapshot; a stray
  pending snapshot is also explicitly cleared in `_on_run_finished()` so it
  can never leak into a later, unrelated run. Cancelled and truncated runs
  still adopt their own (correct, non-authoritative) snapshot the same way —
  they simply can never be exported, so mislabeling cannot occur for them
  either. Correction/recompute never touches either snapshot, since
  `AnalysisPipeline.recompute()` preserves the original run's
  `source_video`/`completion`/`authoritative` fields.

Regression tests in `tests/ui/test_analysis_review.py` (section 7b) reproduce
video and geometry selection drift after a completed run and prove the
exported manifest's provenance still matches the analyzed run, including
across repeated runs, a failed run, and a cancelled re-run started after a
prior completed export.

## Live Capture

The Live Capture group **does not own a camera**. It accepts an externally
constructed `CameraPanelPresenter` (the same one used elsewhere, e.g. the
Connections page) and only ever calls `refresh_status()` on a polling
`QTimer` plus subscribes via the existing `CameraPresenterBridge` — the exact
non-owning "second consumer" pattern already used by `ui/widgets/camera_panel.py`.
When no presenter is supplied, the page shows an explicit
"Live capture requires a shared camera preview; none was supplied to this
page." message and performs no camera work at all.

Each new preview frame derives a separate preview geometry from the loaded
full-resolution geometry before it starts one bounded `_LiveAnalysisThread`.
The production FFmpeg proxy's declared `scale=width:height` operation is an
explicit `stretch` transform, so base/tip/ROI and all detector candidates use
the preview's actual pixel coordinates without mutating the authoritative
geometry.  The shared transform also defines letterbox and crop policy; only a
crop that excludes required geometry is incompatible.  The update carries that
preview geometry, so the canvas overlay is mapped against the displayed frame,
not the full-resolution source.  The thread then calls the additive
`analyze_frame()` function and, on success, **publishes to a
`ProvisionalAnalysisChannel`** rather than updating the UI directly. A
second, independent `QTimer` (`_poll_live_channel`) consumes the channel's
latest update and renders it. This is a deliberate one-slot handoff: if a
result is published before the previous one is consumed, it is dropped and
`ProvisionalChannelStats.dropped_stale` increments — the exact bounded
stale/dropped behavior required by the task, and directly observable in the
rendered text (`dropped-stale=N`). All live text is explicitly prefixed
"Provisional (live) — not authoritative" and the point overlay is drawn only
on the live preview canvas; a provisional result is never written to the
results table, never recomputed/corrected, and never exportable.

**Known architectural tradeoff (accepted, not solved further in this
change):** `CameraPanelPresenter.frame_channel` (a `LatestFrameChannel`) is a
one-slot channel. If the Connections page's `CameraPanel` and this page's
Live Capture group are both polling the same shared presenter concurrently,
they compete as two independent consumers of that one slot, so each may
observe fewer frames than if it were the only consumer. This is an
inherent, already-accepted tradeoff of sharing one presenter across multiple
consumers (not a second camera device) and is called out here rather than
addressed with a broadcast/multi-consumer channel, which is out of scope for
this task.

## Finalized-video handoff

`receive_finalization(result)` accepts anything exposing `.video_path: Path |
None` (in practice a `RunFinalizationResult`). When `video_path is None`,
`.source`/`.status` explicitly state the video is unavailable and "Use as
recorded-file source" stays disabled. When present, `.status` states
"Finalized recording is ready for authoritative analysis handoff." and the
button becomes enabled; clicking it sets the finalized path as the
recorded-file section's video source — i.e., **the exact same authoritative
`AnalysisPipeline.analyze()` path** used for any other recorded video. A
provisional live result is structurally incapable of being presented in this
group or exported, since export only ever reads `self._current_result`,
which is only ever assigned from a completed `_AnalysisRunThread`.

In the production Console this exact contract is driven by the production
window's `RunFinalizationResult` refresh. Pressing "Use as recorded-file
source" merely sets `_video_path` and refreshes validation; it does not start
the thread, capture, or any hardware operation.

## Threading, accessibility, and shutdown

Both `_AnalysisRunThread` and `_LiveAnalysisThread` are `QThread` subclasses
that never touch a Qt widget from `run()`; they emit `succeeded`/`failed`
signals, auto-marshaled onto the GUI thread by Qt's queued cross-thread
connection. `closeEvent` calls `.cancel()` then a bounded `.wait(...)` on any
active thread, stops both polling `QTimer`s, and disposes the camera bridge
subscription — mirroring the established pattern in
`ui/views/marker_suggestion.py`/`ui/views/workflows/calibration.py`. Every
interactive control has an explicit accessible name/description, keyboard
operability is inherited from `AccessibleButton`/`VideoCanvas`/Qt's native
widgets (no mouse-only interaction is introduced), and no state is
communicated by color alone (the "Corrected" column and every status label
are always plain text).

## Test plan

Hardware-free `pytest-qt` tests
(`tests/ui/test_analysis_review.py`) use the existing deterministic
`FakeVideoFrameSource`/`FakeRedMarkerFrameDetector`/`RedBlob`/`RedMarkerScan`
doubles (as already used by `tests/application/test_analysis_pipeline.py`), a
fake `CameraPanelPresenter` built the same way
`tests/ui/test_camera_panel.py` (or equivalent camera-capture tests) do, and
`FakeFilePicker`/`ArtifactFileStore` pointed at a `tmp_path`. Covered
scenarios:

1. Input validation — running without a video/geometry/output location shows
   an explicit, specific status message and never starts a thread.
2. Frame-zero display — the first progress signal shows frame index 0 (not
   skipped), matching the pipeline's guarantee that frame zero is measured.
3. Progress — the progress bar and results table update incrementally as
   frames are scored.
4. Cancellation — cancelling an in-flight run yields an explicit `CANCELLED`,
   non-authoritative result and re-enables the run controls.
5. Truncated result display — a run that hits a decode/read failure produces
   an explicit `TRUNCATED`, non-authoritative result.
6. Correction/recompute — correcting a row updates its angle deterministically
   via `AnalysisPipeline.recompute`, and clearing a marker produces a
   `missing` row with `actuator_angle_degrees is None`; the original result
   object is unchanged (mutation-free recompute).
7. Export — export is refused for a non-authoritative result and for a
   missing output location, and succeeds (producing new document IDs) once
   both are satisfied.
8. Provisional-vs-authoritative labeling — a live provisional update is
   always textually prefixed "not authoritative" and never appears in the
   results table or export.
9. Finalized-video handoff / unavailable state — `receive_finalization` with
   a `video_path` enables "Use as recorded-file source" and shows the
   required substring; with `video_path=None` it shows the required
   "No finalized video" substring and stays disabled.
10. Stale/dropped live updates — publishing two provisional updates to the
    channel before it is polled once increments `dropped_stale`, and the
    dropped-stale count is visible in the rendered text.
11. GUI responsiveness — while a blocking fake detector is running on a
    background thread, a zero-delay `QTimer` still fires (proves the GUI
    thread is not blocked).
12. Repeated runs — running twice in a row (including after a correction)
    resets and rebuilds the table/plot cleanly with no leaked thread state.
13. Bounded close cleanup — closing the page while a run/live thread is
    active cancels and joins it synchronously within `closeEvent`, never
    orphaning a thread.
14. Export provenance immutability (section 7b, added 2026-07-13) — export
    reads `AnalysisRunResult.source_video` and a run-start snapshot of the
    geometry artifact ID, never the live selections. Covered: choosing a new
    video after completion still exports the analyzed video; loading a new
    geometry artifact after completion still exports the analyzed geometry
    ID; a second run with new selections exports its own (different)
    provenance; a run that fails outright never leaks its pending snapshot
    onto a later, unrelated successful run; starting and cancelling a second
    run after a completed export never retroactively changes the first,
    still-current completed result's provenance.
15. Production composition (`tests/ui/test_production_composition.py`) — the
    installed Console contains this real page, shares the workspace store with
    calibration/geometry, accepts finalized/unavailable handoffs without
    auto-analysis/capture, reuses the Connections presenter for provisional
    live mode, and bounds embedded-page timer/bridge/presenter shutdown.
16. Empty first live repaint (added 2026-07-13) — a preview canvas may paint
    before any provisional update exists; its overlay hook returns safely
    without dereferencing an absent update.
