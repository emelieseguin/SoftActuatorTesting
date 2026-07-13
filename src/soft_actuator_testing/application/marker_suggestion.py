"""Qt-free, OpenCV-free guided red-marker suggestion engine.

Complements (never replaces) manual base/tip/ROI selection in
``VideoGeometryWorkflow``: given a single frame, an optional ROI, an optional
base point, and an optional previously-confirmed tip, this module scores
candidate red blobs and returns an explainable, ranked, immutable result.

Frame pixel analysis (HSV conversion, morphology, contour extraction) is
delegated to a replaceable :class:`RedMarkerFrameDetector` adapter — this
module never imports ``cv2`` or ``numpy``; only
``infrastructure/red_marker_detector.py`` does, mirroring the
``VideoFrameSource``/``OpenCvVideoFileReader`` split documented in
``docs/architecture/video-geometry-workflow.md``.

No detection ever runs automatically or silently replaces an operator's
selection: callers explicitly request a scan, explicitly accept a candidate,
and can always correct the result manually afterwards through the geometry
workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import hypot, isfinite, pi
from threading import Event, Lock
from typing import Any, Protocol, runtime_checkable

from soft_actuator_testing.application.services import CancellationToken
from soft_actuator_testing.domain.errors import ErrorCode, GeometryError
from soft_actuator_testing.domain.geometry import FrameSize, NormalizedRoi, PixelPoint

_DEFAULT_AMBIGUITY_MARGIN = 0.05


def _clamp01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _validate_unit_interval(value: float, field_path: str) -> None:
    if not isfinite(value) or not (0.0 <= value <= 1.0):
        raise GeometryError(ErrorCode.GEOMETRY_INVALID, "value must be finite and in the range [0, 1]", field_path)


class MarkerSuggestionCancelled(RuntimeError):
    """Raised when a caller-supplied cancellation token aborts a scan."""


class MarkerSuggestionCancellation:
    """Thread-safe :class:`CancellationToken` for a bounded background scan.

    Mirrors ``calibration_workflow.CaptureCancellation``'s minimal
    ``threading.Event``-backed design so a UI-layer background worker can
    request bounded cancellation of a detection scan without this module
    (or the worker) ever touching Qt.
    """

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class MarkerSuggestionState(str, Enum):
    """Explicit, never-fabricated detection outcome for one suggestion request."""

    NO_DETECTION = "no_detection"
    AMBIGUOUS = "ambiguous"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class HsvRedThresholds:
    """Configurable dual-hue HSV red-detection settings; all fields are explainable.

    Red wraps the OpenCV hue circle (0-179), so two bands are combined:
    ``[0, hue_low_max]`` and ``[hue_high_min, 179]``.
    """

    hue_low_max: int = 10
    hue_high_min: int = 170
    saturation_min: int = 120
    value_min: int = 120
    saturation_max: int = 255
    value_max: int = 255
    morphology_kernel_size: int = 3
    morph_open_iterations: int = 1
    morph_close_iterations: int = 1
    min_area_pixels: float = 40.0
    min_circularity: float = 0.5
    exclusion_radius_pixels: float = 0.0
    max_candidates: int = 5
    ambiguity_margin: float = _DEFAULT_AMBIGUITY_MARGIN

    def __post_init__(self) -> None:
        if not (0 <= self.hue_low_max <= 179):
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "hue_low_max must be within [0, 179]", "hue_low_max")
        if not (0 <= self.hue_high_min <= 179):
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "hue_high_min must be within [0, 179]", "hue_high_min")
        if self.hue_low_max >= self.hue_high_min:
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "hue_low_max must be less than hue_high_min so the two red bands do not overlap",
                "hue_low_max",
            )
        for name, value in (
            ("saturation_min", self.saturation_min),
            ("saturation_max", self.saturation_max),
            ("value_min", self.value_min),
            ("value_max", self.value_max),
        ):
            if not (0 <= value <= 255):
                raise GeometryError(ErrorCode.GEOMETRY_INVALID, f"{name} must be within [0, 255]", name)
        if self.saturation_min > self.saturation_max:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "saturation_min must not exceed saturation_max", "saturation_min")
        if self.value_min > self.value_max:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "value_min must not exceed value_max", "value_min")
        if self.morphology_kernel_size < 1:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "morphology_kernel_size must be at least 1", "morphology_kernel_size")
        if self.morph_open_iterations < 0 or self.morph_close_iterations < 0:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "morphology iterations cannot be negative", "morph_open_iterations")
        if self.min_area_pixels <= 0:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "min_area_pixels must be positive", "min_area_pixels")
        if not (0.0 <= self.min_circularity <= 1.0):
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "min_circularity must be within [0, 1]", "min_circularity")
        if self.exclusion_radius_pixels < 0:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "exclusion_radius_pixels cannot be negative", "exclusion_radius_pixels")
        if self.max_candidates < 1:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "max_candidates must be at least 1", "max_candidates")
        if not (0.0 <= self.ambiguity_margin <= 1.0):
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "ambiguity_margin must be within [0, 1]", "ambiguity_margin")

    def as_dict(self) -> dict[str, Any]:
        """Plain-dict form suitable for versioned persistence (no fabricated keys)."""

        return {
            "hue_low_max": self.hue_low_max,
            "hue_high_min": self.hue_high_min,
            "saturation_min": self.saturation_min,
            "value_min": self.value_min,
            "saturation_max": self.saturation_max,
            "value_max": self.value_max,
            "morphology_kernel_size": self.morphology_kernel_size,
            "morph_open_iterations": self.morph_open_iterations,
            "morph_close_iterations": self.morph_close_iterations,
            "min_area_pixels": self.min_area_pixels,
            "min_circularity": self.min_circularity,
            "exclusion_radius_pixels": self.exclusion_radius_pixels,
            "max_candidates": self.max_candidates,
            "ambiguity_margin": self.ambiguity_margin,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> HsvRedThresholds:
        fields = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in payload.items() if key in fields})


@dataclass(frozen=True)
class RedBlob:
    """One raw connected-component candidate produced by pixel-level analysis.

    Produced only by a :class:`RedMarkerFrameDetector` adapter (OpenCV in
    production); this dataclass itself holds plain numbers/geometry so the
    scoring/ranking logic below stays OpenCV-free and unit-testable.
    """

    centroid: PixelPoint
    bounding_box: NormalizedRoi
    area_pixels: float
    perimeter_pixels: float
    redness_score: float

    def __post_init__(self) -> None:
        if not isfinite(self.area_pixels) or self.area_pixels <= 0:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "blob area must be finite and positive", "area_pixels")
        if not isfinite(self.perimeter_pixels) or self.perimeter_pixels <= 0:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "blob perimeter must be finite and positive", "perimeter_pixels")
        _validate_unit_interval(self.redness_score, "redness_score")

    @property
    def circularity(self) -> float:
        """``4*pi*area / perimeter**2``, clamped to 1.0 (pixelation can exceed it slightly)."""

        return min(1.0, (4.0 * pi * self.area_pixels) / (self.perimeter_pixels**2))


@dataclass(frozen=True)
class RedMarkerScan:
    """Raw per-frame detector output before scoring/ranking/ambiguity resolution."""

    frame_size: FrameSize
    roi: NormalizedRoi | None
    blobs: tuple[RedBlob, ...]
    mask_preview: Any


@runtime_checkable
class RedMarkerFrameDetector(Protocol):
    """Replaceable frame-analysis boundary; only OpenCV adapters cross it."""

    def scan(
        self,
        frame: Any,
        thresholds: HsvRedThresholds,
        roi: NormalizedRoi | None,
        *,
        cancellation: CancellationToken | None = None,
    ) -> RedMarkerScan: ...


@dataclass(frozen=True)
class MarkerSuggestionCandidate:
    """A ranked, explainable red-marker tip candidate."""

    rank: int
    tip_point: PixelPoint
    bounding_box: NormalizedRoi
    area_pixels: float
    circularity: float
    redness_score: float
    size_score: float
    circularity_score: float
    distance_from_base_score: float | None
    temporal_continuity_score: float | None
    confidence: float
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "candidate rank must be at least 1", "rank")
        if not self.reasons:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "a candidate must carry at least one explainable reason", "reasons")
        _validate_unit_interval(self.confidence, "confidence")
        _validate_unit_interval(self.redness_score, "redness_score")
        _validate_unit_interval(self.size_score, "size_score")
        _validate_unit_interval(self.circularity_score, "circularity_score")
        if self.distance_from_base_score is not None:
            _validate_unit_interval(self.distance_from_base_score, "distance_from_base_score")
        if self.temporal_continuity_score is not None:
            _validate_unit_interval(self.temporal_continuity_score, "temporal_continuity_score")


@dataclass(frozen=True)
class MarkerSuggestionResult:
    """Immutable outcome of one suggestion request; never reports a stale tip.

    ``frame_index``/``sequence`` let a caller detect and discard a result that
    resolved after a newer request was already dispatched (see
    :meth:`MarkerSuggestionWorkflow.is_current`).
    """

    state: MarkerSuggestionState
    frame_index: int
    sequence: int
    candidates: tuple[MarkerSuggestionCandidate, ...]
    mask_preview: Any
    roi: NormalizedRoi | None
    thresholds: HsvRedThresholds
    message: str

    def __post_init__(self) -> None:
        if self.frame_index < 0:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "frame_index cannot be negative", "frame_index")
        if self.state is MarkerSuggestionState.NO_DETECTION and self.candidates:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "a no-detection result must not carry candidates", "candidates")
        if self.state is not MarkerSuggestionState.NO_DETECTION and not self.candidates:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, f"a {self.state.value} result requires at least one candidate", "candidates")
        if self.state is MarkerSuggestionState.AMBIGUOUS and len(self.candidates) < 2:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "an ambiguous result requires at least two candidates", "candidates")

    @property
    def best_candidate(self) -> MarkerSuggestionCandidate | None:
        return self.candidates[0] if self.candidates else None


class FakeRedMarkerFrameDetector:
    """Deterministic, hardware/OpenCV-free :class:`RedMarkerFrameDetector` double.

    Scans are registered ahead of time by frame identity (``id(frame)``); tests
    typically pass a small sentinel object (e.g. a plain string or tuple) as
    the "frame" to key a specific pre-built :class:`RedMarkerScan`.
    """

    def __init__(self) -> None:
        self._catalog: dict[int, RedMarkerScan] = {}
        self.scan_calls: list[Any] = []
        self.cancel_before_scan: set[int] = set()

    def register(self, frame: Any, scan: RedMarkerScan) -> None:
        self._catalog[id(frame)] = scan

    def scan(
        self,
        frame: Any,
        thresholds: HsvRedThresholds,
        roi: NormalizedRoi | None,
        *,
        cancellation: CancellationToken | None = None,
    ) -> RedMarkerScan:
        del thresholds, roi  # the fake returns a pre-built scan verbatim
        self.scan_calls.append(frame)
        if id(frame) in self.cancel_before_scan and cancellation is not None and cancellation.is_cancelled():
            raise MarkerSuggestionCancelled("marker suggestion scan was cancelled")
        result = self._catalog.get(id(frame))
        if result is None:
            raise GeometryError(
                ErrorCode.GEOMETRY_INVALID,
                "no fake scan is registered for this frame",
                "frame",
                "Call register() with this frame before scanning it in a test.",
            )
        return result


def _distance(a: PixelPoint, b: PixelPoint) -> float:
    return hypot(a.x - b.x, a.y - b.y)


class MarkerSuggestionWorkflow:
    """Score/rank red-marker candidates for one frame at a time; Qt/cv2-free.

    Holds only the settings and light session memory (last confirmed tip,
    request sequence) needed to make suggestions explainable and to prevent a
    late-arriving background result from ever being treated as current.
    """

    def __init__(self, detector: RedMarkerFrameDetector, *, thresholds: HsvRedThresholds | None = None) -> None:
        self._detector = detector
        self._thresholds = thresholds or HsvRedThresholds()
        self._last_confirmed_tip: PixelPoint | None = None
        self._sequence = 0
        self._state_lock = Lock()

    @property
    def thresholds(self) -> HsvRedThresholds:
        with self._state_lock:
            return self._thresholds

    def set_thresholds(self, thresholds: HsvRedThresholds) -> None:
        with self._state_lock:
            self._thresholds = thresholds

    @property
    def sequence(self) -> int:
        """The most recently issued request sequence number."""

        with self._state_lock:
            return self._sequence

    def note_confirmed_tip(self, point: PixelPoint | None) -> None:
        """Record (or clear) the operator-confirmed tip used for temporal continuity."""

        with self._state_lock:
            self._last_confirmed_tip = point

    def is_current(self, result: MarkerSuggestionResult) -> bool:
        """``False`` once a newer :meth:`suggest` call has been issued.

        Callers (background workers) must check this before applying a result
        so a stale scan is never surfaced as the current tip.
        """

        with self._state_lock:
            return result.sequence == self._sequence

    def suggest(
        self,
        frame: Any,
        *,
        frame_index: int,
        frame_size: FrameSize,
        roi: NormalizedRoi | None = None,
        base_point: PixelPoint | None = None,
        previous_tip: PixelPoint | None = None,
        cancellation: CancellationToken | None = None,
    ) -> MarkerSuggestionResult:
        if frame_index < 0:
            raise GeometryError(ErrorCode.GEOMETRY_INVALID, "frame_index cannot be negative", "frame_index")
        with self._state_lock:
            self._sequence += 1
            sequence = self._sequence
            thresholds = self._thresholds
            continuity_reference = previous_tip if previous_tip is not None else self._last_confirmed_tip

        scan = self._detector.scan(frame, thresholds, roi, cancellation=cancellation)
        if cancellation is not None and cancellation.is_cancelled():
            raise MarkerSuggestionCancelled("marker suggestion scan was cancelled")

        frame_diagonal = hypot(frame_size.width, frame_size.height)
        kept: list[tuple[RedBlob, float, float, float | None, float | None, float, tuple[str, ...]]] = []
        excluded_by_geometry = 0
        for blob in scan.blobs:
            circularity = blob.circularity
            if blob.area_pixels < thresholds.min_area_pixels or circularity < thresholds.min_circularity:
                excluded_by_geometry += 1
                continue
            if (
                base_point is not None
                and thresholds.exclusion_radius_pixels > 0
                and _distance(blob.centroid, base_point) < thresholds.exclusion_radius_pixels
            ):
                excluded_by_geometry += 1
                continue

            reasons: list[str] = [
                f"redness {blob.redness_score:.2f} of the matched dual-hue mask",
                f"area {blob.area_pixels:.0f}px (minimum {thresholds.min_area_pixels:.0f}px)",
                f"circularity {circularity:.2f} (minimum {thresholds.min_circularity:.2f})",
            ]
            size_score = _clamp01(blob.area_pixels / (thresholds.min_area_pixels * 4.0))
            circularity_score = circularity

            distance_from_base_score: float | None = None
            if base_point is not None:
                distance = _distance(blob.centroid, base_point)
                distance_from_base_score = _clamp01(distance / frame_diagonal) if frame_diagonal > 0 else 0.0
                reasons.append(f"{distance:.0f}px from the base point (farther favored as the arm tip)")

            temporal_continuity_score: float | None = None
            if continuity_reference is not None:
                travel = _distance(blob.centroid, continuity_reference)
                max_travel = frame_diagonal * 0.25 if frame_diagonal > 0 else 1.0
                temporal_continuity_score = _clamp01(1.0 - travel / max_travel) if max_travel > 0 else 0.0
                reasons.append(f"{travel:.0f}px from the previous confirmed tip (temporal continuity)")

            weights: list[tuple[float, float]] = [
                (0.30, blob.redness_score),
                (0.15, size_score),
                (0.15, circularity_score),
            ]
            if distance_from_base_score is not None:
                weights.append((0.20, distance_from_base_score))
            if temporal_continuity_score is not None:
                weights.append((0.20, temporal_continuity_score))
            weight_total = sum(weight for weight, _ in weights)
            confidence = _clamp01(sum(weight * value for weight, value in weights) / weight_total)

            kept.append(
                (
                    blob,
                    size_score,
                    circularity_score,
                    distance_from_base_score,
                    temporal_continuity_score,
                    confidence,
                    tuple(reasons),
                )
            )

        kept.sort(key=lambda item: item[5], reverse=True)
        kept = kept[: thresholds.max_candidates]

        candidates = tuple(
            MarkerSuggestionCandidate(
                rank=index + 1,
                tip_point=blob.centroid,
                bounding_box=blob.bounding_box,
                area_pixels=blob.area_pixels,
                circularity=blob.circularity,
                redness_score=blob.redness_score,
                size_score=size_score,
                circularity_score=circularity_score,
                distance_from_base_score=distance_from_base_score,
                temporal_continuity_score=temporal_continuity_score,
                confidence=confidence,
                reasons=reasons,
            )
            for index, (blob, size_score, circularity_score, distance_from_base_score, temporal_continuity_score, confidence, reasons) in enumerate(kept)
        )

        if not candidates:
            state = MarkerSuggestionState.NO_DETECTION
            total_raw = len(scan.blobs)
            if total_raw == 0:
                message = "No red pixels matched the current dual-hue thresholds."
            else:
                message = (
                    f"{total_raw} red blob(s) found but all were excluded by size, circularity, "
                    "or the base-point exclusion radius."
                )
        elif len(candidates) >= 2 and (candidates[0].confidence - candidates[1].confidence) < thresholds.ambiguity_margin:
            state = MarkerSuggestionState.AMBIGUOUS
            message = (
                f"{len(candidates)} candidates are within {thresholds.ambiguity_margin:.2f} confidence of each "
                "other; review the ranked list and mask preview before accepting one."
            )
        else:
            state = MarkerSuggestionState.RESOLVED
            message = f"Top candidate confidence {candidates[0].confidence:.2f}."
        if excluded_by_geometry and state is not MarkerSuggestionState.NO_DETECTION:
            message = f"{message} ({excluded_by_geometry} additional blob(s) excluded by filters.)"

        return MarkerSuggestionResult(
            state=state,
            frame_index=frame_index,
            sequence=sequence,
            candidates=candidates,
            mask_preview=scan.mask_preview,
            roi=roi,
            thresholds=thresholds,
            message=message,
        )


__all__ = [
    "FakeRedMarkerFrameDetector",
    "HsvRedThresholds",
    "MarkerSuggestionCancelled",
    "MarkerSuggestionCandidate",
    "MarkerSuggestionResult",
    "MarkerSuggestionState",
    "MarkerSuggestionWorkflow",
    "RedBlob",
    "RedMarkerFrameDetector",
    "RedMarkerScan",
]
