# UI prototype operator UX evaluation

**Date:** 2026-07-11
**Related todo:** `qa-ui-ux-evaluation`
**Decision status:** Completed operator-task input to the ADR 0005 selection.
[`ADR 0005`](../../architecture/0005-ui-shell-evaluation.md) subsequently
selected Instrument Console after synthesizing this with the
engineering/maintainability review in
[`technical-evaluation.md`](technical-evaluation.md).

## Scope and evidence

This review compares `InstrumentConsoleWindow` (`ui/shells/instrument_console.py`)
and `ExperimentStudioWindow` (`ui/shells/experiment_studio.py`) against
[`0005-ui-shell-evaluation.md`](../../architecture/0005-ui-shell-evaluation.md),
their prototype notes
([`instrument-console.md`](instrument-console.md),
[`experiment-studio.md`](experiment-studio.md)), the shared workflow-page
contract ([`shared-workflow-pages.md`](../../architecture/shared-workflow-pages.md)),
both reference screenshots, and their dedicated test suites
(`tests/ui/test_instrument_console.py`, `tests/ui/test_experiment_studio.py`,
`tests/ui/test_workflow_pages.py`). No standalone "Unified SoftActuatorTesting
UI Implementation Plan" file exists in the repository; every document above
references it only as a title, so ADR 0005 is treated as the authoritative,
concrete statement of the plan's shell-selection criteria for this review.

Evidence gathering:

- Read both shell modules in full and the shared `ui/views/pages.py` /
  `ui/views/registry.py` contract they render.
- Ran the existing targeted suite headlessly:
  `QT_QPA_PLATFORM=offscreen uv run pytest tests/ui/test_instrument_console.py
  tests/ui/test_experiment_studio.py tests/ui/test_workflow_pages.py -q` —
  **96 passed**, 2.10 s. Neither shell nor `ui/views` was modified.
- Exercised both shells offscreen with ad hoc scratch probes (created and
  deleted within this review; no repository file was left behind) to observe
  behavior the existing tests do not assert: run-state visibility after
  navigating away from Live Run, Global Stop rendering/contrast in the idle
  vs. active state, keyboard-focus handling on navigation, and sizing at the
  minimum window size and at 1920×1080.
- Computed WCAG contrast ratios from the reference PNGs and from token values
  in `ui/themes/tokens.py` for the two Global Stop treatments.

## Criterion-by-criterion scores

Scores are 1 (fails the criterion / production risk) to 5 (meets the
criterion well), scored independently per shell against the same shared page
content.

| Criterion | Console | Studio | Winner |
| --- | ---: | ---: | --- |
| Primary-task navigation steps (expert / occasional) | 4 | 4 | Split by persona |
| Visibility of run/connection/file/fault state | 5 | 2 | **Console** |
| Global Stop discoverability | 5 | 2 | **Console** |
| Information hierarchy | 3 | 4 | Studio |
| Accessibility (keyboard, focus, labels, non-color state) | 4 | 3 | Console |
| Progressive disclosure | 2 | 5 | Studio |
| 1280×720 / high-DPI behavior | 2 | 2 | Tie (both unverified) |
| Readiness / error prevention | 3 | 4 | Studio |
| Workflow coherence as one app | 4 | 4 | Tie |

## Evidence and detail per criterion

### 1. Primary-task navigation steps — expert vs. occasional users

- **Expert:** Console reaches any of the 8 pages in one action via
  `Ctrl+1`…`Ctrl+8` (`instrument_console.py:196-197`, asserted by
  `test_navigation_toolbar_actions_have_keyboard_shortcuts`) and exposes a
  single "Run full simulated workflow" command (`Ctrl+Shift+W`) that walks
  workspace → analysis unattended. Studio has no equivalent single-shortcut
  jump table; each stage button requires a mouse/Tab-driven click and no
  keyboard accelerators are defined for stage buttons.
- **Occasional:** Studio's numbered sidebar ("2. Connect" … "7. Analyze"),
  persistent "Experiment summary" card, and single "Complete `Stage` →
  `Next`" primary action give an explicit next-step at all times
  (`experiment_studio.py:393-404`). Console has no equivalent "what's next"
  prompt outside the shared Experiment Setup/Readiness page itself; an
  occasional user must infer next steps from the top status dots.
