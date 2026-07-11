"""Workspace lifecycle tests with no camera, serial, or current-directory dependency."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from soft_actuator_testing.application.services import ArtifactDocument
from soft_actuator_testing.application.workspace import (
    CloseWorkspace,
    CreateWorkspace,
    OpenIndividualFiles,
    OpenWorkspace,
    SaveWorkspace,
    SetStorageRoot,
    WorkspaceController,
    WorkspaceMode,
)
from soft_actuator_testing.domain.artifacts import ArtifactIdentity, ArtifactMetadata, ArtifactType
from soft_actuator_testing.infrastructure.artifact_store import ArtifactFileStore
from soft_actuator_testing.infrastructure.workspace import JsonWorkspaceSettings


def _controller(tmp_path: Path) -> WorkspaceController:
    return WorkspaceController(
        JsonWorkspaceSettings(tmp_path / "preferences" / "workspace.json"),
        store_factory=ArtifactFileStore,
        software_version="test",
    )


def _document(kind: ArtifactType, payload: dict, artifact_id: str) -> ArtifactDocument:
    identity = ArtifactIdentity(kind, artifact_id)
    now = datetime.now(timezone.utc)
    return ArtifactDocument(ArtifactMetadata(identity, now, now), payload)


def test_create_save_close_open_and_restart_preferences_without_hardware(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    controller = _controller(tmp_path)

    assert controller.dispatch(SetStorageRoot(storage)).accepted
    assert controller.dispatch(CreateWorkspace("first-run")).accepted
    root = storage / "first-run"
    assert controller.snapshot.root == root
    assert len(list((root / "artifacts" / "workspace").glob("*.json"))) == 1
    assert controller.dispatch(SaveWorkspace()).accepted
    assert len(list((root / "artifacts" / "workspace").glob("*.json"))) == 2
    assert controller.dispatch(CloseWorkspace()).accepted
    assert controller.snapshot.mode is WorkspaceMode.NONE

    restarted = _controller(tmp_path)
    assert restarted.snapshot.storage_root == storage
    assert restarted.snapshot.recent_workspaces == (root,)
    assert restarted.dispatch(OpenWorkspace(root)).accepted
    assert restarted.snapshot.mode is WorkspaceMode.WORKSPACE
    assert restarted.snapshot.issues == ()


def test_failed_create_removes_partial_workspace_and_preserves_original_error(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()

    class FailingStore:
        def __init__(self, root: Path) -> None:
            self.root = root

        def save(self, document: ArtifactDocument) -> None:
            (self.root / "artifacts" / "workspace").mkdir(parents=True)
            raise OSError("manifest write failed")

    controller = WorkspaceController(
        JsonWorkspaceSettings(tmp_path / "preferences.json"),
        store_factory=FailingStore,
    )
    controller.dispatch(SetStorageRoot(storage))

    result = controller.dispatch(CreateWorkspace("partial"))

    assert not result.accepted
    assert result.message == "manifest write failed"
    assert not (storage / "partial").exists()


def test_relocated_workspace_resolves_analysis_source_without_current_directory(tmp_path: Path, monkeypatch) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    controller = _controller(tmp_path)
    controller.dispatch(SetStorageRoot(storage))
    controller.dispatch(CreateWorkspace("portable"))
    original = storage / "portable"
    video = original / "video" / "source.avi"
    video.parent.mkdir()
    video.write_bytes(b"video")
    store = ArtifactFileStore(original)
    store.save(
        _document(
            ArtifactType.CALIBRATION,
            {"model": {"type": "linear", "coeffs": [10.0, 1.0]}, "samples": [[0.0, 0.1]]},
            "calibration_one",
        )
    )
    store.save(
        _document(
            ArtifactType.GEOMETRY,
            {
                "frame_size": {"width": 192, "height": 128},
                "base_point": {"x": 20, "y": 96},
                "initial_tip_point": {"x": 140, "y": 36},
                "roi": {"left": 10, "top": 15, "right": 180, "bottom": 115},
            },
            "geometry_one",
        )
    )
    store.save(
        _document(
            ArtifactType.ANALYSIS_MANIFEST,
            {"source_video": str(video), "geometry_artifact_id": "geometry_one"},
            "analysis_one",
        )
    )
    relocated = storage / "relocated"
    original.rename(relocated)
    monkeypatch.chdir(tmp_path)

    assert controller.dispatch(OpenWorkspace(relocated)).accepted
    assert {"calibration", "geometry", "analysis_manifest", "video"} <= {
        item.kind for item in controller.snapshot.artifacts
    }
    assert any(item.kind == "video" and item.location == relocated / "video" / "source.avi" for item in controller.snapshot.artifacts)
    assert controller.snapshot.issues == ()


def test_reports_missing_corrupt_and_traversal_artifacts_with_paths(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    controller = _controller(tmp_path)
    controller.dispatch(SetStorageRoot(storage))
    controller.dispatch(CreateWorkspace("invalid-artifacts"))
    root = storage / "invalid-artifacts"
    calibration = root / "artifacts" / "calibration" / "calibration_bad.json"
    calibration.parent.mkdir(parents=True)
    calibration.write_text("{not json", encoding="utf-8")
    analysis = root / "analysis" / "analysis_bad" / "analysis.json"
    analysis.parent.mkdir(parents=True)
    analysis.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_type": "analysis_manifest",
                "artifact_id": "analysis_bad",
                "created_at": "2026-07-11T00:00:00+00:00",
                "updated_at": "2026-07-11T00:00:00+00:00",
                "software_version": None,
                "payload": {"source_video": "../outside.avi", "geometry_artifact_id": "geometry_one"},
            }
        ),
        encoding="utf-8",
    )

    assert controller.dispatch(OpenWorkspace(root)).accepted
    assert any(issue.location == calibration and issue.field_path == "artifact" for issue in controller.snapshot.issues)
    assert any(issue.location == analysis and issue.field_path == "path" for issue in controller.snapshot.issues)


def test_rejects_missing_relative_and_corrupt_workspaces_without_overwriting(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    assert not controller.dispatch(OpenWorkspace(Path("relative"))).accepted

    corrupt = tmp_path / "corrupt"
    (corrupt / "artifacts" / "workspace").mkdir(parents=True)
    (corrupt / "artifacts" / "workspace" / "workspace_future.json").write_text(
        json.dumps({"schema_version": 99}), encoding="utf-8"
    )
    result = controller.dispatch(OpenWorkspace(corrupt))
    assert not result.accepted
    assert controller.snapshot.issues[0].field_path == "schema_version"

    storage = tmp_path / "storage"
    storage.mkdir()
    controller.dispatch(SetStorageRoot(storage))
    assert controller.dispatch(CreateWorkspace("preserve")).accepted
    assert not controller.dispatch(CreateWorkspace("preserve")).accepted
    assert len(list((storage / "preserve" / "artifacts" / "workspace").glob("*.json"))) == 1


def test_individual_file_mode_is_explicit_and_cancel_safe(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    artifact = tmp_path / "calibration.json"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_type": "calibration",
                "artifact_id": "calibration_one",
                "payload": {},
            }
        ),
        encoding="utf-8",
    )
    assert controller.dispatch(OpenIndividualFiles((artifact,))).accepted
    assert controller.snapshot.mode is WorkspaceMode.INDIVIDUAL_FILES
    assert not controller.snapshot.can_save
    assert not controller.dispatch(OpenIndividualFiles((tmp_path / "missing.json",))).accepted
    assert controller.snapshot.issues[0].field_path == "path"


def test_opening_workspace_never_touches_injected_hardware_bombs(tmp_path: Path) -> None:
    class HardwareBomb:
        def __getattr__(self, name: str) -> None:
            raise AssertionError(f"workspace restore attempted hardware access: {name}")

    storage = tmp_path / "storage"
    storage.mkdir()
    controller = _controller(tmp_path)
    controller.dispatch(SetStorageRoot(storage))
    controller.dispatch(CreateWorkspace("offline"))
    controller._serial = HardwareBomb()  # type: ignore[attr-defined]
    controller._camera = HardwareBomb()  # type: ignore[attr-defined]

    assert controller.dispatch(OpenWorkspace(storage / "offline")).accepted
