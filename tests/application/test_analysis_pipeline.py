from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from soft_actuator_testing.application.analysis_pipeline import (
    AnalysisArtifactExporter,
    AnalysisCancellation,
    AnalysisCompletion,
    AnalysisPipeline,
    AnalysisRunResult,
    OperatorCorrection,
    ProvisionalAnalysisChannel,
    ProvisionalAnalysisUpdate,
)
from soft_actuator_testing.application.marker_suggestion import (
    FakeRedMarkerFrameDetector,
    RedBlob,
    RedMarkerScan,
)
from soft_actuator_testing.application.video_geometry_workflow import (
    FakeVideoFrameSource,
    VideoMetadata,
)
from soft_actuator_testing.domain.analysis import AnalysisFrameResult, DetectionState, MarkerDetectionResult
from soft_actuator_testing.domain.errors import DomainError, ErrorCode, GeometryError
from soft_actuator_testing.domain.geometry import FrameSize, NormalizedRoi, PixelPoint, VideoGeometry
from soft_actuator_testing.domain.artifacts import ArtifactType
from soft_actuator_testing.infrastructure.artifact_store import ArtifactFileStore


SOURCE = Path("analysis.avi")
SIZE = FrameSize(100, 80)
GEOMETRY = VideoGeometry(SIZE, PixelPoint(10, 40), PixelPoint(90, 40), NormalizedRoi(0, 0, 100, 80))


def _blob(x: float, y: float, *, area: float = 100.0) -> RedBlob:
    return RedBlob(PixelPoint(x, y), NormalizedRoi(x - 3, y - 3, x + 3, y + 3), area, 36.0, 1.0)


def _pipeline(*, fps: float = 20.0, blobs: tuple[tuple[RedBlob, ...], ...]) -> AnalysisPipeline:
    source = FakeVideoFrameSource()
    frames = tuple(np.zeros((80, 100, 3), dtype=np.uint8) for _ in blobs)
    source.register(SOURCE, frames, fps=fps)
    detector = FakeRedMarkerFrameDetector()
    for frame, frame_blobs in zip(frames, blobs, strict=True):
        detector.register(frame, RedMarkerScan(SIZE, GEOMETRY.actuator_roi, frame_blobs, None))
    return AnalysisPipeline(source, detector)


def test_processes_frame_zero_uses_measured_fps_and_never_reuses_missing_marker() -> None:
    result = _pipeline(blobs=((_blob(90, 40),), (), (_blob(80, 40),))).analyze(SOURCE, GEOMETRY)

    assert result.authoritative
    assert [row.frame_index for row in result.results] == [0, 1, 2]
    assert [row.video_time_seconds for row in result.results] == pytest.approx([0.0, 0.05, 0.1])
    assert result.results[0].detection.state is DetectionState.DETECTED
    assert result.results[1].detection.state is DetectionState.MISSING
    assert result.results[1].detection.point is None
    assert result.results[1].actuator_angle_degrees is None
    assert result.results[2].detection.point == PixelPoint(80, 40)


def test_competing_markers_are_explicitly_ambiguous_without_angle() -> None:
    result = _pipeline(blobs=((_blob(90, 30), _blob(90, 50)),)).analyze(SOURCE, GEOMETRY)

    row = result.results[0]
    assert row.detection.state is DetectionState.AMBIGUOUS
    assert row.detection.point is None
    assert row.actuator_angle_degrees is None
    assert "competing" in row.detection.reasons[0]


def test_invalid_fps_and_mismatched_geometry_fail_closed() -> None:
    with pytest.raises(DomainError, match="measured FPS"):
        _pipeline(fps=0.0, blobs=((_blob(90, 40),),)).analyze(SOURCE, GEOMETRY)
    wrong = VideoGeometry(FrameSize(99, 80), PixelPoint(10, 40), PixelPoint(90, 40), NormalizedRoi(0, 0, 99, 80))
    with pytest.raises(DomainError, match="dimensions"):
        _pipeline(blobs=((_blob(90, 40),),)).analyze(SOURCE, wrong)


