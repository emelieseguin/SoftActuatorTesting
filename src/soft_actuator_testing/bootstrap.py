"""Safe application bootstrap; hardware adapters are not initialized here."""

from __future__ import annotations

import argparse

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
        "--mode",
        choices=("production", "demo"),
        default="production",
        help="production composes safe, disconnected run services; demo uses deterministic fakes",
    )
    parser.add_argument(
        "--prototype",
        choices=("experiment-studio",),
        metavar="SHELL",
        help=(
            "development comparison only: launch the rejected Experiment Studio "
            "prototype instead of the normal Instrument Console shell"
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Launch a hardware-disconnected shell or report safe status."""
    args = _parser().parse_args(argv)
    if args.no_gui:
        print(
            "Soft Actuator Testing is installed. "
            "Production lifecycle composition is available; no hardware was initialized."
        )
        return 0

    from .ui.app import run_application

    return run_application(prototype_shell=args.prototype, production=args.mode == "production")
