"""Build a native SoftActuatorTesting desktop bundle with PyInstaller."""

from __future__ import annotations

import argparse
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_NAME = "SoftActuatorTesting"
RUNTIME_DISTRIBUTIONS = (
    "soft-actuator-testing",
    "PySide6",
    "pyqtgraph",
    "opencv-python",
    "numpy",
    "pyserial",
)
EXCLUDED_MODULES = (
    "pytest",
    "pytestqt",
    "PyQt5",
    "PyQt6",
    "tkinter",
    "matplotlib",
    "IPython",
    "jupyter",
    "notebook",
)


def _platform_details(target: str) -> tuple[tuple[str, ...], str]:
    if target == "linux":
        return ("linux",), ""
    if target == "windows":
        return ("win32",), ".exe"
    raise ValueError(f"unsupported platform {target!r}")


def _data_argument(source: Path, destination: str) -> str:
    return f"{source}{os.pathsep}{destination}"


def _license_destination(package: str, distribution_relative_path: str) -> str:
    """Return PyInstaller's portable destination directory for a license file."""
    normalized_path = distribution_relative_path.replace("\\", "/")
    relative_path = PurePosixPath(normalized_path)
    if (
        not relative_path.parts
        or relative_path.is_absolute()
        or PureWindowsPath(normalized_path).is_absolute()
        or any(part in (".", "..") for part in relative_path.parts)
    ):
        raise ValueError(
            "distribution license path must be a relative path: "
            f"{distribution_relative_path!r}"
        )
    return (
        PurePosixPath("licenses")
        / "third-party"
        / package
        / relative_path.parent
    ).as_posix()


def _installed_license_files(package: str) -> tuple[tuple[Path, str], ...]:
    try:
        package_distribution = distribution(package)
    except PackageNotFoundError:
        return ()
    files = package_distribution.files or ()
    license_files = [
        (
            path,
            _license_destination(package, str(relative)),
        )
        for relative in files
        if "license" in relative.name.casefold()
        and (path := Path(package_distribution.locate_file(relative))).is_file()
    ]
    return tuple(sorted(license_files, key=lambda item: item[1]))


