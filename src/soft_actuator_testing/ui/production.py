"""Production Instrument Console composition with disconnected real services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from soft_actuator_testing.application.analysis_pipeline import AnalysisPipeline
from soft_actuator_testing.application.camera_capture import CameraCaptureService, CameraPanelPresenter
from soft_actuator_testing.application.calibration_workflow import (
    CalibrationWorkflowService,
    SerialCalibrationSampleSource,
)
from soft_actuator_testing.application.run_controller import (
    RunArtifactStorage,
    RunController,
)
from soft_actuator_testing.application.serial_controller import (
    SerialConnectionStatus,
    SerialController,
)
from soft_actuator_testing.application.video_geometry_workflow import VideoGeometryWorkflow
from soft_actuator_testing.application.workspace import WorkspaceController
from soft_actuator_testing.domain.run_state import RunState
from soft_actuator_testing.infrastructure.artifact_store import ArtifactFileStore
from soft_actuator_testing.infrastructure.camera import FfmpegCameraDeviceSource, FfmpegCaptureBackend
from soft_actuator_testing.infrastructure.ffmpeg import CaptureStoragePolicy, FfmpegTools
from soft_actuator_testing.infrastructure.serial_adapter import (
    PySerialTransportFactory,
    SerialAdapter,
    SerialTextParser,
    legacy_field_three_unconfirmed_profile,
)
from soft_actuator_testing.infrastructure.video_file_reader import OpenCvVideoFileReader
from soft_actuator_testing.infrastructure.red_marker_detector import OpenCvRedMarkerFrameDetector
from soft_actuator_testing.infrastructure.workspace import JsonWorkspaceSettings
from soft_actuator_testing.ui.shells.instrument_console import (
    InstrumentConsoleWindow,
    ProductionConsoleStatus,
)
from soft_actuator_testing.ui.themes import DARK_THEME
from soft_actuator_testing.ui.themes.qt_bridge import apply_theme_to_widget, to_qfont
from soft_actuator_testing.ui.views.home_workspace import HomeWorkspaceView
from soft_actuator_testing.ui.views.marker_suggestion import MarkerSuggestionView
from soft_actuator_testing.ui.views.production_run import (
    ProductionConnectionsPage,
    ProductionLiveRunPage,
    ProductionReadinessPage,
)
from soft_actuator_testing.ui.views.video_geometry import VideoGeometryView
from soft_actuator_testing.ui.views.workflows.calibration import CalibrationPage
from soft_actuator_testing.ui.views.workflows.analysis import AnalysisPage
from soft_actuator_testing.ui.widgets.camera_panel import CameraPanel
from soft_actuator_testing.ui.widgets.file_picker import FilePicker, QtFilePicker


class ProductionGeometryPage(QWidget):
    """Real manual geometry and advisory marker workflow without demo state."""

    def __init__(
        self,
        workflow: VideoGeometryWorkflow,
        *,
        file_picker: FilePicker,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAccessibleName("Video Geometry / Marker Setup page")
        apply_theme_to_widget(self, DARK_THEME)
        layout = QVBoxLayout(self)
        heading = QLabel("Video Geometry / Marker Setup", self)
        heading.setFont(to_qfont(DARK_THEME.typography.heading))
        layout.addWidget(heading)
        self.geometry_view = VideoGeometryView(workflow, file_picker=file_picker, parent=self)
        self.marker_suggestion_view = MarkerSuggestionView(self.geometry_view, parent=self)
        layout.addWidget(self.geometry_view)
        layout.addWidget(self.marker_suggestion_view)

    def set_artifact_store(self, store: ArtifactFileStore | None) -> None:
        self.geometry_view.artifact_store = store

    def close(self) -> None:
        self.geometry_view.workflow.close_video()
        super().close()


class ProductionSettingsHelpPage(QWidget):
    """Production help destination without prototype profile/demo settings."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Settings / Profiles / Help page")
        apply_theme_to_widget(self, DARK_THEME)
        layout = QVBoxLayout(self)
        heading = QLabel("Settings / Profiles / Help", self)
        heading.setFont(to_qfont(DARK_THEME.typography.heading))
        detail = QLabel(
            "Hardware remains disconnected until Refresh/Connect is selected. "
            "Choose or create a workspace before capturing artifacts or running a cycle.",
            self,
        )
        detail.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(detail)
        layout.addStretch(1)


