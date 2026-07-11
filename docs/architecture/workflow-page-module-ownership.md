# Workflow page module ownership

**Status:** Implemented.
**Date:** 2026-07-11.
**Related todo:** `modularize-workflow-pages`.

## Ownership

Shared presenter resolution, scenario fixtures, theme application, and snapshot
rendering infrastructure belong in `ui.views.base`. Each workflow module owns
only its Qt controls plus typed presenter commands:

- `workflows/home_workspace.py` — workspace selection and creation
- `workflows/connections_diagnostics.py` — device connections and diagnostics
- `workflows/calibration.py` — sample collection and fitting
- `workflows/video_geometry_marker_setup.py` — preview, geometry, and markers
  (manual geometry authoring itself lives in the independent
  `application/video_geometry_workflow.py` service and `ui/views/video_geometry.py`
  widget it embeds; see `video-geometry-workflow.md`)
- `workflows/experiment_readiness.py` — experiment configuration and readiness
- `workflows/live_run.py` — live telemetry, preview, and run controls
- `workflows/analysis.py` — analysis source, progress, and review
- `workflows/settings_help.py` — session settings, profiles, and help

`ui.views.registry` owns shell-neutral navigation metadata; both shells consume
it unchanged. `ui.views` remains the stable public import surface and
`ui.views.pages` re-exports its former public classes for compatibility.

## Refactor validation plan

Run `uv run pytest tests/ui/test_workflow_pages.py` and presenter/shell UI
tests to verify all page factories, presenter subscriptions, commands, and both
shells. Then run the default `uv run pytest` suite.
