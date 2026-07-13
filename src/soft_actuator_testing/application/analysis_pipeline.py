"""Qt-free authoritative finalized-video analysis and bounded live previews."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from pathlib import Path
from threading import Event, Lock
from typing import Any, Iterable
from uuid import uuid4

from soft_actuator_testing.application.marker_suggestion import (
    HsvRedThresholds,
    MarkerSuggestionCancelled,
    MarkerSuggestionState,
    MarkerSuggestionWorkflow,
    RedMarkerFrameDetector,
)
from soft_actuator_testing.application.services import ArtifactDocument, ArtifactStore, CancellationToken
from soft_actuator_testing.application.video_geometry_workflow import VideoFrameSource, VideoProbeCancelled
from soft_actuator_testing.domain.analysis import (
    AnalysisFrameResult,
    DetectionState,
    MarkerCandidate,
    MarkerDetectionResult,
)
from soft_actuator_testing.domain.artifacts import ArtifactIdentity, ArtifactMetadata, ArtifactType
from soft_actuator_testing.domain.errors import DomainError, ErrorCode, GeometryError
from soft_actuator_testing.domain.geometry import PixelPoint, VideoGeometry


class AnalysisCompletion(str, Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TRUNCATED = "truncated"


class AnalysisCancellation:
    """Thread-safe cancellation token checked at every frame boundary."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


@dataclass(frozen=True)
class AnalysisRunResult:
    """Immutable output from one finalized-video pass.

    Cancelled and truncated passes are coherent contiguous prefixes only. They
    are intentionally not authoritative, unlike a completed finalized-video
    pass. A probe-cancelled empty prefix has no measured FPS, because the
    pipeline never invents a timestamp source it did not observe.
    """

    source_video: Path
    geometry: VideoGeometry
    measured_fps: float | None
    results: tuple[AnalysisFrameResult, ...]
    completion: AnalysisCompletion
    authoritative: bool
    detail: str = ""

    def __post_init__(self) -> None:
        if self.measured_fps is None:
            if self.completion is not AnalysisCompletion.CANCELLED or self.results:
                raise DomainError(
                    ErrorCode.VALIDATION,
                    "unknown measured FPS is allowed only for an empty probe-cancelled result",
                    "measured_fps",
                )
        elif not isfinite(self.measured_fps) or self.measured_fps <= 0:
            raise DomainError(ErrorCode.VALIDATION, "measured FPS must be finite and positive", "measured_fps")
        if self.authoritative != (self.completion is AnalysisCompletion.COMPLETED):
            raise DomainError(
                ErrorCode.VALIDATION,
                "only a completed finalized-video analysis can be authoritative",
                "authoritative",
            )
        for expected_index, result in enumerate(self.results):
            if result.frame_index != expected_index:
                raise DomainError(
                    ErrorCode.VALIDATION,
                    "analysis results must be a contiguous frame-zero prefix",
                    "results",
                )


@dataclass(frozen=True)
class OperatorCorrection:
    """One operator-requested row replacement for :meth:`AnalysisPipeline.recompute`.

    ``point=None`` explicitly clears the marker for this frame back to a
    ``missing``, angle-free row (the reviewer's "clear marker point" action)
    rather than fabricating a placeholder coordinate.
    """

    frame_index: int
    point: PixelPoint | None
    reason: str = "operator correction"

    def __post_init__(self) -> None:
        if self.frame_index < 0:
            raise DomainError(ErrorCode.VALIDATION, "frame index cannot be negative", "correction.frame_index")
        if not self.reason.strip():
            raise DomainError(ErrorCode.VALIDATION, "correction reason cannot be empty", "correction.reason")


@dataclass(frozen=True)
class ProvisionalAnalysisUpdate:
    """A non-persistable live preview result; never an authoritative measurement."""

    result: AnalysisFrameResult
    authoritative: bool = False
    preview_geometry: VideoGeometry | None = None

    def __post_init__(self) -> None:
        if self.authoritative:
            raise DomainError(
                ErrorCode.VALIDATION,
                "live analysis updates are provisional and cannot be authoritative",
                "authoritative",
            )


