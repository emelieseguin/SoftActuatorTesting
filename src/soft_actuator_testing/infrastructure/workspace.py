"""Concrete replaceable workspace-preference persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from soft_actuator_testing.application.workspace import WorkspacePreferences


class JsonWorkspaceSettings:
    """Persist workspace preferences at an explicit, current-directory-independent path."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (
            Path(path).expanduser().resolve()
            if path is not None
            else (Path.home() / ".config" / "soft-actuator-testing" / "workspace-settings.json").resolve()
        )

    def load(self) -> WorkspacePreferences:
        if not self.path.is_file():
            return WorkspacePreferences()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if value.get("schema_version") != 1:
                return WorkspacePreferences()
            storage = self._absolute(value.get("storage_root"))
            recents = tuple(
                path
                for item in value.get("recent_workspaces", [])
                if (path := self._absolute(item)) is not None
            )
            return WorkspacePreferences(storage, recents)
        except (OSError, ValueError, json.JSONDecodeError, AttributeError):
            return WorkspacePreferences()

    def save(self, preferences: WorkspacePreferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "storage_root": str(preferences.storage_root) if preferences.storage_root else None,
            "recent_workspaces": [str(path) for path in preferences.recent_workspaces],
        }
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _absolute(value: object) -> Path | None:
        if not isinstance(value, str):
            return None
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else None
