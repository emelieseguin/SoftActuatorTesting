# ADR 0002: Artifact versioning and legacy compatibility policy

**Status:** Accepted.
**Date:** 2026-07-11.
**Related todo:** `architecture-record` (Phase 0) in the approved Unified
SoftActuatorTesting UI Implementation Plan.

## Context

None of the legacy JSON/CSV artifacts carry a schema version, and several
have documented defects that must not be preserved as default behavior (see
[`../initial-implementation/calibration-and-video.md`](../initial-implementation/calibration-and-video.md)
and
[`../initial-implementation/notebook-analysis.md`](../initial-implementation/notebook-analysis.md)):

| Legacy artifact | Current shape | Known defect |
| --- | --- | --- |
| Calibration JSON (operator-chosen path) | `{"model": {...}, "samples": [...]}` | No schema/version metadata; a file with samples but no model can be saved. |
| Geometry JSON (`<video>_config.json`) | `angle_base_point: {x, y}`, `angle_tip_point: {x, y}`, `actuator_roi` as either `{x, y, w, h}` or `{top_left, bottom_right}` | Missing base/tip serialize as `{}`; missing ROI corners default to `0`/`100`, silently saving a fabricated `0,0,100,100` ROI. |
| Pressure CSV (`runs/run_<timestamp>/data.csv`) | Header `time_s,volts,pressure_kPa` | Formatting raises when no calibration is loaded, and there is no explicit "missing calibrated value" representation. |
| Angle CSV (`analysis_<timestamp>.csv`) | Per-frame rows from the notebook | Timestamp-only naming can collide; no detection-quality flag; stale tip positions can be reported as valid. |

The rewrite must keep these legacy files importable while fixing these
defects in the new, versioned formats, per the plan's "Import legacy files
and produce richer versioned artifacts, with optional legacy-compatible
export" decision.

## Decision

### Every artifact is a versioned JSON document

Workspace, calibration, geometry, run manifest, and analysis artifacts each
get:

- an explicit `schema_version`;
- a collision-resistant artifact/run ID;
- creation/update metadata (timestamps, software/platform version where
  applicable);
- validation before use, with actionable errors that name the offending
  field path.

### Implemented schema version 1 (2026-07-11)

`infrastructure.artifact_store.ArtifactFileStore` writes JSON documents for
workspace, calibration, geometry, run manifest, and analysis manifest with this
envelope:

```json
{
  "schema_version": 1,
  "artifact_type": "calibration",
  "artifact_id": "calibration_<uuid4-hex>",
  "created_at": "2026-07-11T20:00:00+00:00",
  "updated_at": "2026-07-11T20:00:00+00:00",
  "software_version": "optional",
  "payload": {}
}
```

- Workspace payload requires `name`; references named `*_path`,
  `source_video`, `source_path`, `video_path`, `calibration_snapshot`,
  `geometry_snapshot`, or `output_files` are stored workspace-relative.
- Calibration payload requires `model.type` (`linear`/`quadratic`),
  `model.coeffs` (two/three finite values), and finite
  `[known_pressure_kPa, measured_voltage]` `samples`.
- Geometry payload requires `frame_size`, `base_point`, `initial_tip_point`,
  and exclusive-edge `roi` (`left`, `top`, `right`, `bottom`) within the
  source frame.
- Run manifest payload requires `completion` (`clean`, `stopped`, `aborted`,
  or `faulted`) and may carry portable output/snapshot references.
- Analysis manifest requires a relative `source_video` and
  `geometry_artifact_id`.

Pressure data is versioned `runs/<id>/pressure.csv` with
`schema_version,artifact_id,time_s,volts,pressure_kPa`; an empty
`pressure_kPa` is the explicit raw-only representation. Analysis results are
versioned `analysis/<id>/angles.csv` with
`schema_version,artifact_id,frame_index,video_time_seconds,tip_x,tip_y,actuator_angle_degrees,detection_state,confidence,correction_applied,legacy_import`.
The `legacy_import` flag permits legacy angle rows that have an angle but no
recoverable tip coordinates; new rows still require coordinates for every
non-missing detection.

### Field-by-field compatibility mapping

**Calibration** — preserve `model` and `samples` verbatim, then add schema
version, artifact ID, units, capture timestamps/source, fit metrics, a valid
input domain, optional notes/provenance, and a validation status. Legacy
calibration JSON remains importable as-is. Legacy export writes only the
compatible `model`/`samples` subset (linear/quadratic models and sample
pairs) — no new fields leak into the legacy-compatible file.

