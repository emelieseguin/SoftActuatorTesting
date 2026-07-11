"""Smoke tests enforcing the architecture's Qt-free, OpenCV-isolated core boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "soft_actuator_testing"
FORBIDDEN_IMPORTS = {"PySide6", "PyQt5", "PyQt6", "pyqtgraph", "cv2"}


def imported_roots(path: Path) -> set[str]:
    """Return top-level imported module names without importing the target."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".", maxsplit=1)[0])
    return names


@pytest.mark.parametrize("package_name", ["domain", "application"])
def test_core_packages_do_not_import_qt_pyqtgraph_or_opencv(package_name: str) -> None:
    """Domain and application code remain usable without the presentation
    stack, and OpenCV (frame analysis only, never authoritative recording)
    stays isolated to dedicated infrastructure adapters."""
    package = PACKAGE_ROOT / package_name
    imported = {
        imported_name
        for source_file in package.rglob("*.py")
        for imported_name in imported_roots(source_file)
    }
    assert not imported & FORBIDDEN_IMPORTS