def _write_third_party_notices(destination: Path) -> None:
    lines = [
        "SoftActuatorTesting third-party notices",
        "",
        "This bundle contains the following runtime distributions.",
        "See DEPENDENCY_LICENSES.md and copied license files for details.",
        "",
    ]
    for package in RUNTIME_DISTRIBUTIONS:
        try:
            package_distribution = distribution(package)
        except PackageNotFoundError:
            lines.append(f"- {package}: metadata unavailable")
            continue
        metadata = package_distribution.metadata
        license_expression = metadata.get("License-Expression") or metadata.get("License") or "not declared"
        lines.append(
            f"- {metadata.get('Name', package)} {package_distribution.version}: {license_expression}"
        )
    lines.extend(
        (
            "",
            "PyInstaller is a build-time dependency. Its bootloader is distributed with",
            "the application under PyInstaller's GPL-2.0-or-later license exception.",
            "FFmpeg and FFprobe are external prerequisites and are not bundled.",
            "",
        )
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")


UI_SMOKE_APPLICATION_NAME = f"{APPLICATION_NAME}UiSmoke"


def _application_configuration(target: str) -> dict[str, Any]:
    _, executable_suffix = _platform_details(target)
    build_root = REPOSITORY_ROOT / "build" / "desktop" / target
    distribution_root = REPOSITORY_ROOT / "dist" / "desktop" / target
    notice = build_root / "package-assets" / "THIRD_PARTY_NOTICES.txt"
    data_files = [
        (REPOSITORY_ROOT / "LICENSE", "licenses"),
        (REPOSITORY_ROOT / "docs" / "architecture" / "dependency-licenses.md", "licenses"),
        (notice, "licenses"),
    ]
    for package in RUNTIME_DISTRIBUTIONS:
        data_files.extend(_installed_license_files(package))
    return {
        "application_name": APPLICATION_NAME,
        "entrypoint": str(REPOSITORY_ROOT / "tools" / "frozen_entrypoint.py"),
        "target": target,
        "executable": str(distribution_root / APPLICATION_NAME / f"{APPLICATION_NAME}{executable_suffix}"),
        "distpath": str(distribution_root),
        "workpath": str(build_root / "work"),
        "specpath": str(build_root / "spec"),
        "paths": [str(REPOSITORY_ROOT / "src")],
        "hidden_imports": (
            "cv2",
            "pyqtgraph",
            "soft_actuator_testing.ui.production",
        ),
        "collect_all": ("cv2", "pyqtgraph"),
        "copy_metadata": RUNTIME_DISTRIBUTIONS,
        "excluded_modules": EXCLUDED_MODULES,
        "data_files": tuple((str(source), destination) for source, destination in data_files),
        "notices_path": str(notice),
    }


def _ui_smoke_configuration(target: str) -> dict[str, Any]:
    """Configuration for a second, packaging-only executable.

    This bundle's only purpose is to construct and close the real production
    Instrument Console under offscreen Qt with no hardware, proving the
    frozen production composition still builds (see
    ``tools/packaging_ui_smoke.py``). It shares the main bundle's Qt/OpenCV
    collection so the composition's imports resolve, but it carries no
    license data files: it is a test helper, not a distributed release
    artifact.
    """

    _, executable_suffix = _platform_details(target)
    build_root = REPOSITORY_ROOT / "build" / "desktop" / target / "ui-smoke"
    distribution_root = REPOSITORY_ROOT / "dist" / "desktop" / target
    name = UI_SMOKE_APPLICATION_NAME
    return {
        "application_name": name,
        "entrypoint": str(REPOSITORY_ROOT / "tools" / "packaging_ui_smoke.py"),
        "target": target,
        "executable": str(distribution_root / name / f"{name}{executable_suffix}"),
        "distpath": str(distribution_root),
        "workpath": str(build_root / "work"),
        "specpath": str(build_root / "spec"),
        "paths": [str(REPOSITORY_ROOT / "src")],
        "hidden_imports": (
            "cv2",
            "pyqtgraph",
            "soft_actuator_testing.ui.production",
        ),
        "collect_all": ("cv2", "pyqtgraph"),
        "copy_metadata": RUNTIME_DISTRIBUTIONS,
        "excluded_modules": EXCLUDED_MODULES,
        "data_files": (),
        "notices_path": None,
    }


def build_configuration(target: str, component: str = "app") -> dict[str, Any]:
    if component == "app":
        return _application_configuration(target)
    if component == "ui-smoke":
        return _ui_smoke_configuration(target)
    raise ValueError(f"unsupported component {component!r}")


def _pyinstaller_arguments(configuration: dict[str, Any]) -> list[str]:
    arguments = [
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        configuration["application_name"],
        "--distpath",
        configuration["distpath"],
        "--workpath",
        configuration["workpath"],
        "--specpath",
        configuration["specpath"],
    ]
    for source_path in configuration["paths"]:
        arguments.extend(("--paths", source_path))
    for module in configuration["hidden_imports"]:
        arguments.extend(("--hidden-import", module))
    for package in configuration["collect_all"]:
        arguments.extend(("--collect-all", package))
    for package in configuration["copy_metadata"]:
        arguments.extend(("--copy-metadata", package))
    for module in configuration["excluded_modules"]:
        arguments.extend(("--exclude-module", module))
    for source, destination in configuration["data_files"]:
        arguments.extend(("--add-data", _data_argument(Path(source), destination)))
    arguments.append(configuration["entrypoint"])
    return arguments


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("linux", "windows"), required=True)
    parser.add_argument(
        "--component",
        choices=("app", "ui-smoke"),
        default="app",
        help=(
            "'app' builds the normal release bundle (default); 'ui-smoke' builds "
            "the packaging-only helper that constructs and closes the real "
            "production Instrument Console under offscreen Qt with no hardware"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the deterministic configuration without invoking PyInstaller",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    supported_hosts, _ = _platform_details(args.platform)
    configuration = build_configuration(args.platform, args.component)
    if args.dry_run:
        print(json.dumps(configuration, indent=2, sort_keys=True))
        return 0
    if sys.platform not in supported_hosts:
        expected = " or ".join(supported_hosts)
        raise SystemExit(
            f"Cannot build {args.platform} on {sys.platform}; run this command on {expected}. "
            "PyInstaller builds native extension and Qt assets."
        )
    if configuration["notices_path"] is not None:
        _write_third_party_notices(Path(configuration["notices_path"]))
    from PyInstaller.__main__ import run as run_pyinstaller

    run_pyinstaller(_pyinstaller_arguments(configuration))
    print(f"Built {configuration['executable']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
