"""Scaffold package and entry-point smoke tests."""

from __future__ import annotations

import os
import subprocess
import sys
from importlib.metadata import entry_points
from types import ModuleType

import pytest

import soft_actuator_testing
from soft_actuator_testing.bootstrap import main


def test_package_exposes_version() -> None:
    assert soft_actuator_testing.__version__ == "0.1.0"


def test_console_entry_point_targets_bootstrap() -> None:
    scripts = entry_points(group="console_scripts")
    entry_point = scripts["soft-actuator-testing"]
    assert entry_point.value == "soft_actuator_testing.bootstrap:main"


def test_module_entry_point_reports_safe_scaffold_status() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "soft_actuator_testing", "--no-gui"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Production lifecycle composition is available; no hardware was initialized." in result.stdout


def test_normal_cli_launch_selects_the_default_shell(monkeypatch) -> None:
    from soft_actuator_testing.ui import app

    selected: list[str | None] = []
    monkeypatch.setattr(
        app,
        "run_application",
        lambda *, prototype_shell=None, production=False: selected.append(prototype_shell) or 17,
    )

    assert main([]) == 17
    assert selected == [None]


def test_explicit_prototype_cli_launches_experiment_studio(monkeypatch) -> None:
    from soft_actuator_testing.ui import app

    selected: list[str | None] = []
    monkeypatch.setattr(
        app,
        "run_application",
        lambda *, prototype_shell=None, production=False: selected.append(prototype_shell) or 19,
    )

    assert main(["--prototype", "experiment-studio"]) == 19
    assert selected == ["experiment-studio"]


def test_explicit_mode_production_with_prototype_is_rejected_with_exit_code_2(capsys) -> None:
    """The CLI must reject, not silently resolve, an ambiguous request.

    ``--mode production`` combined with ``--prototype`` asks for two
    mutually-exclusive shells at once (the real production Instrument
    Console vs. the demo-only Experiment Studio prototype); argparse's
    ``parser.error`` gives a deterministic ``SystemExit(2)`` with an
    actionable message on stderr, rather than silently picking one.
    """

    with pytest.raises(SystemExit) as exc_info:
        main(["--mode", "production", "--prototype", "experiment-studio"])

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "--mode production cannot be combined with --prototype" in stderr
    assert "experiment-studio" in stderr


def test_explicit_mode_production_with_prototype_is_rejected_end_to_end() -> None:
    """Same rejection, exercised through the real console-script entry point."""

    result = subprocess.run(
        [sys.executable, "-m", "soft_actuator_testing", "--mode", "production", "--prototype", "experiment-studio"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--mode production cannot be combined with --prototype" in result.stderr


def test_mode_demo_with_prototype_is_not_rejected(monkeypatch) -> None:
    """Only the explicit production+prototype combination is rejected."""

    from soft_actuator_testing.ui import app

    selected: list[tuple[str | None, bool]] = []
    monkeypatch.setattr(
        app,
        "run_application",
        lambda *, prototype_shell=None, production=False: selected.append((prototype_shell, production)) or 21,
    )

    assert main(["--mode", "demo", "--prototype", "experiment-studio"]) == 21
    assert selected == [("experiment-studio", False)]


def test_explicit_mode_production_without_prototype_still_launches_production(monkeypatch) -> None:
    from soft_actuator_testing.ui import app

    selected: list[tuple[str | None, bool]] = []
    monkeypatch.setattr(
        app,
        "run_application",
        lambda *, prototype_shell=None, production=False: selected.append((prototype_shell, production)) or 23,
    )

    assert main(["--mode", "production"]) == 23
    assert selected == [(None, True)]


def test_no_gui_returns_without_importing_or_launching_ui(monkeypatch, capsys) -> None:
    class ForbiddenUiModule(ModuleType):
        def __getattr__(self, name: str):
            raise AssertionError(f"--no-gui accessed UI attribute {name}")

    monkeypatch.setitem(
        sys.modules,
        "soft_actuator_testing.ui.app",
        ForbiddenUiModule("soft_actuator_testing.ui.app"),
    )

    assert main(["--no-gui", "--prototype", "experiment-studio"]) == 0
    assert "Production lifecycle composition is available; no hardware was initialized." in capsys.readouterr().out


def test_production_composition_does_not_import_demo_services() -> None:
    script = """
import os
import sys
from types import ModuleType

os.environ["QT_QPA_PLATFORM"] = "offscreen"
blocked_demo = ModuleType("soft_actuator_testing.ui.demo")
sys.modules["soft_actuator_testing.ui.demo"] = blocked_demo

from PySide6.QtWidgets import QApplication
from soft_actuator_testing.ui.production import create_production_composition

application = QApplication([])
composition = create_production_composition()
assert composition.window.environment is None
composition.window.close()
application.quit()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.stderr == ""
