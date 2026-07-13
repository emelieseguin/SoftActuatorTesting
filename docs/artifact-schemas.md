# Artifact schemas and compatibility

## Common rules

Current schema version is **1**. JSON artifacts use this envelope:

| Field | Rule |
| --- | --- |
| `schema_version` | Positive integer; readers reject newer versions and writers only write current V1 |
| `artifact_type` | `workspace`, `calibration`, `geometry`, `run_manifest`, or `analysis_manifest` |
| `artifact_id` | Type-prefixed, collision-resistant ID; `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` and no `..` |
| `created_at`, `updated_at` | Timezone-aware ISO-8601 timestamps; update cannot precede creation |
| `software_version` | Optional non-empty string |
| `payload` | JSON-safe, validated type-specific object |

All stored artifact references are workspace-relative POSIX paths. Absolute
inputs are accepted only when they resolve inside the workspace; `..`, empty
paths, workspace escape, and silently overwriting an existing artifact are
rejected. `ArtifactFileStore` reserves a destination exclusively, writes and
fsyncs a same-directory temporary file, replaces it, then fsyncs the directory
on non-Windows systems. A directory-fsync failure after replace reports
publication uncertainty rather than claiming durability.

| Artifact | Canonical path |
| --- | --- |
| Workspace | `artifacts/workspace/<id>.json` |
| Calibration | `artifacts/calibration/<id>.json` |
| Geometry | `artifacts/geometry/<id>.json` |
| Run manifest / pressure | `runs/<run-id>/run.json`, `runs/<run-id>/pressure.csv` |
| Analysis manifest / rows | `analysis/<analysis-id>/analysis.json`, `analysis/<analysis-id>/angles.csv` |

## JSON payloads

| Type | Required payload fields | Workflow-produced provenance/additions |
| --- | --- | --- |
| Workspace | `name` (non-empty) | `saved_at`; preferences are deliberately separate OS-local settings |
| Calibration | `model.type` (`linear`/`quadratic`), `model.coeffs` (2/3 finite values), non-empty `samples` of `[known_pressure_kPa, measured_voltage]` | `units`, `validation_status`, `input_domain`, `fit_quality`, `residuals`, `sample_provenance`, `notes`, `created_by` |
| Geometry | `frame_size.width/height`, `base_point`, `initial_tip_point`, `roi.left/top/right/bottom` | `selection_provenance`, `representative_frame_index`, `source_video_name`, and optional tip suggestion provenance/settings |
| Run manifest | `completion`: `clean`, `stopped`, `aborted`, or `faulted` | requested outcome/reason, experiment timing, camera and serial profile, calibration/geometry snapshots, platform data, output files, warnings and cleanup errors |
| Analysis manifest | `source_video` and `geometry_artifact_id` | `source_video` is always workspace-relative (external operator selections are imported under `video/analysis-imports/`), plus measured FPS, completion/detail, `authoritative`, frame count, results ID, detector settings |

Geometry points must be within the source frame; ROI is non-empty and uses
exclusive right/bottom edges. Although the store validates a geometry
`initial_tip_point`, the authoring workflow cannot save until base, tip, and
ROI are complete. Calibration coefficients represent pressure in kPa from
voltage in V: linear `[slope, intercept]`; quadratic
`[quadratic, linear, intercept]`.

The run writer stores the full payload below. Consumers should preserve
unknown additive payload fields rather than relying on a fixed manifest shape.
In particular, V1 manifests written before the `capture` area existed remain
loadable: capture evidence is additive provenance and does not change any
measurement value or completion interpretation.

| Run payload area | Fields emitted by `RunController` |
| --- | --- |
| Outcome | `completion`, `requested_completion`, `reason`, `started_at`, `ended_at` |
| Experiment | `experiment.name`, `cycles`, `on_milliseconds`, `off_milliseconds` |
| Capture | `recording_enabled`; compatibility projection `camera.device`, `requested_profile`, `finalized_video`, `health`; full additive `capture` evidence |
| Measurement | `pressure_csv.path`, columns, units, raw-only provenance; `serial_profile` |
| Snapshots/provenance | `calibration_model_snapshot`, `geometry_model_snapshot`, `platform_provenance` |
| Diagnostics | `output_files`, `warnings`, `cleanup_errors` |

### Additive V1 capture evidence

New cyclic-run manifests include `payload.capture`. It records facts available
through the typed capture contract, not inferred hardware claims:

| Area | Contents |
| --- | --- |
| Target and selection | Requested width/height/FPS, selected device identifier, public backend/mode only when provided, and selected encoder |
| FFmpeg | Sanitized public command plus public build/version when exposed; unknown values are `null` and command secrets, environment substitutions, external paths, and URLs are redacted |
| Startup/negotiation | Startup-proof flag, unavailable proof timestamp as `null`, observed proof components, and negotiated input resolution/FPS/pixel format/codec |
| Progress/preview | Frames, FPS, speed, output time/size, duplicate/drop/malformed counters; preview received/consumed/replaced counters. Current contracts do not expose preview timestamp/rate/profile, so those fields are `null` |
| Terminal/verification | Controller and capture stop reasons, clean/failure/cooperative/drainer/escalation facts, FFprobe readability, and explicit `null` duration/frame-count/stream fields when the typed result does not expose them |
| Files/promotion | Workspace-relative `partial_path` and `final_path` only when reported inside the workspace, plus promoted/retained/not-started outcome |

`status` distinguishes disabled capture from unavailable, completed, failed, or
retained-partial capture. `null` means the fact was not available; zero and
`false` preserve reported counter/boolean values. The standalone Connections
`capture-status.json` is not linked from a run manifest because the cyclic
capture result contract does not expose a typed standalone-status reference.

