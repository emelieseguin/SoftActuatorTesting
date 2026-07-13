# Documentation index

All operational procedures, architecture records, and compatibility evidence
live under `docs/`.

## Use and support

- [Operator guide](operator-guide.md) — installation, safe startup, the complete
  production Instrument Console workflow, data handling, accessibility, and
  safety limits.
- [Troubleshooting](troubleshooting.md) — symptom-based recovery, diagnostics to
  collect, and conditions that require stopping a run.
- [Hardware 4K60 acceptance](hardware-4k60-acceptance.md) — the
  owner-threshold and native-rig evidence procedure; it is not a certification.

## Maintenance and release

- [Maintainer guide](maintainer-guide.md) — layering, lifecycle ownership,
  protocol uncertainty, analysis authority, and approved extension boundaries.
- [Artifact schemas and compatibility](artifact-schemas.md) — V1 JSON/CSV
  contracts, paths, atomicity, and legacy import/export rules.
- [Test and release guide](test-and-release.md) — default and opt-in test
  selection, safe CLI checks, native packaging, notices, and release checklist.
- [Continuation plan](continuation-plan.md) — final software handoff, invariants,
  verification commands, and the remaining external hardware decisions.

## Design and history

- [Architecture records](architecture/README.md) — accepted decisions and
  implementation/hardening/packaging records for the unified application.
- [Legacy implementation inventory](initial-implementation/README.md) —
  fact-checked historical scripts and notebook behavior retained solely for
  compatibility context.

For packaging redistribution and runtime-license obligations, start with
[desktop packaging](architecture/desktop-packaging.md) and
[dependency licenses](architecture/dependency-licenses.md).
