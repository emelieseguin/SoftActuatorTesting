"""Scaffold package and entry-point smoke tests."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import entry_points
from types import ModuleType

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
