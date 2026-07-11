"""External-system adapters and persistence implementations."""

from .artifact_store import ArtifactFileStore, ArtifactPersistenceError
from .legacy_import import LegacyArtifactImporter
from .serial_adapter import (
    AcknowledgementFrame,
    CommandReceipt,
    CommandState,
    DiagnosticFrame,
    ErrorFrame,
    ParserProfile,
    PySerialTransportFactory,
    RunMarkerFrame,
    SerialAdapter,
    SerialConnectionConfig,
    SerialPort,
    SerialTextParser,
    TelemetryFrame,
    legacy_field_three_unconfirmed_profile,
)
from .workspace import JsonWorkspaceSettings

__all__ = [
    "AcknowledgementFrame",
    "ArtifactFileStore",
    "ArtifactPersistenceError",
    "CommandReceipt",
    "CommandState",
    "DiagnosticFrame",
    "ErrorFrame",
    "JsonWorkspaceSettings",
    "LegacyArtifactImporter",
    "ParserProfile",
    "PySerialTransportFactory",
    "RunMarkerFrame",
    "SerialAdapter",
    "SerialConnectionConfig",
    "SerialPort",
    "SerialTextParser",
    "TelemetryFrame",
    "legacy_field_three_unconfirmed_profile",
]
