"""Restart-safe tests for the replaceable workspace preference adapter."""

from __future__ import annotations

import json
from pathlib import Path

from soft_actuator_testing.application.workspace import WorkspacePreferences
from soft_actuator_testing.infrastructure.workspace import JsonWorkspaceSettings


def test_json_workspace_settings_round_trip_without_current_directory(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state" / "settings.json"
    storage = tmp_path / "storage"
    recent = tmp_path / "recent"
    monkeypatch.chdir(tmp_path)
    JsonWorkspaceSettings(path).save(WorkspacePreferences(storage, (recent,)))

    restarted = JsonWorkspaceSettings(path)
    assert restarted.load() == WorkspacePreferences(storage, (recent,))


def test_json_workspace_settings_recovers_from_invalid_content(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"schema_version": 99, "storage_root": "relative"}), encoding="utf-8")
    assert JsonWorkspaceSettings(path).load() == WorkspacePreferences()