- Both shells let an operator jump directly to Live Run without completing
  prerequisite stages (verified: `studio.navigate_to("live-run")` succeeds
  immediately after cold boot, and Console's nav rail is never gated) —
  neither shell prevents out-of-order navigation, only the shared
  `LiveRunPage.start_run` button, so this criterion nets to a persona split
  rather than a clear winner.

### 2. Visibility of run/connection/file/fault state — **Console wins clearly**

- Console's top status strip shows five persistent, always-visible indicators
  (Connection, Calibration, Camera, Storage, Run) plus a File/Context dock
  (workspace, calibration, geometry, analysis-source, run-state text) on
  **every** page (`_refresh_status_strip`, `_refresh_file_context`). Verified:
  after starting a run on Live Run and navigating to Analysis, `run_status`
  remains visible and correctly shows `"Run status: Info"` (running).
- Studio's only run-state indicator (`cockpit_status`) is inside
  `run_cockpit`, which is hidden whenever the active stage is not Live Run
  (`self.run_cockpit.setVisible(is_run)`, `experiment_studio.py:337-338`).
  Verified by direct probe: after starting a run and navigating to Analysis,
  `studio.run_cockpit.isVisible()` is `False` and `cockpit_status` is hidden
  along with it — the sidebar's "Run readiness" indicator still reads
  `"Warning"` (a stage-completion signal, not a run-in-progress signal), so
  an operator who leaves the Run stage during an active run has **no
  persistent visual cue that a run is active** except the low-contrast Stop
  label described below.
- Studio also has no equivalent to Console's File/Context dock; per-page
  context (workspace, calibration, geometry) is visible only while viewing
  that page's own stage.

### 3. Global Stop discoverability — **Console wins clearly**

- Console's Stop is a dedicated bordered button, heading-weight font, a
  non-color glyph label (`"⏹ STOP"`), and an explicit error-red foreground
  fixed at the start of the always-visible top strip
  (`instrument_console.py:258-273`). Measured contrast of its enabled-state
  red (`#F26D6D`) against the dark strip background (`#1D2024`) is
  **≈5.6:1**, passing WCAG AA for normal text.
- Studio's Stop is a plain `QToolBar` `QAction` with no icon and no explicit
  color styling (`experiment_studio.py:115-130`); the reference screenshot
  renders it as small grey text with **no button chrome at all**. Measured
  contrast of its idle/disabled-state grey text against the toolbar
  background is **≈2.4:1** — well under WCAG AA's 4.5:1 (or even the 3:1
  large-text/UI-component floor). When a run is active the text darkens to
  near-black (~14:1, acceptable), but the control still has no persistent
  visual weight (no fill, border, or icon) to draw the eye compared with
  Console's always-obvious red button, and it is the visual style — not
  keyboard reachability — that is deficient: Studio's shortcut
  (`Ctrl+Shift+S`), accessible name (`"Stop active run"`), and description
  are all present and correct (`test_global_stop_is_visible_accessible_and_
  keyboard_reachable`).
- Both shells correctly disable Stop unless a run is active
  (`instrument_console.py:532-541`, `experiment_studio.py:426-427`), and both
  have keyboard shortcuts that work from any page — the gap is exclusively
  visual salience in the idle Studio treatment, which is the state an
  operator is in most of the time.

### 4. Information hierarchy — Studio slightly ahead

- Console simultaneously surfaces a left nav rail, a 5-indicator top strip
  plus scenario switch plus Stop, a central page, and up to 4 docks
  (Telemetry, Run Control, Event Log, File/Context) by default — appropriate
  density for a trained operator per ADR 0005's own framing, but a lot of
  concurrent visual information for a first look, and the dock arrangement is
  user-restorable so its layout is not guaranteed consistent session to
  session.
- Studio uses a clean three-zone layout (stage sidebar + summary card, stage
  context header, single content card) with one workflow at a time and no
  movable chrome, which is easier to parse at a glance and matches ADR
  0005's "generous spacing, card-based sections" design goal.
- This is a legitimate trade-off rather than a defect in either: Console's
  density is the intended expert value proposition; Studio's simplicity is
  the intended occasional-user value proposition.

### 5. Accessibility (keyboard, focus, labels, non-color state) — Console ahead

- Both shells give accessible names/descriptions to nav controls, Stop, and
  status indicators, and both use the shared `StatusIndicator` /
  `NotificationBanner` non-color-only (color + glyph + text) presentation
  from `ui/themes` and `ui/widgets` (shared code, so this part is identical).
