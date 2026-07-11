# ADR 0005: Two-shell visual evaluation and selection criteria

**Status:** Accepted — Instrument Console selected as the normal shell,
conditional on the mandatory pre-production work below. This document first
fixed the build-and-compare method before either prototype existed, then
records the evidence-based outcome dated 2026-07-11.
**Date:** 2026-07-11.
**Related todo:** `architecture-record` (Phase 0) in the approved Unified
SoftActuatorTesting UI Implementation Plan. Building the prototypes is
tracked separately under Phase 2 (`prototype-console`, `prototype-studio`)
and the Phase 3 selection gate (`select-shell` in the tracked task graph).

## Context

The legacy software is four disconnected scripts with no shared navigation
model. Rather than guessing at one production information architecture, the
plan requires building two full functional prototypes against the same
mocked domain/application layer, then selecting one for production so the
rejected architecture does not become a second application to maintain.

## Decision

### Build two comparable prototypes

Both variants:

- launch from the same executable in demo mode;
- use the same domain models, view-model contracts, commands, fake services,
  and deterministic sample data (see
  [`0001-ui-framework-and-qt-boundaries.md`](0001-ui-framework-and-qt-boundaries.md)
  for why this sharing is possible — only `ui/shells/*` and `ui/views/*`
  differ);
- include every planned page (shell, home/workspace, connections and
  diagnostics, calibration, video geometry and marker setup, experiment
  setup and readiness, live run, analysis, settings) with representative
  populated, empty, loading, ready, running, completed, and fault states;
- support a complete simulated task from workspace creation through
  analysis;
- have deterministic Windows and Linux reference screenshots;
- meet keyboard, focus, contrast, target-size, and non-color-only state
  rules.

### Variant A — Instrument Console

Dark, dense, dockable shell for trained operators: compact spacing, tabular
numeric readouts, a central video/plot area with dockable connection,
run-control, telemetry, log, and file panels, and user-restorable dock
layouts. Global Stop and run state stay fixed even if docks move or
collapse. Risks to evaluate: cognitive load for occasional users,
small-screen behavior, dock-layout discoverability, and accessibility of
dense controls.

### Variant B — Experiment Studio

Light, guided shell for occasional/reproducible use: generous spacing,
card-based sections, left navigation through Connect, Calibrate, Configure
Video, Prepare Run, Run, and Analyze, with a persistent experiment summary
and readiness status. Progressive disclosure hides device/detector detail;
completed stages remain revisitable (not a modal wizard); the active Run
stage switches to a focused cockpit layout with a persistent Stop. Risks to
evaluate: extra navigation for expert users, hiding troubleshooting detail,
and whether the stage model fits reruns/partial workflows.

### Selection criteria

The selection review compares, for both variants:

- completion and error rates for the primary operator tasks;
- time and navigation steps for expert and occasional-user scenarios;
- visibility of run, connection, file, and fault state;
- behavior at 1280x720, 1920x1080, and high-DPI scaling;
- keyboard-only use and screen-reader labels;
- amount of duplicated presentation code;
- maintainability and automated-test stability;
- feedback from at least one representative operator, if available.

## Outcome — 2026-07-11

### Evidence and limitation

The selection synthesizes both implementations, dedicated tests, prototype
notes and 1280×720 reference images, the operator-task
[`ux-evaluation.md`](../ui/prototypes/ux-evaluation.md), and the engineering
[`technical-evaluation.md`](../ui/prototypes/technical-evaluation.md). The
weighted score also has a
[`visual companion`](../ui/prototypes/shell-selection-decision.html). The
repository has no standalone copy of the approved *Unified
SoftActuatorTesting UI Implementation Plan*; its tracked task graph and this
ADR's pre-committed criteria are the concrete plan evidence available here.

No representative human operator feedback was available. The operator
assessment is therefore an expert inspection of code, rendered prototypes,
and automated behavior—not a usability study. Scores must not be read as
measured completion/error rates, and the missing operator study remains a
pre-production evidence gap.

### Weighted decision

Scores use 1 (unacceptable) through 5 (strong) and intentionally give 45% of
the decision to the two safety-relevant criteria. The weighted result is the
sum of `weight × score`, divided by 100.

| Criterion | Weight | Instrument Console | Experiment Studio | Decision rationale |
| --- | ---: | ---: | ---: | --- |
| Persistent run, connection, file, and fault visibility | 25% | 5 | 2 | Console keeps five statuses and context visible across pages; Studio hides active-run state outside its Run stage. |
| Global Stop correctness and salience | 20% | 3 | 1 | Console provides a fixed, high-contrast control and demonstrates abort semantics. Neither shell has the required authoritative presenter command; Studio also reports Global Stop as a clean completion. |
| Maintainability and presenter migration cost | 15% | 2 | 3 | Studio is smaller and avoids dock persistence. Both depend directly on `DemoEnvironment`; Console has more shell projections to replace. |
| Occasional-user guidance and error prevention | 10% | 2 | 5 | Studio's readiness sentence, ordered revisitable stages, and progressive disclosure are materially clearer. |
| Accessibility implementation | 10% | 4 | 3 | Both label controls and avoid color-only status; Console additionally moves focus into selected page content. Real screen-reader and complete tab-order evidence is absent. |
| Responsive/high-DPI evidence | 5% | 2 | 2 | Both have only 1280×720 offscreen smoke grabs; required platform, 1920×1080, and scaled evidence is absent. |
| Shared-page workflow coherence | 10% | 4 | 4 | Both host the same registry and all scientific workflows as shared pages; neither gets credit for duplicated workflow content. |
| Expert task efficiency | 5% | 5 | 3 | Console offers one-action page shortcuts, persistent diagnostics, and a full demo traversal; Studio requires more staged navigation. |
| **Weighted result** | **100%** | **3.50 / 5** | **2.60 / 5** | **Select Instrument Console.** |

