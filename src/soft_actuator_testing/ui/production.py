"""Safe real-adapter production composition; no demo services are constructed."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import QMainWindow, QTabWidget, QToolBar

from soft_actuator_testing.application.camera_capture import CameraCaptureService, CameraPanelPresenter
from soft_actuator_testing.application.calibration_workflow import SerialCalibrationSampleSource
from soft_actuator_testing.application.run_controller import LegacySerialRunPort, RunArtifactStorage, RunController
from soft_actuator_testing.application.serial_controller import SerialController
from soft_actuator_testing.application.workspace import WorkspaceController
from soft_actuator_testing.infrastructure.artifact_store import ArtifactFileStore
from soft_actuator_testing.infrastructure.camera import FfmpegCameraDeviceSource, FfmpegCaptureBackend
from soft_actuator_testing.infrastructure.ffmpeg import EncoderSelection, FfmpegTools
from soft_actuator_testing.infrastructure.serial_adapter import (
    PySerialTransportFactory,
    SerialAdapter,
    SerialTextParser,
    legacy_field_three_unconfirmed_profile,
)
from soft_actuator_testing.infrastructure.workspace import JsonWorkspaceSettings
from soft_actuator_testing.ui.views.production_run import (
    ProductionConnectionsPage,
    ProductionLiveRunPage,
    ProductionReadinessPage,
)
from soft_actuator_testing.ui.widgets.file_picker import QtFilePicker
from soft_actuator_testing.ui.widgets import AccessibleButton


class ProductionConsoleWindow(QMainWindow):
    """A production-only shell whose close path owns bounded real cleanup."""

    def __init__(self, session: "ProductionComposition") -> None:
        super().__init__()
        self.session = session
        self.setWindowTitle("Soft Actuator Testing — Production Instrument Console")
        self.setAccessibleName("Production Instrument Console")
        self.file_picker = QtFilePicker(self)
        tabs = QTabWidget(self)
        tabs.addTab(session.connections_page, "Connections")
        tabs.addTab(session.readiness_page, "Readiness")
        tabs.addTab(session.live_run_page, "Live Run")
        self.setCentralWidget(tabs)
        toolbar = QToolBar("Run safety", self)
        self.global_stop_button = AccessibleButton("Global Stop", parent=toolbar)
        self.global_stop_button.clicked.connect(self.trigger_global_stop)
        toolbar.addWidget(self.global_stop_button)
        self.addToolBar(toolbar)

    def trigger_global_stop(self) -> None:
        self.session.run_controller.global_stop()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.session.run_controller.close()
        super().closeEvent(event)


@dataclass
class ProductionComposition:
    run_controller: RunController
    serial_controller: SerialController
    calibration_source: SerialCalibrationSampleSource
    workspace_controller: WorkspaceController
    file_picker: QtFilePicker
    connections_page: ProductionConnectionsPage
    readiness_page: ProductionReadinessPage
    live_run_page: ProductionLiveRunPage
    window: ProductionConsoleWindow | None = None


def create_production_composition(
    *,
    serial: LegacySerialRunPort | None = None,
    camera: CameraCaptureService | None = None,
    storage: RunArtifactStorage | None = None,
    software_version: str | None = None,
    preferences_path: Path | None = None,
) -> ProductionComposition:
    """Construct real-but-disconnected adapters; no port/camera is opened here."""

    real_serial = serial or SerialController(
        SerialAdapter(
            PySerialTransportFactory(),
            parser=SerialTextParser(legacy_field_three_unconfirmed_profile()),
        )
    )
    workspace = WorkspaceController(
        JsonWorkspaceSettings(preferences_path or Path.home() / ".soft-actuator-testing.json"),
        store_factory=ArtifactFileStore,
        software_version=software_version,
    )
    capture, camera_presenter = _camera_services(camera)
    active_storage = storage
    run = RunController(serial=real_serial, camera=capture, storage=active_storage, software_version=software_version)
    def bind_workspace(snapshot) -> None:
        if snapshot.root is not None:
            run.set_storage(ArtifactFileStore(snapshot.root))
    workspace.state.subscribe(bind_workspace, emit_current=True)

    def root() -> Path:
        selected = workspace.snapshot.root
        return selected or Path.home()

    def selected_camera() -> str:
        return camera_presenter.state.snapshot.selected_device if camera_presenter is not None else ""

    connections = ProductionConnectionsPage(real_serial, camera_presenter, root)
    readiness = ProductionReadinessPage(
        run,
        workspace=root,
        calibration=lambda: None,
        geometry=lambda: None,
        selected_camera=selected_camera,
    )
    live = ProductionLiveRunPage(run)
    picker = QtFilePicker()
    composition = ProductionComposition(
        run,
        real_serial,
        SerialCalibrationSampleSource(real_serial),
        workspace,
        picker,
        connections,
        readiness,
        live,
    )
    composition.window = ProductionConsoleWindow(composition)
    return composition


def _camera_services(
    injected: CameraCaptureService | None,
) -> tuple[CameraCaptureService | None, CameraPanelPresenter | None]:
    if injected is not None:
        # An injected production capture has its own device presenter supplied
        # by an integration host; don't construct an unsafe second owner.
        return injected, None
    try:
        tools = FfmpegTools.discover()
    except Exception:
        return None, None
    # Discovery only resolves executables; device listing remains an explicit
    # CameraPanel Refresh action. Encoder probing is deliberately deferred.
    source = FfmpegCameraDeviceSource(tools)
    backend = FfmpegCaptureBackend(
        tools,
        encoder=EncoderSelection("libx264", ("-c:v", "libx264", "-preset", "ultrafast", "-crf", "23")),
    )
    capture = CameraCaptureService(backend)
    return capture, CameraPanelPresenter(source, capture)


__all__ = ["ProductionComposition", "ProductionConsoleWindow", "create_production_composition"]
