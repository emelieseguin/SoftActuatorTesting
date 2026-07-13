"""Run hardware-free smoke checks against a native desktop bundle."""

from __future__ import annotations

import argparse
import os
import runpy
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_NAME = "SoftActuatorTesting"
UI_SMOKE_APPLICATION_NAME = "SoftActuatorTestingUiSmoke"
UI_SMOKE_TIMEOUT_SECONDS = 30
_UI_SMOKE_MODULE = runpy.run_path(str(REPOSITORY_ROOT / "tools" / "packaging_ui_smoke.py"))


def _default_artifact(platform: str) -> Path:
    executable = f"{APPLICATION_NAME}.exe" if platform == "windows" else APPLICATION_NAME
    return REPOSITORY_ROOT / "dist" / "desktop" / platform / APPLICATION_NAME / executable


def _default_ui_smoke_artifact(platform: str) -> Path:
    executable = f"{UI_SMOKE_APPLICATION_NAME}.exe" if platform == "windows" else UI_SMOKE_APPLICATION_NAME
    return REPOSITORY_ROOT / "dist" / "desktop" / platform / UI_SMOKE_APPLICATION_NAME / executable


def _default_ui_smoke_preferences_path(platform: str) -> Path:
    return REPOSITORY_ROOT / "build" / "desktop" / platform / "ui-smoke" / "smoke-workspace-settings.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("linux", "windows"), required=True)
    parser.add_argument("--artifact", type=Path, help="override the packaged executable path")
    parser.add_argument(
        "--ui-smoke-artifact",
        type=Path,
        help="override the packaged UI-smoke executable path (see --component ui-smoke)",
    )
    parser.add_argument(
        "--skip-ui-smoke",
        action="store_true",
        help="skip constructing the packaged production Instrument Console (import/version checks only)",
    )
    return parser


def _run(executable: Path, arguments: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    return subprocess.run(
        [str(executable), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=environment,
    )


def _run_ui_smoke(executable: Path, platform: str) -> None:
    if not executable.is_file():
        raise SystemExit(
            f"Packaged UI-smoke executable is missing: {executable}. Build it with "
            "'uv run python tools/package_desktop.py --platform "
            f"{platform} --component ui-smoke'."
        )
    preferences_path = _default_ui_smoke_preferences_path(platform)
    preferences_path.parent.mkdir(parents=True, exist_ok=True)
    if preferences_path.is_file():
        preferences_path.unlink()
    result = _run(
        executable,
        ["--preferences-path", str(preferences_path)],
        timeout=UI_SMOKE_TIMEOUT_SECONDS,
    )
    output = f"{result.stdout}\n{result.stderr}"
    success_message = _UI_SMOKE_MODULE["SUCCESS_MESSAGE"]
    demo_module_name = _UI_SMOKE_MODULE["DEMO_MODULE_NAME"]
    if result.returncode != 0 or success_message not in output:
        raise SystemExit(f"packaged UI smoke failed for {executable} (exit {result.returncode}):\n{output}")
    if "demo_module_imported=False" not in output:
        raise SystemExit(f"packaged UI smoke imported {demo_module_name} (must stay production-only):\n{output}")
    if "serial_status=DISCONNECTED" not in output:
        raise SystemExit(f"packaged UI smoke did not keep the serial seam lazily disconnected:\n{output}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    expected_host = "win32" if args.platform == "windows" else "linux"
    if not sys.platform.startswith(expected_host):
        raise SystemExit(
            f"Cannot execute a {args.platform} bundle on {sys.platform}; "
            "run this smoke check on a native runner."
        )
    executable = (args.artifact or _default_artifact(args.platform)).resolve()
    if not executable.is_file():
        raise SystemExit(f"Packaged executable is missing: {executable}")
    checks = {
        "--no-gui": "no hardware was initialized",
        "--version": "0.1.0",
        "--smoke-imports": "Packaged runtime imports and resources are available.",
    }
    for argument, expected in checks.items():
        result = _run(executable, [argument])
        output = f"{result.stdout}\n{result.stderr}"
        if result.returncode != 0 or expected not in output:
            raise SystemExit(
                f"{argument} smoke failed for {executable} (exit {result.returncode}):\n{output}"
            )
    if not args.skip_ui_smoke:
        ui_smoke_executable = (args.ui_smoke_artifact or _default_ui_smoke_artifact(args.platform)).resolve()
        _run_ui_smoke(ui_smoke_executable, args.platform)
    print(f"Packaged {args.platform} smoke passed: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