def test_cancellation_returns_only_a_contiguous_non_authoritative_prefix() -> None:
    cancellation = AnalysisCancellation()

    class CancellingDetector(FakeRedMarkerFrameDetector):
        def scan(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            result = super().scan(*args, **kwargs)
            cancellation.cancel()
            return result

    source = FakeVideoFrameSource()
    frames = tuple(np.zeros((80, 100, 3), dtype=np.uint8) for _ in range(2))
    source.register(SOURCE, frames)
    detector = CancellingDetector()
    for frame in frames:
        detector.register(frame, RedMarkerScan(SIZE, GEOMETRY.actuator_roi, (_blob(90, 40),), None))

    result = AnalysisPipeline(source, detector).analyze(SOURCE, GEOMETRY, cancellation=cancellation)
    assert result.completion is AnalysisCompletion.CANCELLED
    assert not result.authoritative
    assert result.results == ()


def test_probe_cancellation_returns_an_empty_non_authoritative_prefix_without_an_invented_fps() -> None:
    source = FakeVideoFrameSource()
    source.register(SOURCE, (np.zeros((80, 100, 3), dtype=np.uint8),))
    source.cancel_probe_for.add(SOURCE)
    cancellation = AnalysisCancellation()
    cancellation.cancel()

    result = AnalysisPipeline(source, FakeRedMarkerFrameDetector()).analyze(SOURCE, GEOMETRY, cancellation=cancellation)

    assert result.completion is AnalysisCompletion.CANCELLED
    assert not result.authoritative
    assert result.results == ()
    assert result.measured_fps is None
    assert "probing was cancelled" in result.detail


def test_overreported_frame_count_returns_a_non_authoritative_truncated_prefix() -> None:
    frames = (
        np.zeros((80, 100, 3), dtype=np.uint8),
        np.zeros((80, 100, 3), dtype=np.uint8),
    )

    class OverreportedHandle:
        metadata = VideoMetadata(SIZE, frame_count=3, fps=10.0)

        def __init__(self) -> None:
            self.closed = False

        def read_frame(self, frame_index: int):
            if frame_index >= len(frames):
                raise GeometryError(ErrorCode.GEOMETRY_INVALID, "cannot read the requested frame", "frame_index")
            return frames[frame_index]

        def close(self) -> None:
            self.closed = True

    class OverreportedSource:
        def __init__(self) -> None:
            self.handle = OverreportedHandle()

        def open(self, _source: Path, *, cancellation=None):  # type: ignore[no-untyped-def]
            del cancellation
            return self.handle

    source = OverreportedSource()
    detector = FakeRedMarkerFrameDetector()
    for frame in frames:
        detector.register(frame, RedMarkerScan(SIZE, GEOMETRY.actuator_roi, (_blob(90, 40),), None))

    result = AnalysisPipeline(source, detector).analyze(SOURCE, GEOMETRY)

    assert result.completion is AnalysisCompletion.TRUNCATED
    assert not result.authoritative
    assert [row.frame_index for row in result.results] == [0, 1]
    assert result.measured_fps == pytest.approx(10.0)
    assert "cannot read" in result.detail
    assert source.handle.closed


def test_corrections_recompute_immutably_and_validate_frame_bounds() -> None:
    original = _pipeline(blobs=((_blob(90, 40),),)).analyze(SOURCE, GEOMETRY)
    corrected = AnalysisPipeline.recompute(original, (OperatorCorrection(0, PixelPoint(70, 50)),))

    assert original.results[0].detection.state is DetectionState.DETECTED
    assert corrected.results[0].detection.state is DetectionState.MANUAL
    assert corrected.results[0].detection.correction_applied
    assert corrected.results[0].detection.point == PixelPoint(70, 50)
    with pytest.raises(DomainError, match="outside"):
        AnalysisPipeline.recompute(original, (OperatorCorrection(1, PixelPoint(70, 50)),))


def test_provisional_channel_is_bounded_and_never_authoritative() -> None:
    channel = ProvisionalAnalysisChannel()
    one = AnalysisFrameResult.from_detection(0, 0.0, GEOMETRY.base_point, MarkerDetectionResult(DetectionState.DETECTED, PixelPoint(90, 40), 1.0))
    two = AnalysisFrameResult.from_detection(1, 0.1, GEOMETRY.base_point, MarkerDetectionResult(DetectionState.DETECTED, PixelPoint(80, 40), 1.0))
    channel.publish(ProvisionalAnalysisUpdate(one))
    channel.publish(ProvisionalAnalysisUpdate(two))

    latest = channel.consume_latest()
    assert latest is not None and latest.result is two and not latest.authoritative
    assert channel.stats.dropped_stale == 1
    with pytest.raises(DomainError, match="provisional"):
        ProvisionalAnalysisUpdate(one, authoritative=True)


def test_exports_versioned_reasoned_csv_and_manifest_without_overwriting(tmp_path: Path) -> None:
    analysis = _pipeline(blobs=((_blob(90, 40),), ())).analyze(SOURCE, GEOMETRY)
    store = ArtifactFileStore(tmp_path)
    (tmp_path / "video").mkdir()
    (tmp_path / "video" / "analysis.avi").touch()
    results, manifest = AnalysisArtifactExporter(store, software_version="test").export(
        analysis, source_video="video/analysis.avi", geometry_artifact_id="geometry_fixture"
    )

    loaded = store.load(ArtifactType.ANALYSIS_RESULTS, results.metadata.identity.artifact_id)
    assert loaded.payload["rows"][0]["detection_reason"]
    assert loaded.payload["rows"][1]["detection_state"] == "missing"
    loaded_manifest = store.load(ArtifactType.ANALYSIS_MANIFEST, manifest.metadata.identity.artifact_id)
    assert loaded_manifest.payload["authoritative"] is True
    assert (tmp_path / "analysis" / results.metadata.identity.artifact_id / "angles.csv").is_file()


def test_external_analysis_videos_are_imported_without_mutation_and_keep_portable_provenance(tmp_path: Path) -> None:
    analysis = _pipeline(blobs=((_blob(90, 40),),)).analyze(SOURCE, GEOMETRY)
    external_root = tmp_path / "external-video"
    external_root.mkdir()
    external = external_root / "operator-selection.avi"
    original_bytes = b"external-video-bytes"
    external.write_bytes(original_bytes)

    store = ArtifactFileStore(tmp_path / "workspace")
    _, manifest = AnalysisArtifactExporter(store).export(
        analysis,
        source_video=str(external),
        geometry_artifact_id="geometry_fixture",
    )

    persisted = store.load(ArtifactType.ANALYSIS_MANIFEST, manifest.metadata.identity.artifact_id)
    reference = persisted.payload["source_video"]
    assert reference.startswith("video/analysis-imports/imported-")
    assert not Path(reference).is_absolute()
    assert store.resolve_workspace_path(reference).read_bytes() == original_bytes
    assert external.read_bytes() == original_bytes


def test_duplicate_external_video_names_import_to_distinct_workspace_sources(tmp_path: Path) -> None:
    analysis = _pipeline(blobs=((_blob(90, 40),),)).analyze(SOURCE, GEOMETRY)
    first_directory = tmp_path / "one"
    second_directory = tmp_path / "two"
    first_directory.mkdir()
    second_directory.mkdir()
    first = first_directory / "capture.avi"
    second = second_directory / "capture.avi"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    store = ArtifactFileStore(tmp_path / "workspace")
    exporter = AnalysisArtifactExporter(store)

    _, first_manifest = exporter.export(analysis, source_video=str(first), geometry_artifact_id="geometry_fixture")
    _, second_manifest = exporter.export(analysis, source_video=str(second), geometry_artifact_id="geometry_fixture")
    first_reference = store.load(ArtifactType.ANALYSIS_MANIFEST, first_manifest.metadata.identity.artifact_id).payload[
        "source_video"
    ]
    second_reference = store.load(ArtifactType.ANALYSIS_MANIFEST, second_manifest.metadata.identity.artifact_id).payload[
        "source_video"
    ]

    assert first_reference != second_reference
    assert store.resolve_workspace_path(first_reference).read_bytes() == b"first"
    assert store.resolve_workspace_path(second_reference).read_bytes() == b"second"


def test_manifest_write_failure_rolls_back_the_just_published_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analysis = _pipeline(blobs=((_blob(90, 40),),)).analyze(SOURCE, GEOMETRY)
    store = ArtifactFileStore(tmp_path)
    source = tmp_path / "video" / "analysis.avi"
    source.parent.mkdir()
    source.write_bytes(b"workspace-video")
    replace_module = "soft_actuator_testing.infrastructure.artifact_store.os.replace"
    import os

    real_replace = os.replace
    calls = 0

    def fail_second_replace(source_path: object, destination_path: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected manifest replace failure")
        real_replace(source_path, destination_path)

    monkeypatch.setattr(replace_module, fail_second_replace)
    with pytest.raises(DomainError, match="atomic write failed"):
        AnalysisArtifactExporter(store).export(
            analysis,
            source_video="video/analysis.avi",
            geometry_artifact_id="geometry_fixture",
        )

    assert not list((tmp_path / "analysis").rglob("angles.csv"))
    assert not list((tmp_path / "analysis").rglob("analysis.json"))
    assert not list((tmp_path / "analysis").rglob("*.tmp"))


def test_non_authoritative_cancelled_or_truncated_prefix_cannot_be_exported(tmp_path: Path) -> None:
    partial = AnalysisRunResult(
        SOURCE,
        GEOMETRY,
        10.0,
        (),
        AnalysisCompletion.CANCELLED,
        authoritative=False,
    )

    with pytest.raises(DomainError, match="authoritative"):
        AnalysisArtifactExporter(ArtifactFileStore(tmp_path)).export(
            partial,
            source_video="video/analysis.avi",
            geometry_artifact_id="geometry_fixture",
        )