**Instrument Console is selected as the normal production-shell direction.**
Its decisive advantage is not its dark theme or docking: it is persistent
operator awareness and an unmistakable Global Stop across every shared
workflow page. Those properties are harder and riskier to retrofit than
Studio's guidance patterns. This priority outweighs Studio's genuine
maintainability and occasional-user advantages.

This is an information-architecture choice, not production acceptance. The
current Console still uses demo-specific state, directly calls fake services,
and manually projects Global Stop into page widgets. Its existing Stop
behavior and state strip are evidence for shell selection only; they are not
approved hardware-control semantics.

### Retained ideas from Experiment Studio

The rejected navigation shell remains useful as a development comparison, but
will not be maintained as a second normal application. Retain these ideas
inside the selected Console and shared pages:

- the plain-language readiness/"what is missing and what comes next" summary;
- progressive disclosure and spacious card groupings for infrequent users;
- revisitable stage progress as optional guidance, without replacing the
  Console's single shared-page navigation model;
- the light theme tokens as a supported visual theme, not a separate shell;
- a stable, safe default layout and explicit reset path so docking knowledge
  is not a prerequisite for use.

Do not copy Studio's local `_completed_stages`, `PageScenario` run gating, or
ordinary clean-stop forwarding into production.

### Mandatory work before production or device integration

1. **Complete next-phase task `presenter-state-integration`.** Define
   application-owned presenter/view-model snapshots and commands for every
   shared workflow page. Replace `DemoEnvironment`, widget-label reads, local
   completion sets, and `PageScenario` as sources of operational truth. All
   pages and the selected shell must subscribe to the same lifecycle,
   readiness, connection, file, and fault state.
2. Route Global Stop through one safety-reviewed, idempotent application abort
   command with specified behavior for starting, running, stopping,
   disconnected, duplicate, timeout, and fault cases. Render its acknowledged
   result everywhere. Keep ordinary clean completion a separate command.
3. Keep run/fault status and the high-contrast Global Stop fixed,
   non-dockable, keyboard reachable, and visible from every page. Validate
   disabled and enabled contrast, target size, text/glyph labeling, and stale
   callback disposal.
4. Add Console guidance from presenter readiness data: explicit missing
   prerequisites, recommended next action, and progressively disclosed
   diagnostics. Guidance must never create a second workflow state machine.
5. Make any persisted dock layout versioned, validated, transactional, and
   recoverable to an on-screen safe default. Layout restore must never contact
   a device or move/hide safety chrome.
6. Validate the selected shell at 1280×720 and 1920×1080 at 100%, 150%, and
   200% on Windows and Linux, including clipping, Stop visibility, keyboard
   traversal/focus, screen-reader names/descriptions, contrast, and target
   sizes. Conduct a representative operator study when access is available
   and record completion/error findings without retroactively changing these
   observed prototype facts.
7. Replace private-widget/timing-heavy prototype checks with public
   presenter-driven UI contracts for disconnect, reconnect, fault, stale
   update, duplicate Stop, and Stop-while-stopping behavior.

### Mandatory presenter gate outcome — 2026-07-11

Items 1–4 and the presenter-driven behavioral portion of item 7 are implemented
by `application/presentation.py`, the shared page/Console adapters, and the
tests documented in
[`presenter-state-contracts.md`](presenter-state-contracts.md). Layout
persistence hardening, the platform/DPI matrix, and representative operator
study remain separate production-readiness work; this gate does not claim
those later obligations are complete.

### Selection implementation test plan

- Assert that the normal GUI factory creates Instrument Console.
- Assert that Experiment Studio can be requested only through the explicit
  development/prototype CLI option.
- For both launch choices, assert that serial and camera fakes remain
  disconnected and the run lifecycle remains disconnected on construction.
- Assert that `--no-gui` returns before importing or launching the UI.
- Run the targeted bootstrap/shell tests, then the complete hardware-excluded
  suite.

## Consequences

- The normal GUI entry point opens Instrument Console over deterministic demo
  services; this does not begin real workflow or device integration.
- Experiment Studio remains available only through an explicit
  prototype/development CLI option for comparison. It is not a second normal
  application and receives no independent workflow implementation.
- Any UI code written before the selection review must stay within
  `ui/shells/instrument_console.py` / `ui/shells/experiment_studio.py` and
  their dedicated views, not leak shell-specific assumptions into
  `application/` or `domain/`.
- The production direction is blocked on `presenter-state-integration` and the
  safety, accessibility, responsive, and representative-operator evidence
  listed above.
