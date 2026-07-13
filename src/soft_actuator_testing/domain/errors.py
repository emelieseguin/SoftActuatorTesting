"""Structured errors returned by the Qt-free domain contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorCode(str, Enum):
    """Stable categories for errors surfaced by application services."""

    VALIDATION = "validation"
    NON_FINITE_VALUE = "non_finite_value"
    CALIBRATION_INVALID = "calibration_invalid"
    GEOMETRY_INVALID = "geometry_invalid"
    ILLEGAL_TRANSITION = "illegal_transition"
    ARTIFACT_INVALID = "artifact_invalid"
    ARTIFACT_PUBLICATION_UNCERTAIN = "artifact_publication_uncertain"


@dataclass(frozen=True)
class DomainError(ValueError):
    """An actionable domain error with a machine-readable category and path."""

    code: ErrorCode
    message: str
    field_path: str | None = None
    guidance: str | None = None

    def __post_init__(self) -> None:
        ValueError.__init__(self, str(self))

    def __str__(self) -> str:
        path = f"{self.field_path}: " if self.field_path else ""
        guidance = f" {self.guidance}" if self.guidance else ""
        return f"{path}{self.message}.{guidance}".rstrip()


class CalibrationError(DomainError):
    """A calibration sample, model, or fit is invalid."""


class GeometryError(DomainError):
    """Geometry does not describe a valid region in a video frame."""


class StateTransitionError(DomainError):
    """A requested run-state transition is not legal."""
