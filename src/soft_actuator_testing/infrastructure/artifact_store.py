"""Versioned, portable, atomic persistence for project artifacts."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any, Mapping, TextIO

from soft_actuator_testing.application.services import ArtifactDocument
from soft_actuator_testing.domain.artifacts import (
    ArtifactIdentity,
    ArtifactMetadata,
    ArtifactType,
    CURRENT_SCHEMA_VERSION,
    require_supported_schema_version,
)
from soft_actuator_testing.domain.errors import DomainError, ErrorCode
from soft_actuator_testing.domain.run_state import RunCompletion


class ArtifactPersistenceError(DomainError):
    """An artifact cannot be safely read, validated, or persisted."""


_JSON_ARTIFACTS = frozenset(
    {
        ArtifactType.WORKSPACE,
        ArtifactType.CALIBRATION,
        ArtifactType.GEOMETRY,
        ArtifactType.RUN_MANIFEST,
        ArtifactType.ANALYSIS_MANIFEST,
    }
)
_PATH_KEYS = frozenset(
    {
        "source_video",
        "source_path",
        "video_path",
        "calibration_snapshot",
        "geometry_snapshot",
        "output_files",
    }
)
_ANALYSIS_COLUMNS = (
    "schema_version",
    "artifact_id",
    "frame_index",
    "video_time_seconds",
    "tip_x",
    "tip_y",
    "actuator_angle_degrees",
    "detection_state",
    "confidence",
    "correction_applied",
    "legacy_import",
)
_PRESSURE_COLUMNS = ("schema_version", "artifact_id", "time_s", "volts", "pressure_kPa")


class ArtifactFileStore:
    """Filesystem implementation of the application artifact-store protocol.

    All persisted path references are workspace-relative.  Callers may supply
    an absolute path inside the workspace; it is converted before persistence.
    """

    def __init__(self, workspace_root: Path) -> None:
        self.root = Path(workspace_root).expanduser().resolve()

    def save(self, document: ArtifactDocument) -> None:
        document = self._validated_document(document)
        target = self._path_for(document.metadata.identity.artifact_type, document.metadata.identity.artifact_id)
        if document.metadata.identity.artifact_type in _JSON_ARTIFACTS:
            content = json.dumps(self._json_document(document), indent=2, sort_keys=True, allow_nan=False) + "\n"
        elif document.metadata.identity.artifact_type is ArtifactType.ANALYSIS_RESULTS:
            content = self._analysis_csv(document)
        elif document.metadata.identity.artifact_type is ArtifactType.PRESSURE_DATA:
            content = self._pressure_csv(document)
        else:  # pragma: no cover - protected by ArtifactType enum
            raise self._error("artifact type is not persistable", "artifact_type")
        self._atomic_create(target, content)

    def preflight_run_storage(self, required_bytes: int = 0) -> None:
        """Fail early when a run workspace cannot accept durable capture output."""

        if required_bytes < 0:
            raise ValueError("required_bytes must not be negative")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe = self.root / ".run-storage-probe"
            with probe.open("x", encoding="utf-8") as handle:
                handle.write("probe")
                handle.flush()
                os.fsync(handle.fileno())
            probe.unlink()
            available = os.statvfs(self.root).f_bavail * os.statvfs(self.root).f_frsize
        except OSError as error:
            raise self._error(
                f"workspace is not writable: {error}",
                "workspace",
                "Select a writable workspace before starting a run.",
            ) from error
        if available < required_bytes:
            raise self._error(
                f"workspace has {available} free bytes but run requires {required_bytes}",
                "storage.capacity",
                "Free storage or reduce the requested capture size before starting.",
            )

    def begin_run_artifacts(
        self,
        *,
        run_id: str | None = None,
        software_version: str | None = None,
    ) -> "DurableRunArtifacts":
        """Reserve a run directory and synchronously durable pressure CSV.

        This intentionally streams pressure rows rather than accumulating them
        for finalization, so a process or camera failure retains every row
        accepted before that failure.
        """

        identity = ArtifactIdentity(
            ArtifactType.RUN_MANIFEST,
            run_id or ArtifactIdentity.new(ArtifactType.RUN_MANIFEST).artifact_id,
        )
        directory = self.resolve_workspace_path(Path("runs") / identity.artifact_id)
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as error:
            raise self._error(
                "refusing to overwrite an existing run directory",
                "artifact_id",
                "Generate a new run ID before starting.",
            ) from error
        except OSError as error:
            raise self._error(f"cannot create run directory: {error}", "workspace") from error
        try:
            return DurableRunArtifacts(self, identity, directory, software_version)
        except Exception:
            try:
                directory.rmdir()
            except OSError:
                pass
            raise

    def import_legacy(
        self,
        source: Path,
        artifact_type: ArtifactType,
        *,
        frame_size: tuple[int, int] | None = None,
    ) -> ArtifactDocument:
        """Read a legacy file without moving, modifying, or overwriting it."""

        from .legacy_import import LegacyArtifactImporter

        return LegacyArtifactImporter().import_file(source, artifact_type, frame_size=frame_size)

    def export_legacy(self, document: ArtifactDocument, destination: Path) -> None:
        """Write the intentionally narrow historical representation atomically."""

        from .legacy_import import LegacyArtifactImporter

        LegacyArtifactImporter().export_file(document, destination)

    def load(self, artifact_type: ArtifactType, artifact_id: str) -> ArtifactDocument:
        ArtifactIdentity(artifact_type, artifact_id)
        path = self._path_for(artifact_type, artifact_id)
        if not path.is_file():
            raise self._error("artifact does not exist", "artifact_id", "Choose an existing artifact ID.")
        try:
            if artifact_type in _JSON_ARTIFACTS:
                with path.open(encoding="utf-8") as handle:
                    value = json.load(handle)
                return self._from_json_document(value, artifact_type)
            if artifact_type is ArtifactType.ANALYSIS_RESULTS:
                return self._from_analysis_csv(path, artifact_id)
            if artifact_type is ArtifactType.PRESSURE_DATA:
                return self._from_pressure_csv(path, artifact_id)
        except (OSError, json.JSONDecodeError, csv.Error) as error:
            raise self._error(f"cannot read artifact: {error}", "artifact") from error
        raise self._error("artifact type is not persistable", "artifact_type")

    def resolve_workspace_path(self, relative_path: str | Path) -> Path:
        """Resolve a stored reference without permitting workspace escape."""

        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise self._error(
                "path must be relative to the workspace and must not traverse parents",
                "path",
                "Move the referenced file into the workspace and use a relative path.",
            )
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise self._error("path escapes the workspace", "path")
        return resolved

    def _path_for(self, artifact_type: ArtifactType, artifact_id: str) -> Path:
        safe_id = ArtifactIdentity(artifact_type, artifact_id).artifact_id
        if artifact_type is ArtifactType.WORKSPACE:
            return self.resolve_workspace_path(Path("artifacts") / "workspace" / f"{safe_id}.json")
        if artifact_type in {ArtifactType.CALIBRATION, ArtifactType.GEOMETRY}:
            return self.resolve_workspace_path(Path("artifacts") / artifact_type.value / f"{safe_id}.json")
        if artifact_type in {ArtifactType.RUN_MANIFEST, ArtifactType.PRESSURE_DATA}:
            name = "run.json" if artifact_type is ArtifactType.RUN_MANIFEST else "pressure.csv"
            return self.resolve_workspace_path(Path("runs") / safe_id / name)
        if artifact_type in {ArtifactType.ANALYSIS_MANIFEST, ArtifactType.ANALYSIS_RESULTS}:
            name = "analysis.json" if artifact_type is ArtifactType.ANALYSIS_MANIFEST else "angles.csv"
            return self.resolve_workspace_path(Path("analysis") / safe_id / name)
        raise self._error("artifact type is not persistable", "artifact_type")

    def _atomic_create(self, target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            reservation = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise self._error(
                "refusing to overwrite an existing artifact",
                "artifact_id",
                "Generate a new artifact ID or explicitly choose a new destination.",
            ) from error
        else:
            os.close(reservation)

        temporary: Path | None = None
        replaced = False
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            replaced = True
            temporary = None
        except OSError as error:
            raise self._error(
                f"atomic write failed: {error}",
                "artifact",
                "Check workspace permissions and free space, then retry.",
            ) from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            # A failed write or replacement leaves only our empty reservation.
            if not replaced:
                target.unlink(missing_ok=True)

    def _validated_document(self, document: ArtifactDocument) -> ArtifactDocument:
        if not isinstance(document, ArtifactDocument):
            raise self._error("document must be an ArtifactDocument", "document")
        metadata = document.metadata
        if metadata.identity.schema_version != CURRENT_SCHEMA_VERSION:
            require_supported_schema_version(metadata.identity.schema_version)
            raise self._error(
                "schema version has no writer in this application",
                "schema_version",
                "Migrate the document before saving it.",
            )
        payload = self._portable_payload(dict(document.payload), "payload")
        self._validate_payload(metadata.identity.artifact_type, payload)
        return ArtifactDocument(metadata, payload)

    def _json_document(self, document: ArtifactDocument) -> dict[str, Any]:
        metadata = document.metadata
        return {
            "schema_version": metadata.identity.schema_version,
            "artifact_type": metadata.identity.artifact_type.value,
            "artifact_id": metadata.identity.artifact_id,
            "created_at": metadata.created_at.isoformat(),
            "updated_at": metadata.updated_at.isoformat(),
            "software_version": metadata.software_version,
            "payload": dict(document.payload),
        }

    def _from_json_document(self, value: Any, requested_type: ArtifactType) -> ArtifactDocument:
        value = migrate_document(value, requested_type)
        root = self._mapping(value, "artifact")
        version = self._integer(root.get("schema_version"), "schema_version")
        require_supported_schema_version(version)
        artifact_type = self._enum_type(root.get("artifact_type"), "artifact_type")
        if artifact_type is not requested_type:
            raise self._error("artifact type does not match requested type", "artifact_type")
        identity = ArtifactIdentity(artifact_type, self._string(root.get("artifact_id"), "artifact_id"), version)
        metadata = ArtifactMetadata(
            identity,
            self._timestamp(root.get("created_at"), "created_at"),
            self._timestamp(root.get("updated_at"), "updated_at"),
            root.get("software_version"),
        )
        payload = self._portable_payload(self._mapping(root.get("payload"), "payload"), "payload")
        self._validate_payload(artifact_type, payload)
        return ArtifactDocument(metadata, payload)

    def _analysis_csv(self, document: ArtifactDocument) -> str:
        rows = document.payload["rows"]
        output = [",".join(_ANALYSIS_COLUMNS)]
        for row in rows:
            output.append(
                ",".join(
                    self._csv_value(self._analysis_cell(document, row, column))
                    for column in _ANALYSIS_COLUMNS
                )
            )
        return "\n".join(output) + "\n"

    @staticmethod
    def _analysis_cell(document: ArtifactDocument, row: Mapping[str, Any], column: str) -> Any:
        if column == "schema_version":
            return document.metadata.identity.schema_version
        if column == "artifact_id":
            return document.metadata.identity.artifact_id
        if column == "legacy_import":
            return document.payload.get("legacy_import", False)
        return row.get(column)

    def _pressure_csv(self, document: ArtifactDocument) -> str:
        output = [",".join(_PRESSURE_COLUMNS)]
        for row in document.payload["rows"]:
            output.append(
                ",".join(
                    self._csv_value(row.get(column) if column not in {"schema_version", "artifact_id"} else (
                        document.metadata.identity.schema_version if column == "schema_version" else document.metadata.identity.artifact_id
                    ))
                    for column in _PRESSURE_COLUMNS
                )
            )
        return "\n".join(output) + "\n"

    def _from_analysis_csv(self, path: Path, artifact_id: str) -> ArtifactDocument:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            raise self._error("analysis CSV must contain at least one data row", "rows")
        metadata = self._csv_metadata(rows[0], ArtifactType.ANALYSIS_RESULTS, artifact_id)
        legacy_import = rows[0].get("legacy_import") == "true"
        payload = {
            "rows": [self._analysis_row(row, f"rows[{index}]") for index, row in enumerate(rows)],
            **({"legacy_import": True} if legacy_import else {}),
        }
        self._validate_payload(ArtifactType.ANALYSIS_RESULTS, payload)
        return ArtifactDocument(metadata, payload)

    def _from_pressure_csv(self, path: Path, artifact_id: str) -> ArtifactDocument:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        # Aborted/faulted runs may have created and durably flushed only the
        # header before any decoded telemetry arrived.  The companion run
        # manifest is authoritative for its timestamps/completion.
        if not rows:
            now = datetime.now().astimezone()
            metadata = ArtifactMetadata(
                ArtifactIdentity(ArtifactType.PRESSURE_DATA, artifact_id),
                now,
                now,
            )
            return ArtifactDocument(metadata, {"rows": []})
        metadata = self._csv_metadata(rows[0], ArtifactType.PRESSURE_DATA, artifact_id)
        payload = {"rows": [self._pressure_row(row, f"rows[{index}]") for index, row in enumerate(rows)]}
        self._validate_payload(ArtifactType.PRESSURE_DATA, payload)
        return ArtifactDocument(metadata, payload)

    def _csv_metadata(self, row: Mapping[str, str], kind: ArtifactType, artifact_id: str) -> ArtifactMetadata:
        try:
            version = int(row.get("schema_version", ""))
        except ValueError as error:
            raise self._error("must be an integer", "rows[0].schema_version") from error
        require_supported_schema_version(version)
        if row.get("artifact_id") != artifact_id:
            raise self._error("CSV artifact ID does not match its path", "rows[0].artifact_id")
        # CSV data does not duplicate timestamps; its companion manifest is authoritative.
        now = datetime.now().astimezone()
        return ArtifactMetadata(ArtifactIdentity(kind, artifact_id, version), now, now)

    def _validate_payload(self, artifact_type: ArtifactType, payload: Mapping[str, Any]) -> None:
        if artifact_type is ArtifactType.WORKSPACE:
            self._string(payload.get("name"), "payload.name")
        elif artifact_type is ArtifactType.CALIBRATION:
            self._validate_calibration(payload)
        elif artifact_type is ArtifactType.GEOMETRY:
            self._validate_geometry(payload)
        elif artifact_type is ArtifactType.RUN_MANIFEST:
            completion = self._string(payload.get("completion"), "payload.completion")
            if completion not in {member.value for member in RunCompletion}:
                raise self._error("completion must be clean, stopped, aborted, or faulted", "payload.completion")
        elif artifact_type is ArtifactType.ANALYSIS_MANIFEST:
            self._string(payload.get("source_video"), "payload.source_video")
            self._string(payload.get("geometry_artifact_id"), "payload.geometry_artifact_id")
        elif artifact_type is ArtifactType.ANALYSIS_RESULTS:
            rows = self._list(payload.get("rows"), "payload.rows")
            for index, row in enumerate(rows):
                self._validate_analysis_row(
                    self._mapping(row, f"payload.rows[{index}]"),
                    f"payload.rows[{index}]",
                    allow_missing_tip=payload.get("legacy_import") is True,
                )
        elif artifact_type is ArtifactType.PRESSURE_DATA:
            rows = self._list(payload.get("rows"), "payload.rows")
            for index, row in enumerate(rows):
                self._validate_pressure_row(self._mapping(row, f"payload.rows[{index}]"), f"payload.rows[{index}]")

    def _validate_calibration(self, payload: Mapping[str, Any]) -> None:
        model = self._mapping(payload.get("model"), "payload.model")
        model_type = self._string(model.get("type"), "payload.model.type")
        coefficients = self._list(model.get("coeffs"), "payload.model.coeffs")
        expected = 2 if model_type == "linear" else 3 if model_type == "quadratic" else 0
        if not expected:
            raise self._error("model type must be linear or quadratic", "payload.model.type")
        if len(coefficients) != expected:
            raise self._error(f"{model_type} model requires exactly {expected} coefficients", "payload.model.coeffs")
        for index, value in enumerate(coefficients):
            self._number(value, f"payload.model.coeffs[{index}]")
        samples = self._list(payload.get("samples"), "payload.samples")
        if not samples:
            raise self._error("at least one calibration sample is required", "payload.samples")
        for index, sample in enumerate(samples):
            pair = self._list(sample, f"payload.samples[{index}]")
            if len(pair) != 2:
                raise self._error("sample must be [known_pressure_kPa, measured_voltage]", f"payload.samples[{index}]")
            self._number(pair[0], f"payload.samples[{index}][0]")
            self._number(pair[1], f"payload.samples[{index}][1]")

    def _validate_geometry(self, payload: Mapping[str, Any]) -> None:
        size = self._mapping(payload.get("frame_size"), "payload.frame_size")
        width = self._integer(size.get("width"), "payload.frame_size.width")
        height = self._integer(size.get("height"), "payload.frame_size.height")
        if width <= 0 or height <= 0:
            raise self._error("frame dimensions must be positive", "payload.frame_size")
        for name in ("base_point", "initial_tip_point"):
            point = self._mapping(payload.get(name), f"payload.{name}")
            x, y = self._number(point.get("x"), f"payload.{name}.x"), self._number(point.get("y"), f"payload.{name}.y")
            if not (0 <= x < width and 0 <= y < height):
                raise self._error("point is outside frame bounds", f"payload.{name}")
        roi = self._mapping(payload.get("roi"), "payload.roi")
        left, top = self._number(roi.get("left"), "payload.roi.left"), self._number(roi.get("top"), "payload.roi.top")
        right, bottom = self._number(roi.get("right"), "payload.roi.right"), self._number(roi.get("bottom"), "payload.roi.bottom")
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise self._error("ROI must be non-empty and within frame bounds", "payload.roi")

    def _validate_analysis_row(self, row: Mapping[str, Any], path: str, *, allow_missing_tip: bool = False) -> None:
        self._integer(row.get("frame_index"), f"{path}.frame_index")
        state = self._string(row.get("detection_state"), f"{path}.detection_state")
        if state not in {"detected", "manual", "missing", "held"}:
            raise self._error("detection_state is invalid", f"{path}.detection_state")
        self._number(row.get("video_time_seconds"), f"{path}.video_time_seconds")
        self._number(row.get("confidence"), f"{path}.confidence")
        if state == "missing":
            if any(row.get(key) is not None for key in ("tip_x", "tip_y", "actuator_angle_degrees")):
                raise self._error("missing detection must not carry a tip or angle", path)
        else:
            self._number(row.get("actuator_angle_degrees"), f"{path}.actuator_angle_degrees")
            for key in ("tip_x", "tip_y"):
                if not allow_missing_tip or row.get(key) is not None:
                    self._number(row.get(key), f"{path}.{key}")

    def _validate_pressure_row(self, row: Mapping[str, Any], path: str) -> None:
        self._number(row.get("time_s"), f"{path}.time_s")
        self._number(row.get("volts"), f"{path}.volts")
        if row.get("pressure_kPa") is not None:
            self._number(row.get("pressure_kPa"), f"{path}.pressure_kPa")

    def _portable_payload(self, value: Any, path: str, key: str | None = None) -> Any:
        if isinstance(value, Mapping):
            return {str(child_key): self._portable_payload(child, f"{path}.{child_key}", str(child_key)) for child_key, child in value.items()}
        if isinstance(value, list):
            return [self._portable_payload(child, f"{path}[{index}]", key) for index, child in enumerate(value)]
        if key in _PATH_KEYS or (key is not None and key.endswith("_path")):
            if not isinstance(value, str):
                raise self._error("path reference must be a string", path)
            candidate = Path(value).expanduser()
            if candidate.is_absolute():
                try:
                    return candidate.resolve().relative_to(self.root).as_posix()
                except ValueError as error:
                    raise self._error("absolute path is outside the workspace", path) from error
            self.resolve_workspace_path(candidate)
            return candidate.as_posix()
        return value

    def _analysis_row(self, row: Mapping[str, str], path: str) -> dict[str, Any]:
        return {
            "frame_index": self._csv_integer(row.get("frame_index"), f"{path}.frame_index"),
            "video_time_seconds": self._csv_number(row.get("video_time_seconds"), f"{path}.video_time_seconds"),
            "tip_x": self._csv_optional_number(row.get("tip_x"), f"{path}.tip_x"),
            "tip_y": self._csv_optional_number(row.get("tip_y"), f"{path}.tip_y"),
            "actuator_angle_degrees": self._csv_optional_number(row.get("actuator_angle_degrees"), f"{path}.actuator_angle_degrees"),
            "detection_state": self._string(row.get("detection_state"), f"{path}.detection_state"),
            "confidence": self._csv_number(row.get("confidence"), f"{path}.confidence"),
            "correction_applied": row.get("correction_applied") == "true",
        }

    def _pressure_row(self, row: Mapping[str, str], path: str) -> dict[str, Any]:
        return {
            "time_s": self._csv_number(row.get("time_s"), f"{path}.time_s"),
            "volts": self._csv_number(row.get("volts"), f"{path}.volts"),
            "pressure_kPa": self._csv_optional_number(row.get("pressure_kPa"), f"{path}.pressure_kPa"),
        }

    @staticmethod
    def _csv_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return str(value).lower()
        return str(value)

    @staticmethod
    def _error(message: str, path: str, guidance: str | None = None) -> ArtifactPersistenceError:
        return ArtifactPersistenceError(ErrorCode.ARTIFACT_INVALID, message, path, guidance)

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
        if not isinstance(value, int) or isinstance(value, bool):
            raise self._error("must be an integer", path)
        return value

    def _number(self, value: Any, path: str) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise self._error("must be a finite number", path)
        return float(value)

    def _optional_number(self, value: Any, path: str) -> float | None:
        return None if value in (None, "") else self._number(value, path)

    def _csv_integer(self, value: str | None, path: str) -> int:
        try:
            parsed = int(value or "")
        except ValueError as error:
            raise self._error("must be an integer", path) from error
        return self._integer(parsed, path)

    def _csv_number(self, value: str | None, path: str) -> float:
        try:
            parsed = float(value or "")
        except ValueError as error:
            raise self._error("must be a finite number", path) from error
        return self._number(parsed, path)

    def _csv_optional_number(self, value: str | None, path: str) -> float | None:
        return None if value in (None, "") else self._csv_number(value, path)

    def _timestamp(self, value: Any, path: str) -> datetime:
        if not isinstance(value, str):
            raise self._error("must be an ISO-8601 timestamp", path)
        try:
            timestamp = datetime.fromisoformat(value)
        except ValueError as error:
            raise self._error("must be an ISO-8601 timestamp", path) from error
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise self._error("must include a timezone", path)
        return timestamp

    def _enum_type(self, value: Any, path: str) -> ArtifactType:
        try:
            return ArtifactType(value)
        except ValueError as error:
            raise self._error("unknown artifact type", path) from error


@dataclass
class DurableRunArtifacts:
    """Run-scoped pressure sink with per-row flush/fsync durability."""

    store: ArtifactFileStore
    identity: ArtifactIdentity
    directory: Path
    software_version: str | None = None
    _io_lock: RLock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._io_lock = RLock()
        self._pressure_path = self.directory / "pressure.csv"
        self._handle: TextIO = self._pressure_path.open("x", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._handle, fieldnames=_PRESSURE_COLUMNS)
        self._writer.writeheader()
        self._flush()
        self._closed = False
        self._manifest_saved = False

    @property
    def run_id(self) -> str:
        return self.identity.artifact_id

    @property
    def pressure_path(self) -> Path:
        return self._pressure_path

    def append_pressure(self, *, time_s: float, volts: float, pressure_kpa: float | None) -> None:
        with self._io_lock:
            if self._closed:
                raise RuntimeError("pressure artifacts are already finalized")
            self.store._validate_pressure_row(
                {"time_s": time_s, "volts": volts, "pressure_kPa": pressure_kpa},
                "pressure",
            )
            self._writer.writerow(
                {
                    "schema_version": self.identity.schema_version,
                    "artifact_id": self.identity.artifact_id,
                    "time_s": time_s,
                    "volts": volts,
                    "pressure_kPa": pressure_kpa,
                }
            )
            self._flush()

    def finalize(self, payload: Mapping[str, Any]) -> Path:
        """Close pressure output then atomically write exactly one run manifest."""

        with self._io_lock:
            if self._manifest_saved:
                return self.directory / "run.json"
            if not self._closed:
                self._flush()
                self._handle.close()
                self._closed = True
            now = datetime.now().astimezone()
            self.store.save(
                ArtifactDocument(
                    ArtifactMetadata(self.identity, now, now, self.software_version),
                    dict(payload),
                )
            )
            self._manifest_saved = True
            return self.directory / "run.json"

    def _flush(self) -> None:
        self._handle.flush()
        os.fsync(self._handle.fileno())


def migrate_document(value: Any, artifact_type: ArtifactType) -> Any:
    """Apply the explicit migrator for a supported JSON artifact version.

    Version one is intentionally an identity migrator.  Future versions must
    add a new entry rather than relying on a reader to guess older fields.
    """

    if not isinstance(value, Mapping):
        raise ArtifactPersistenceError(ErrorCode.ARTIFACT_INVALID, "must be an object", "artifact")
    version = value.get("schema_version")
    require_supported_schema_version(version)
    migrator = _JSON_MIGRATORS[artifact_type].get(version)
    if migrator is None:
        raise ArtifactPersistenceError(
            ErrorCode.ARTIFACT_INVALID,
            "no migrator exists for this artifact schema version",
            "schema_version",
            "Upgrade the application or migrate the artifact with a supported version.",
        )
    return migrator(value)


def _migrate_v1(document: Mapping[str, Any]) -> Mapping[str, Any]:
    return document


_JSON_MIGRATORS = {artifact_type: {1: _migrate_v1} for artifact_type in _JSON_ARTIFACTS}