**Geometry** — preserve base, optional initial tip, and ROI, then add schema
version, artifact ID, source video identity/dimensions, a normalized ROI,
representative frame index/time, detector settings, and manual/automatic
provenance per selection. Unlike the legacy tool, incomplete geometry (a
missing base, tip, or ROI corner) is a validation error, not a silently
fabricated value — the `0,0,100,100`-style defect is not carried forward.

**Run manifest and pressure data** — each run gets one directory containing
`run.json`, `pressure.csv`, optional `video.<container>` (recording is
enabled by default for cyclic runs), run-scoped logs, and links to the
calibration/geometry snapshots used. `pressure.csv` keeps `time_s`, `volts`,
and `pressure_kPa`; a missing calibrated pressure is represented explicitly
(not by a formatting error) and documented. Additional quality/source
columns may only be added through a versioned schema bump, never silently.
`run.json` also records software/platform versions, device/profile
identities, start/end times, synchronization mode, requested vs. measured
capture mode, recorder/encoder, frame/drop counters, output files, warnings,
and a clean/stopped/aborted/faulted completion state (see
[`0003-concurrency-and-run-finalization.md`](0003-concurrency-and-run-finalization.md)).

**Analysis** — the versioned angle CSV includes at least frame index, video
time in seconds, tip x/y, actuator angle in degrees, an explicit detection
state (`detected`, `manual`, `missing`, or `held`), confidence/quality, and
correction flags. A companion analysis manifest records the source video,
geometry artifact, detector settings, software version, summary counts, and
output paths. Collision-resistant naming replaces the legacy
timestamp-only `analysis_<timestamp>.csv` naming defect.

### Legacy import/export adapters

- Legacy import adapters read legacy files, validate them, and produce
  current versioned artifacts — reporting missing/invalid fields as
  actionable errors instead of the legacy tools' silent defaults.
- Legacy angle CSV import requires the source video's measured frame rate because
  the old CSV contains frame indices but no timestamps or FPS. Versioned
  `video_time_seconds` is derived as `Frame / frame_rate_hz` and records that
  provenance; the importer never invents a one-second interval.
- Legacy export is optional and one-directional: it writes only the
  historically compatible subset of a current artifact. It never becomes the
  primary persisted format, and it never round-trips extra fields back in.
- The original imported file is preserved unless the operator explicitly
  exports over a chosen destination.

### Persistence rules (apply to every artifact type)

- Validate before use.
- Use atomic write/replace where the platform/filesystem supports it.
- Refuse silent overwrite; use collision-resistant IDs.
- Resolve paths independently of the current working directory.
- Report validation errors with field paths and corrective guidance.

### Migration policy

- Each artifact type gets an explicit per-`schema_version` migrator function
  when a new version is introduced.
- An artifact whose `schema_version` is unknown/newer than the running
  application fails closed with an actionable error; the application does
  not guess a compatible interpretation.
- Version 1 has an explicit identity migrator per JSON artifact type in
  `migrate_document`. A later version must add its own migrator before readers
  accept it; no implicit field-default migration is allowed.
- Writes reserve a new destination with exclusive creation, write/fsync a
  same-directory temporary file, and replace the reservation atomically.
  Failed replacements remove both the reservation and temporary file.

## Consequences

- All reads/writes of these artifacts must go through the
  `infrastructure/artifact_store.py` and `infrastructure/legacy_import.py`
  services described in
  [`0001-ui-framework-and-qt-boundaries.md`](0001-ui-framework-and-qt-boundaries.md)'s
  package layout, rather than ad hoc `json.dump`/`open()` calls from views —
  this is what makes atomicity, collision-safety, and validation consistently
  enforced in one place.
- Historical legacy files remain usable for import, which preserves existing
  experiment data collected before this rewrite.
- Legacy calibration JSON, geometry JSON (with caller-supplied frame size),
  pressure CSV, and angle CSV (with caller-supplied source frame rate) are
  imported without modifying the source.
  Geometry rejects missing/out-of-frame selections; reverse corner order is
  normalized only when the resulting rectangle is in bounds. Serial transcript
  text is deliberately rejected because it is not an authoritative protocol.
- The legacy-compatible export path must be kept intentionally narrow so it
  cannot silently become a second, drifting source of truth for the new
  schema.