@dataclass(frozen=True)
class ProvisionalChannelStats:
    published: int = 0
    consumed: int = 0
    dropped_stale: int = 0


class ProvisionalAnalysisChannel:
    """One-slot channel that replaces stale live updates rather than queueing them."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._latest: ProvisionalAnalysisUpdate | None = None
        self._stats = ProvisionalChannelStats()

    def publish(self, update: ProvisionalAnalysisUpdate) -> None:
        with self._lock:
            stale = self._latest is not None
            self._latest = update
            self._stats = replace(
                self._stats,
                published=self._stats.published + 1,
                dropped_stale=self._stats.dropped_stale + int(stale),
            )

    def consume_latest(self) -> ProvisionalAnalysisUpdate | None:
        with self._lock:
            result, self._latest = self._latest, None
            if result is not None:
                self._stats = replace(self._stats, consumed=self._stats.consumed + 1)
            return result

    @property
    def stats(self) -> ProvisionalChannelStats:
        with self._lock:
            return self._stats


class AnalysisPipeline:
    """Process a finalized video using shared marker detection/scoring policy."""

    def __init__(
        self,
        video_source: VideoFrameSource,
        detector: RedMarkerFrameDetector,
        *,
        thresholds: HsvRedThresholds | None = None,
    ) -> None:
        self._video_source = video_source
        self._detector = detector
        # The notebook's 60px base exclusion is retained for offline analysis;
        # guided geometry suggestions intentionally keep their more general 0px default.
        self._thresholds = thresholds or HsvRedThresholds(exclusion_radius_pixels=60.0)

    def analyze(
        self,
        source_video: Path,
        geometry: VideoGeometry,
        *,
        cancellation: CancellationToken | None = None,
        on_progress: Callable[[AnalysisFrameResult, Any, int], None] | None = None,
    ) -> AnalysisRunResult:
        """Analyze a finalized video.

        ``on_progress``, if given, is called after each frame is scored (with
        the new row, the raw frame that produced it, and the video's total
        frame count) purely so a caller can render an incremental preview and
        progress bar; it never changes what is analyzed or returned.
        """
        try:
            handle = self._video_source.open(Path(source_video), cancellation=cancellation)
        except VideoProbeCancelled as error:
            return self._result(
                source_video,
                geometry,
                None,
                [],
                AnalysisCompletion.CANCELLED,
                detail=str(error),
            )
        try:
            metadata = handle.metadata
            if metadata.frame_size != geometry.frame_size:
                raise DomainError(
                    ErrorCode.VALIDATION,
                    "video dimensions do not match the selected geometry",
                    "geometry.frame_size",
                )
            fps = float(metadata.fps)
            if not isfinite(fps) or fps <= 0:
                raise DomainError(
                    ErrorCode.VALIDATION,
                    "source video has no valid measured FPS; timestamps cannot be invented",
                    "video.fps",
                )

            scorer = MarkerSuggestionWorkflow(self._detector, thresholds=self._thresholds)
            results: list[AnalysisFrameResult] = []
            for frame_index in range(metadata.frame_count):
                if _cancelled(cancellation):
                    return self._result(source_video, geometry, fps, results, AnalysisCompletion.CANCELLED)
                try:
                    frame = handle.read_frame(frame_index)
                except GeometryError as error:
                    # OpenCV's frame count is known to over-report for some
                    # containers. A decode/read failure at this boundary is a
                    # bounded EOF/truncation result, not a reason to discard
                    # the verified prefix. Do not catch detector/programming
                    # errors below this boundary.
                    return self._result(
                        source_video,
                        geometry,
                        fps,
                        results,
                        AnalysisCompletion.TRUNCATED,
                        detail=str(error),
                    )
                try:
                    suggestion = scorer.suggest(
                        frame,
                        frame_index=frame_index,
                        frame_size=geometry.frame_size,
                        roi=geometry.actuator_roi,
                        base_point=geometry.base_point,
                        cancellation=cancellation,
                    )
                except MarkerSuggestionCancelled:
                    return self._result(source_video, geometry, fps, results, AnalysisCompletion.CANCELLED)
                if _cancelled(cancellation):
                    return self._result(source_video, geometry, fps, results, AnalysisCompletion.CANCELLED)
                detection = _detection_from_suggestion(suggestion)
                result_row = AnalysisFrameResult.from_detection(
                    frame_index, frame_index / fps, geometry.base_point, detection
                )
                results.append(result_row)
                if on_progress is not None:
                    on_progress(result_row, frame, metadata.frame_count)
            return self._result(source_video, geometry, fps, results, AnalysisCompletion.COMPLETED)
        finally:
            handle.close()

    @staticmethod
    def recompute(
        original: AnalysisRunResult,
        corrections: Iterable[OperatorCorrection],
    ) -> AnalysisRunResult:
        """Return a new result set; old result objects are never modified."""

        replacements: dict[int, OperatorCorrection] = {}
        for correction in corrections:
            if correction.frame_index >= len(original.results):
                raise DomainError(
                    ErrorCode.VALIDATION,
                    "correction frame is outside the available result prefix",
                    "correction.frame_index",
                )
            if correction.point is not None:
                correction.point.validate_in(original.geometry.frame_size, "correction.point")
            replacements[correction.frame_index] = correction
        rows = list(original.results)
        for index, correction in replacements.items():
            prior = rows[index]
            if correction.point is None:
                detection = MarkerDetectionResult(
                    DetectionState.MISSING,
                    None,
                    0.0,
                    correction_applied=True,
                    reasons=(correction.reason,),
                )
            else:
                detection = MarkerDetectionResult(
                    DetectionState.MANUAL,
                    correction.point,
                    1.0,
                    correction_applied=True,
                    reasons=(correction.reason,),
                )
            rows[index] = AnalysisFrameResult.from_detection(
                prior.frame_index, prior.video_time_seconds, original.geometry.base_point, detection
            )
        return AnalysisRunResult(
            original.source_video,
            original.geometry,
            original.measured_fps,
            tuple(rows),
            original.completion,
            original.authoritative,
        )

    @staticmethod
    def _result(
        source_video: Path,
        geometry: VideoGeometry,
        fps: float | None,
        results: list[AnalysisFrameResult],
        completion: AnalysisCompletion,
        *,
        detail: str = "",
    ) -> AnalysisRunResult:
        return AnalysisRunResult(
            Path(source_video),
            geometry,
            fps,
            tuple(results),
            completion,
            authoritative=completion is AnalysisCompletion.COMPLETED,
            detail=detail,
        )


class AnalysisArtifactExporter:
    """Create the versioned results CSV and companion manifest through one store."""

    def __init__(self, store: ArtifactStore, *, software_version: str | None = None) -> None:
        self._store = store
        self._software_version = software_version

    def export(
        self,
        analysis: AnalysisRunResult,
        *,
        source_video: str,
        geometry_artifact_id: str,
    ) -> tuple[ArtifactDocument, ArtifactDocument]:
        if not analysis.authoritative:
            raise DomainError(
                ErrorCode.ARTIFACT_INVALID,
                "only completed authoritative analysis can be exported",
                "analysis.completion",
            )
        if not source_video:
            raise DomainError(ErrorCode.ARTIFACT_INVALID, "source video is required", "source_video")
        if not geometry_artifact_id:
            raise DomainError(ErrorCode.ARTIFACT_INVALID, "geometry artifact ID is required", "geometry_artifact_id")
        import_source = getattr(self._store, "import_analysis_source", None)
        publish_pair = getattr(self._store, "publish_analysis_export", None)
        if not callable(import_source) or not callable(publish_pair):
            raise DomainError(
                ErrorCode.ARTIFACT_INVALID,
                "analysis export requires a transactional workspace artifact store",
                "artifact_store",
            )
        source_reference = import_source(Path(source_video))
        artifact_id = f"analysis_{uuid4().hex}"
        now = datetime.now(timezone.utc)
        results_document = ArtifactDocument(
            ArtifactMetadata(ArtifactIdentity(ArtifactType.ANALYSIS_RESULTS, artifact_id), now, now, self._software_version),
            {"rows": [_row(result) for result in analysis.results]},
        )
        manifest_document = ArtifactDocument(
            ArtifactMetadata(ArtifactIdentity(ArtifactType.ANALYSIS_MANIFEST, artifact_id), now, now, self._software_version),
            {
                "source_video": source_reference,
                "geometry_artifact_id": geometry_artifact_id,
                "measured_fps": analysis.measured_fps,
                "completion": analysis.completion.value,
                "completion_detail": analysis.detail,
                "authoritative": analysis.authoritative,
                "frame_count": len(analysis.results),
                "results_artifact_id": artifact_id,
                "detector_settings": self._settings(),
            },
        )
        publish_pair(results_document, manifest_document)
        return results_document, manifest_document

    def _settings(self) -> dict[str, Any]:
        return {"algorithm": "shared-red-marker-scoring-v1"}


def analyze_frame(
    detector: RedMarkerFrameDetector,
    frame: Any,
    *,
    frame_index: int,
    video_time_seconds: float,
    geometry: VideoGeometry,
    thresholds: HsvRedThresholds | None = None,
    cancellation: CancellationToken | None = None,
) -> AnalysisFrameResult:
    """Score exactly one frame with the same shared detector/scoring policy
    :meth:`AnalysisPipeline.analyze` uses for a finalized video.

    This lets a bounded live-preview consumer (see
    ``ProvisionalAnalysisChannel``) reuse identical, deterministic per-frame
    scoring instead of duplicating it, without this module ever owning a
    camera or a second detection pipeline. The returned row is never
    authoritative on its own; only a completed :class:`AnalysisRunResult`
    from :meth:`AnalysisPipeline.analyze` is authoritative.
    """

    scorer = MarkerSuggestionWorkflow(
        detector, thresholds=thresholds or HsvRedThresholds(exclusion_radius_pixels=60.0)
    )
    suggestion = scorer.suggest(
        frame,
        frame_index=frame_index,
        frame_size=geometry.frame_size,
        roi=geometry.actuator_roi,
        base_point=geometry.base_point,
        cancellation=cancellation,
    )
    detection = _detection_from_suggestion(suggestion)
    return AnalysisFrameResult.from_detection(frame_index, video_time_seconds, geometry.base_point, detection)


def _cancelled(token: CancellationToken | None) -> bool:
    return token is not None and token.is_cancelled()


def _detection_from_suggestion(suggestion: Any) -> MarkerDetectionResult:
    candidates = tuple(
        MarkerCandidate(candidate.tip_point, candidate.area_pixels, candidate.confidence)
        for candidate in suggestion.candidates
    )
    if suggestion.state is MarkerSuggestionState.NO_DETECTION:
        return MarkerDetectionResult(
            DetectionState.MISSING,
            None,
            0.0,
            candidates,
            reasons=("no marker candidate passed the configured detector thresholds",),
        )
    if suggestion.state is MarkerSuggestionState.AMBIGUOUS:
        return MarkerDetectionResult(
            DetectionState.AMBIGUOUS,
            None,
            suggestion.candidates[0].confidence,
            candidates,
            reasons=("competing marker candidates require operator review",),
        )
    best = suggestion.best_candidate
    assert best is not None
    return MarkerDetectionResult(
        DetectionState.DETECTED,
        best.tip_point,
        best.confidence,
        candidates,
        reasons=best.reasons,
    )


def _row(result: AnalysisFrameResult) -> dict[str, Any]:
    detection = result.detection
    return {
        "frame_index": result.frame_index,
        "video_time_seconds": result.video_time_seconds,
        "tip_x": None if detection.point is None else detection.point.x,
        "tip_y": None if detection.point is None else detection.point.y,
        "actuator_angle_degrees": result.actuator_angle_degrees,
        "detection_state": detection.state.value,
        "confidence": detection.confidence,
        "correction_applied": detection.correction_applied,
        "detection_reason": "; ".join(detection.reasons),
    }


__all__ = [
    "AnalysisArtifactExporter",
    "AnalysisCancellation",
    "AnalysisCompletion",
    "AnalysisPipeline",
    "AnalysisRunResult",
    "OperatorCorrection",
    "ProvisionalAnalysisChannel",
    "ProvisionalAnalysisUpdate",
    "ProvisionalChannelStats",
    "analyze_frame",
]
