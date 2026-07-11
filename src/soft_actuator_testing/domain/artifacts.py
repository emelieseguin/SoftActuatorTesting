"""Versioned artifact identities and measurement units."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re
from uuid import uuid4

from .errors import DomainError, ErrorCode


CURRENT_SCHEMA_VERSION = 1


class ArtifactType(str, Enum):
    CALIBRATION = "calibration"
    GEOMETRY = "geometry"
    RUN_MANIFEST = "run_manifest"
    ANALYSIS_MANIFEST = "analysis_manifest"
    ANALYSIS_RESULTS = "analysis_results"
    WORKSPACE = "workspace"
    PRESSURE_DATA = "pressure_data"


class Unit(str, Enum):
    VOLT = "V"
    KILOPASCAL = "kPa"
    SECOND = "s"
    PIXEL = "px"
    DEGREE = "deg"


@dataclass(frozen=True)
class ArtifactIdentity:
    """Stable identity carried by each versioned persisted artifact."""

    artifact_type: ArtifactType
    artifact_id: str
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_id, str) or not self.artifact_id.strip():
            raise DomainError(
                ErrorCode.ARTIFACT_INVALID,
                "artifact_id must not be empty",
                "artifact_id",
                "Generate a collision-resistant ID before persisting the artifact.",
            )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.artifact_id) or ".." in self.artifact_id:
            raise DomainError(
                ErrorCode.ARTIFACT_INVALID,
                "artifact_id contains unsafe path characters",
                "artifact_id",
                "Use a generated artifact ID or letters, digits, dot, underscore, and hyphen only.",
            )
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version < 1
        ):
            raise DomainError(
                ErrorCode.ARTIFACT_INVALID,
                "schema_version must be a positive integer",
                "schema_version",
            )

    @classmethod
    def new(
        cls,
        artifact_type: ArtifactType,
        schema_version: int = CURRENT_SCHEMA_VERSION,
    ) -> ArtifactIdentity:
        return cls(artifact_type, f"{artifact_type.value}_{uuid4().hex}", schema_version)


def require_supported_schema_version(schema_version: int) -> int:
    """Fail closed when a document is newer than this application understands."""

    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version < 1:
        raise DomainError(
            ErrorCode.ARTIFACT_INVALID,
            "schema_version must be a positive integer",
            "schema_version",
        )
    if schema_version > CURRENT_SCHEMA_VERSION:
        raise DomainError(
            ErrorCode.ARTIFACT_INVALID,
            "artifact schema_version is newer than this application supports",
            "schema_version",
            "Upgrade the application before opening this artifact.",
        )
    return schema_version


@dataclass(frozen=True)
class ArtifactMetadata:
    """Creation/update metadata required alongside an artifact identity."""

    identity: ArtifactIdentity
    created_at: datetime
    updated_at: datetime
    software_version: str | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise DomainError(
                    ErrorCode.ARTIFACT_INVALID,
                    f"{field_name} must be timezone-aware",
                    field_name,
                    "Use a UTC timestamp.",
                )
        if self.updated_at < self.created_at:
            raise DomainError(
                ErrorCode.ARTIFACT_INVALID,
                "updated_at cannot precede created_at",
                "updated_at",
            )

    @classmethod
    def now(cls, identity: ArtifactIdentity, software_version: str | None = None) -> ArtifactMetadata:
        timestamp = datetime.now(timezone.utc)
        return cls(identity, timestamp, timestamp, software_version)
