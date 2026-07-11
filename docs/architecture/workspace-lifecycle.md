# Workspace lifecycle and test plan

**Status:** Implemented.
**Date:** 2026-07-11.
**Related todo:** `workspace-files`.

## Decision

`application.workspace` owns immutable workspace snapshots, commands, and a
Qt-free controller. `infrastructure.workspace` supplies the replaceable JSON
preferences adapter. The controller is the only workspace filesystem client:
views render its snapshots and dispatch its commands; they do not derive labels
by inspecting paths.

A workspace is a directory containing one or more versioned workspace
documents written through `ArtifactFileStore`. Saving creates a new workspace
document instead of overwriting an existing document. References remain
workspace-relative and are resolved through `ArtifactFileStore`, so relocation
does not depend on the process current working directory. A restored workspace
only inspects persisted artifacts; it never owns or invokes serial/camera
services.

Recent roots and the selected writable storage root are persisted through the
`WorkspaceSettings` protocol. The concrete JSON adapter uses an explicit
settings path (or an OS configuration location), not the current directory.
Individual files are an explicit read-only mode and are not mistaken for a
workspace.

## Test plan

- Unit/integration tests create, save, reopen, relocate, and close workspaces;
  verify collision-safe manifests, portable references, recents, and
  restart-safe preferences.
- Exercise invalid/missing directories, corrupt/newer-schema documents,
  traversal references, cancellation, and individual-file mode with actionable
  issues.
- `pytest-qt` tests drive picker commands and drag/drop validation through the
  workspace view using only `FakeFilePicker`.
- Use a guard service in restoration tests to prove opening a workspace does
  not attempt hardware access. Run the workspace subsets and then the full
  hardware-excluded suite.
