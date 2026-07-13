"""End-to-end hardware-free evidence for the production Console composition."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from time import monotonic, sleep

import numpy as np
from PySide6.QtCore import Qt

from soft_actuator_testing.application.calibration_workflow import (
    CalibrationWorkflowService,
    FakeCalibrationSampleSource,
)
from soft_actuator_testing.application.analysis_pipeline import AnalysisPipeline
from soft_actuator_testing.application.camera_capture import CaptureHealth, CaptureResult
from soft_actuator_testing.application.serial_controller import (
    SerialConnectionStatus,
    SerialController,
)
from soft_actuator_testing.application.video_geometry_workflow import (
    FakeVideoFrameSource,
    VideoGeometryWorkflow,
)
from soft_actuator_testing.application.workspace import CloseWorkspace, CreateWorkspace, OpenWorkspace
from soft_actuator_testing.domain.calibration import CalibrationModelType, CalibrationSample
from soft_actuator_testing.domain.run_state import RunCompletion
from soft_actuator_testing.infrastructure.serial_adapter import (
    CommandReceipt,
    CommandState,
    SerialAdapter,
    SerialConnectionConfig,
    SerialTextParser,
    legacy_field_three_unconfirmed_profile,
)
from soft_actuator_testing.infrastructure.red_marker_detector import OpenCvRedMarkerFrameDetector
from soft_actuator_testing.infrastructure.ffmpeg import FfmpegTools
from soft_actuator_testing.infrastructure.video_file_reader import OpenCvVideoFileReader
from soft_actuator_testing.ui import production
from soft_actuator_testing.ui.production import create_production_composition
from soft_actuator_testing.ui.views import page_navigation
from soft_actuator_testing.ui.views.workflows.analysis import AnalysisPage
from soft_actuator_testing.ui.widgets.file_picker import FakeFilePicker


class _FakeSerialController(SerialController):
    """A connected controller seam which records commands but opens no port."""

    def __init__(self, order: list[str]) -> None:
        super().__init__(profile=legacy_field_three_unconfirmed_profile())
        self._snapshot = replace(self.snapshot, status=SerialConnectionStatus.CONNECTED)
        self.order = order
        self.commands: list[str] = []
        self.disconnects = 0

    @staticmethod
    def _receipt(command: str) -> CommandReceipt:
        return CommandReceipt(command, command, datetime.now(timezone.utc), CommandState.SENT)

    def set_legacy_parameters(self, *, cycles: int, on_milliseconds: int, off_milliseconds: int):
        commands = (
            f"CMD:SET CYCLES {cycles}",
            f"CMD:SET ON {on_milliseconds}",
            f"CMD:SET OFF {off_milliseconds}",
        )
        self.commands.extend(commands)
        return tuple(self._receipt(command) for command in commands)

    def send_command(self, command: str, **kwargs):
        del kwargs
        if command == "CMD:START":
            self.order.append("serial-start")
        self.commands.append(command)
        return self._receipt(command)

    def start_legacy_run(self):
        self.order.append("serial-start")
        self.commands.append("CMD:START")
        return self._receipt("CMD:START")

    def stop_legacy_run(self):
        self.commands.append("CMD:STOP")
        return self._receipt("CMD:STOP")

    def subscribe_frames(self, *_args, **_kwargs):
        class _EmptySubscription:
            def drain(self):
                return ()

            def close(self) -> None:
                return None

        return _EmptySubscription()

    def poll(self, maximum: int | None = None):
        del maximum
        return ()

    def disconnect(self):
        self.disconnects += 1
        return None


class _FakeCamera:
    """Capture seam that proves the run controller starts camera first."""

    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.health = CaptureHealth(ready=True)
        self.started: list[tuple[Path, str]] = []
        self.stops = 0

    def start_capture(self, output_directory: Path, device_identifier: str, *, duration_seconds=None) -> None:
        del duration_seconds
        self.order.append("camera-start")
        self.started.append((output_directory, device_identifier))

    def stop_capture(self, reason: str) -> CaptureResult:
        self.stops += 1
        video = self.started[-1][0] / "video.mkv"
        video.write_bytes(b"finalized")
        return CaptureResult(reason, video, video, True, True, self.health)


def test_default_production_camera_defers_runtime_encoder_probe_and_has_capacity_preflight(
    monkeypatch,
    tmp_path: Path,
) -> None:
    ffmpeg = tmp_path / "ffmpeg"
    ffprobe = tmp_path / "ffprobe"
    ffmpeg.write_text("", encoding="utf-8")
    ffprobe.write_text("", encoding="utf-8")
    tools = FfmpegTools(ffmpeg, ffprobe)
    monkeypatch.setattr(
        production.FfmpegTools,
        "discover",
        classmethod(lambda cls: tools),
    )

    capture, presenter = production._camera_services(None)

    assert capture is not None
    assert presenter is not None
    assert capture._storage_preflight is not None
    assert capture._backend._encoder is None


def _complete_geometry() -> VideoGeometryWorkflow:
    reader = FakeVideoFrameSource()
    source = Path("synthetic-video.mkv")
    reader.register(source, (np.zeros((16, 16, 3), dtype=np.uint8),))
    workflow = VideoGeometryWorkflow(reader)
    workflow.load_video(source)
    workflow.set_base_point(1, 1)
    workflow.set_tip_point(8, 8)
    workflow.set_roi_xywh(0, 0, 16, 16)
    return workflow


class _TranscriptTransport:
    def __init__(self) -> None:
        self.is_open = True
        self.lines: Queue[bytes] = Queue()
        self.writes: list[bytes] = []

    def readline(self) -> bytes:
        try:
            return self.lines.get(timeout=0.01)
        except Empty:
            return b""

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def close(self) -> None:
        self.is_open = False


class _TranscriptFactory:
    def __init__(self, transport: _TranscriptTransport) -> None:
        self.transport = transport

    def enumerate_ports(self):
        return ()

    def open(self, _config):
        return self.transport


def _wait_until(qtbot, predicate, *, timeout_seconds: float = 2.0) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if predicate():
            return
        qtbot.wait(10)
    assert predicate()


def test_production_console_composes_real_disconnected_workflows_end_to_end(qtbot, tmp_path: Path) -> None:
    order: list[str] = []
    serial = _FakeSerialController(order)
    camera = _FakeCamera(order)
    calibration = CalibrationWorkflowService(
        FakeCalibrationSampleSource(())
    )
    geometry = _complete_geometry()
    workspace = tmp_path / "production-e2e"
    picker = FakeFilePicker(queued_results=[tmp_path, workspace])
    composition = create_production_composition(
        serial=serial,
        camera=camera,  # type: ignore[arg-type]
        camera_device_provider=lambda: "fake-camera",
        calibration_service=calibration,
        geometry_workflow=geometry,
        preferences_path=tmp_path / "preferences.json",
        file_picker=picker,
    )
    qtbot.addWidget(composition.window)

    # Construction only wires services: it neither opens a port nor starts camera discovery/capture.
    assert order == []
    assert camera.started == []
    assert composition.window.environment is None
    assert composition.window.pages.keys() == {item.key for item in page_navigation()}
    assert not hasattr(composition.window, "scenario_switch")
    assert "Demo" not in composition.window.windowTitle()
    assert isinstance(composition.analysis_page, AnalysisPage)
    assert isinstance(composition.analysis_pipeline, AnalysisPipeline)
    assert isinstance(composition.analysis_pipeline._video_source, OpenCvVideoFileReader)
    assert isinstance(composition.analysis_pipeline._detector, OpenCvRedMarkerFrameDetector)

    composition.workspace_page.choose_storage_root()
    composition.workspace_page.workspace_name.setText("production-e2e")
    composition.workspace_page.create_workspace()
    assert composition.workspace_controller.snapshot.root == workspace
    workspace_store = composition.analysis_page.artifact_store
    assert workspace_store is not None
    assert composition.run_controller._storage is workspace_store
    assert composition.calibration_page.artifact_store is workspace_store
    assert composition.geometry_page.geometry_view.artifact_store is workspace_store
    assert composition.analysis_page._artifact_exporter._store is workspace_store
    assert "Workspace output" in composition.analysis_page.output_location_label.text()
    assert not composition.analysis_page.choose_output_button.isEnabled()
    composition.workspace_page.close_workspace_button.click()
    assert composition.run_controller._storage is None
    assert composition.calibration_page.artifact_store is None
    assert composition.geometry_page.geometry_view.artifact_store is None
    assert composition.analysis_page.artifact_store is None
    assert composition.analysis_page._artifact_exporter is None
    assert "unavailable" in composition.analysis_page.output_location_label.text()
    composition.workspace_page.choose_workspace()
    assert composition.workspace_controller.snapshot.root == workspace
    assert composition.analysis_page.artifact_store is not None
    assert composition.run_controller._storage is composition.analysis_page.artifact_store
    assert composition.calibration_page.artifact_store is composition.analysis_page.artifact_store
    assert composition.geometry_page.geometry_view.artifact_store is composition.analysis_page.artifact_store
    assert [call.method for call in picker.calls] == [
        "get_existing_directory",
        "get_existing_directory",
    ]

    calibration.replace_samples(
        (
            CalibrationSample(0, 0),
            CalibrationSample(50, 1),
            CalibrationSample(100, 2),
        )
    )
    composition.calibration_page._render_workflow()
    qtbot.mouseClick(composition.calibration_page.fit_button, Qt.MouseButton.LeftButton)
    assert calibration.snapshot.fit is not None
    assert geometry.ready_geometry is not None
    calibration_document = calibration.save(composition.calibration_page.artifact_store)
    geometry_document = geometry.save(composition.geometry_page.geometry_view.artifact_store)
    assert (workspace / "artifacts" / "calibration" / f"{calibration_document.metadata.identity.artifact_id}.json").is_file()
    assert (workspace / "artifacts" / "geometry" / f"{geometry_document.metadata.identity.artifact_id}.json").is_file()

    composition.readiness_page.configure()
    assert composition.run_controller.snapshot.readiness.ready

    composition.run_controller.start()
    assert order.index("camera-start") < order.index("serial-start")

    for command in (
        CloseWorkspace(),
        CreateWorkspace("blocked-during-run"),
        OpenWorkspace(workspace),
    ):
        result = composition.workspace_controller.dispatch(command)
        assert not result.accepted
        assert composition.workspace_controller.snapshot.root == workspace
        assert "Workspace action blocked" in composition.workspace_page.workspace_summary.text()
    assert not (tmp_path / "blocked-during-run").exists()

    finalization = composition.run_controller.complete()
    assert finalization.video_path is not None
    assert composition.workspace_controller.dispatch(CloseWorkspace()).accepted
    assert composition.workspace_controller.dispatch(OpenWorkspace(workspace)).accepted

    composition.window._refresh_production_run()
    assert isinstance(composition.analysis_page, AnalysisPage)
    assert composition.analysis_page.finalized_video == finalization.video_path
    assert "ready for authoritative analysis" in composition.analysis_page.status.text()
    assert composition.analysis_page.use_finalized_button.isEnabled()
    camera_starts_before_handoff = list(camera.started)
    composition.analysis_page.use_finalized_button.click()
    assert composition.analysis_page._video_path == finalization.video_path
    assert composition.analysis_page._run_thread is None
    assert camera.started == camera_starts_before_handoff
    composition.analysis_page.receive_finalization(replace(finalization, video_path=None))
    assert "No finalized video" in composition.analysis_page.source.text()
    assert not composition.analysis_page.use_finalized_button.isEnabled()

    started = monotonic()
    composition.window.close()
    assert monotonic() - started < 1.0
    assert serial.disconnects >= 1
    assert camera.stops == 1
    # Neither embedded page is top-level, so neither receives its own
    # closeEvent automatically from the shell closing; composition-level
    # close() explicitly stops both timers deterministically (see
    # docs/architecture/quality-ui-accessibility.md).
    assert not composition.live_run_page._timer.isActive()
    assert composition.analysis_page._run_thread is None
    assert composition.analysis_page._live_thread is None


def test_window_close_stops_the_embedded_camera_panel_poll_timer(qtbot, tmp_path: Path) -> None:
    """An embedded ``CameraPanel`` never gets its own closeEvent from the shell.

    Composition-level ``close()`` must call ``stop_polling()`` explicitly so
    the panel's poll timer stops deterministically alongside the rest of the
    bounded shutdown, without double-invoking ``presenter.close()`` (which
    the composition already calls directly with its own timeout).
    """

    from soft_actuator_testing.application.camera_capture import (
        CameraCaptureService,
        CameraDevice,
        CameraPanelPresenter,
        CaptureHealth,
        LatestFrameChannel,
        PreviewFrame,
    )
    from soft_actuator_testing.infrastructure.camera import FakeCameraDeviceSource
    from soft_actuator_testing.ui.widgets.camera_panel import CameraPanel

    class _FakeCameraPanelBackend:
        def __init__(self) -> None:
            self.frame_channel: LatestFrameChannel[PreviewFrame] = LatestFrameChannel()
            self.health = CaptureHealth()
            self.close_timeouts: list[float | None] = []

        def start(self, output_directory, device_identifier, *, readiness_timeout):
            del output_directory, device_identifier, readiness_timeout

        def stop(self, reason="operator", *, timeout=None):
            del reason, timeout
            return None

        def close(self, *, timeout=None):
            self.close_timeouts.append(timeout)
            return self.stop("close")

    order: list[str] = []
    serial = _FakeSerialController(order)
    backend = _FakeCameraPanelBackend()
    presenter = CameraPanelPresenter(
        FakeCameraDeviceSource([CameraDevice("fake-0", "Synthetic camera", "fake")]),
        CameraCaptureService(backend),
    )
    composition = create_production_composition(
        serial=serial,
        camera_presenter=presenter,
        preferences_path=tmp_path / "preferences.json",
        file_picker=FakeFilePicker(queued_results=[]),
    )
    qtbot.addWidget(composition.window)

    assert isinstance(composition.connections_page.camera_panel, CameraPanel)
    assert composition.connections_page.camera_panel._poll_timer.isActive()
    assert composition.analysis_page._camera_presenter is presenter
    assert composition.analysis_page._camera_bridge is not None
    assert composition.analysis_page._camera_poll_timer is not None
    assert composition.analysis_page._camera_poll_timer.isActive()
    assert composition.analysis_page._live_display_timer is not None
    assert composition.analysis_page._live_display_timer.isActive()

    started = monotonic()
    composition.window.close()

    assert monotonic() - started < 1.0
    assert not composition.connections_page.camera_panel._poll_timer.isActive()
    assert not composition.analysis_page._camera_poll_timer.isActive()
    assert not composition.analysis_page._live_display_timer.isActive()
    assert composition.analysis_page._camera_bridge is not None
    assert backend.close_timeouts == [5.0]


def test_production_serial_fanout_keeps_diagnostics_calibration_and_run_independent(qtbot, tmp_path: Path) -> None:
    """Exercise the real serial adapter through the production composition."""

    transport = _TranscriptTransport()
    serial = SerialController(
        SerialAdapter(
            _TranscriptFactory(transport),
            parser=SerialTextParser(legacy_field_three_unconfirmed_profile()),
        )
    )
    assert serial.connect(SerialConnectionConfig("FAKE0"))
    camera = _FakeCamera([])
    workspace = tmp_path / "serial-fanout"
    composition = create_production_composition(
        serial=serial,
        camera=camera,  # type: ignore[arg-type]
        camera_device_provider=lambda: "fake-camera",
        geometry_workflow=_complete_geometry(),
        preferences_path=tmp_path / "preferences.json",
        file_picker=FakeFilePicker(queued_results=[tmp_path, workspace]),
    )
    qtbot.addWidget(composition.window)
    composition.workspace_page.choose_storage_root()
    composition.workspace_page.workspace_name.setText("serial-fanout")
    composition.workspace_page.create_workspace()
    composition.calibration_service.replace_samples(
        (
            CalibrationSample(0, 0),
            CalibrationSample(50, 1),
            CalibrationSample(100, 2),
        )
    )
    composition.calibration_service.fit(CalibrationModelType.LINEAR)
    composition.readiness_page.configure()
    assert composition.run_controller.snapshot.readiness.ready

    composition.run_controller.start()
    captured: list[object] = []
    capture_errors: list[Exception] = []

    def capture() -> None:
        try:
            captured.append(
                composition.calibration_source.request_after(composition.calibration_source.current_sequence())
            )
        except Exception as error:  # pragma: no cover - asserted below
            capture_errors.append(error)

    calibration_worker = Thread(target=capture)
    calibration_worker.start()
    _wait_until(qtbot, lambda: b"CMD:CAL_ON\n" in transport.writes)
    sleep(0.05)
    transport.lines.put(b"0.1,ignored,1.25\n")
    _wait_until(qtbot, lambda: bool(captured) or bool(capture_errors))
    calibration_worker.join(1)
    assert not calibration_worker.is_alive()
    assert not capture_errors
    assert captured[0].volts == 1.25
    _wait_until(qtbot, lambda: len(composition.run_controller.snapshot.telemetry) == 1)
    assert "Mapped telemetry" in serial.snapshot.diagnostic_text

    transport.lines.put(b"--- end run ---\n")
    _wait_until(
        qtbot,
        lambda: composition.run_controller.finalization_result is not None,
    )
    assert composition.run_controller.finalization_result is not None
    assert composition.run_controller.finalization_result.completion is RunCompletion.CLEAN
    _wait_until(qtbot, lambda: "Run ended marker received" in serial.snapshot.diagnostic_text)
    assert "Run ended marker received" in serial.snapshot.diagnostic_text

    composition.readiness_page.configure()
    composition.run_controller.start()
    transport.lines.put(b"ERROR: simulated controller fault\n")
    _wait_until(
        qtbot,
        lambda: composition.run_controller.finalization_result is not None,
    )
    assert composition.run_controller.finalization_result is not None
    assert composition.run_controller.finalization_result.completion is RunCompletion.FAULTED
    _wait_until(qtbot, lambda: "simulated controller fault" in serial.snapshot.diagnostic_text)
    assert "simulated controller fault" in serial.snapshot.diagnostic_text
    composition.window.close()
