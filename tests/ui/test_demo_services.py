"""Tests for deterministic fake/demo services: no hardware, reproducible output."""

from __future__ import annotations

import numpy as np

from soft_actuator_testing.application.services import ArtifactDocument
from soft_actuator_testing.domain.analysis import DetectionState
from soft_actuator_testing.domain.artifacts import ArtifactIdentity, ArtifactMetadata, ArtifactType
from soft_actuator_testing.domain.run_state import RunCompletion, RunState
from soft_actuator_testing.ui.demo import build_demo_environment
from soft_actuator_testing.ui.demo.fake_services import (
    FakeAnalysisService,
    FakeArtifactStore,
    FakeCameraService,
    FakeMarkerDetector,
    FakeRunLifecycleService,
    FakeSerialService,
    demo_video_geometry,
)


def test_build_demo_environment_is_deterministic_across_calls() -> None:
    first = build_demo_environment()
    second = build_demo_environment()
    assert first.geometry == second.geometry
    assert first.calibration_fit.model == second.calibration_fit.model
    assert first.run_snapshot == second.run_snapshot
    assert [n.message for n in first.notifications] == [n.message for n in second.notifications]


def test_fake_serial_service_yields_deterministic_telemetry() -> None:
    first = list(FakeSerialService(sample_count=5).telemetry())
    second = list(FakeSerialService(sample_count=5).telemetry())
    assert first == second
    assert len(first) == 5
    assert all(sample.volts is not None for sample in first)


def test_fake_serial_service_never_touches_a_real_port() -> None:
    serial = FakeSerialService()
    serial.connect()
    assert serial.is_connected is True
    serial.send_command("CMD:START")
    assert serial.sent_commands == ["CMD:START"]
    serial.disconnect()
    assert serial.is_connected is False


def test_fake_camera_service_yields_deterministic_synthetic_frames() -> None:
    camera = FakeCameraService(frame_count=3)
    size = camera.open()
    frames = list(camera.frames())
    assert len(frames) == 3
    for frame in frames:
        assert frame.image.shape == (size.height, size.width, 3)
        assert frame.image.dtype == np.uint8
    camera.close()
    assert camera.is_open is False

    # Same construction args must reproduce identical pixel data.
    other_frames = list(FakeCameraService(frame_count=3).frames())
    for a, b in zip(frames, other_frames):
        assert np.array_equal(a.image, b.image)


def test_fake_marker_detector_cycles_deterministic_states() -> None:
    detector = FakeMarkerDetector()
    geometry = demo_video_geometry()
    camera = FakeCameraService(frame_count=4)
    states = [detector.detect(frame, geometry).state for frame in camera.frames()]
    assert states == [
        DetectionState.DETECTED,
        DetectionState.DETECTED,
        DetectionState.HELD,
        DetectionState.MISSING,
    ]


def test_fake_run_lifecycle_service_follows_legal_domain_transitions() -> None:
    service = FakeRunLifecycleService()
    assert service.snapshot().state is RunState.DISCONNECTED
    service.connect()
    service.mark_idle()
    service.mark_ready()
    assert service.readiness() is True

    service.start()
    assert service.snapshot().state is RunState.RUNNING

    service.stop()
    assert service.snapshot().state is RunState.STOPPING

    result = service.finalize(RunCompletion.CLEAN)
    assert result.snapshot.state is RunState.COMPLETED
    assert result.snapshot.completion is RunCompletion.CLEAN

    # Repeated finalize with the same outcome remains idempotent.
    assert service.finalize(RunCompletion.CLEAN).idempotent is True


def test_fake_analysis_service_produces_deterministic_frame_results() -> None:
    geometry = demo_video_geometry()
    first = list(FakeAnalysisService(frame_count=4).analyze(None, geometry))
    second = list(FakeAnalysisService(frame_count=4).analyze(None, geometry))
    assert first == second
    assert len(first) == 4


def test_fake_artifact_store_save_load_and_export_never_touch_disk(tmp_path) -> None:
    store = FakeArtifactStore()
    identity = ArtifactIdentity.new(ArtifactType.CALIBRATION)
    metadata = ArtifactMetadata.now(identity)
    document = ArtifactDocument(metadata=metadata, payload={"model": "linear"})

    store.save(document)
    loaded = store.load(ArtifactType.CALIBRATION, identity.artifact_id)
    assert loaded == document

    destination = tmp_path / "export.json"
    store.export_legacy(document, destination)
    assert not destination.exists()  # the fake never writes real files
    assert store.exported == [(identity.artifact_id, destination)]


def test_fake_artifact_store_import_legacy_creates_a_new_identity(tmp_path) -> None:
    store = FakeArtifactStore()
    source = tmp_path / "legacy_calibration.json"
    source.write_text("{}")
    document = store.import_legacy(source, ArtifactType.CALIBRATION)
    assert document.metadata.identity.artifact_type is ArtifactType.CALIBRATION
    assert store.load(ArtifactType.CALIBRATION, document.metadata.identity.artifact_id) == document
