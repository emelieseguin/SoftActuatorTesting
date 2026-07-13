"""Static configuration and safe-runtime coverage for desktop packaging."""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

from soft_actuator_testing.bootstrap import main


ROOT = Path(__file__).resolve().parents[1]
PACKAGER = ROOT / "tools" / "package_desktop.py"
UI_SMOKE_HELPER = ROOT / "tools" / "packaging_ui_smoke.py"


def _configuration(platform: str, component: str = "app") -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, str(PACKAGER), "--platform", platform, "--component", component, "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_linux_packaging_configuration_collects_runtime_and_notices() -> None:
    configuration = _configuration("linux")

    assert configuration["entrypoint"].endswith("tools/frozen_entrypoint.py")
    assert configuration["executable"].endswith("dist/desktop/linux/SoftActuatorTesting/SoftActuatorTesting")
    assert set(configuration["collect_all"]) == {"cv2", "pyqtgraph"}
    assert "PySide6" in configuration["copy_metadata"]
    assert "pytest" in configuration["excluded_modules"]
    data_files = {tuple(item) for item in configuration["data_files"]}
    assert any(source.endswith("/LICENSE") and target == "licenses" for source, target in data_files)
    assert any(source.endswith("dependency-licenses.md") and target == "licenses" for source, target in data_files)
    assert any(source.endswith("THIRD_PARTY_NOTICES.txt") and target == "licenses" for source, target in data_files)


def test_license_files_keep_distribution_relative_paths_in_bundle() -> None:
    configuration = _configuration("linux")
    license_data = [
        tuple(item)
        for item in configuration["data_files"]
        if item[1].startswith("licenses/third-party/")
    ]
    destinations = [
        (PurePosixPath(destination) / Path(source).name).as_posix()
        for source, destination in license_data
    ]

    assert len(destinations) == len(set(destinations))
    assert all(not PurePosixPath(destination).is_absolute() for destination in destinations)
    assert all(not PureWindowsPath(destination).is_absolute() for destination in destinations)

    numpy_destinations = {
        (PurePosixPath(destination) / Path(source).name).as_posix()
        for source, destination in license_data
        if source.endswith("LICENSE.txt")
        and destination.startswith("licenses/third-party/numpy/")
    }
    assert any(".dist-info/licenses/LICENSE.txt" in destination for destination in numpy_destinations)
    assert any(
        destination.endswith("numpy/_core/include/numpy/random/LICENSE.txt")
        for destination in numpy_destinations
    )

    opencv_destinations = {
        (PurePosixPath(destination) / Path(source).name).as_posix()
        for source, destination in license_data
        if source.endswith("LICENSE.txt")
        and destination.startswith("licenses/third-party/opencv-python/")
    }
    assert "licenses/third-party/opencv-python/cv2/LICENSE.txt" in opencv_destinations
    assert any(
        "opencv_python-" in destination and destination.endswith(".dist-info/LICENSE.txt")
        for destination in opencv_destinations
    )


def test_license_destination_normalizes_windows_distribution_paths() -> None:
    license_destination = runpy.run_path(str(PACKAGER))["_license_destination"]
    destinations = {
        license_destination(
            "numpy",
            r"numpy-2.5.1.dist-info\licenses\LICENSE.txt",
        ),
        license_destination(
            "numpy",
            r"numpy\_core\include\numpy\random\LICENSE.txt",
        ),
    }

    assert destinations == {
        "licenses/third-party/numpy/numpy-2.5.1.dist-info/licenses",
        "licenses/third-party/numpy/numpy/_core/include/numpy/random",
    }
    assert all(not PureWindowsPath(destination).is_absolute() for destination in destinations)


def test_windows_packaging_configuration_is_deterministic_without_cross_building() -> None:
    configuration = _configuration("windows")

    assert configuration["target"] == "windows"
    assert configuration["executable"].endswith(
        "dist/desktop/windows/SoftActuatorTesting/SoftActuatorTesting.exe"
    )
    assert configuration["workpath"].endswith("build/desktop/windows/work")
    assert "soft_actuator_testing.ui.production" in configuration["hidden_imports"]


def test_runtime_import_smoke_does_not_construct_hardware_or_a_window(capsys) -> None:
    assert main(["--smoke-imports"]) == 0
    assert "Packaged runtime imports and resources are available." in capsys.readouterr().out


def test_ui_smoke_packaging_configuration_shares_runtime_collection_without_release_assets() -> None:
    configuration = _configuration("linux", component="ui-smoke")

    assert configuration["entrypoint"].endswith("tools/packaging_ui_smoke.py")
    assert configuration["executable"].endswith(
        "dist/desktop/linux/SoftActuatorTestingUiSmoke/SoftActuatorTestingUiSmoke"
    )
    assert configuration["workpath"].endswith("build/desktop/linux/ui-smoke/work")
    assert set(configuration["collect_all"]) == {"cv2", "pyqtgraph"}
    assert "soft_actuator_testing.ui.production" in configuration["hidden_imports"]
    assert "pytest" in configuration["excluded_modules"]
    # The UI-smoke helper is a packaging-only test aid, not a distributed
    # release artifact: it carries no license data files or third-party notice.
    assert configuration["data_files"] == []
    assert configuration["notices_path"] is None


