from __future__ import annotations

from datetime import datetime, timezone

import pytest

from soft_actuator_testing.domain.artifacts import (
    ArtifactIdentity,
    ArtifactMetadata,
    ArtifactType,
    CURRENT_SCHEMA_VERSION,
    require_supported_schema_version,
)
from soft_actuator_testing.domain.errors import DomainError, ErrorCode


def test_artifact_identity_is_versioned_and_collision_resistant() -> None:
    first = ArtifactIdentity.new(ArtifactType.CALIBRATION)
    second = ArtifactIdentity.new(ArtifactType.CALIBRATION)

    assert first.schema_version == CURRENT_SCHEMA_VERSION
    assert first.artifact_id != second.artifact_id
    assert first.artifact_id.startswith("calibration_")


def test_artifact_metadata_requires_aware_monotonic_timestamps() -> None:
    identity = ArtifactIdentity.new(ArtifactType.GEOMETRY)
    now = datetime.now(timezone.utc)
    assert ArtifactMetadata(identity, now, now).identity == identity
    with pytest.raises(DomainError, match="timezone-aware"):
        ArtifactMetadata(identity, datetime.now(), now)


def test_newer_schema_versions_fail_closed() -> None:
    assert require_supported_schema_version(CURRENT_SCHEMA_VERSION) == CURRENT_SCHEMA_VERSION
    with pytest.raises(DomainError, match="newer"):
        require_supported_schema_version(CURRENT_SCHEMA_VERSION + 1)


def test_artifact_envelope_rejects_wrong_runtime_types() -> None:
    with pytest.raises(DomainError) as invalid_type:
        ArtifactIdentity("calibration", "calibration_one")  # type: ignore[arg-type]
    assert invalid_type.value.code is ErrorCode.ARTIFACT_INVALID

    identity = ArtifactIdentity.new(ArtifactType.CALIBRATION)
    with pytest.raises(DomainError) as invalid_timestamp:
        ArtifactMetadata(identity, "2026-01-01", datetime.now(timezone.utc))  # type: ignore[arg-type]
    assert invalid_timestamp.value.field_path == "created_at"

    now = datetime.now(timezone.utc)
    with pytest.raises(DomainError) as invalid_version:
        ArtifactMetadata(identity, now, now, software_version=0)  # type: ignore[arg-type]
    assert invalid_version.value.field_path == "software_version"
