# Manual platform / DPI / screen-reader evidence matrix

**Date:** 2026-07-14
**Related todo:** `shell-workspace-remediation`
**Scope:** Instrument Console production shell (`ui/shells/instrument_console.py`)
only. This document tracks ADR 0005's mandatory-work item 6 — the
platform/DPI/screen-reader evidence that **cannot** be produced by an
offscreen/headless automated test run and requires a human operator on real
hardware/OS/assistive-technology combinations.

## Why this document exists

`docs/architecture/quality-ui-accessibility.md` and the automated suite in
`tests/ui/test_instrument_console.py` already give strong, repeatable,
CI-enforced evidence for everything Qt's `offscreen` platform plugin can
prove without a real display, window manager, or assistive technology:
keyboard focus traversal order, keyboard shortcut activation, accessible
names/descriptions, and rendered-pixmap scaling at 100/150/200%
(`QT_SCALE_FACTOR`). See "What automated tests already cover" below for the
exact tests.

What the `offscreen` platform **cannot** prove, because it never creates a
real window, never talks to a real window manager or display compositor, and
never runs a real screen reader against real platform accessibility APIs
(UIA on Windows, AT-SPI on Linux):

- Real screen-reader announcement text and behavior (NVDA/JAWS/Narrator,
  Orca).
- Real OS-level DPI scaling interactions (Windows per-monitor DPI awareness,
  Linux fractional-scaling compositors), as opposed to Qt's own
  `QT_SCALE_FACTOR` override.
- Real visual clipping/overlap under a live window manager, multi-monitor
  moves, and OS-level high-contrast/theming.
- A representative operator's actual end-to-end task success/error rate.

Every cell below is explicitly **Pending — not yet executed**. No cell may be
marked complete without a dated entry recording who ran it, on what
hardware/OS/AT combination, and what was observed (including failures). This
document must not be edited to claim completion of cells that have not
actually been executed on real hardware.

## What automated tests already cover (do not re-litigate manually)

These are already enforced by `uv run pytest tests/ui/test_instrument_console.py`
and do not need manual re-verification (only re-run if this test file
regresses):

| Automated evidence | Test(s) |
| --- | --- |
| Keyboard Tab/Shift+Tab focus traversal reaches navigation, workspace, and dock controls | `test_tab_key_focus_traversal_reaches_navigation_and_workspace_controls`, `test_shift_tab_focus_traversal_moves_backwards` |
| Global Stop is keyboard-reachable via a registered application-wide shortcut from any page | `test_global_stop_shortcut_works_from_any_page` |
| Save/Restore layout keyboard shortcuts activate the mode-appropriate action (demo vs. production) | `test_save_and_restore_layout_shortcuts_activate_the_demo_actions`, `test_save_and_restore_layout_shortcuts_activate_the_production_actions` |
| Accessible names/descriptions exist for key controls (Stop, status indicators, nav buttons, docks) and update with state | `test_accessible_names_are_present_for_key_controls`, `test_navigation_buttons_have_distinct_accessible_names`, `test_production_stop_wording_updates_with_run_state` |
| Rendered pixmap scales correctly at representative 100/150/200% `QT_SCALE_FACTOR` values | `test_console_renders_at_representative_100_150_200_percent_scaling` |
| No "demo" wording leaks into the production shell (wording correctness, not a visual/AT check) | `test_production_console_never_shows_demo_wording`, `test_production_console_has_no_demo_menu` |

## Evidence matrix

Platforms: **Windows 11** and **Ubuntu Linux** (per ADR 0005 item 6).
Resolutions: **1280×720** and **1920×1080** (per ADR 0005 item 6).
Scale factors: **100%**, **150%**, **200%** (OS-level display scaling, not
`QT_SCALE_FACTOR`).

For each Platform × Resolution × Scale cell, record: no clipping/overlap,
Global Stop always visible and reachable, keyboard traversal/focus order
matches automated expectations, contrast, and target (hit-area) sizes.

| Platform | Resolution | Scale | Status |
| --- | --- | --- | --- |
| Windows 11 | 1280×720 | 100% | Pending — not yet executed |
| Windows 11 | 1280×720 | 150% | Pending — not yet executed |
| Windows 11 | 1280×720 | 200% | Pending — not yet executed |
| Windows 11 | 1920×1080 | 100% | Pending — not yet executed |
| Windows 11 | 1920×1080 | 150% | Pending — not yet executed |
| Windows 11 | 1920×1080 | 200% | Pending — not yet executed |
| Ubuntu Linux | 1280×720 | 100% | Pending — not yet executed |
| Ubuntu Linux | 1280×720 | 150% | Pending — not yet executed |
| Ubuntu Linux | 1280×720 | 200% | Pending — not yet executed |
| Ubuntu Linux | 1920×1080 | 100% | Pending — not yet executed |
| Ubuntu Linux | 1920×1080 | 150% | Pending — not yet executed |
| Ubuntu Linux | 1920×1080 | 200% | Pending — not yet executed |

### Screen-reader announcement evidence

For each screen reader, record whether every key control (page navigation,
Global Stop, status indicators, docks, Save/Restore layout actions, workspace
file actions) announces a correct, non-generic name/role/state when
navigated to with the screen reader's own navigation commands (not just Qt's
own accessible-name property, which the automated suite already checks).

| Platform | Screen reader | Status |
| --- | --- | --- |
| Windows 11 | NVDA | Pending — not yet executed |
| Windows 11 | JAWS | Pending — not yet executed |
| Windows 11 | Narrator | Pending — not yet executed |
| Ubuntu Linux | Orca | Pending — not yet executed |

### Representative operator study

Per ADR 0005 item 6: "Conduct a representative operator study when access is
available and record completion/error findings without retroactively
changing these observed prototype facts."

| Study | Status |
| --- | --- |
| Representative operator task-completion/error-rate session on the production Instrument Console | Pending — not yet executed; no session has been scheduled or run |

## How to complete a cell

1. Run the packaged/production build (not the demo shell) on the target
   platform/resolution/scale combination, or the equivalent development
   build for pre-release evaluation.
2. Work through the checklist for that cell (clipping, Stop visibility,
   keyboard traversal, contrast, target sizes; or, for screen-reader rows,
   navigate every control listed above with the screen reader's own
   commands).
3. Replace **exactly** that cell's "Pending — not yet executed" with a dated
   result (pass/fail, observed issues, tester name/handle), citing any filed
   follow-up issue for failures. Do not batch-edit multiple cells based on
   assumption or extrapolation from one platform/scale to another.
4. If a cell fails, do not mark it "pass with a caveat" — record it as a
   failure with the specific defect, and file/reference a tracked follow-up.
