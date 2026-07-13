"""Safe application bootstrap; hardware adapters are not initialized here."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch the hardware-disconnected Soft Actuator Testing GUI."
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="print safe status without importing or opening the GUI",
    )
    parser.add_argument(
        "--smoke-imports",
        action="store_true",
        help="verify packaged Qt, OpenCV, PyQtGraph, and production imports without opening a window",
    )
    parser.add_argument(
        "--mode",
        choices=("production", "demo"),
        default=None,
        help=(
            "production composes safe, disconnected run services; demo uses "
            "deterministic fakes. Defaults to production unless --prototype is "
            "given, in which case it defaults to demo. Explicit "
            "'--mode production' combined with --prototype is rejected: "
            "prototype shells are a demo-only development comparison, never a "
            "second production shell (see ADR 0005)."
        ),
    )
    parser.add_argument(
        "--prototype",
        choices=("experiment-studio",),
        metavar="SHELL",
        help=(
            "development comparison only: launch the rejected Experiment Studio "
            "prototype instead of the normal Instrument Console shell. Always a "
            "demo/fake-service composition; incompatible with an explicit "
            "'--mode production'"
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Launch a hardware-disconnected shell or report safe status."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.no_gui:
        print(
            "Soft Actuator Testing is installed. "
            "Production lifecycle composition is available; no hardware was initialized."
        )
        return 0
    if args.smoke_imports:
        _smoke_runtime_imports()
        return 0

    if args.mode == "production" and args.prototype is not None:
        parser.error(
            "--mode production cannot be combined with --prototype "
            f"{args.prototype}: the prototype shells are a demo-only "
            "development comparison (ADR 0005), never a second production "
            "shell. Drop --mode production to launch the requested prototype, "
            "or drop --prototype to launch the real production Instrument "
            "Console."
        )

    # Unambiguous precedence, documented above and in the operator guide:
    # an explicit --prototype without an explicit --mode implies demo (never
    # silently opens production); an explicit --mode is always honored as-is
    # (the conflicting production+prototype combination was already rejected
    # above).
    production = (args.mode or "production") == "production" and args.prototype is None

    from .ui.app import run_application

    return run_application(prototype_shell=args.prototype, production=production)


def _smoke_runtime_imports() -> None:
    """Check frozen-runtime imports and resources without creating Qt widgets."""

    import cv2
    import pyqtgraph
    from PySide6.QtCore import QLibraryInfo

    from .ui import production  # noqa: F401 - verifies the real production composition imports.

    plugin_path = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    resources = {
        "Qt plugins": plugin_path.is_dir(),
        "OpenCV": Path(cv2.__file__).is_file(),
        "PyQtGraph": Path(pyqtgraph.__file__).is_file(),
    }
    missing = [name for name, available in resources.items() if not available]
    if missing:
        raise RuntimeError(f"Packaged runtime resource discovery failed: {', '.join(missing)}")
    print("Packaged runtime imports and resources are available.")
