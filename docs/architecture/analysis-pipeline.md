# Finalized-video analysis pipeline and test plan

**Status:** Implemented.  
**Date:** 2026-07-13.  
**Related todo:** `analysis-pipeline`.

## Design

`application.analysis_pipeline` is the Qt-free orchestrator for finalized
recordings.  It opens a prerecorded video through the existing
`VideoFrameSource` seam and uses the existing `RedMarkerFrameDetector` plus
`MarkerSuggestionWorkflow` scoring policy; OpenCV remains confined to
`infrastructure.video_file_reader` and `infrastructure.red_marker_detector`.
It validates video dimensions against `VideoGeometry`, requires a finite,
positive *measured* source FPS, processes indices `0..frame_count-1`, and
uses `index / measured_fps` for every timestamp.

The legacy dual-HSV detection, morphology, area filtering, and 60-pixel base
exclusion are retained through the shared detector/scoring settings.
Intentional corrections are: the notebook's farthest-blob-only choice is
replaced with shared explainable ranking; frame zero is not consumed for
display and lost; missing markers never reuse the prior marker/smoothing
buffer; close competing candidates are explicitly `ambiguous`, not silently
selected; output carries state, confidence, reason, point when available, and
an angle only for a resolved/manual point.  Angle calculation is the geometry-only signed image
`atan2(dy, dx)` in `domain.analysis`; it has no calibration dependency and
does not retain the notebook's orientation-specific acute-angle folding.

Cancellation is checked before each read and after each detection.  It returns
an immutable, contiguous prefix marked `cancelled`; a cancellation during video
metadata probing returns an empty cancelled prefix with no invented FPS.  A
`GeometryError` raised specifically by `read_frame()` (for example an
over-reported OpenCV frame count or decode exhaustion) returns the verified
prefix marked `truncated`, including the read-error detail. Both are explicitly
non-authoritative; detector and other unexpected errors still propagate.
Operator corrections create a new immutable result by replacing requested rows
with `manual` detections; the original result is unchanged.

`AnalysisArtifactExporter` first imports an operator-selected video outside the
workspace into a UUID-named `video/analysis-imports/` copy (without changing
the source) and records only that portable reference.  It then publishes the
versioned results CSV and companion manifest through one rollback-safe store
operation: both retain exclusive-create/fsync/replace semantics, and a
pre-publication manifest failure removes the just-written results CSV.  Legacy
angle import remains deliberately narrow: it requires a finite positive
measured FPS to derive timestamps.

The one-slot `ProvisionalAnalysisChannel` discards stale live updates and marks
them non-authoritative.  Preview analysis derives a separate geometry by the
capture proxy's explicit `stretch` transform, mapping base/tip/ROI to preview
pixels while leaving the authoritative geometry untouched.  The pure geometry
transform also defines letterbox and crop behavior; crop is rejected only when
the base, tip, or ROI falls outside the visible preview.  Preview candidates
and overlays therefore use preview-pixel coordinates.  It is preview-only:
only completed finalized-video analysis is authoritative/persistable as a
final result.

## Test plan

- **2026-07-13 final parity plan (`analysis-preview-export-integrity`):**
  unit-test exact full-frame-to-preview geometry transforms for a 3840×2160
  geometry on a 960×540 preview, including base, tip, ROI, detected candidates,
  and overlay coordinates; explicitly exercise stretch, letterbox, and crop
  policies, accepting a crop only when all required geometry remains visible.
  Exercise external-video import into a workspace with duplicate source names,
  source-byte immutability, and portable manifest provenance.  Inject a failure
  while publishing the second member of an analysis result/manifest pair and
  assert that neither artifact remains; also retain collision and durability
  coverage.
- Domain: explicit detected/missing/ambiguous/manual invariants and
  calibration-independent angle calculations.
- Application fakes: frame zero, measured timestamps, invalid FPS and geometry
  rejection, missing/ambiguous results with no stale coordinate, in-loop and
  probe cancellation, over-reported-count truncation prefix policy, immutable
  corrections/recomputation, and one-slot provisional stream authority/drop
  behavior.
- Infrastructure/synthetic video: real OpenCV fixture verifies frame zero,
  timestamps, missing and ambiguous marker frames, and artifact/legacy
  import/export validation.
- Persistence: results include reason fields, remain collision-safe/atomic via
  the existing store, and malformed rows fail with field paths.
