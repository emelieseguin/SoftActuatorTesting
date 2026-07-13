"""Qt-free workspace lifecycle commands and immutable presentation snapshots."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import json
import os
from pathlib import Path
import shutil
from threading import RLock
from typing import Protocol, runtime_checkable

from soft_actuator_testing.application.services import ArtifactDocument
from soft_actuator_testing.domain.artifacts import ArtifactIdentity, ArtifactMetadata, ArtifactType, require_supported_schema_version
from soft_actuator_testing.domain.errors import DomainError, ErrorCode


class WorkspaceMode(str, Enum):
    NONE = "none"
    WORKSPACE = "workspace"
    INDIVIDUAL_FILES = "individual-files"


@dataclass(frozen=True)
class WorkspaceArtifactSummary:
    kind: str
    location: Path
    status: str
    detail: str
    artifact_id: str | None = None


@dataclass(frozen=True)
class WorkspaceIssue:
    location: Path | None
    message: str
    field_path: str | None = None


@dataclass(frozen=True)
class WorkspacePreferences:
    storage_root: Path | None = None
    recent_workspaces: tuple[Path, ...] = ()


@runtime_checkable
class WorkspaceSettings(Protocol):
    """Replaceable persistence boundary for non-artifact workspace preferences."""

    def load(self) -> WorkspacePreferences: ...

    def save(self, preferences: WorkspacePreferences) -> None: ...


@runtime_checkable
class WorkspaceArtifactStore(Protocol):
    """The subset of versioned artifact persistence used by a workspace."""

    def load(self, artifact_type: ArtifactType, artifact_id: str) -> ArtifactDocument: ...

    def save(self, document: ArtifactDocument) -> None: ...

    def resolve_workspace_path(self, relative_path: str | Path) -> Path: ...


@dataclass(frozen=True)
class WorkspaceSnapshot:
    mode: WorkspaceMode
    root: Path | None
    name: str | None
    storage_root: Path | None
    recent_workspaces: tuple[Path, ...]
    artifacts: tuple[WorkspaceArtifactSummary, ...] = ()
    issues: tuple[WorkspaceIssue, ...] = ()
    status: str = "No workspace is open."
    can_save: bool = False
    revision: int = 0


@dataclass(frozen=True)
class CreateWorkspace:
    name: str
    storage_root: Path | None = None


@dataclass(frozen=True)
class OpenWorkspace:
    path: Path


@dataclass(frozen=True)
class SaveWorkspace:
    pass


@dataclass(frozen=True)
class CloseWorkspace:
    pass


@dataclass(frozen=True)
class OpenIndividualFiles:
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class SetStorageRoot:
    path: Path


WorkspaceCommand = CreateWorkspace | OpenWorkspace | SaveWorkspace | CloseWorkspace | OpenIndividualFiles | SetStorageRoot


@dataclass(frozen=True)
class WorkspaceCommandResult:
    accepted: bool
    message: str


class WorkspaceStateStore:
    """Small synchronous state source that makes workspace rendering authoritative."""

    def __init__(self, snapshot: WorkspaceSnapshot) -> None:
        self._snapshot = snapshot
        self._listeners: dict[int, Callable[[WorkspaceSnapshot], None]] = {}
        self._next_listener = 1
        self._lock = RLock()

    @property
    def snapshot(self) -> WorkspaceSnapshot:
        with self._lock:
            return self._snapshot

    def publish(self, snapshot: WorkspaceSnapshot) -> None:
        with self._lock:
            if snapshot == self._snapshot:
                return
            self._snapshot = snapshot
            listeners = tuple(self._listeners.values())
        for listener in listeners:
            listener(snapshot)

    def subscribe(self, listener: Callable[[WorkspaceSnapshot], None], *, emit_current: bool = False) -> Callable[[], None]:
        with self._lock:
            identifier = self._next_listener
            self._next_listener += 1
            self._listeners[identifier] = listener
            current = self._snapshot

        def unsubscribe() -> None:
            with self._lock:
                self._listeners.pop(identifier, None)

        if emit_current:
            listener(current)
        return unsubscribe


class WorkspaceController:
    """Filesystem-owning workspace service; it never receives hardware services."""

    def __init__(
        self,
        settings: WorkspaceSettings,
        *,
        store_factory: Callable[[Path], WorkspaceArtifactStore],
        software_version: str | None = None,
        mutation_guard: Callable[[], str | None] | None = None,
    ) -> None:
        self._settings = settings
        self._store_factory = store_factory
        self._software_version = software_version
        self._mutation_guard = mutation_guard
        preferences = settings.load()
        self._snapshot = WorkspaceSnapshot(
            mode=WorkspaceMode.NONE,
            root=None,
            name=None,
            storage_root=preferences.storage_root,
            recent_workspaces=preferences.recent_workspaces,
        )
        self.state = WorkspaceStateStore(self._snapshot)

    @property
    def snapshot(self) -> WorkspaceSnapshot:
        return self.state.snapshot

    def dispatch(self, command: WorkspaceCommand) -> WorkspaceCommandResult:
        if isinstance(
            command,
            (SetStorageRoot, CreateWorkspace, OpenWorkspace, CloseWorkspace, OpenIndividualFiles),
        ) and self._mutation_guard is not None:
            reason = self._mutation_guard()
            if reason:
                self._replace(
                    issues=(WorkspaceIssue(self.snapshot.root, reason, "workspace"),),
                    status=f"Workspace action blocked: {reason}",
                )
                return WorkspaceCommandResult(False, reason)
        try:
            result = self._dispatch(command)
        except (DomainError, OSError, ValueError) as error:
            issue = WorkspaceIssue(None, str(error), getattr(error, "field_path", None))
            self._replace(issues=(issue,), status=f"Workspace action failed: {error}")
            return WorkspaceCommandResult(False, str(error))
        return result

    def _dispatch(self, command: WorkspaceCommand) -> WorkspaceCommandResult:
        if isinstance(command, SetStorageRoot):
            root = self._require_directory(command.path, "storage_root")
            if not os.access(root, os.W_OK | os.X_OK):
                raise self._error("storage root is not writable", "storage_root")
            self._replace(storage_root=root, issues=(), status=f"Storage root set to {root}.")
            self._persist_preferences()
            return WorkspaceCommandResult(True, "Storage root selected.")
        if isinstance(command, CreateWorkspace):
            return self._create(command)
        if isinstance(command, OpenWorkspace):
            return self._open(command.path)
        if isinstance(command, SaveWorkspace):
            return self._save()
        if isinstance(command, CloseWorkspace):
            self._replace(
                mode=WorkspaceMode.NONE,
                root=None,
                name=None,
                artifacts=(),
                issues=(),
                status="Workspace closed.",
                can_save=False,
            )
            return WorkspaceCommandResult(True, "Workspace closed.")
        if isinstance(command, OpenIndividualFiles):
            return self._open_individual(command.paths)
        raise self._error("unknown workspace command", "command")

    def _create(self, command: CreateWorkspace) -> WorkspaceCommandResult:
        name = command.name.strip()
        if not name or Path(name).name != name or name in {".", ".."}:
            raise self._error("workspace name must be a single non-empty directory name", "name")
        storage_root = command.storage_root or self.snapshot.storage_root
        if storage_root is None:
            raise self._error("select a writable storage root before creating a workspace", "storage_root")
        root = self._require_directory(storage_root, "storage_root")
        if not os.access(root, os.W_OK | os.X_OK):
            raise self._error("storage root is not writable", "storage_root")
        workspace_root = root / name
        if workspace_root.exists():
            raise self._error("refusing to overwrite an existing workspace directory", "name")
        workspace_root.mkdir()
        try:
            self._write_manifest(workspace_root, name)
        except DomainError as error:
            if error.code is not ErrorCode.ARTIFACT_PUBLICATION_UNCERTAIN:
                shutil.rmtree(workspace_root, ignore_errors=True)
            raise
        except Exception:
            shutil.rmtree(workspace_root, ignore_errors=True)
            raise
        self._activate_workspace(workspace_root, name, status=f"Created workspace {workspace_root}.")
        return WorkspaceCommandResult(True, "Workspace created.")

    def _open(self, requested: Path) -> WorkspaceCommandResult:
        root = self._workspace_root_for(requested)
        manifests, manifest_issues = self._workspace_documents(root)
        if not manifests:
            issues = manifest_issues or (
                WorkspaceIssue(root, "No valid versioned workspace document was found.", "artifacts.workspace"),
            )
            if self.snapshot.mode is WorkspaceMode.WORKSPACE:
                self._replace(
                    issues=issues,
                    status=f"Workspace could not be opened; active workspace {self.snapshot.root} is unchanged.",
                )
                return WorkspaceCommandResult(False, issues[0].message)
            self._replace(
                mode=WorkspaceMode.NONE,
                root=None,
                name=None,
                artifacts=(),
                issues=issues,
                status="Workspace could not be opened.",
                can_save=False,
            )
            return WorkspaceCommandResult(False, issues[0].message)
        latest = max(manifests, key=lambda document: document.metadata.updated_at)
        self._activate_workspace(root, str(latest.payload["name"]), status=f"Opened workspace {root}.")
        if manifest_issues:
            self._replace(
                issues=manifest_issues + self.snapshot.issues,
                status=f"{self.snapshot.status} {len(manifest_issues)} workspace manifest issue(s) need attention.",
            )
        return WorkspaceCommandResult(True, "Workspace opened.")

    def _save(self) -> WorkspaceCommandResult:
        snapshot = self.snapshot
        if snapshot.mode is not WorkspaceMode.WORKSPACE or snapshot.root is None or snapshot.name is None:
            raise self._error("open a workspace before saving", "workspace")
        self._write_manifest(snapshot.root, snapshot.name)
        self._activate_workspace(snapshot.root, snapshot.name, status=f"Saved a new workspace manifest in {snapshot.root}.")
        return WorkspaceCommandResult(True, "Workspace saved.")

    def _open_individual(self, paths: tuple[Path, ...]) -> WorkspaceCommandResult:
        if not paths:
            raise self._error("choose at least one file", "paths")
        artifacts: list[WorkspaceArtifactSummary] = []
        issues: list[WorkspaceIssue] = []
        for requested in paths:
            path = self._absolute_path(requested, "paths")
            if not path.is_file():
                issues.append(WorkspaceIssue(path, "Selected file does not exist.", "path"))
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise self._error("artifact document must be an object", "artifact")
                require_supported_schema_version(value.get("schema_version"))
                artifact_type = ArtifactType(value.get("artifact_type"))
                artifact_id = value.get("artifact_id")
                if not isinstance(artifact_id, str):
                    raise self._error("artifact_id must be a string", "artifact_id")
                artifacts.append(
                    WorkspaceArtifactSummary(
                        artifact_type.value,
                        path,
                        "loaded",
                        f"Individual {artifact_type.value} artifact.",
                        artifact_id,
                    )
                )
            except (DomainError, OSError, ValueError, json.JSONDecodeError) as error:
                issues.append(WorkspaceIssue(path, str(error), getattr(error, "field_path", None)))
                artifacts.append(WorkspaceArtifactSummary("unknown", path, "invalid", str(error)))
        self._replace(
            mode=WorkspaceMode.INDIVIDUAL_FILES,
            root=None,
            name=None,
            artifacts=tuple(artifacts),
            issues=tuple(issues),
            status="Opened individual files; this selection cannot be saved as a workspace.",
            can_save=False,
        )
        return WorkspaceCommandResult(not issues, "Individual files opened." if not issues else "Some selected files are invalid.")

    def _activate_workspace(self, root: Path, name: str, *, status: str) -> None:
        artifacts, issues = self._artifact_summaries(root)
        recents = self._updated_recents(root)
        self._replace(
            mode=WorkspaceMode.WORKSPACE,
            root=root,
            name=name,
            artifacts=tuple(artifacts),
            issues=tuple(issues),
            recent_workspaces=recents,
            status=status if not issues else f"{status} {len(issues)} issue(s) need attention.",
            can_save=True,
        )
        self._persist_preferences()

    def _artifact_summaries(self, root: Path) -> tuple[list[WorkspaceArtifactSummary], list[WorkspaceIssue]]:
        store = self._store_factory(root)
        summaries: list[WorkspaceArtifactSummary] = []
        issues: list[WorkspaceIssue] = []
        for kind, directory in ((ArtifactType.CALIBRATION, root / "artifacts" / "calibration"), (ArtifactType.GEOMETRY, root / "artifacts" / "geometry")):
            for path in sorted(directory.glob("*.json")) if directory.is_dir() else ():
                self._summarize_document(store, kind, path.stem, path, summaries, issues)
        video_directory = root / "video"
        if video_directory.is_dir():
            for path in sorted(candidate for candidate in video_directory.rglob("*") if candidate.is_file()):
                try:
                    path.resolve().relative_to(root)
                except ValueError:
                    issues.append(
                        WorkspaceIssue(
                            path,
                            "Workspace video path escapes the workspace through a symbolic link.",
                            "video",
                        )
                    )
                    summaries.append(WorkspaceArtifactSummary("video", path, "invalid", "Path escapes workspace."))
                    continue
                summaries.append(WorkspaceArtifactSummary("video", path, "loaded", "Workspace video file."))
        analysis_directory = root / "analysis"
        if analysis_directory.is_dir():
            for path in sorted(analysis_directory.glob("*/analysis.json")):
                document = self._summarize_document(
                    store, ArtifactType.ANALYSIS_MANIFEST, path.parent.name, path, summaries, issues
                )
                if document is not None:
                    self._check_analysis_source(store, document, path, summaries, issues)
                    self._check_analysis_geometry(store, document, path, summaries, issues)
        run_directory = root / "runs"
        if run_directory.is_dir():
            for path in sorted(run_directory.glob("*/run.json")):
                document = self._summarize_document(
                    store, ArtifactType.RUN_MANIFEST, path.parent.name, path, summaries, issues
                )
                if document is not None:
                    self._check_run_outputs(store, document, path, summaries, issues)
        return summaries, issues

    def _summarize_document(
        self,
        store: WorkspaceArtifactStore,
        kind: ArtifactType,
        artifact_id: str,
        path: Path,
        summaries: list[WorkspaceArtifactSummary],
        issues: list[WorkspaceIssue],
    ) -> ArtifactDocument | None:
        try:
            document = store.load(kind, artifact_id)
            summaries.append(WorkspaceArtifactSummary(kind.value, path, "loaded", self._detail(document), artifact_id))
            return document
        except DomainError as error:
            summaries.append(WorkspaceArtifactSummary(kind.value, path, "invalid", str(error), artifact_id))
            issues.append(WorkspaceIssue(path, str(error), error.field_path))
            return None

    def _check_analysis_source(
        self,
        store: ArtifactFileStore,
        document: ArtifactDocument,
        path: Path,
        summaries: list[WorkspaceArtifactSummary],
        issues: list[WorkspaceIssue],
    ) -> None:
        try:
            source = store.resolve_workspace_path(document.payload["source_video"])
        except DomainError as error:
            issues.append(WorkspaceIssue(path, str(error), error.field_path))
            return
        if source.is_file():
            summaries.append(WorkspaceArtifactSummary("video", source, "loaded", "Analysis source video."))
        else:
            issues.append(WorkspaceIssue(source, "Analysis source video is missing.", "payload.source_video"))
            summaries.append(WorkspaceArtifactSummary("video", source, "missing", "Referenced by analysis artifact."))

    def _check_analysis_geometry(
        self,
        store: ArtifactFileStore,
        document: ArtifactDocument,
        path: Path,
        summaries: list[WorkspaceArtifactSummary],
        issues: list[WorkspaceIssue],
    ) -> None:
        geometry_id = document.payload["geometry_artifact_id"]
        try:
            store.load(ArtifactType.GEOMETRY, geometry_id)
        except DomainError as error:
            issues.append(
                WorkspaceIssue(
                    path,
                    f"Referenced geometry artifact is unavailable: {error}",
                    "payload.geometry_artifact_id",
                )
            )
            summaries.append(
                WorkspaceArtifactSummary(
                    ArtifactType.GEOMETRY.value,
                    path,
                    "missing",
                    f"Referenced geometry artifact {geometry_id} is unavailable.",
                    geometry_id,
                )
            )

    def _check_run_outputs(
        self,
        store: ArtifactFileStore,
        document: ArtifactDocument,
        path: Path,
        summaries: list[WorkspaceArtifactSummary],
        issues: list[WorkspaceIssue],
    ) -> None:
        output_files = document.payload.get("output_files", [])
        if not isinstance(output_files, list):
            return
        for index, relative_path in enumerate(output_files):
            try:
                output = store.resolve_workspace_path(relative_path)
            except DomainError as error:
                issues.append(WorkspaceIssue(path, str(error), f"payload.output_files[{index}]"))
                continue
            if output.is_file():
                summaries.append(WorkspaceArtifactSummary("run_output", output, "loaded", "Run output file."))
            else:
                issues.append(WorkspaceIssue(output, "Run output file is missing.", f"payload.output_files[{index}]"))
                summaries.append(WorkspaceArtifactSummary("run_output", output, "missing", "Referenced by run manifest."))

    def _workspace_documents(self, root: Path) -> tuple[list[ArtifactDocument], tuple[WorkspaceIssue, ...]]:
        directory = root / "artifacts" / "workspace"
        if not directory.is_dir():
            return [], ()
        store = self._store_factory(root)
        documents: list[ArtifactDocument] = []
        issues: list[WorkspaceIssue] = []
        for path in sorted(directory.glob("*.json")):
            try:
                documents.append(store.load(ArtifactType.WORKSPACE, path.stem))
            except DomainError as error:
                issues.append(WorkspaceIssue(path, str(error), error.field_path))
        return documents, tuple(issues)

    def _write_manifest(self, root: Path, name: str) -> None:
        identity = ArtifactIdentity.new(ArtifactType.WORKSPACE)
        document = ArtifactDocument(
            ArtifactMetadata.now(identity, self._software_version),
            {"name": name, "saved_at": datetime.now().astimezone().isoformat()},
        )
        self._store_factory(root).save(document)

    def _workspace_root_for(self, requested: Path) -> Path:
        path = self._absolute_path(requested, "path")
        if path.is_dir():
            return path
        if path.is_file() and path.parent.name == "workspace" and path.parent.parent.name == "artifacts":
            return path.parent.parent.parent
        raise self._error("choose a workspace directory or a versioned workspace manifest", "path")

    def _updated_recents(self, root: Path) -> tuple[Path, ...]:
        return tuple([root, *(item for item in self.snapshot.recent_workspaces if item != root)][:10])

    def _persist_preferences(self) -> None:
        snapshot = self.snapshot
        self._settings.save(WorkspacePreferences(snapshot.storage_root, snapshot.recent_workspaces))

    def _replace(self, **changes: object) -> None:
        self._snapshot = replace(self._snapshot, revision=self._snapshot.revision + 1, **changes)
        self.state.publish(self._snapshot)

    @staticmethod
    def _detail(document: ArtifactDocument) -> str:
        identity = document.metadata.identity
        if identity.artifact_type is ArtifactType.CALIBRATION:
            return f"Calibration {document.payload['model']['type']} model."
        if identity.artifact_type is ArtifactType.GEOMETRY:
            return "Geometry configuration."
        if identity.artifact_type is ArtifactType.ANALYSIS_MANIFEST:
            return "Analysis manifest."
        return f"{identity.artifact_type.value} artifact."

    @staticmethod
    def _absolute_path(value: Path, field_path: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise WorkspaceController._error("path must be absolute; relative paths are not resolved from the current directory", field_path)
        return path.resolve()

    def _require_directory(self, value: Path, field_path: str) -> Path:
        path = self._absolute_path(value, field_path)
        if not path.is_dir():
            raise self._error("path must be an existing directory", field_path)
        return path

    @staticmethod
    def _error(message: str, field_path: str) -> DomainError:
        return DomainError(ErrorCode.ARTIFACT_INVALID, message, field_path)
