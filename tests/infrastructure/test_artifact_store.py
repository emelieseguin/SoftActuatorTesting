"""Compatibility, validation, path, and atomicity tests for artifact persistence."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from soft_actuator_testing.application.services import ArtifactDocument
from soft_actuator_testing.domain.artifacts import ArtifactIdentity, ArtifactMetadata, ArtifactType
from soft_actuator_testing.domain.errors import DomainError
from soft_actuator_testing.infrastructure.artifact_store import ArtifactFileStore
from soft_actuator_testing.infrastructure.legacy_import import LegacyArtifactImporter


FIXTURES = Path(__file__).parents[1] / "fixtures"


def _document(kind: ArtifactType, payload: dict, artifact_id: str | None = None) -> ArtifactDocument:
    identity = ArtifactIdentity(kind, artifact_id or ArtifactIdentity.new(kind).artifact_id)
    now = datetime.now(timezone.utc)
    return ArtifactDocument(ArtifactMetadata(identity, now, now, "test"), payload)


def test_all_valid_legacy_fixtures_import_and_preserve_originals() -> None:
    importer = LegacyArtifactImporter()
    source_files = (
        FIXTURES / "calibration" / "valid-linear.json",
        FIXTURES / "calibration" / "valid-quadratic.json",
        FIXTURES / "geometry" / "valid-synthetic-red-marker_config.json",
        FIXTURES / "pressure" / "calibrated-pressure.csv",
        FIXTURES / "pressure" / "raw-missing-pressure.csv",
        FIXTURES / "angle" / "angles-valid.csv",
        FIXTURES / "angle" / "angles-missing-values.csv",
    )
    originals = {path: path.read_bytes() for path in source_files}
    calibration = importer.import_file(FIXTURES / "calibration" / "valid-linear.json", ArtifactType.CALIBRATION)
    quadratic = importer.import_file(FIXTURES / "calibration" / "valid-quadratic.json", ArtifactType.CALIBRATION)
    geometry_source = FIXTURES / "geometry" / "valid-synthetic-red-marker_config.json"
    geometry = importer.import_file(geometry_source, ArtifactType.GEOMETRY, frame_size=(192, 128))
    pressure = importer.import_file(FIXTURES / "pressure" / "calibrated-pressure.csv", ArtifactType.PRESSURE_DATA)
    raw_pressure = importer.import_file(FIXTURES / "pressure" / "raw-missing-pressure.csv", ArtifactType.PRESSURE_DATA)
    angles = importer.import_file(
        FIXTURES / "angle" / "angles-valid.csv",
        ArtifactType.ANALYSIS_RESULTS,
        frame_rate_hz=10.0,
    )
    missing_angles = importer.import_file(
        FIXTURES / "angle" / "angles-missing-values.csv",
        ArtifactType.ANALYSIS_RESULTS,
        frame_rate_hz=10.0,
    )

    assert calibration.payload["model"]["type"] == "linear"
    assert quadratic.payload["model"]["type"] == "quadratic"
    assert geometry.payload["roi"] == {"left": 10.0, "top": 15.0, "right": 180.0, "bottom": 115.0}
    assert pressure.payload["rows"][1]["pressure_kPa"] == 100.0
    assert raw_pressure.payload["rows"][0]["pressure_kPa"] is None
    assert angles.payload["rows"][0]["actuator_angle_degrees"] == pytest.approx(-26.565051)
    assert angles.payload["rows"][0]["video_time_seconds"] == pytest.approx(0.1)
    assert angles.payload["timing_provenance"] == "source_fps_derived"
    assert missing_angles.payload["rows"][0]["detection_state"] == "missing"
    assert missing_angles.payload["rows"][1]["actuator_angle_degrees"] is None
    assert {path: path.read_bytes() for path in source_files} == originals


@pytest.mark.parametrize(
    ("source", "kind", "kwargs", "field_path"),
    [
        ("calibration/invalid-missing-model.json", ArtifactType.CALIBRATION, {}, "model"),
        ("calibration/invalid-linear-short-coeffs.json", ArtifactType.CALIBRATION, {}, "model.coeffs"),
        ("geometry/missing-points_config.json", ArtifactType.GEOMETRY, {"frame_size": (192, 128)}, "angle_base_point.x"),
        ("geometry/reverse-order-roi_config.json", ArtifactType.GEOMETRY, {"frame_size": (192, 128)}, "actuator_roi"),
        ("geometry/negative-dimension-roi_config.json", ArtifactType.GEOMETRY, {"frame_size": (192, 128)}, "actuator_roi.w"),
        ("geometry/reversed-corners-roi_config.json", ArtifactType.GEOMETRY, {"frame_size": (192, 128)}, "actuator_roi"),
        ("geometry/out-of-bounds-roi_config.json", ArtifactType.GEOMETRY, {"frame_size": (192, 128)}, "actuator_roi"),
        ("serial/command-lines.txt", ArtifactType.RUN_MANIFEST, {}, "artifact_type"),
        ("serial/telemetry-normal-with-markers.txt", ArtifactType.RUN_MANIFEST, {}, "artifact_type"),
        ("serial/telemetry-malformed-and-short.txt", ArtifactType.RUN_MANIFEST, {}, "artifact_type"),
    ],
)
def test_invalid_or_unrelated_legacy_fixtures_are_rejected_with_paths(
    source: str, kind: ArtifactType, kwargs: dict, field_path: str
) -> None:
    with pytest.raises(DomainError) as raised:
        LegacyArtifactImporter().import_file(FIXTURES / source, kind, **kwargs)
    assert raised.value.field_path == field_path


def test_versioned_documents_round_trip_for_every_artifact_kind(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path)
    documents = [
        _document(ArtifactType.WORKSPACE, {"name": "demo"}),
        _document(
            ArtifactType.CALIBRATION,
            {"model": {"type": "linear", "coeffs": [100.0, -10.0]}, "samples": [[0.0, 0.1]]},
        ),
        _document(
            ArtifactType.GEOMETRY,
            {
                "frame_size": {"width": 192, "height": 128},
                "base_point": {"x": 20, "y": 96},
                "initial_tip_point": {"x": 140, "y": 36},
                "roi": {"left": 10, "top": 15, "right": 180, "bottom": 115},
            },
        ),
        _document(ArtifactType.RUN_MANIFEST, {"completion": "clean", "output_files": ["runs/output.txt"]}),
        _document(
            ArtifactType.ANALYSIS_MANIFEST,
            {"source_video": "video/source.avi", "geometry_artifact_id": "geometry_reference"},
        ),
        _document(
            ArtifactType.PRESSURE_DATA,
            {"rows": [{"time_s": 0.0, "volts": 0.1, "pressure_kPa": None}]},
        ),
        _document(
            ArtifactType.ANALYSIS_RESULTS,
            {
                "rows": [
                    {
                        "frame_index": 0,
                        "video_time_seconds": 0.0,
                        "tip_x": None,
                        "tip_y": None,
                        "actuator_angle_degrees": None,
                        "detection_state": "missing",
                        "confidence": 0.0,
                        "correction_applied": False,
                    }
                ]
            },
        ),
    ]
    for document in documents:
        store.save(document)
        loaded = store.load(document.metadata.identity.artifact_type, document.metadata.identity.artifact_id)
        assert loaded.metadata.identity == document.metadata.identity
        assert loaded.payload == document.payload


def test_legacy_export_is_narrow_and_round_trips(tmp_path: Path) -> None:
    importer = LegacyArtifactImporter()
    source = FIXTURES / "calibration" / "valid-linear.json"
    imported = importer.import_file(source, ArtifactType.CALIBRATION)
    destination = tmp_path / "legacy-calibration.json"

    importer.export_file(imported, destination)

    assert json.loads(destination.read_text()) == json.loads(source.read_text())
    with pytest.raises(DomainError, match="overwrite"):
        importer.export_file(imported, destination)


def test_legacy_export_supports_geometry_pressure_and_angles(tmp_path: Path) -> None:
    importer = LegacyArtifactImporter()
    geometry = importer.import_file(
        FIXTURES / "geometry" / "valid-synthetic-red-marker_config.json",
        ArtifactType.GEOMETRY,
        frame_size=(192, 128),
    )
    pressure = importer.import_file(FIXTURES / "pressure" / "raw-missing-pressure.csv", ArtifactType.PRESSURE_DATA)
    angles = importer.import_file(
        FIXTURES / "angle" / "angles-missing-values.csv",
        ArtifactType.ANALYSIS_RESULTS,
        frame_rate_hz=10.0,
    )

    importer.export_file(geometry, tmp_path / "geometry.json")
    importer.export_file(pressure, tmp_path / "pressure.csv")
    importer.export_file(angles, tmp_path / "angles.csv")

    assert json.loads((tmp_path / "geometry.json").read_text())["actuator_roi"]["w"] == 170.0
    assert (tmp_path / "pressure.csv").read_text().splitlines()[1].endswith(",")
    assert (tmp_path / "angles.csv").read_text().splitlines()[1].endswith(",")


def test_imported_legacy_angles_remain_explicit_after_versioned_csv_save(tmp_path: Path) -> None:
    legacy = LegacyArtifactImporter().import_file(
        FIXTURES / "angle" / "angles-missing-values.csv",
        ArtifactType.ANALYSIS_RESULTS,
        frame_rate_hz=10.0,
    )
    store = ArtifactFileStore(tmp_path)

    store.save(legacy)
    loaded = store.load(ArtifactType.ANALYSIS_RESULTS, legacy.metadata.identity.artifact_id)

    assert loaded.payload["legacy_import"] is True
    assert loaded.payload["rows"][0]["actuator_angle_degrees"] is None
    assert loaded.payload["rows"][1]["actuator_angle_degrees"] is None


def test_prior_v1_analysis_csv_header_loads_and_resaves_without_losing_scientific_rows(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path)
    fixture = FIXTURES / "angle" / "versioned-v1-prior-header.csv"
    target = tmp_path / "analysis" / "analysis_prior_v1" / "angles.csv"
    target.parent.mkdir(parents=True)
    target.write_bytes(fixture.read_bytes())

    prior = store.load(ArtifactType.ANALYSIS_RESULTS, "analysis_prior_v1")

    assert prior.payload == {
        "rows": [
            {
                "frame_index": 0,
                "video_time_seconds": 0.0,
                "tip_x": 140.0,
                "tip_y": 36.0,
                "actuator_angle_degrees": -26.565051,
                "detection_state": "detected",
                "confidence": 0.9,
                "correction_applied": False,
            }
        ]
    }
    resaved = _document(ArtifactType.ANALYSIS_RESULTS, dict(prior.payload), "analysis_prior_v1_resaved")
    store.save(resaved)
    resaved_path = tmp_path / "analysis" / "analysis_prior_v1_resaved" / "angles.csv"
    assert "detection_reason" in resaved_path.read_text(encoding="utf-8").splitlines()[0]
    assert store.load(ArtifactType.ANALYSIS_RESULTS, "analysis_prior_v1_resaved").payload == prior.payload


def test_prior_v1_run_manifest_without_additive_capture_evidence_loads_unchanged(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path)
    fixture = FIXTURES / "run" / "versioned-v1-before-capture-evidence.json"
    target = tmp_path / "runs" / "run_manifest_legacy_capture" / "run.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(fixture.read_bytes())

    loaded = store.load(ArtifactType.RUN_MANIFEST, "run_manifest_legacy_capture")

    assert loaded.payload == {
        "completion": "clean",
        "output_files": ["runs/run_manifest_legacy_capture/pressure.csv"],
    }


@pytest.mark.parametrize("frame_rate_hz", [None, 0.0, -1.0, float("nan")])
def test_legacy_angle_import_requires_a_valid_source_frame_rate(frame_rate_hz: float | None) -> None:
    with pytest.raises(DomainError) as raised:
        LegacyArtifactImporter().import_file(
            FIXTURES / "angle" / "angles-valid.csv",
            ArtifactType.ANALYSIS_RESULTS,
            frame_rate_hz=frame_rate_hz,
        )
    assert raised.value.field_path == "frame_rate_hz"


def test_synthetic_video_fixture_is_a_portable_workspace_reference(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path)
    video = FIXTURES / "video" / "synthetic-red-marker.avi"
    assert video.is_file() and video.stat().st_size > 0
    workspace_video = tmp_path / "video" / video.name
    workspace_video.parent.mkdir()
    workspace_video.write_bytes(video.read_bytes())
    document = _document(
        ArtifactType.ANALYSIS_MANIFEST,
        {"source_video": str(workspace_video), "geometry_artifact_id": "geometry_synthetic"},
    )

    store.save(document)

    assert store.load(ArtifactType.ANALYSIS_MANIFEST, document.metadata.identity.artifact_id).payload["source_video"] == (
        f"video/{video.name}"
    )


def test_collisions_and_unsafe_ids_are_rejected(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path)
    document = _document(ArtifactType.WORKSPACE, {"name": "first"}, "workspace_fixed")
    store.save(document)
    with pytest.raises(DomainError, match="overwrite"):
        store.save(document)
    with pytest.raises(DomainError, match="unsafe"):
        ArtifactIdentity(ArtifactType.WORKSPACE, "../escape")


def test_atomic_failure_cleans_reservation_and_temp_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ArtifactFileStore(tmp_path)
    document = _document(ArtifactType.WORKSPACE, {"name": "fail"}, "workspace_atomic")
    target = tmp_path / "artifacts" / "workspace" / "workspace_atomic.json"

    def fail_replace(*_args: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr("soft_actuator_testing.infrastructure.artifact_store.os.replace", fail_replace)
    with pytest.raises(DomainError, match="atomic write failed"):
        store.save(document)
    assert not target.exists()
    assert not list(target.parent.glob("*.tmp"))


def test_paths_are_portable_after_workspace_relocation_and_escape_is_rejected(tmp_path: Path) -> None:
    original = tmp_path / "original"
    relocated = tmp_path / "relocated"
    original.mkdir()
    source_video = original / "video" / "source.avi"
    source_video.parent.mkdir()
    source_video.touch()
    store = ArtifactFileStore(original)
    document = _document(
        ArtifactType.ANALYSIS_MANIFEST,
        {"source_video": str(source_video), "geometry_artifact_id": "geometry_reference"},
        "analysis_portable",
    )
    store.save(document)
    persisted = json.loads((original / "analysis" / "analysis_portable" / "analysis.json").read_text())
    assert persisted["payload"]["source_video"] == "video/source.avi"
    original.rename(relocated)
    relocated_store = ArtifactFileStore(relocated)
    loaded = relocated_store.load(ArtifactType.ANALYSIS_MANIFEST, "analysis_portable")
    assert relocated_store.resolve_workspace_path(loaded.payload["source_video"]) == relocated / "video" / "source.avi"
    with pytest.raises(DomainError, match="traverse"):
        relocated_store.resolve_workspace_path("../outside")


def test_newer_and_malformed_documents_fail_closed_with_field_paths(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path)
    target = tmp_path / "artifacts" / "workspace" / "workspace_future.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "schema_version": 99,
                "artifact_type": "workspace",
                "artifact_id": "workspace_future",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "software_version": None,
                "payload": {"name": "future"},
            }
        )
    )
    with pytest.raises(DomainError) as raised:
        store.load(ArtifactType.WORKSPACE, "workspace_future")
    assert raised.value.field_path == "schema_version"


def test_versioned_csv_round_trips_quoted_reasons_and_rejects_malformed_rows(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path)
    document = _document(
        ArtifactType.ANALYSIS_RESULTS,
        {
            "rows": [
                {
                    "frame_index": 0,
                    "video_time_seconds": 0.0,
                    "tip_x": 1.0,
                    "tip_y": 2.0,
                    "actuator_angle_degrees": 3.0,
                    "detection_state": "detected",
                    "confidence": 1.0,
                    "correction_applied": False,
                    "detection_reason": 'candidate, "selected"',
                }
            ]
        },
        "analysis_quoted",
    )
    store.save(document)
    path = tmp_path / "analysis" / "analysis_quoted" / "angles.csv"
    assert 'candidate, ""selected""' in path.read_text(encoding="utf-8")
    assert store.load(ArtifactType.ANALYSIS_RESULTS, "analysis_quoted").payload == document.payload

    path.write_text(
        "schema_version,artifact_id,frame_index,video_time_seconds,tip_x,tip_y,"
        "actuator_angle_degrees,detection_state,confidence,correction_applied,detection_reason,legacy_import\n"
        "1,analysis_quoted,0,0,1,2,3,detected,1,maybe,reason,false\n",
        encoding="utf-8",
    )
    with pytest.raises(DomainError) as invalid_bool:
        store.load(ArtifactType.ANALYSIS_RESULTS, "analysis_quoted")
    assert invalid_bool.value.field_path == "rows[0].correction_applied"

    path.write_text(
        "schema_version,artifact_id,frame_index,video_time_seconds,tip_x,tip_y,"
        "actuator_angle_degrees,detection_state,confidence,correction_applied,detection_reason,legacy_import\n"
        "1,other,0,0,1,2,3,detected,1,false,reason,false\n",
        encoding="utf-8",
    )
    with pytest.raises(DomainError) as mismatched_id:
        store.load(ArtifactType.ANALYSIS_RESULTS, "analysis_quoted")
    assert mismatched_id.value.field_path == "rows[0].artifact_id"


def test_versioned_csv_rejects_empty_extra_and_nonfinite_rows(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path)
    directory = tmp_path / "analysis" / "analysis_invalid"
    directory.mkdir(parents=True)
    path = directory / "angles.csv"
    header = (
        "schema_version,artifact_id,frame_index,video_time_seconds,tip_x,tip_y,"
        "actuator_angle_degrees,detection_state,confidence,correction_applied,detection_reason,legacy_import\n"
    )
    path.write_text(header + "\n", encoding="utf-8")
    with pytest.raises(DomainError) as blank:
        store.load(ArtifactType.ANALYSIS_RESULTS, "analysis_invalid")
    assert blank.value.field_path == "row[2]"

    path.write_text(header + "1,analysis_invalid,0,nan,1,2,3,detected,1,false,,false\n", encoding="utf-8")
    with pytest.raises(DomainError) as nonfinite:
        store.load(ArtifactType.ANALYSIS_RESULTS, "analysis_invalid")
    assert nonfinite.value.field_path == "rows[0].video_time_seconds"


def test_json_unknown_nonfinite_payload_and_symlink_escape_fail_closed(tmp_path: Path) -> None:
    store = ArtifactFileStore(tmp_path)
    with pytest.raises(DomainError) as nonfinite:
        store.save(_document(ArtifactType.WORKSPACE, {"name": "test", "unknown": float("nan")}))
    assert nonfinite.value.field_path == "payload.unknown"

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "video").symlink_to(outside, target_is_directory=True)
    with pytest.raises(DomainError) as escaped:
        store.resolve_workspace_path("video/source.avi")
    assert escaped.value.field_path == "path"


def test_atomic_finalization_error_retains_replaced_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ArtifactFileStore(tmp_path)
    document = _document(ArtifactType.WORKSPACE, {"name": "durability"}, "workspace_durable")
    target = tmp_path / "artifacts" / "workspace" / "workspace_durable.json"

    def fail_directory_fsync(_directory: Path) -> None:
        raise OSError("injected directory fsync failure")

    monkeypatch.setattr(store, "_fsync_directory", fail_directory_fsync)
    with pytest.raises(DomainError, match="durability is uncertain") as raised:
        store.save(document)
    assert raised.value.code.name == "ARTIFACT_PUBLICATION_UNCERTAIN"
    assert target.is_file()


def test_legacy_import_rejects_unknown_json_fields_and_malformed_csv_rows(tmp_path: Path) -> None:
    calibration = tmp_path / "calibration.json"
    calibration.write_text(
        json.dumps(
            {
                "model": {"type": "linear", "coeffs": [1.0, 0.0]},
                "samples": [[1.0, 1.0]],
                "unrecognized": "do not guess",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(DomainError) as unknown:
        LegacyArtifactImporter().import_file(calibration, ArtifactType.CALIBRATION)
    assert unknown.value.field_path == "root"

    pressure = tmp_path / "pressure.csv"
    pressure.write_text("time_s,volts,pressure_kPa\n0,1,,extra\n", encoding="utf-8")
    with pytest.raises(DomainError) as malformed:
        LegacyArtifactImporter().import_file(pressure, ArtifactType.PRESSURE_DATA)
    assert malformed.value.field_path == "row[2]"
