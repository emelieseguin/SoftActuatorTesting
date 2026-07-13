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
            else default_workspace_settings_path()
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
        storage_root = self._preference_path(preferences.storage_root, "storage_root")
        recent_workspaces = tuple(
            self._preference_path(path, f"recent_workspaces[{index}]")
            for index, path in enumerate(preferences.recent_workspaces)
        )
        payload = {
            "schema_version": 1,
            "storage_root": str(storage_root) if storage_root else None,
            "recent_workspaces": [str(path) for path in recent_workspaces],
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
            self._fsync_directory(self.path.parent)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _absolute(value: object) -> Path | None:
        if not isinstance(value, str):
            return None
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else None

    @staticmethod
    def _preference_path(value: Path | None, field_path: str) -> Path | None:
        if value is None:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{field_path} must be an absolute path")
        return path.resolve()

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def default_workspace_settings_path(
    *,
    environment: dict[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    """Return the current user's native configuration location without using CWD."""

    env = os.environ if environment is None else environment
    native_platform = os.name if platform is None else platform
    if native_platform == "nt":
        app_data = env.get("APPDATA")
        root = Path(app_data).expanduser() if app_data else Path.home() / "AppData" / "Roaming"
        return (root / "SoftActuatorTesting" / "workspace-settings.json").resolve()
    config_home = env.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return (root / "soft-actuator-testing" / "workspace-settings.json").resolve()