- **Focus-on-navigate differs:** Console explicitly moves keyboard focus into
  the newly active page on every navigation
  (`self._pages[key].setFocus(Qt.FocusReason.OtherFocusReason)`,
  `instrument_console.py:467`), covered by
  `test_navigating_moves_focus_into_the_active_page`. Studio's `navigate_to`
  has no equivalent call (confirmed absent by inspection and by grep — no
  `setFocus` in `experiment_studio.py`), and no Studio test asserts focus
  after navigation. A keyboard-only user tabbing after selecting a Studio
  stage must tab back through the sidebar's stage list before reaching page
  content — Console does not have this friction.
- Both correctly implement the `Ctrl+Shift+S` Stop shortcut and label focus
  targets with `StrongFocus`; neither shell's tests validate full tab order
  or a real screen reader, which is a shared gap (see
  [`technical-evaluation.md`](technical-evaluation.md)).

### 6. Progressive disclosure — **Studio wins clearly**

- Studio implements an explicit, tested progressive-disclosure control:
  "Show/Hide advanced demo details" defaults to collapsed
  (`self.advanced_details.setVisible(False)`,
  `experiment_studio.py:260-280`), verified by
  `test_accessibility_and_advanced_disclosure_are_explicit`.
- Console has no equivalent layered-detail mechanism inside a page; its only
  disclosure control is showing/hiding an entire dock, which removes a whole
  panel rather than progressively revealing secondary detail, and by default
  all four docks and the full 5-indicator status strip are visible
  simultaneously with no default-collapsed state.

### 7. 1280×720 / high-DPI behavior — tie, both unverified beyond a single grab

- Both default to 1280×720 (`resize(1280, 720)` in both constructors) and
  both have a single deterministic screenshot smoke test that only asserts
  `width() == 1280`, `height() == 720`, and `devicePixelRatio() >= 1` — not
  rendering correctness, clipping, or DPI scaling. Confirmed by reading both
  `test_screenshot_grab_smoke_at_1280_by_720` tests; neither exercises
  1920×1080 or a >1.0 DPI scale, and offscreen `devicePixelRatio()` is always
  `1.0` in this environment, so the assertion cannot currently detect a
  high-DPI regression.
- At minimum size (Console 1024×640, Studio 960×600, both probed directly),
  neither shell crops critical controls, but Console's minimum width is
  tighter relative to its 8-icon nav rail plus 5-indicator strip plus Stop
  plus scenario switch, all sharing one row — this combination has less
  slack than Studio's single sidebar + single content card, though neither
  was tested at a real 1920×1080/DPI matrix as ADR 0005 requires. This
  matches the responsive-readiness gap already flagged in
  [`technical-evaluation.md`](technical-evaluation.md).

### 8. Readiness / error prevention — Studio slightly ahead

- Both shells share the exact same `LiveRunPage` gating (Start is disabled
  until "Enable demo readiness" is invoked) — identical behavior since it is
  shared-page code, not shell-specific.
- Studio additionally computes and always displays a plain-language readiness
  sentence in its persistent sidebar summary — e.g. `"Blocked ! — complete:
  Connect, Calibrate, Video, Prepare."` — updated on every navigation and
  stage completion (`_refresh_context`, `experiment_studio.py:406-423`).
  Console has no equivalent proactive "what's missing" sentence; an operator
  must cross-reference which of the five top-strip dots are non-success to
  infer the same information themselves.
- This readiness sentence is itself only a UI convenience, not a hard block:
  as noted under criterion 1, both shells still let an operator navigate
  directly to Live Run before completing prerequisites; only the shared
  Start button enforces the real gate.

### 9. Workflow coherence as one application — tie, with a caveat

- Both shells instantiate every `PAGE_REGISTRY` factory against the same
  `DemoEnvironment`, so the two shells render **the exact same eight
  workflow pages** with identical widgets and fake-service behavior
  (`registry.py:50-58`); no workflow that exists in one shell is missing from
  the other, and adopting either preserves full workflow coverage.
