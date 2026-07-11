# UI prototype technical evaluation

**Date:** 2026-07-11
**Related todo:** `qa-ui-technical-evaluation`
**Decision status:** Completed technical input to the ADR 0005 selection.
[`ADR 0005`](../../architecture/0005-ui-shell-evaluation.md) subsequently
selected Instrument Console after giving greater weight to safety-relevant
operator visibility and Global Stop.

## Scope and evidence

This review compares `InstrumentConsoleWindow` and `ExperimentStudioWindow`
against ADR 0001 and ADR 0005, including their dedicated tests and prototype
notes. It is an implementation inspection, not a usability study.

| Evidence | Result |
| --- | --- |
| ADR 0001 | The UI imports Qt while `domain/` and `application/` have no forbidden Qt/PyQtGraph imports (static scan). |
| Shared architecture | Both shells instantiate every `PAGE_REGISTRY` factory with the same `DemoEnvironment` and `FilePicker`; workflow pages and fake services are shared. |
| Size/complexity | Console: 702 LOC, 34 functions, largest method 80 LOC. Studio: 448 LOC, 19 functions, largest method 108 LOC. |
| Targeted validation | `QT_QPA_PLATFORM=offscreen uv run pytest tests/ui/test_instrument_console.py tests/ui/test_experiment_studio.py tests/test_import_boundaries.py -q` — **33 passed** in 2.08 s. |
| State probe | Setting Studio to `PageScenario.RUNNING` left lifecycle `DISCONNECTED`, enabled the global Stop action, and left the Live Run Stop button disabled. |
| Test/static gaps | Shell screenshots are smoke grabs at 1280×720 only. They assert dimensions and `devicePixelRatio() >= 1`, not rendering correctness, 1920×1080, DPI scale, Windows/Linux output, or resize/focus traversal. |

The sharing is real and appropriately keeps shell-specific navigation/chrome
inside `ui/shells`, satisfying ADR 0005's immediate prototype boundary.
However, the shared seam is still demo-specific: `PageFactory`, pages, and
both shells are typed around `DemoEnvironment`, and page event handlers call
`environment.services` directly. `SnapshotStore` and `CommandDispatcher`
exist in `ui.presenters` but neither shell nor the workflow pages use them.
This is compatible with an evaluation prototype, but it is not yet the
presenter/application seam required for economical production integration.

## Scores

Scores are 1 (unacceptable production risk) to 5 (production-ready for this
criterion); they assess implementation engineering only.

| Criterion | Console | Studio | Evidence and assessment |
| --- | ---: | ---: | --- |
| Shared code / ADR 0001 boundary | 3 | 3 | Shared registry, pages, widgets, fake services, and Qt-free application/domain boundary are positive; demo-specific dependencies remain in the shared page contract. |
| Shell complexity and maintainability | 2 | 3 | The Console owns docks, status projection, context projection, run controls, layout capture, walkthrough, menus, and navigation in one 702-LOC module. Studio is smaller but its workspace builder is 108 LOC and owns a second workflow-state model. |
| Replacing demos with presenters/services | 1 | 2 | Direct calls to fake services and page widgets make both expensive to connect to real asynchronous application presenters. Studio has fewer shell-only projections to replace. |
| State synchronization | 2 | 1 | Console derives several shell fields from page scenario events plus direct service reads. Studio derives readiness, Stop enablement, and progress from independent scenario/completion state rather than lifecycle state. |
| Global Stop correctness | 2 | 1 | Console aborts the lifecycle directly, while Studio forwards to the ordinary clean-completion page stop. Neither has one authoritative, idempotent stop command or state subscription. |
| Layout restoration / persistence readiness | 2 | 4 | Console demonstrates dock restoration but only stores unversioned bytes in memory and reports restoration without checking success. Studio deliberately has no mutable dock layout to persist. |
| Accessibility implementation | 4 | 4 | Both set meaningful accessible names/descriptions, strong-focus navigation, text/glyph state labels, and a keyboard Stop shortcut. Coverage does not validate a screen reader, complete tab order, contrast, or target sizes at scale. |
| Responsive and high-DPI readiness | 2 | 2 | Both default to 1280×720; Console minimum is 1024×640 and dense docks/toolbar compete for width. Studio has a fixed 230–300 px sidebar and 960×600 minimum. No required viewport/DPI matrix is tested. |
| Automated-test stability | 3 | 3 | Deterministic fakes and offscreen targeted tests are good. Tests rely heavily on private widget fields, fixed `waitExposed`/short waits, and smoke screenshots rather than public UI contracts or visual assertions. |
| Hardware/persistence integration cost | 1 | 2 | Console additionally couples shell code to fake telemetry calibration, fake workspace selection, direct lifecycle transitions, and Qt layout bytes. Studio avoids docks, but still calls page methods and uses `PageScenario` as workflow state. |

## High-confidence issues

