# Documentation index

Per `AGENTS.md`, all design notes, architecture decisions, and procedures
live under `/docs`. Start with:

- [`continuation-plan.md`](continuation-plan.md) — the current implementation
  status, remaining work graph, invariants, stopped sub-agent ledger,
  verification commands, and exact restart prompt.
- [`architecture/`](architecture/README.md) — accepted architecture decisions
  and implementation/test records for the unified rewrite, including capture,
  serial, calibration, geometry, marker suggestions, and run lifecycle.
- [`initial-implementation/`](initial-implementation/README.md) — the
  legacy workflow inventory: a fact-checked analysis of the current scripts
  and notebook under [`../old-files/`](../old-files/), kept as the
  historical starting point and legacy-import reference for the rewrite.

Use [`architecture/README.md`](architecture/README.md) for fixed design
decisions and [`initial-implementation/README.md`](initial-implementation/README.md)
for the original behavior that compatibility work must preserve.