def test_ui_smoke_packaging_configuration_windows_is_deterministic_without_cross_building() -> None:
    configuration = _configuration("windows", component="ui-smoke")

    assert configuration["target"] == "windows"
    assert configuration["executable"].endswith(
        "dist/desktop/windows/SoftActuatorTestingUiSmoke/SoftActuatorTestingUiSmoke.exe"
    )
    assert configuration["workpath"].endswith("build/desktop/windows/ui-smoke/work")


def _evict_demo_module_cache(monkeypatch) -> None:
    """Force ``soft_actuator_testing.ui.demo`` (and submodules) out of ``sys.modules``.

    Several unrelated test modules import ``ui.demo`` at *collection* time
    (``tests/application/test_presentation.py``,
    ``tests/ui/test_presenter_integration.py``,
    ``tests/ui/test_workflow_pages.py``, ``tests/ui/test_app_bootstrap.py``,
    ``tests/ui/test_demo_services.py``), which happens before any test body
    runs and regardless of execution order. Without this eviction, the demo
    module is already resident in ``sys.modules`` by the time this test runs
    in a full-suite session, so a plain "was it imported during construction"
    check degrades into a vacuous, order-dependent pass: it can never observe
    a real regression once the module is already cached process-wide.
    Evicting it first -- and relying on ``monkeypatch`` to restore whatever
    was cached beforehand once this test ends -- makes the assertion below
    strict and reproducible regardless of collection/test order, without
    weakening what it actually proves (the production composition path is
    still exercised unmodified; only the *observation* point is reset).
    """

    demo_module_prefix = "soft_actuator_testing.ui.demo"
    polluted_modules = [
        name for name in list(sys.modules) if name == demo_module_prefix or name.startswith(f"{demo_module_prefix}.")
    ]
    for name in polluted_modules:
        monkeypatch.delitem(sys.modules, name, raising=False)


def test_packaging_ui_smoke_constructs_and_closes_real_production_console_without_hardware(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    """The packaging-only helper must build the real production composition.

    It must do so without FFmpeg/camera discovery, without opening a serial
    port, and without ever importing the demo module -- matching the frozen
    executable's contract asserted by ``tools/smoke_desktop.py``. This
    in-process variant passes ``pump_events=False``: showing the window and
    re-entering ``QApplication.exec()`` is exercised instead by the
    subprocess-based ``test_packaging_ui_smoke_cli_reports_evidence`` below,
    which runs in an isolated process exactly like the real frozen
    executable and cannot disturb this shared test session's Qt state.

    ``_evict_demo_module_cache`` runs first so this assertion stays strict and
    order-independent even when other tests already imported ``ui.demo``
    earlier in the same pytest session (see its docstring).
    """

    del qtbot  # ensures a QApplication exists for this test process

    _evict_demo_module_cache(monkeypatch)

    from soft_actuator_testing.infrastructure.ffmpeg import FfmpegTools

    def _discover_must_not_be_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("packaging UI smoke must not perform FFmpeg/camera discovery")

    monkeypatch.setattr(FfmpegTools, "discover", staticmethod(_discover_must_not_be_called))

    module = runpy.run_path(str(UI_SMOKE_HELPER))

    evidence = module["construct_and_close_production_console"](
        preferences_path=tmp_path / "workspace-settings.json",
        pump_events=False,
    )

    assert evidence["demo_module_imported"] == "False"
    assert evidence["serial_status"] == "DISCONNECTED"
    assert "Production" in evidence["window_title"]


def test_packaging_ui_smoke_demo_import_guard_actually_detects_a_regression(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    """Prove the demo-import guard is a real, non-vacuous check.

    Regardless of test order or of ``ui.demo`` already being cached by other
    tests, if construction *did* newly import the demo module, the packaging
    helper must raise -- not silently report success. This simulates that
    regression by wrapping the real ``create_production_composition`` so it
    additionally imports ``ui.demo`` as a side effect, proving the guard
    added in ``construct_and_close_production_console`` (and exercised above
    with ``_evict_demo_module_cache``) would actually fail a genuinely broken
    production composition rather than always reporting a vacuous pass.
    """

    del qtbot  # ensures a QApplication exists for this test process

    _evict_demo_module_cache(monkeypatch)

    from soft_actuator_testing.ui import production

    real_create_production_composition = production.create_production_composition

    def _create_production_composition_with_demo_leak(*args: object, **kwargs: object):
        import soft_actuator_testing.ui.demo  # noqa: F401  (simulated regression)

        return real_create_production_composition(*args, **kwargs)

    monkeypatch.setattr(production, "create_production_composition", _create_production_composition_with_demo_leak)

    module = runpy.run_path(str(UI_SMOKE_HELPER))

    try:
        module["construct_and_close_production_console"](
            preferences_path=tmp_path / "workspace-settings.json",
            pump_events=False,
        )
    except RuntimeError as error:
        assert "was imported while constructing the production composition" in str(error)
    else:
        raise AssertionError("expected the demo-import guard to reject the simulated regression")


def test_packaging_ui_smoke_cli_reports_evidence(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(UI_SMOKE_HELPER),
            "--preferences-path",
            str(tmp_path / "workspace-settings.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        timeout=30,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "Packaged production Instrument Console constructed and closed without hardware." in result.stdout
    assert "demo_module_imported=False" in result.stdout
    assert "serial_status=DISCONNECTED" in result.stdout

