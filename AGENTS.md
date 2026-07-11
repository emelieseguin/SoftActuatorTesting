# AGENTS.md

Guidelines and rules for anyone (human or AI agent) working on the SoftActuatorTesting project.

## Language & Tooling

- **Python only.** All code in this project must be written in Python.
- **Project management via [uv](https://docs.astral.sh/uv/).** The project should be structured to be fully compatible with `uv`:
  - Use `pyproject.toml` as the single source of truth for dependencies and project metadata.
  - Use `uv add` / `uv remove` to manage dependencies (do not hand-edit lockfiles).
  - Use `uv run` to execute scripts and tools within the managed environment.
  - Use `uv sync` to install/reproduce the environment.

## Repository Structure

- **`/src/*`** — All source code lives here. No application/analysis code should live at the repository root or scattered in ad-hoc folders.
- **`/docs/*`** — All documentation lives here. This includes design notes, architecture decisions, data collection procedures, and analysis methodology.
- Legacy/one-off files (e.g. `old-files/`) should be migrated into `/src` and `/docs` as they are cleaned up, or removed if obsolete.

## Documentation Requirements

- **Important decisions and code must be documented in `/docs`.**
  - Any non-trivial design or architectural decision (e.g. why a library was chosen, how a test rig is configured, data format decisions) must be recorded in `/docs`.
  - Documentation must be kept up to date as the code changes — treat outdated docs as a bug.
  - When adding or changing a significant module in `/src`, update or add corresponding documentation in `/docs` in the same change.
  - Prefer concise, dated entries so history of decisions is traceable.

## Testing

- Unit testing is expected and encouraged for code under `/src`.
- Use `pytest` (run via `uv run pytest`) as the standard test framework, with tests mirroring the `/src` structure (e.g. `tests/data_collection`, `tests/analysis`).
- Before implementing a non-trivial feature or change, an agent should first create a short test plan describing what will be tested and how (unit tests, edge cases, expected behavior), and note it in `/docs` alongside the related decision/documentation.
- New or changed functionality should include corresponding unit tests in the same change.

## General Expectations for Agents

- Before making changes, check `/docs` for existing context on the area being modified.
- After making a change that affects behavior, structure, or decisions, update the relevant file(s) in `/docs`.
- Keep new code under `/src`, organized by logical sub-module (e.g. `/src/data_collection`, `/src/analysis`).
- Do not introduce non-Python tooling or alternative package managers (pip-only workflows, poetry, conda, etc.) without explicit discussion — `uv` is the standard.