## CSV files

| File | Exact V1 header | Semantics |
| --- | --- | --- |
| Pressure | `schema_version,artifact_id,time_s,volts,pressure_kPa` | Every row has finite time and volts. Empty `pressure_kPa` is explicit raw-only data, not zero or failed serialization. The run sink flushes and fsyncs header and every appended row. A manifest is written after pressure closes. |
| Analysis (current V1) | `schema_version,artifact_id,frame_index,video_time_seconds,tip_x,tip_y,actuator_angle_degrees,detection_state,confidence,correction_applied,detection_reason,legacy_import` | Frame index is strictly increasing; time is measured-FPS-derived; confidence is [0,1]. `missing`/`ambiguous` rows carry no tip or angle; `missing` confidence is zero. |

`detection_state` accepts `detected`, `manual`, `missing`, `ambiguous`, and
`held`. `correction_applied` and `legacy_import` are lowercase CSV booleans.
The current analysis writer emits a non-empty `detection_reason` for its
pipeline output. A V1 reader also accepts the prior V1 header that omits only
`detection_reason`; this was an additive provenance column and does not change
scientific interpretation. Do not rewrite a prior-header file merely to add
the column.

CSV rows intentionally do not duplicate artifact timestamps. For non-empty
CSV, identity is checked against the directory ID; the companion run/analysis
manifest is the timestamp/provenance authority. An empty pressure CSV
(header-only aborted/faulted run) is valid; an analysis CSV must have at least
one data row.

## Legacy import and export

Legacy import does not modify the source. It accepts only repository-documented
shapes and reports field paths for invalid input.

| Legacy source | Import requirement | Versioned result |
| --- | --- | --- |
| Calibration JSON | Exact `model`/`samples`; linear/quadratic coefficient arity | Calibration artifact with units, valid status, and source filename |
| Geometry JSON | Caller supplies frame dimensions; valid points and either `x,y,w,h` or ordered corners | Normalized frame-bounded geometry, `selection_provenance=legacy_import` |
| Pressure CSV | Exact `time_s,volts,pressure_kPa` header | Pressure rows; blank/`nan` pressure becomes null |
| Notebook angle CSV | Exact `Frame,ActuatorAngle_deg`; caller supplies finite positive measured FPS | Analysis rows with `frame_index / FPS`, `legacy_import=true`, no recoverable tips |

Legacy serial text is intentionally not an importable run artifact because it
does not establish firmware semantics. Legacy export is one-way, atomic, and
refuses an existing destination: calibration exports `model`/`samples`;
geometry exports legacy point and `x,y,w,h` ROI fields; pressure exports the
three legacy columns; analysis exports `Frame,ActuatorAngle_deg`. Extra V1
provenance is not round-tripped into legacy files.

For a future schema version, add an explicit migrator before accepting it.
Unknown/newer versions fail closed. V1 JSON migration is deliberately identity
only; do not add implicit defaulting that changes measurement meaning. V1
readers accept both old run manifests without `capture` and later V1 manifests
with it; writers emit the additive area but never rewrite an older manifest to
invent unavailable capture facts.

## Verification Summary

Fact-checked against the current working tree on 2026-07-13. Factual
verification of schema/compatibility claims only.

- **Claims checked:** 42
- **Confirmed:** 42
- **Corrected:** 0
- **Unverifiable:** 0

Representative confirmations:

- `CURRENT_SCHEMA_VERSION = 1`; envelope validates `artifact_type` (the five
  listed types), `artifact_id` regex `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` with no
  `..`, positive-integer `schema_version` that rejects newer versions, plus
  timezone-aware timestamps and optional `software_version`
  (`domain/artifacts.py:14-124`).
- Canonical paths match exactly: `artifacts/workspace|calibration|geometry/<id>.json`,
  `runs/<id>/run.json`+`pressure.csv`, `analysis/<id>/analysis.json`+`angles.csv`
  (`infrastructure/artifact_store.py:228-239`). Atomic write with same-directory
  temp, replace, and non-Windows directory fsync reporting publication
  uncertainty on failure (`artifact_store.py:242-289`).
- CSV headers are byte-for-byte correct, including the prior-V1 analysis header
  that omits only `detection_reason` (`artifact_store.py:52-79`). Empty pressure
  CSV is valid; analysis CSV requires ≥1 data row; identity checked against the
  directory ID (`artifact_store.py:398-441`).
- Payload requirements confirmed per type, including calibration coeff arity and
  ordering (`[slope, intercept]` / `[quadratic, linear, intercept]`), geometry
  `roi.left/top/right/bottom` with exclusive edges, run manifest areas emitted by
  `RunController._manifest_payload`, and analysis manifest `source_video` +
  `geometry_artifact_id` plus `measured_fps`, completion/`completion_detail`,
  `authoritative`, `frame_count`, `results_artifact_id`, `detector_settings`
  (`domain/calibration.py:57-96`, `domain/geometry.py:53-121`,
  `run_controller.py`, `analysis_pipeline.py:374-386`,
  `artifact_store.py:452-455,509-523`).
- `detection_state` accepts `detected`/`manual`/`missing`/`ambiguous`/`held`,
  missing confidence is zero (`domain/analysis.py:14-63`).
- Legacy import/export shapes, coefficient arity, `selection_provenance=legacy_import`,
  `legacy_import=true` with `frame_index/FPS`, one-way atomic export refusing an
  existing destination, and the exact three/two legacy column sets are all
  confirmed (`infrastructure/legacy_import.py:22-232,288-295`).
