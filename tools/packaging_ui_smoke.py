"""Packaging-only helper that constructs and closes the real production Console.

This module is a standalone PyInstaller entry point, never imported through
``soft_actuator_testing.bootstrap``. It exists so packaged-artifact smoke can
prove the *real* production Instrument Console composition
(``soft_actuator_testing.ui.production.create_production_composition``) still
builds inside a frozen bundle, without changing the normal CLI's startup
semantics and without touching hardware:

* Qt runs under the ``offscreen`` platform plugin (set before any Qt import).
* The camera capture seam is an inert, in-process backend so construction
  never runs FFmpeg discovery or opens a device; the serial seam keeps the
  production default (a lazy, un-opened adapter -- no port is enumerated or
  opened).
* Workspace preferences are redirected to a caller-supplied path so the
  packaged smoke run never reads or writes a real operator's configuration
  directory.
* ``soft_actuator_testing.ui.demo`` must never be imported: the production
  composition path is demo-free by construction (see
  ``InstrumentConsoleWindow.__init__``), and this helper asserts that here.

The window is shown briefly under the offscreen platform, a bounded
``QTimer`` guarantees the Qt event loop returns even if nothing else quits
it, and the composition is closed deterministically before exit.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEMO_MODULE_NAME = "soft_actuator_testing.ui.demo"
SUCCESS_MESSAGE = "Packaged production Instrument Console constructed and closed without hardware."


class _NoOpCameraCaptureBackend:
    """An inert ``CameraCaptureBackend`` that never discovers or opens a camera.

    Injecting this into ``create_production_composition(camera=...)`` bypasses
    the default composition's ``FfmpegTools.discover()`` call, so packaged
    smoke never depends on (or probes for) an FFmpeg installation.
    """

    def __init__(self) -> None:
        from soft_actuator_testing.application.camera_capture import CaptureHealth, LatestFrameChannel

        self._frame_channel: LatestFrameChannel = LatestFrameChannel()
        self._health = CaptureHealth()

    @property
    def frame_channel(self):
        return self._frame_channel

    @property
    def health(self):
        return self._health

    @property
    def result(self):
        return None

    def start(self, output_directory: Path, device_identifier: str, *, readiness_timeout: float) -> None:
        del output_directory, device_identifier, readiness_timeout
        raise RuntimeError("packaged UI smoke never starts a capture")

    def stop(self, reason: str = "operator", *, timeout: float | None = None):
        del reason, timeout
        raise RuntimeError("packaged UI smoke never stops a capture")

    def close(self, *, timeout: float | None = None):
        del timeout
        return None


def construct_and_close_production_console(
    *,
    preferences_path: Path,
    event_loop_seconds: float = 0.3,
    pump_events: bool = True,
) -> dict[str, str]:
    """Build the real production Instrument Console, pump events, then close it.

    Returns a small evidence dictionary (demo-import status, serial status,
    window title) for the caller to print/assert on. Raises ``RuntimeError``
    if constructing the composition newly imports the demo module, or if the
    serial seam shows anything other than its lazy, disconnected default --
    either would mean the packaged smoke path drifted from the real
    production contract. (A fresh frozen process never has the demo module
    preloaded, so this check is exact there; it only tracks the *delta* here
    so the same helper stays valid when reused inside a long-lived
    interpreter, such as this project's own test suite, where unrelated
    demo-mode tests may already have imported it earlier.)

    ``pump_events`` is ``True`` for the real packaged/frozen entry point
    (``main``), which briefly shows the window and runs a bounded nested Qt
    event loop before closing it, per the packaged-smoke requirement. It
    defaults to ``True`` here too, but this project's own in-process test
    coverage passes ``False``: showing a window and re-entering
    ``QApplication.exec()`` disturbs Qt's application-wide shortcut/active-window
    bookkeeping (``Qt.ShortcutContext.ApplicationShortcut``) for whichever
    *other* pytest-qt test happens to run next in the same shared
    interpreter/QApplication -- a hazard specific to reusing this helper
    in-process that does not exist for the real, single-purpose frozen
    executable, which exits immediately afterward. The subprocess-based CLI
    test in ``tests/test_desktop_packaging.py`` exercises the full
    ``pump_events=True`` path exactly as the frozen executable runs it.
    """

    demo_already_imported = DEMO_MODULE_NAME in sys.modules

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from soft_actuator_testing.application.camera_capture import CameraCaptureService
    from soft_actuator_testing.application.serial_controller import SerialConnectionStatus
    from soft_actuator_testing.ui.production import create_production_composition

    application = QApplication.instance() or QApplication([])
    application.setApplicationName("Soft Actuator Testing (packaging smoke)")

    composition = create_production_composition(
        camera=CameraCaptureService(_NoOpCameraCaptureBackend()),
        preferences_path=preferences_path,
    )
    try:
        demo_imported_by_construction = not demo_already_imported and DEMO_MODULE_NAME in sys.modules
        if demo_imported_by_construction:
            raise RuntimeError(f"{DEMO_MODULE_NAME} was imported while constructing the production composition")
        serial_status = composition.serial_controller.snapshot.status
        if serial_status is not SerialConnectionStatus.DISCONNECTED:
            raise RuntimeError(
                "packaged UI smoke expected the lazy, un-opened serial default "
                f"(DISCONNECTED); observed {serial_status!r}"
            )
        window_title = composition.window.windowTitle()
        if "Demo" in window_title:
            raise RuntimeError("packaged UI smoke constructed a demo window instead of production")

        if pump_events:
            composition.window.show()
            QTimer.singleShot(max(1, int(event_loop_seconds * 1000)), application.quit)
            application.exec()
    finally:
        composition.window.close()
        # The window owns application-wide (``Qt.ShortcutContext.ApplicationShortcut``)
        # keyboard shortcuts (for example the Global Stop shortcut). ``close()``
        # only hides a QMainWindow; it does not destroy the C++ object or
        # release those global shortcuts. Qt's own deferred ``deleteLater()``
        # is not reliably flushed by ``processEvents()`` alone here, so the
        # underlying C++ object is deleted immediately and synchronously via
        # shiboken (a PySide6 dependency already required by this project).
        # This guarantees nothing from this smoke run outlives it and shadows
        # an equivalent shortcut a later window registers in the same process
        # (relevant when this helper is reused inside a long-lived
        # interpreter, such as this project's own test suite).
        import shiboken6

        if shiboken6.isValid(composition.window):
            shiboken6.delete(composition.window)
        application.processEvents()

    return {
        "demo_module_imported": str(demo_imported_by_construction),
        "serial_status": serial_status.name,
        "window_title": window_title,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preferences-path",
        type=Path,
        required=True,
        help="workspace-settings.json path used instead of the real operator configuration directory",
    )
    parser.add_argument(
        "--event-loop-seconds",
        type=float,
        default=0.3,
        help="how long to pump the offscreen Qt event loop before closing the window",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # Set before any Qt module is imported. This module is imported directly
    # by tests (see tests/test_desktop_packaging.py) to exercise
    # ``construct_and_close_production_console`` inside an already-running
    # Qt session, so the offscreen override belongs here in the standalone
    # entry point rather than at module import time -- otherwise importing
    # this module for introspection would force a platform switch on a test
    # session that may already have created its own QApplication.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    args = _parser().parse_args(argv)
    evidence = construct_and_close_production_console(
        preferences_path=args.preferences_path,
        event_loop_seconds=args.event_loop_seconds,
    )
    print(SUCCESS_MESSAGE)
    print(f"demo_module_imported={evidence['demo_module_imported']}")
    print(f"serial_status={evidence['serial_status']}")
    print(f"window_title={evidence['window_title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
