# SoftActuatorTesting

**Last Updated:** 2026-07-11

A research repository for testing and characterising soft actuators. This project collects experimental data, analysis scripts, and results related to soft actuator performance evaluation.

## Overview

This repository supports experimental workflows for soft actuator testing, including:

- Data collection and logging from actuator test rigs
- Analysis and post-processing of experimental results
- Documentation of test procedures and configurations

## Repository Structure

| Path | Description |
|------|-------------|
| `README.md` | Project overview and documentation |
| `src/soft_actuator_testing/` | Unified desktop application package |
| `tests/` | Hardware-free tests and compatibility fixtures |
| `docs/continuation-plan.md` | Current status, remaining plan, agent handoff, and restart prompt |
| `docs/architecture/` | Accepted rewrite decisions and implementation test plans |
| `LICENSE` | Project licence |

## Getting Started

Clone the repository, then reproduce the managed development environment with
[`uv`](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/emelieseguin/SoftActuatorTesting.git
cd SoftActuatorTesting
uv sync
uv run pytest
```

The safe scaffold status check does not initialize hardware:

```bash
uv run soft-actuator-testing --no-gui
```

Running `uv run soft-actuator-testing` opens the current real-but-disconnected
production composition. It does not connect to serial devices or cameras during
startup. The production composition is an integration checkpoint and is not yet
the complete ADR 0005-selected Instrument Console; see
[`docs/continuation-plan.md`](docs/continuation-plan.md).

The complete populated Instrument Console prototype remains available with
deterministic demo services:

```bash
uv run soft-actuator-testing --mode demo
```

The rejected Experiment Studio shell remains available only for development
comparison:

```bash
uv run soft-actuator-testing --prototype experiment-studio
```

## Implementation status

Workspace, calibration, serial, FFmpeg recording/preview, manual geometry,
guided red-marker suggestions, artifact persistence, and the core cyclic-run
lifecycle are implemented with hardware-free coverage. Remaining work is the
full production-shell composition, angle-analysis pipeline and review UI,
quality hardening, Windows/Linux packaging, and operator/maintainer handoff.
Physical 3840x2160 at 60 fps certification remains blocked on representative
hardware and explicit acceptance thresholds.

## Contributing

Please open an issue or pull request for any proposed changes or additions.
