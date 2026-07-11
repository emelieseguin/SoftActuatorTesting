"""Test-session setup for the Qt presentation layer.

All UI tests run in headless ``offscreen`` Qt mode — no real display, no
native dialogs, and no hardware. Setting the platform here (before any test
module imports PySide6) means `uv run pytest` stays headless-safe without
requiring callers to export ``QT_QPA_PLATFORM`` themselves.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
