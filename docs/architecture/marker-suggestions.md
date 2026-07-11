# Guided red-marker suggestions

**Status:** Implemented.
**Date:** 2026-07-11.
**Related todo:** `marker-suggestions`.

## Ownership

This todo owns:

- `application/marker_suggestion.py` — the Qt-free, OpenCV-free scoring/
  ranking engine (`HsvRedThresholds`, `RedBlob`/`RedMarkerScan`,
  `MarkerSuggestionCandidate`/`MarkerSuggestionResult`/`MarkerSuggestionState`,
  `MarkerSuggestionCancellation`/`MarkerSuggestionCancelled`,
  `FakeRedMarkerFrameDetector`, and the `MarkerSuggestionWorkflow` orchestrator
  itself). This module never imports `cv2` or Qt.
- `infrastructure/red_marker_detector.py` — the only module that imports `cv2`
  for this feature; a replaceable adapter (`OpenCvRedMarkerFrameDetector`)
  implementing the engine's `RedMarkerFrameDetector` protocol (dual-hue HSV
  masking, morphology, ROI restriction, contour/blob extraction).
- `ui/views/marker_suggestion.py` — a bare, embeddable `QWidget`
  (`MarkerSuggestionView`) that composes the engine with an existing
  `VideoGeometryView` instance: configurable threshold controls, a bounded
  cancellable detection worker, a ranked candidate table with confidence and
  reasons, a mask preview, and a candidate bounding-box overlay drawn on the
  *same* canvas the manual editor already uses.
- A small, additive extension of `application/video_geometry_workflow.py`:
  `tip_provenance`/`tip_selection_confidence`/`tip_selection_reasons`/
  `tip_suggestion_settings` fields on `GeometryEditState`/
  `GeometryWorkflowSnapshot`, the new `accept_marker_suggestion()` method, and
  conditional persistence of those fields in `as_document()`/`load_document()`.
  `set_tip_point()`/`nudge_tip()`/`clear_tip()` were extended (not replaced)
  to reset provenance to `"manual"`/`None` so a later manual correction is
  never mistaken for an unreviewed detector output.
- The new deterministic fixture `tests/fixtures/video/synthetic-marker-
  suggestions.avi` and its generator script (see
  `tests/fixtures/README.md`).
- Embedding `MarkerSuggestionView` inside
  `ui/views/workflows/video_geometry_marker_setup.py`, alongside (not
  replacing) the pre-existing demo "Set manual geometry"/"Detect marker
  automatically" buttons and their presenter dispatch, which remain untouched.

`domain/analysis.py`'s existing `MarkerCandidate`/`MarkerDetectionResult` are
intentionally **not** reused or modified: they belong to the separate,
pending analysis-pipeline/analysis-ui todos, and are too lean (no bounding
box, no confidence components, no reasons) for this task's explainability
requirements. `domain/errors.py` is also unmodified; every new validation
error reuses the existing `GeometryError`/`ErrorCode.GEOMETRY_INVALID`.

## Design decisions

- **Complement, never replace, manual selection.** No detection ever runs
  automatically. An operator explicitly requests a scan for the frame
  currently on screen, reviews ranked candidates/reasons/mask preview, and
  either accepts one candidate (which becomes an ordinary tip point via
  `accept_marker_suggestion`) or ignores all of them and keeps using the
  existing manual base/tip/ROI controls. Correcting an accepted candidate
  afterwards with `set_tip_point`/`nudge_tip` reverts provenance to
  `"manual"` and clears the suggestion metadata — nothing is fabricated or
  silently kept as authoritative once a human overrides it.
- **Dual-hue HSV, not a single band.** OpenCV's 8-bit HSV hue wraps at 179;
  red therefore needs two bands (`[0, hue_low_max]` and `[hue_high_min,
  179]`), combined with `cv2.bitwise_or`. Default thresholds
  (`hue_low_max=10`, `hue_high_min=170`, `saturation_min=value_min=120`) are
  taken directly from the legacy `old-files/PneumaticActuatorAnalysis-V1.ipynb`
  (cell 8) detector, which is the closest prior art for this exact
  hardware/lighting setup. `tests/infrastructure/test_red_marker_detector.py`
  proves the high-hue band is load-bearing by showing a single-band
  configuration loses the wraparound marker the default dual-band
  configuration finds.