@dataclass
class ProductionComposition:
    """All real production workflow services and their selected Console shell."""

    run_controller: RunController
    serial_controller: SerialController
    calibration_source: SerialCalibrationSampleSource
    calibration_service: CalibrationWorkflowService
    geometry_workflow: VideoGeometryWorkflow
    workspace_controller: WorkspaceController
    file_picker: FilePicker
    workspace_page: HomeWorkspaceView
    connections_page: ProductionConnectionsPage
    calibration_page: CalibrationPage
    geometry_page: ProductionGeometryPage
    readiness_page: ProductionReadinessPage
    live_run_page: ProductionLiveRunPage
    analysis_pipeline: AnalysisPipeline
    analysis_page: AnalysisPage
    settings_page: ProductionSettingsHelpPage
    window: InstrumentConsoleWindow


def create_production_composition(
    *,
    serial: SerialController | None = None,
    camera: CameraCaptureService | None = None,
    camera_presenter: CameraPanelPresenter | None = None,
    camera_device_provider: Callable[[], str] | None = None,
    storage: RunArtifactStorage | None = None,
    software_version: str | None = None,
    preferences_path: Path | None = None,
    file_picker: FilePicker | None = None,
    calibration_service: CalibrationWorkflowService | None = None,
    geometry_workflow: VideoGeometryWorkflow | None = None,
) -> ProductionComposition:
    """Construct real adapters without opening ports, cameras, or native dialogs."""

    real_serial = serial or SerialController(
        SerialAdapter(
            PySerialTransportFactory(),
            parser=SerialTextParser(legacy_field_three_unconfirmed_profile()),
        )
    )
    picker = file_picker or QtFilePicker()
    def workspace_mutation_blocker() -> str | None:
        state = run.snapshot.lifecycle.state
        if state in {RunState.STARTING, RunState.RUNNING, RunState.STOPPING}:
            return (
                f"Workspace changes are unavailable while a run is {state.value}. "
                "Stop or finalize the run before changing workspace."
            )
        return None

    workspace = WorkspaceController(
        JsonWorkspaceSettings(preferences_path),
        store_factory=ArtifactFileStore,
        software_version=software_version,
        mutation_guard=workspace_mutation_blocker,
    )
    capture, discovered_presenter = _camera_services(camera)
    active_camera_presenter = camera_presenter or discovered_presenter
    run = RunController(
        serial=real_serial,
        camera=capture,
        storage=storage,
        software_version=software_version,
    )
    calibration_source = SerialCalibrationSampleSource(real_serial)
    active_calibration = calibration_service or CalibrationWorkflowService(calibration_source)
    active_geometry = geometry_workflow or VideoGeometryWorkflow(OpenCvVideoFileReader())
    analysis_detector = OpenCvRedMarkerFrameDetector()
    analysis_pipeline = AnalysisPipeline(OpenCvVideoFileReader(), analysis_detector)

    workspace_page = HomeWorkspaceView(controller=workspace, file_picker=picker)
    connections = ProductionConnectionsPage(
        real_serial,
        active_camera_presenter,
        lambda: workspace.snapshot.root,
        camera_auto_refresh=False,
    )
    calibration = CalibrationPage(
        calibration_service=active_calibration,
        file_picker=picker,
        production_mode=True,
    )
    geometry = ProductionGeometryPage(active_geometry, file_picker=picker)
    analysis = AnalysisPage(
        pipeline=analysis_pipeline,
        camera_presenter=active_camera_presenter,
        live_detector=analysis_detector,
        file_picker=picker,
        production_mode=True,
        software_version=software_version,
        workspace_output_only=True,
    )
    settings = ProductionSettingsHelpPage()

    def selected_camera() -> str:
        if camera_device_provider is not None:
            return camera_device_provider()
        if active_camera_presenter is None:
            return ""
        snapshot = active_camera_presenter.state.snapshot
        return snapshot.selected_device if snapshot.can_start else ""

    readiness = ProductionReadinessPage(
        run,
        workspace=lambda: workspace.snapshot.root,
        calibration=lambda: active_calibration.snapshot.fit,
        geometry=lambda: active_geometry.ready_geometry,
        selected_camera=selected_camera,
    )
    live = ProductionLiveRunPage(run)

    unbound = object()
    bound_workspace_root: object = unbound
    bound_store: ArtifactFileStore | None = None

    def bind_workspace(snapshot) -> None:
        nonlocal bound_workspace_root, bound_store
        if snapshot.root == bound_workspace_root:
            return
        store = ArtifactFileStore(snapshot.root) if snapshot.root is not None else None
        previous_store = bound_store
        # A workspace transition is one synchronous capability update.  If the
        # run controller rejects it (for example due to a concurrent active
        # run), none of the other workflows are rebound.
        run.set_storage(store)
        try:
            calibration.artifact_store = store
            geometry.set_artifact_store(store)
            analysis.set_artifact_store(store)
        except Exception:
            run.set_storage(previous_store)
            calibration.artifact_store = previous_store
            geometry.set_artifact_store(previous_store)
            analysis.set_artifact_store(previous_store)
            raise
        bound_store = store
        bound_workspace_root = snapshot.root

    workspace.state.subscribe(bind_workspace, emit_current=True)

    def status() -> ProductionConsoleStatus:
        finalization = run.finalization_result
        return ProductionConsoleStatus(
            workspace=workspace.snapshot.root,
            calibration_ready=bool(
                active_calibration.snapshot.fit
                    and active_calibration.snapshot.fit.adequacy.is_adequate
            ),
                geometry_ready=active_geometry.ready_geometry is not None,
            serial_connected=real_serial.snapshot.status is SerialConnectionStatus.CONNECTED,
            camera_selected=bool(selected_camera()),
            analysis_source=analysis.finalized_video,
            analysis_message=analysis.status.text(),
        )

    closed = False

    def close() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        analysis.close()
        geometry.geometry_view.workflow.close_video()
        # ``live`` and ``connections.camera_panel`` are embedded pages inside
        # the shell's stacked widget, so they never receive their own
        # ``closeEvent`` when the InstrumentConsoleWindow closes. Stop their
        # timers deterministically here rather than relying on eventual
        # process exit or Qt object deletion.
        live.close()
        if isinstance(connections.camera_panel, CameraPanel):
            connections.camera_panel.stop_polling()
        run.close()
        if active_camera_presenter is not None:
            active_camera_presenter.close(timeout=5.0)

    pages = {
        "home": workspace_page,
        "connections": connections,
        "calibration": calibration,
        "geometry": geometry,
        "experiment": readiness,
        "live-run": live,
        "analysis": analysis,
        "settings": settings,
    }
    window = InstrumentConsoleWindow(
        production_run=run,
        production_pages=pages,
        production_status=status,
        production_check_readiness=readiness.configure,
        production_close=close,
        file_picker=picker,
    )
    return ProductionComposition(
        run,
        real_serial,
        calibration_source,
        active_calibration,
        active_geometry,
        workspace,
        picker,
        workspace_page,
        connections,
        calibration,
        geometry,
        readiness,
        live,
        analysis_pipeline,
        analysis,
        settings,
        window,
    )


def _camera_services(
    injected: CameraCaptureService | None,
) -> tuple[CameraCaptureService | None, CameraPanelPresenter | None]:
    if injected is not None:
        return injected, None
    try:
        tools = FfmpegTools.discover()
    except Exception:
        return None, None
    source = FfmpegCameraDeviceSource(tools)
    backend = FfmpegCaptureBackend(tools)
    capture = CameraCaptureService(
        backend,
        storage_preflight=CaptureStoragePolicy().preflight,
    )
    return capture, CameraPanelPresenter(source, capture)


ProductionConsoleWindow = InstrumentConsoleWindow

__all__ = [
    "ProductionComposition",
    "ProductionConsoleWindow",
    "ProductionGeometryPage",
    "create_production_composition",
]
