"""Strict adapters for repository-owned pre-versioning artifact formats."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping

from soft_actuator_testing.application.services import ArtifactDocument
from soft_actuator_testing.domain.artifacts import ArtifactIdentity, ArtifactMetadata, ArtifactType
from soft_actuator_testing.domain.errors import DomainError, ErrorCode


class LegacyArtifactImporter:
    """Imports only documented legacy artifact shapes; serial text is excluded."""

    def import_file(
        self,
        source: Path,
        artifact_type: ArtifactType,
        *,
        frame_size: tuple[int, int] | None = None,
        frame_rate_hz: float | None = None,
    ) -> ArtifactDocument:
        source = Path(source)
        if not source.is_file():
            raise self._error("legacy source file does not exist", "source")
        if artifact_type is ArtifactType.CALIBRATION:
            return self._calibration(source)
        if artifact_type is ArtifactType.GEOMETRY:
            if frame_size is None:
                raise self._error(
                    "legacy geometry has no frame dimensions",
                    "frame_size",
                    "Supply the source video's width and height when importing geometry.",
                )
            return self._geometry(source, frame_size)
        if artifact_type is ArtifactType.PRESSURE_DATA:
            return self._pressure(source)
        if artifact_type is ArtifactType.ANALYSIS_RESULTS:
            if frame_rate_hz is None:
                raise self._error(
                    "legacy angle data has no frame rate or timestamps",
                    "frame_rate_hz",
                    "Supply the source video's measured frame rate when importing angle results.",
                )
            return self._angles(source, frame_rate_hz)
        raise self._error(
            "this artifact type has no supported legacy importer",
            "artifact_type",
            "Serial transcripts are not a firmware protocol and cannot be imported as run artifacts.",
        )

    def export_file(self, document: ArtifactDocument, destination: Path) -> None:
        destination = Path(destination)
        if destination.exists():
            raise self._error(
                "refusing to overwrite an existing legacy export",
                "destination",
                "Choose a new destination or remove the old export explicitly.",
            )
        artifact_type = document.metadata.identity.artifact_type
        payload = document.payload
        if artifact_type is ArtifactType.CALIBRATION:
            content = json.dumps({"model": payload["model"], "samples": payload["samples"]}, indent=2, allow_nan=False) + "\n"
        elif artifact_type is ArtifactType.GEOMETRY:
            roi = payload["roi"]
            content = json.dumps(
                {
                    "angle_base_point": payload["base_point"],
                    "angle_tip_point": payload["initial_tip_point"],
                    "actuator_roi": {
                        "x": roi["left"],
                        "y": roi["top"],
                        "w": roi["right"] - roi["left"],
                        "h": roi["bottom"] - roi["top"],
                    },
                },
                indent=2,
                allow_nan=False,
            ) + "\n"
        elif artifact_type is ArtifactType.PRESSURE_DATA:
            content = self._csv_text(
                ("time_s", "volts", "pressure_kPa"),
                ((row["time_s"], row["volts"], row["pressure_kPa"]) for row in payload["rows"]),
            )
        elif artifact_type is ArtifactType.ANALYSIS_RESULTS:
            content = self._csv_text(
                ("Frame", "ActuatorAngle_deg"),
                ((row["frame_index"], row["actuator_angle_degrees"]) for row in payload["rows"]),
            )
        else:
            raise self._error("legacy export is not defined for this artifact type", "artifact_type")
        self._atomic_create(destination, content)

    def _calibration(self, source: Path) -> ArtifactDocument:
        data = self._json(source)
        model = self._mapping(data.get("model"), "model")
        model_type = self._string(model.get("type"), "model.type")
        coeffs = self._list(model.get("coeffs"), "model.coeffs")
        expected = 2 if model_type == "linear" else 3 if model_type == "quadratic" else 0
        if not expected:
            raise self._error("model type must be linear or quadratic", "model.type")
        if len(coeffs) != expected:
            raise self._error(f"{model_type} model requires exactly {expected} coefficients", "model.coeffs")
        for index, value in enumerate(coeffs):
            self._number(value, f"model.coeffs[{index}]")
        samples = self._list(data.get("samples"), "samples")
        if not samples:
            raise self._error("at least one calibration sample is required", "samples")
        for index, item in enumerate(samples):
            pair = self._list(item, f"samples[{index}]")
            if len(pair) != 2:
                raise self._error("sample must be [known_pressure_kPa, measured_voltage]", f"samples[{index}]")
            self._number(pair[0], f"samples[{index}][0]")
            self._number(pair[1], f"samples[{index}][1]")
        return self._document(
            ArtifactType.CALIBRATION,
            {
                "model": {"type": model_type, "coeffs": coeffs},
                "samples": samples,
                "units": {"voltage": "V", "pressure": "kPa"},
                "validation_status": "valid",
                "legacy_source_name": source.name,
            },
        )

    def _geometry(self, source: Path, frame_size: tuple[int, int]) -> ArtifactDocument:
        width, height = frame_size
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            raise self._error("frame dimensions must be positive integers", "frame_size")
        data = self._json(source)
        base = self._point(data.get("angle_base_point"), "angle_base_point", width, height)
        tip = self._point(data.get("angle_tip_point"), "angle_tip_point", width, height)
        roi = self._mapping(data.get("actuator_roi"), "actuator_roi")
        if {"x", "y", "w", "h"} <= roi.keys():
            x, y = self._number(roi["x"], "actuator_roi.x"), self._number(roi["y"], "actuator_roi.y")
            right, bottom = x + self._number(roi["w"], "actuator_roi.w"), y + self._number(roi["h"], "actuator_roi.h")
        elif {"top_left", "bottom_right"} <= roi.keys():
            first = self._point(roi["top_left"], "actuator_roi.top_left", width, height)
            second = self._point(roi["bottom_right"], "actuator_roi.bottom_right", width, height)
            x, y, right, bottom = first["x"], first["y"], second["x"], second["y"]
        else:
            raise self._error("ROI must contain x/y/w/h or top_left/bottom_right", "actuator_roi")
        left, top = min(x, right), min(y, bottom)
        right, bottom = max(x, right), max(y, bottom)
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise self._error(
                "ROI must be non-empty and within source frame bounds",
                "actuator_roi",
                "Select a region inside the supplied source-video dimensions.",
            )
        return self._document(
            ArtifactType.GEOMETRY,
            {
                "frame_size": {"width": width, "height": height},
                "base_point": base,
                "initial_tip_point": tip,
                "roi": {"left": left, "top": top, "right": right, "bottom": bottom},
                "selection_provenance": "legacy_import",
                "legacy_source_name": source.name,
            },
        )

    def _pressure(self, source: Path) -> ArtifactDocument:
        rows = self._read_csv(source, ("time_s", "volts", "pressure_kPa"))
        parsed = []
        for index, row in enumerate(rows, start=2):
            parsed.append(
                {
                    "time_s": self._number(row.get("time_s"), f"row[{index}].time_s"),
                    "volts": self._number(row.get("volts"), f"row[{index}].volts"),
                    "pressure_kPa": self._optional_number(row.get("pressure_kPa"), f"row[{index}].pressure_kPa"),
                }
            )
        return self._document(ArtifactType.PRESSURE_DATA, {"rows": parsed, "legacy_source_name": source.name})

    def _angles(self, source: Path, frame_rate_hz: float) -> ArtifactDocument:
        frame_rate_hz = self._number(frame_rate_hz, "frame_rate_hz")
        if frame_rate_hz <= 0:
            raise self._error("frame rate must be positive", "frame_rate_hz")
        rows = self._read_csv(source, ("Frame", "ActuatorAngle_deg"))
        parsed = []
        for index, row in enumerate(rows, start=2):
            angle = self._optional_number(row.get("ActuatorAngle_deg"), f"row[{index}].ActuatorAngle_deg")
            frame_index = self._integer(row.get("Frame"), f"row[{index}].Frame")
            if frame_index < 0:
                raise self._error("frame index cannot be negative", f"row[{index}].Frame")
            parsed.append(
                {
                    "frame_index": frame_index,
                    "video_time_seconds": frame_index / frame_rate_hz,
                    "tip_x": None,
                    "tip_y": None,
                    "actuator_angle_degrees": angle,
                    "detection_state": "manual" if angle is not None else "missing",
                    "confidence": 1.0 if angle is not None else 0.0,
                    "correction_applied": False,
                }
            )
        return self._document(
            ArtifactType.ANALYSIS_RESULTS,
            {
                "rows": parsed,
                "legacy_import": True,
                "legacy_source_name": source.name,
                "source_frame_rate_hz": frame_rate_hz,
                "timing_provenance": "source_fps_derived",
            },
        )

    def _document(self, artifact_type: ArtifactType, payload: Mapping[str, Any]) -> ArtifactDocument:
        identity = ArtifactIdentity.new(artifact_type)
        return ArtifactDocument(ArtifactMetadata.now(identity), payload)

    def _json(self, source: Path) -> Mapping[str, Any]:
        try:
            with source.open(encoding="utf-8") as handle:
                return self._mapping(json.load(handle), "root")
        except (OSError, json.JSONDecodeError) as error:
            raise self._error(f"cannot read legacy JSON: {error}", "source") from error

    def _read_csv(self, source: Path, expected_header: tuple[str, ...]) -> list[dict[str, str]]:
        try:
            with source.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if tuple(reader.fieldnames or ()) != expected_header:
                    raise self._error(f"expected CSV header {','.join(expected_header)}", "header")
                return list(reader)
        except OSError as error:
            raise self._error(f"cannot read legacy CSV: {error}", "source") from error

    def _point(self, value: Any, path: str, width: int, height: int) -> dict[str, float]:
        point = self._mapping(value, path)
        x, y = self._number(point.get("x"), f"{path}.x"), self._number(point.get("y"), f"{path}.y")
        if not (0 <= x < width and 0 <= y < height):
            raise self._error("point is outside source frame bounds", path)
        return {"x": x, "y": y}

    def _atomic_create(self, target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            reservation = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise self._error("refusing to overwrite an existing export", "destination") from error
        else:
            os.close(reservation)
        temporary: Path | None = None
        replaced = False
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            replaced = True
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if not replaced:
                target.unlink(missing_ok=True)

    @staticmethod
    def _csv_text(header: tuple[str, ...], rows: Any) -> str:
        from io import StringIO

        output = StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
        return output.getvalue()

    @staticmethod
    def _error(message: str, path: str, guidance: str | None = None) -> DomainError:
        return DomainError(ErrorCode.ARTIFACT_INVALID, message, path, guidance)

    def _mapping(self, value: Any, path: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise self._error("must be an object", path)
        return value

    def _list(self, value: Any, path: str) -> list[Any]:
        if not isinstance(value, list):
            raise self._error("must be an array", path)
        return value

    def _string(self, value: Any, path: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise self._error("must be a non-empty string", path)
        return value

    def _integer(self, value: Any, path: str) -> int:
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError as error:
                raise self._error("must be an integer", path) from error
        if not isinstance(value, int) or isinstance(value, bool):
            raise self._error("must be an integer", path)
        return value

    def _number(self, value: Any, path: str) -> float:
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError as error:
                raise self._error("must be a finite number", path) from error
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise self._error("must be a finite number", path)
        return float(value)

    def _optional_number(self, value: Any, path: str) -> float | None:
        if value in (None, "", "nan", "NaN", "NAN"):
            return None
        return self._number(value, path)