- However, as documented in
  [`technical-evaluation.md`](technical-evaluation.md), the two shells
  currently derive "is a run active" and "did Stop succeed cleanly" from
  different sources (Console reads `run_lifecycle.snapshot()`; Studio reads
  `LiveRunPage.scenario`, and its own Global Stop finalizes a **clean**
  completion where Console's finalizes an **aborted** one). This means that,
  at the state level, the two prototypes do not yet agree on what "the app"
  is doing during an emergency stop — a correctness caveat this UX review
  surfaces because it directly affects the fault-state and Stop-outcome
  visibility an operator would rely on, even though it does not reduce
  either shell's score for having complete workflow content.

## Risks

- **Studio's run-state blind spot is a safety-relevant gap**, not just a
  cosmetic one: an operator monitoring hardware from any page other than
  Live Run currently has no reliable persistent cue that a run is active if
  Studio ships unchanged.
- **Studio's Global Stop is under-styled for its criticality.** A single
  global emergency control for a physical test rig should not depend on
  default toolbar text rendering for its visual salience.
- **Console's density is a real cognitive-load risk for occasional users**,
  exactly as ADR 0005 anticipated; it has no persistent "what's next"
  guidance comparable to Studio's summary card.
- **Neither shell's DPI/1920×1080 behavior is verified**, so a selection
  decision today is made without the full evidence ADR 0005 asks for; this is
  a shared gap, not a differentiator.
- **Cross-shell state-model disagreement** (see criterion 9) means today's
  prototypes cannot be blended piecemeal without first unifying run-state and
  Stop-outcome derivation, regardless of which navigation model is selected.

## Recommended shell (operator-UX perspective)

**Instrument Console**, conditional on closing its occasional-user guidance
gap, is the stronger starting point from a pure operator-task-safety
perspective. ADR 0005 names "visibility of run, connection, file, and fault
state" and Global Stop as explicit, first-class selection criteria, and on
both of those Console is verifiably and substantially safer today: a fault or
active run stays visible from every page, and Stop is unmissable. Studio's
occasional-user strengths (guided flow, persistent readiness sentence,
progressive disclosure) are real and worth keeping, but they are gaps that
are comparatively cheap to add to Console (e.g., a persistent "what's next"
hint sourced from the same readiness data Studio already computes, and a
default-collapsed detail panel), whereas Studio's run-visibility and Stop-
salience gaps require reworking chrome that is currently tied to its
single-stage-at-a-time layout premise.

This recommendation is **from the operator-UX evidence only**; the parallel
[`technical-evaluation.md`](technical-evaluation.md) conditionally recommends
Studio on engineering/maintainability grounds. The two evaluations weigh
different, legitimate concerns and are expected to disagree — `select-shell`
must synthesize both, plus any operator feedback ADR 0005 calls for, before
recording the outcome in a follow-up ADR.

## Concrete improvements required before production

Whichever shell is selected:

1. Make run/fault-state visibility persistent from every page, not only the
   Live Run/cockpit view. If Studio is selected, promote its run-state
   indicator out of the hidden `run_cockpit` container into the always-visible
   global toolbar.
2. Give Global Stop a fixed, high-contrast, button-chrome treatment
   regardless of shell — at minimum matching Console's ≈5.6:1 contrast — not
   plain toolbar text, in every scenario state (idle and active).
3. Add `setFocus` into the active page/stage content on every navigation in
   Experiment Studio, matching Console's tested behavior, so keyboard-only
   operators land in usable content instead of back at the stage list.
4. Add a persistent, low-effort "what's next" or readiness hint to
   Instrument Console (it can reuse the same readiness computation Studio
   already implements) so occasional users are not left to infer next steps
   from status dots alone.
5. Resolve the cross-shell run-state/Stop-outcome disagreement identified in
   `technical-evaluation.md` before either shell's Stop/fault behavior is
   trusted in production; this UX review depends on that single source of
   truth to keep whichever visibility improvements above are made accurate.
6. Complete the ADR 0005-required 1280×720/1920×1080/high-DPI/Windows-Linux
   verification matrix (currently only a single 1280×720 offscreen grab per
   shell) before the `select-shell` gate closes.

## Completion and blockers

The requested operator-task comparison, scores, evidence (including two new
offscreen behavioral probes beyond the existing test suite), risks, and
required improvements are complete. Neither shell nor `ui/views` was
modified; the existing 96-test targeted suite
(`test_instrument_console.py`, `test_experiment_studio.py`,
`test_workflow_pages.py`) was run unchanged and passed. No execution blocker
was encountered. ADR 0005 records the subsequent selection and explicitly
records that representative operator feedback was not available rather than
treating this inspection as a usability study.