- **Detector output is unopinionated; scoring is a separate policy layer.**
  `OpenCvRedMarkerFrameDetector.scan()` returns every contour that survives
  morphology/ROI masking, with no area/circularity filtering — filtering,
  scoring, and ranking are entirely the Qt/cv2-free
  `MarkerSuggestionWorkflow`'s responsibility. This keeps the explainable
  scoring policy (redness/size/circularity/distance-from-base/temporal
  continuity, weighted and renormalized over only the components that are
  actually available) unit-testable with a fake detector, independent of any
  real pixel data.
- **Confidence components, not a single opaque score.** Every
  `MarkerSuggestionCandidate` carries `redness_score`, `size_score`,
  `circularity_score`, and (when available) `distance_from_base_score`/
  `temporal_continuity_score`, plus a human-readable `reasons` tuple built
  from the same numbers — so a rendered candidate list can always show *why*
  a candidate ranked where it did, not just a bare percentage.
- **Explicit `NO_DETECTION`/`AMBIGUOUS`/`RESOLVED` states.** A
  `MarkerSuggestionResult`'s invariants (enforced in `__post_init__`) forbid
  a `NO_DETECTION` result from carrying candidates and forbid any other state
  from being empty, and require at least two candidates to be present for
  `AMBIGUOUS`. Ambiguity is declared whenever the top two candidates'
  confidence gap is below a configurable `ambiguity_margin` (default `0.05`)
  — the ranked list and mask preview are still shown so the operator can make
  an informed choice, rather than hiding the close call.
- **Never report a stale tip as current.** Two independent guards exist: (1)
  `MarkerSuggestionWorkflow` stamps every result with a monotonically
  increasing `sequence`; `is_current(result)` returns `False` once a newer
  `suggest()` call has been issued, so a slow/late background scan can never
  overwrite a newer one. (2) `MarkerSuggestionView` additionally checks that
  a result's `frame_index` still matches the frame currently shown by
  `VideoGeometryView` before rendering it or enabling "Accept", and a
  lightweight polling `check_staleness()` (wired to a timer in the running
  UI, callable directly and deterministically in tests) disables acceptance
  the moment the operator scrubs to a different frame after a scan
  completed.
- **Heavy pixel work stays off the GUI thread, with bounded cancellation.**
  `MarkerSuggestionView` mirrors `_CalibrationCaptureThread`
  (`ui/views/workflows/calibration.py`): a `QThread` subclass
  (`_MarkerDetectionThread`) owns exactly one `suggest()` call, a duplicate
  request while one is active is rejected rather than queued, cancellation
  uses a `threading.Event`-backed `MarkerSuggestionCancellation` token
  checked at multiple stages inside `OpenCvRedMarkerFrameDetector.scan()`
  (before HSV conversion, after masking, before contour extraction), and
  widget teardown (`closeEvent`) always cancels and bound-joins any active
  thread rather than orphaning it.
- **Continuity uses whatever tip is currently authoritative.** Rather than
  requiring an explicit `note_confirmed_tip()` call from the UI, each
  `suggest()` request passes the geometry workflow's *current* snapshot tip
  point as `previous_tip` — so temporal continuity scoring reflects whatever
  the operator has most recently confirmed (whether by manual placement or
  by accepting an earlier suggestion), without extra bookkeeping.
- **Results and thresholds are immutable, versioned dataclasses.**
  `HsvRedThresholds`, `RedBlob`, `RedMarkerScan`, `MarkerSuggestionCandidate`,
  and `MarkerSuggestionResult` are all frozen dataclasses; a candidate's
  `bounding_box`/`tip_point` and the mask preview referenced by a result are
  never mutated in place, so a caller holding a reference to an old result
  cannot observe it silently changing underneath it.
