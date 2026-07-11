# Legacy compatibility fixtures

This corpus is a small, sanitized compatibility baseline derived from the
repository-owned legacy formats in `old-files/PressureCalibration.py`,
`old-files/DataCollection-V2.py`, `old-files/VideoConfig.py`, and
`old-files/PneumaticActuatorAnalysis-V1.ipynb`. Values, frames, and serial
messages are synthetic; no lab capture, operator identity, device name, or
private path is included. The repository owns the source formats, and these
fixtures introduce no third-party licensing or privacy concern.

## Fixtures and expected import behavior

- `calibration/valid-*.json` use the legacy `model`/`samples` shape and should
  load as linear or quadratic calibrations. `invalid-missing-model.json` is
  syntactically valid JSON but must be rejected because the data GUI requires
  `model.coeffs`. `invalid-linear-short-coeffs.json` exposes the legacy
  arity-validation defect: the data GUI accepts it on load, then falls back to
  the raw value when applying it.
- `geometry/valid-*.json` matches the 192×128 synthetic video. The reverse,
  missing, and out-of-bounds files intentionally represent legacy save defects.
  A compatible importer should reject missing geometry and normalize/reject
  reverse or out-of-bounds ROIs. The legacy notebook rejects the missing base,
  accepts positive reverse values as a wrong crop, and clips out-of-bounds ROI
  cropping rather than validating it.
- `pressure/calibrated-pressure.csv` has the legacy
  `time_s,volts,pressure_kPa` header. `raw-missing-pressure.csv` records the
  intended raw-only representation using an empty pressure field; the legacy
  writer cannot create such rows because formatting `pressure=None` raises
  `TypeError`.
- `angle/angles-valid.csv` and `angles-missing-values.csv` use the notebook's
  `Frame,ActuatorAngle_deg` header. `nan` is the legacy pre-detection value;
  the blank field is an additional missing-value case a new importer must
  handle deliberately. Because this format contains no timestamps or FPS, the
  importer must require the source video's measured frame rate rather than
  inventing elapsed time.
- `serial/command-lines.txt` contains the repository's `CMD:*` command text.
  `telemetry-normal-with-markers.txt` has the observed run markers and normal
  three-field rows, where legacy code reads field three as volts. The malformed
  file covers one-field, short two-field, nonnumeric, and reader-error input.
  It is not an authoritative firmware protocol: field order and acknowledgements
  remain unknown. The `legacy-field-3-unconfirmed` parser profile can map that
  observed third field only when explicitly selected; the default parser does
  not infer it. See `docs/architecture/serial-protocol-and-test-plan.md`.

## Synthetic video

`video/generate_synthetic_video.py` deterministically creates
`synthetic-red-marker.avi`: three 192×128, 10 fps black frames with a 7-pixel
red marker. It uses only the existing legacy virtual environment's Python,
OpenCV, and NumPy with MJPG encoding. The valid geometry fixture uses its first
marker location. Regenerate it from the repository root with:

```bash
old-files/.venv/bin/python tests/fixtures/video/generate_synthetic_video.py
```

The generated video is intentionally tiny and contains no real experiment data.

## Synthetic marker-suggestions video

`video/generate_marker_suggestion_video.py` deterministically creates
`synthetic-marker-suggestions.avi`: nine 192×128, 10 fps frames, each built
for one required guided-marker-suggestion regression scenario (see
`docs/architecture/marker-suggestions.md`):

| Frame | Scenario |
| --- | --- |
| 0 | Baseline single pure-red marker (frame-zero processing). |
| 1 | A marker colored near the OpenCV hue wraparound boundary (~173), exercising the high-hue band of the dual-hue mask. |
| 2 | An orange decoy (hue ~16, outside both red bands) plus one genuine red marker. |
| 3 | Blank frame — no marker present (`NO_DETECTION`). |
| 4 | One marker inside a test ROI, one outside it (ROI restriction). |
| 5 | Two equidistant, equal-size, equal-color markers from a shared base point, with no prior confirmed tip (ambiguity). |
| 6 | The first frame of a temporal-continuity pair: two equal blobs: only a previously confirmed tip can break the tie. |
| 7 | The second frame of the temporal-continuity pair. |
| 8 | A marker below the default `min_area_pixels` threshold (tests threshold reconfiguration). |

It uses this project's own `.venv` Python, OpenCV, and NumPy with MJPG
encoding — no lab capture, operator identity, or private path is included.
Regenerate it from the repository root with:

```bash
.venv/bin/python tests/fixtures/video/generate_marker_suggestion_video.py
```
