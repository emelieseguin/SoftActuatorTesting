# Data-integrity hardening test plan

**Date:** 2026-07-13  
**Related todo:** `quality-data-integrity`

## Scope

Before implementation, audit and regression-test the persistence boundary,
workspace restoration, legacy adapters, and Qt-free scientific contracts.
The changes must preserve version-1 and documented legacy compatibility while
failing closed for malformed or ambiguous scientific data.

## Planned verification

- Domain contracts reject wrong runtime types, booleans where numeric values are
  required, non-finite values, invalid dimensions/ROIs, invalid IDs, and
  inconsistent analysis detections with stable error codes and field paths.
- Versioned JSON and CSV persistence rejects malformed/empty/extra CSV rows,
  cross-row schema or artifact-ID mismatches, invalid boolean cells, and
  non-finite values. CSV writers quote textual fields and preserve deterministic
  row order.
- Writes reserve destinations without overwriting, fsync file and directory
  metadata where supported, clean only their own incomplete reservation on
  failure, retain the original write error, and keep successful repeated
  finalization idempotent.
- Stored references remain relative, reject traversal and symlink escape, and
  workspace restoration reports missing analysis videos, geometry references,
  and run outputs without activating an invalid candidate or discarding an
  already active workspace.
- Legacy import/export accepts only exact documented CSV/JSON shapes; it
  requires finite positive measured FPS for angle timing, preserves an explicit
  legacy missing-value representation, rejects malformed rows/unknown fields
  and invalid ROI dimensions/bounds, and never fabricates source metadata.

Targeted domain, artifact, legacy, and workspace tests run first, followed by
`uv run pytest`.

## Reviewer remediation plan — 2026-07-13

- Load both the original version-1 analysis CSV header and the additive
  `detection_reason` layout. Re-saving a prior-header artifact may use the
  current layout, but must preserve every scientific row value and provenance.
- Reject legacy `{x,y,w,h}` ROIs with non-positive dimensions and
  `top_left`/`bottom_right` ROIs whose corners are not ordered, before any
  normalization could reinterpret operator data.
- Mark an artifact write that fails only after `os.replace()` as publication
  uncertain. Workspace creation must retain that directory and its possible
  manifest, while still rolling back a pre-publication failure and surfacing the
  original error.

## Implementation outcome

The persistence boundary now serializes pressure and analysis CSV through
`csv.writer`, rejects malformed headers, blank/short/extra rows, non-finite
cells, non-canonical booleans, cross-row identity drift, and non-monotonic
analysis frame indices. JSON payloads are checked recursively before writing.
Artifact and legacy exports reserve destinations, fsync file and parent
directory metadata on platforms that support directory fsync, and do not remove
a successfully replaced file when finalization reporting fails.

The original and additive version-1 analysis CSV headers are both accepted; a
resave uses the additive header without changing frame measurements. Legacy ROI
imports now reject negative/zero dimensions and reversed corner ordering rather
than reinterpreting them. A post-replace directory-fsync failure now carries
the explicit `artifact_publication_uncertain` error code, and workspace creation
keeps the possible manifest for operator recovery; pre-publication failures
continue to roll back the empty workspace.

Workspace restore now preserves an existing active workspace if another
candidate is invalid and reports stale analysis geometry/video and run-output
references, including video symlinks that escape the workspace. Legacy JSON
and CSV adapters require their documented shapes and reject unknown JSON keys
or malformed rows; the legacy `nan` angle sentinel remains an explicit missing
value for compatibility.

The serial parser's unknown third legacy field remains intentionally outside
this scope. It is confined to the existing
`legacy-field-3-unconfirmed` profile; no persistence adapter assigns scientific
meaning to it.

**Verification:** targeted persistence/domain/workflow tests: 139 passed.
Full default suite: 519 passed, 1 hardware/external-FFmpeg deselected.