- **Persistence never fabricates provenance.** `tip_provenance`/
  `tip_selection_confidence`/`tip_selection_reasons`/
  `marker_suggestion_settings` are included in `as_document()`'s payload only
  when present, and `load_document()` type-checks and defaults safely
  (`None`/`()`) rather than inventing plausible-looking values for older or
  malformed documents. `infrastructure/artifact_store.py`'s
  `_validate_geometry` already tolerates unknown extra payload keys, so no
  change to that file (or to the `GEOMETRY` artifact schema) was required.
- **Extra `cv2` isolation is enforced automatically.**
  `tests/test_import_boundaries.py` now also forbids `cv2` in `domain`/
  `application`, for free, alongside the existing Qt/pyqtgraph bans — this
  extends the existing single-parametrized test rather than adding a new one.

## Test plan

- **Application, fake-detector policy
  (`tests/application/test_marker_suggestion.py`):** `HsvRedThresholds`
  validation (overlapping hue bands, out-of-range fields, dict round-trip,
  unknown-key tolerance), all three `MarkerSuggestionState` values and their
  dataclass invariants, min-area/min-circularity/base-exclusion-radius
  filtering, ambiguity-margin configurability, `max_candidates` capping,
  distance-from-base scoring, temporal continuity (implicit and explicit
  `previous_tip`, clearing via `note_confirmed_tip(None)`), threshold
  reconfiguration, cancellation at both the pre-scan and post-scan
  checkpoints (including via the real `MarkerSuggestionCancellation` token),
  sequence/staleness guarding via `is_current()`, negative `frame_index`
  rejection, and frozen-dataclass immutability.
- **Infrastructure, real pixels
  (`tests/infrastructure/test_red_marker_detector.py`):** exercises the real
  `tests/fixtures/video/synthetic-marker-suggestions.avi` fixture for
  frame-zero processing, dual-hue wraparound (and a negative check that a
  single-band configuration misses it), decoy rejection, a no-marker frame,
  ROI restriction (including mask-preview zeroing outside the ROI), the
  below-default-threshold small marker, the two ambiguous/temporal-continuity
  frame pairs, missing-frame rejection, cancellation, and morphology/area
  reconfiguration.
- **End-to-end pipeline
  (`tests/application/test_marker_suggestion_pipeline.py`):** wires the real
  `OpenCvVideoFileReader` + real `OpenCvRedMarkerFrameDetector` +
  `MarkerSuggestionWorkflow` together and proves every required regression
  scenario end-to-end: frame-zero processing, dual-hue wrap, decoy exclusion,
  no-marker `NO_DETECTION`, ROI restriction, ambiguity, temporal continuity
  across the frame-6/frame-7 pair, threshold-change revealing the frame-8
  small marker, cancellation against the real detector, and manual
  fallback/correction after accepting a suggestion (including the persisted
  `as_document()` payload).
- **UI (`tests/ui/test_marker_suggestion.py`):** hardware/OpenCV-free, using
  `FakeVideoFrameSource`/`FakeRedMarkerFrameDetector` and a small blocking
  detector double for the threading tests — default/no-video states, ranked
  candidate population and mask preview, explicit `NO_DETECTION`/`AMBIGUOUS`
  rendering, accept-then-manually-correct provenance transition, rerunning
  detection after scrubbing to a new frame (with the staleness guard
  disabling acceptance in between), threshold reconfiguration (including a
  rejected invalid combination), the bounded-cancellable background worker
  (GUI-thread responsiveness proof, duplicate-request rejection, cancellation,
  cleanup), and bounded thread cleanup on widget close.
- **Page wiring:** `tests/ui/test_workflow_pages.py`'s existing page-factory
  coverage continues to pass unmodified, proving the embedded
  `MarkerSuggestionView` does not disturb the existing demo presenter flow or
  page contract.

Run `uv run pytest tests/application/test_marker_suggestion.py
tests/application/test_marker_suggestion_pipeline.py
tests/application/test_video_geometry_workflow.py
tests/infrastructure/test_red_marker_detector.py
tests/ui/test_marker_suggestion.py tests/ui/test_video_geometry.py
tests/ui/test_workflow_pages.py tests/test_import_boundaries.py`, then the
full `uv run pytest` suite.
