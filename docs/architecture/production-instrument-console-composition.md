# Production Instrument Console composition

**Date:** 2026-07-13  
**Related todo:** `analysis-production-integration`

## Analysis production-integration test plan (2026-07-13)

Replace the handoff-only analysis placeholder with the existing real
`AnalysisPage(production_mode=True)`. Hardware-free composition coverage will
prove that construction uses the OpenCV reader/detector and the already-owned
camera presenter without opening hardware; workspace open/close moves one
shared artifact-store capability across run, calibration, geometry, and
analysis; finalized and unavailable run results reach the page without
starting analysis; and closing the embedded page bounds its workers/timers and
does not close the shared presenter more than once. Package/import smoke will
continue to prove imports and startup are hardware-disconnected.

## Decision

Production mode now composes the ADR 0005-selected `InstrumentConsoleWindow`,
not a separate three-tab shell. Its eight destinations are Workspace,
Connections, Calibration, Geometry / Marker Setup, Readiness, Live Run,
Analysis, and Settings / Help.

The composition owns real but disconnected `SerialController`,
`SerialCalibrationSampleSource`, `CalibrationWorkflowService`,
FFmpeg-backed camera services when FFmpeg tools are discoverable, the native
workspace controller/store, and the OpenCV-backed geometry workflow. It does
not create a demo environment, demo presenter, fake sample source, or
prototype scenario state.

Workspace selection is the only source of the run artifact store and camera
preview output directory. With no active workspace, run readiness is blocked;
there is no home-directory or current-directory fallback. Workspace changes
rebind calibration and geometry artifact stores as well as run storage.
While a run is starting, running, or stopping, workspace root-changing
commands (create, open, close, storage-root selection, and individual-file
opening) are rejected before the workspace snapshot changes. The existing
workspace page renders the rejection as an operator-visible status/issue;
after finalization, normal workspace changes resume.

Calibration readiness reads the real calibration workflow fit. Geometry
readiness reads the real complete base/tip/ROI geometry. Analysis is the real
`AnalysisPage(production_mode=True)`, built with one authoritative
`AnalysisPipeline(OpenCvVideoFileReader, OpenCvRedMarkerFrameDetector)`. Its
recorded-file progress/cancellation, review/correction, versioned export, and
explicit authoritative/provisional labels are therefore available in the
production Console, not only in a prototype.

The analysis page receives the exact `RunFinalizationResult` handoff. A
finalized `video_path` enables the operator's explicit "Use as recorded-file
source" action; it only selects the path and never auto-starts analysis or
opens camera hardware. A missing path remains visibly unavailable. For
provisional live analysis it consumes the same existing
`CameraPanelPresenter`/bridge as Connections, never creates a second camera
owner, and never treats live results as exportable.

One `ArtifactFileStore` capability is created only after a workspace opens and
is synchronously rebound across run, calibration, geometry, and analysis.
Analysis output is workspace-only in this composition: its generic output
picker is disabled, so no home/current-directory fallback can make analysis
runnable. The page creates its `AnalysisArtifactExporter` from that bound
store when exporting.

Camera and serial construction remains disconnected. The production camera
panel does not discover devices during construction; Refresh and Connect remain
operator actions. Run ordering and cleanup remain owned by `RunController`.

## Test plan

Hardware-free composition tests must verify:

1. construction performs no serial open, camera discovery, or capture;
2. the Console has every registered workflow destination and no demo controls;
3. native workspace create/open state supplies the sole artifact root;
4. fitted calibration and complete geometry flow into run readiness;
5. fake camera proof precedes fake serial `CMD:START`;
6. finalized video and explicit no-video completion states reach analysis;
7. accepting a finalized source selects it without starting analysis or camera
   hardware;
8. the analysis live view reuses the existing camera presenter and its embedded
   timers/bridge stop during bounded close alongside the presenter exactly once;
9. closing routes through the bounded idempotent run cleanup path.
8. production calibration fitting never dispatches a demo presenter command,
   and active-run workspace create/open/close commands preserve the active
   workspace until finalization.

Targeted UI/composition tests run before the full default `uv run pytest`
suite. Physical devices and external FFmpeg remain outside the default suite.

## Verification (2026-07-13)

`tests/ui/test_production_composition.py`,
`tests/ui/test_analysis_review.py`, package/import smoke, and desktop
packaging dry-runs passed. The full default suite passed **535 tests with one
hardware/external test deselected**; `uv build` produced both source and wheel
distributions.