1. **There is no single source of truth for run state.** The Studio enables
   `stop_action` from `LiveRunPage.scenario` (`experiment_studio.py:425-427`),
   whereas the Console reads `run_lifecycle.snapshot()` for its Stop
   (`instrument_console.py:526-535`). `PageScenario` is explicitly only a
   presentation flag (`views/pages.py:111-119`), so a scenario change can
   advertise a running/non-running state independently of the lifecycle.
   Studio also records stage completion locally without running the associated
   page use case (`experiment_studio.py:342-355`). A real presenter update,
   reconnect, fault, or resumed run will therefore leave shell indicators,
   readiness, and action enablement stale or contradictory.

2. **“Global Stop” has inconsistent, unsafe production semantics.** Console
   directly calls `lifecycle.stop()` and `finalize(ABORTED)` then manually
   changes Live Run widgets (`instrument_console.py:581-605`). Studio calls
   `LiveRunPage.stop_run()` (`experiment_studio.py:368-377`), which finalizes
   `CLEAN` (`views/pages.py:454-461`). The dedicated tests encode this
   difference: Console expects `ABORTED`; Studio expects “completed cleanly.”
   A global emergency control must dispatch one authoritative, idempotent
   abort/stop command and render the resulting lifecycle snapshot everywhere.

3. **The current shared boundary is coupled to demo implementation details.**
   `PageFactory` takes `DemoEnvironment` (`views/registry.py:24`), and pages
   directly invoke fake serial, camera, lifecycle, analysis, and artifact
   methods. Console further reads widget labels as data for its File / Context
   dock (`instrument_console.py:552-562`) and contains an
   `isinstance(FakeFilePicker)` branch (`:653`). Replacing demo data with real
   application presenters would require changing shared views and both shells,
   contrary to the intended replaceable adapter direction in ADR 0001.

4. **Console layout restoration is only a demo proof, not safe persistence.**
   `LayoutSnapshot` is raw geometry/state bytes with no schema/version,
   platform/screen validation, corruption handling, or fallback
   (`instrument_console.py:83-89, 610-632`). `restore_demo_layout()` ignores
   the boolean result, then reports success. `apply_layout()` can restore
   geometry before a state restore failure. This is acceptable for in-memory
   evaluation only; persisted production layouts can produce partial or
   off-screen restoration.

5. **ADR 0005's responsive/reference evidence is incomplete.** ADR 0005
   requires 1280×720, 1920×1080, high-DPI, and deterministic Windows/Linux
   reference screenshots. The two dedicated test modules contain only one
   1280×720 grab each, with no comparison baseline; their prototype notes
   explicitly call the PNGs review artifacts rather than pixel baselines.
   This leaves dense Console docking, Studio sidebar width, text scaling, and
   accessibility focus at DPI unproven.

## Recommended production shell

**Select Experiment Studio, conditional on the improvements below and the
separate ADR 0005 operator evaluation.** It has 36% fewer lines than Console,
does not make user-restorable docking a prerequisite, and its guided,
revisitable stage model has a lower migration surface for a first production
presenter-backed shell. Retain reusable Console ideas—not its second
navigation model—as optional components: persistent run/status presentation,
event history, telemetry, and an expert diagnostics panel.

The Console should not be selected solely because it currently exposes more
data: its dock/persistence and duplicate shell projections materially increase
production state and test obligations. Studio is **not** suitable to ship
unchanged; its current local stage and Stop state make the conditional
recommendation essential.

## Required pre-production improvements

1. Define presenter/view-model protocols owned by `application` for workspace,
   connection, calibration, readiness, run, analysis, and settings snapshots
   and commands. Make shells/pages consume those contracts, not
   `DemoEnvironment`; keep demo services as adapters that implement the same
   protocols.
2. Introduce one application run controller/presenter snapshot subscription.
   Derive every Start/Stop enablement, status strip/cockpit state, and
   readiness display from it. Route Global Stop through one idempotent,
   safety-reviewed abort command; specify clean-stop versus emergency-abort
   behavior and test it from every navigation location.
3. Replace Studio's `_completed_stages` and `PageScenario` gating with
   presenter-provided workflow/readiness state. “Complete and continue” must
   dispatch the real stage command or be removed from production.
4. If Console features are retained, persist layout through a dedicated
   settings adapter with a versioned payload, validation/size sanitization,
   transactional restore, visible failure feedback, and a default-layout
   fallback. Never let layout restoration touch device or run services.
5. Add deterministic UI contract tests for presenter-driven state changes,
   disconnect/fault/reconnect, duplicate Stop, Stop while stopping, and stale
   callback disposal. Prefer public object names/accessibility contracts over
   private fields and timing-sensitive exposure waits.
6. Validate both candidate layouts at 1280×720 and 1920×1080 at 100%, 150%,
   and 200% scale on Windows and Linux. Check no clipped critical controls,
   keyboard tab order/focus restoration, contrast, target dimensions, and
   screen-reader names/descriptions; maintain approved reference images or
   layout assertions.

## Completion and blockers

The requested engineering comparison, targeted tests, static boundary scan,
scores, recommendation, and required improvements are complete. No execution
blocker was encountered. ADR 0005 records the subsequent selection; the
pre-production work above remains mandatory and the current demo state/Stop
implementations remain unapproved for device control.
