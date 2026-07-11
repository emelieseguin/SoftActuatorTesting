"""Qt-free presenter snapshots, commands, state store, and workflow controller."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Generic, Protocol, TypeAlias, TypeVar, runtime_checkable

from soft_actuator_testing.application.services import (
    AnalysisService,
    CameraService,
    MarkerDetector,
    RunLifecycleService,
    SerialService,
)
from soft_actuator_testing.domain.calibration import CalibrationFit, CalibrationSample
from soft_actuator_testing.domain.geometry import VideoGeometry
from soft_actuator_testing.domain.run_state import RunCompletion, RunSnapshot, RunState


class ConnectionStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FAULT = "fault"


class AnalysisMode(str, Enum):
    RECORDED_FILE = "recorded-file"
    LIVE_CAPTURE = "live-capture"


class NoticeSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class PreviewFrameSnapshot:
    width: int
    height: int
    channels: int
    rgb_bytes: bytes
    frame_index: int
    frame_count: int | None
    description: str


@dataclass(frozen=True)
class TelemetryPointSnapshot:
    timestamp_seconds: float
    pressure_kpa: float


@dataclass(frozen=True)
class WorkspaceSnapshot:
    path: Path | None
    summary: str
    is_selected: bool
    is_demo: bool = True


@dataclass(frozen=True)
class DeviceConnectionsSnapshot:
    controller: ConnectionStatus
    camera: ConnectionStatus
    diagnostic_text: str

    @property
    def all_connected(self) -> bool:
        return self.controller is ConnectionStatus.CONNECTED and self.camera is ConnectionStatus.CONNECTED


@dataclass(frozen=True)
class CalibrationSnapshot:
    samples: tuple[CalibrationSample, ...]
    fit_summary: str
    is_ready: bool


@dataclass(frozen=True)
class GeometrySnapshot:
    summary: str
    is_ready: bool
    preview: PreviewFrameSnapshot | None = None


@dataclass(frozen=True)
class ReadinessSnapshot:
    is_ready: bool
    missing: tuple[str, ...]
    guidance: str
    next_action: str
    diagnostics: tuple[str, ...]
    experiment_name: str
    cycles: int
    record_video: bool = True


@dataclass(frozen=True)
class RunPresenterSnapshot:
    lifecycle: RunSnapshot
    can_start: bool
    can_global_stop: bool
    status_text: str
    outcome_text: str
    telemetry: tuple[TelemetryPointSnapshot, ...] = ()
    preview: PreviewFrameSnapshot | None = None


@dataclass(frozen=True)
class AnalysisSnapshot:
    mode: AnalysisMode
    source: Path | None
    progress_percent: int
    review: str
    is_complete: bool


@dataclass(frozen=True)
class SettingsSnapshot:
    profile: str
    compact_mode: bool
    result: str


@dataclass(frozen=True)
class NotificationSnapshot:
    identifier: int
    message: str
    severity: NoticeSeverity


@dataclass(frozen=True)
class FaultSnapshot:
    code: str
    message: str
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApplicationSnapshot:
    workspace: WorkspaceSnapshot
    devices: DeviceConnectionsSnapshot
    calibration: CalibrationSnapshot
    geometry: GeometrySnapshot
    readiness: ReadinessSnapshot
    run: RunPresenterSnapshot
    analysis: AnalysisSnapshot
    settings: SettingsSnapshot
    notifications: tuple[NotificationSnapshot, ...] = ()
    faults: tuple[FaultSnapshot, ...] = ()
    completed_steps: frozenset[str] = frozenset()
    revision: int = 0


# Workspace commands
@dataclass(frozen=True)
class SelectWorkspace:
    path: Path


@dataclass(frozen=True)
class CreateWorkspace:
    path: Path = Path("/demo/soft-actuator-new")


# Connection commands
@dataclass(frozen=True)
class ConnectDevices:
    pass


@dataclass(frozen=True)
class DisconnectDevices:
    reason: str = "Operator disconnected demo devices."


@dataclass(frozen=True)
class RequestDiagnostics:
    pass


# Calibration commands
@dataclass(frozen=True)
class CollectCalibrationSamples:
    pass


@dataclass(frozen=True)
class FitCalibration:
    pass


# Geometry commands
@dataclass(frozen=True)
class SetManualGeometry:
    pass


@dataclass(frozen=True)
class DetectMarker:
    pass


# Readiness commands
@dataclass(frozen=True)
class ConfigureExperiment:
    name: str
    cycles: int
    record_video: bool = True


@dataclass(frozen=True)
class EvaluateReadiness:
    pass


# Run commands. Begin/confirmation are separate so STARTING and STOPPING are
# observable states rather than implementation timing accidents.
@dataclass(frozen=True)
class BeginRun:
    pass


@dataclass(frozen=True)
class ConfirmRunStarted:
    pass


@dataclass(frozen=True)
class CompleteRun:
    """Record ordinary controller-declared clean completion."""


@dataclass(frozen=True)
class RequestRunStop:
    """Request an ordinary stop without assigning its eventual outcome."""


@dataclass(frozen=True)
class ConfirmRunStopped:
    pass


@dataclass(frozen=True)
class GlobalStop:
    """Idempotent emergency-abort acknowledgement."""


@dataclass(frozen=True)
class ReportRunFault:
    reason: str
    code: str = "run-fault"


@dataclass(frozen=True)
class ReportRunTimeout:
    reason: str = "Run cleanup timed out."


# Analysis/settings/notification commands
@dataclass(frozen=True)
class ChooseAnalysisSource:
    path: Path


@dataclass(frozen=True)
class SetAnalysisMode:
    mode: AnalysisMode


@dataclass(frozen=True)
class RunAnalysis:
    pass


@dataclass(frozen=True)
class ApplySettings:
    profile: str
    compact_mode: bool


@dataclass(frozen=True)
class DismissNotification:
    identifier: int


WorkspaceCommand: TypeAlias = SelectWorkspace | CreateWorkspace
DeviceCommand: TypeAlias = ConnectDevices | DisconnectDevices | RequestDiagnostics
CalibrationCommand: TypeAlias = CollectCalibrationSamples | FitCalibration
GeometryCommand: TypeAlias = SetManualGeometry | DetectMarker
ReadinessCommand: TypeAlias = ConfigureExperiment | EvaluateReadiness
RunCommand: TypeAlias = (
    BeginRun
    | ConfirmRunStarted
    | CompleteRun
    | RequestRunStop
    | ConfirmRunStopped
    | GlobalStop
    | ReportRunFault
    | ReportRunTimeout
)
AnalysisCommand: TypeAlias = ChooseAnalysisSource | SetAnalysisMode | RunAnalysis
SettingsCommand: TypeAlias = ApplySettings
NotificationCommand: TypeAlias = DismissNotification
ApplicationCommand: TypeAlias = (
    WorkspaceCommand
    | DeviceCommand
    | CalibrationCommand
    | GeometryCommand
    | ReadinessCommand
    | RunCommand
    | AnalysisCommand
    | SettingsCommand
    | NotificationCommand
)


@dataclass(frozen=True)
class CommandResult:
    accepted: bool
    message: str
    idempotent: bool = False


SnapshotT = TypeVar("SnapshotT")
SnapshotCallback = Callable[[SnapshotT], None]


class Subscription:
    """An idempotent subscription handle safe to dispose during publication."""

    def __init__(self, dispose: Callable[[], None]) -> None:
        self._dispose_callback = dispose
        self._disposed = False
        self._lock = RLock()

    @property
    def is_disposed(self) -> bool:
        return self._disposed

    def dispose(self) -> None:
        with self._lock:
            if self._disposed:
                return
            self._disposed = True
            callback = self._dispose_callback
            self._dispose_callback = lambda: None
        callback()

    def __call__(self) -> None:
        self.dispose()


class StateStore(Generic[SnapshotT]):
    """Single Qt-free current-state/subscription seam for immutable snapshots."""

    def __init__(self, initial: SnapshotT) -> None:
        self._snapshot = initial
        self._subscribers: dict[int, SnapshotCallback[SnapshotT]] = {}
        self._next_identifier = 1
        self._lock = RLock()

    @property
    def snapshot(self) -> SnapshotT:
        with self._lock:
            return self._snapshot

    def publish(self, snapshot: SnapshotT) -> None:
        with self._lock:
            if snapshot == self._snapshot:
                return
            self._snapshot = snapshot
            callbacks = tuple(self._subscribers.values())
        for callback in callbacks:
            callback(snapshot)

    def subscribe(
        self,
        callback: SnapshotCallback[SnapshotT],
        *,
        emit_current: bool = False,
    ) -> Subscription:
        with self._lock:
            identifier = self._next_identifier
            self._next_identifier += 1
            self._subscribers[identifier] = callback
            current = self._snapshot

        def dispose() -> None:
            with self._lock:
                self._subscribers.pop(identifier, None)

        subscription = Subscription(dispose)
        if emit_current:
            callback(current)
        return subscription


@runtime_checkable
class ApplicationStateSource(Protocol):
    @property
    def snapshot(self) -> ApplicationSnapshot: ...

    def subscribe(
        self,
        callback: SnapshotCallback[ApplicationSnapshot],
        *,
        emit_current: bool = False,
    ) -> Subscription: ...


@runtime_checkable
class ApplicationCommandDispatcher(Protocol):
    def dispatch(self, command: ApplicationCommand) -> CommandResult: ...


@dataclass(frozen=True)
class PresenterSession:
    state: ApplicationStateSource
    commands: ApplicationCommandDispatcher


class RunLifecycleCoordinator(Protocol):
    def snapshot(self) -> RunSnapshot: ...

    def connect(self): ...

    def mark_idle(self): ...

    def mark_ready(self): ...

    def begin_start(self): ...

    def mark_running(self): ...

    def mark_fault(self): ...

    def stop(self): ...

    def finalize(self, completion: RunCompletion): ...

    def disconnect(self): ...


def _run_label(snapshot: RunSnapshot) -> str:
    label = snapshot.state.value.replace("_", " ").title()
    if snapshot.completion is not None:
        label = f"{label} ({snapshot.completion.value})"
    return label


class WorkflowController:
    """Synchronous orchestration over injected adapters for demo/test use.

    Real workspace, serial, camera, calibration, geometry, recording, and
    analysis implementations remain outside this module. They can implement the
    same command/state seams without changing views.
    """

    _ACTIVE_RUN_STATES = frozenset({RunState.STARTING, RunState.RUNNING, RunState.STOPPING})

    def __init__(
        self,
        *,
        serial: SerialService,
        camera: CameraService,
        detector: MarkerDetector,
        lifecycle: RunLifecycleCoordinator,
        analysis: AnalysisService,
        geometry: VideoGeometry,
        calibration_fit: CalibrationFit,
        calibration_samples: Sequence[CalibrationSample],
    ) -> None:
        self._serial = serial
        self._camera = camera
        self._detector = detector
        self._lifecycle = lifecycle
        self._analysis_service = analysis
        self._geometry_model = geometry
        self._calibration_fit = calibration_fit
        self._calibration_samples = tuple(calibration_samples)
        self._notice_identifier = 0

        snapshot = ApplicationSnapshot(
            workspace=WorkspaceSnapshot(
                path=Path("/demo/soft-actuator-run-0042"),
                summary="3 artifacts • calibration ready • geometry ready",
                is_selected=True,
            ),
            devices=DeviceConnectionsSnapshot(
                controller=ConnectionStatus.DISCONNECTED,
                camera=ConnectionStatus.DISCONNECTED,
                diagnostic_text="Devices are disconnected. Demo mode never opens a real port or camera.",
            ),
            calibration=CalibrationSnapshot(
                samples=(),
                fit_summary="Demo calibration is available; collect samples to inspect the fit.",
                is_ready=True,
            ),
            geometry=GeometrySnapshot(
                summary="Base (8, 40), tip (32, 12), ROI 4,4–60,44",
                is_ready=True,
            ),
            readiness=ReadinessSnapshot(
                is_ready=False,
                missing=(),
                guidance="",
                next_action="",
                diagnostics=(),
                experiment_name="Cyclic pressure validation",
                cycles=10,
            ),
            run=RunPresenterSnapshot(
                lifecycle=lifecycle.snapshot(),
                can_start=False,
                can_global_stop=False,
                status_text="Disconnected",
                outcome_text="No run has completed.",
            ),
            analysis=AnalysisSnapshot(
                mode=AnalysisMode.RECORDED_FILE,
                source=Path("recorded-demo.mp4"),
                progress_percent=0,
                review="No analysis results yet.",
                is_complete=False,
            ),
            settings=SettingsSnapshot(
                profile="Operator",
                compact_mode=False,
                result="Settings are session-only in the prototype.",
            ),
            notifications=(
                NotificationSnapshot(
                    identifier=0,
                    message="Demo mode: hardware is disconnected; commands use deterministic adapters.",
                    severity=NoticeSeverity.INFO,
                ),
            ),
        )
        self._snapshot = self._derive(snapshot)
        self.store: StateStore[ApplicationSnapshot] = StateStore(self._snapshot)

    @property
    def snapshot(self) -> ApplicationSnapshot:
        return self.store.snapshot

    def session(self) -> PresenterSession:
        return PresenterSession(state=self.store, commands=self)

    def dispatch(self, command: ApplicationCommand) -> CommandResult:
        try:
            result = self._dispatch(command)
        except Exception as error:
            result = self._record_fault(
                code="command-failed",
                message=f"{type(command).__name__} failed: {error}",
            )
        self._snapshot = self._derive(replace(self._snapshot, revision=self._snapshot.revision + 1))
        self.store.publish(self._snapshot)
        return result

    def _dispatch(self, command: ApplicationCommand) -> CommandResult:
        if isinstance(command, SelectWorkspace):
            self._snapshot = replace(
                self._snapshot,
                workspace=WorkspaceSnapshot(
                    path=command.path,
                    summary="Selected workspace is represented in memory only.",
                    is_selected=True,
                ),
            )
            return CommandResult(True, f"Selected workspace {command.path}.")
        if isinstance(command, CreateWorkspace):
            self._snapshot = replace(
                self._snapshot,
                workspace=WorkspaceSnapshot(
                    path=command.path,
                    summary="Empty deterministic workspace created; no filesystem changes.",
                    is_selected=True,
                ),
            )
            return CommandResult(True, "Created deterministic workspace.")
        if isinstance(command, ConnectDevices):
            return self._connect_devices()
        if isinstance(command, DisconnectDevices):
            return self._disconnect_devices(command.reason)
        if isinstance(command, RequestDiagnostics):
            self._serial.send_command("DEMO:DIAGNOSTICS")
            sample = next(self._serial.telemetry())
            devices = replace(
                self._snapshot.devices,
                diagnostic_text=f"DEMO:DIAGNOSTICS\n{sample.raw_line}\nNo physical controller was contacted.",
            )
            self._snapshot = replace(
                self._snapshot,
                devices=devices,
                completed_steps=self._snapshot.completed_steps | {"connections"},
            )
            return CommandResult(True, "Diagnostics completed.")
        if isinstance(command, CollectCalibrationSamples):
            self._snapshot = replace(
                self._snapshot,
                calibration=replace(self._snapshot.calibration, samples=self._calibration_samples),
            )
            return CommandResult(True, "Collected deterministic calibration samples.")
        if isinstance(command, FitCalibration):
            adequacy = self._calibration_fit.adequacy
            calibration = replace(
                self._snapshot.calibration,
                fit_summary=(
                    f"Linear demo fit • R² {adequacy.r_squared:.3f} • "
                    f"RMSE {adequacy.rmse_kpa:.3f} kPa"
                ),
                is_ready=True,
            )
            self._snapshot = replace(
                self._snapshot,
                calibration=calibration,
                completed_steps=self._snapshot.completed_steps | {"calibration"},
            )
            return CommandResult(True, "Calibration fit completed.")
        if isinstance(command, SetManualGeometry):
            self._snapshot = replace(
                self._snapshot,
                geometry=replace(
                    self._snapshot.geometry,
                    summary="Manual geometry: base (10, 40), tip (34, 12), ROI 6,4–60,44",
                    is_ready=True,
                ),
            )
            return CommandResult(True, "Manual geometry selected.")
        if isinstance(command, DetectMarker):
            frame = next(self._camera.frames())
            result = self._detector.detect(frame, self._geometry_model)
            if result.point is None:
                summary = f"Automatic marker: {result.state.value}; no point available."
            else:
                summary = (
                    f"Automatic marker: {result.state.value}; "
                    f"tip ({result.point.x:.0f}, {result.point.y:.0f}), "
                    f"confidence {result.confidence:.1f}"
                )
            self._snapshot = replace(
                self._snapshot,
                geometry=replace(
                    self._snapshot.geometry,
                    summary=summary,
                    is_ready=True,
                    preview=self._preview(frame, "demo geometry frame"),
                ),
                completed_steps=self._snapshot.completed_steps | {"geometry"},
            )
            return CommandResult(True, "Marker detection completed.")
        if isinstance(command, ConfigureExperiment):
            readiness = replace(
                self._snapshot.readiness,
                experiment_name=command.name.strip(),
                cycles=command.cycles,
                record_video=command.record_video,
            )
            self._snapshot = replace(self._snapshot, readiness=readiness)
            return CommandResult(True, "Experiment configuration updated.")
        if isinstance(command, EvaluateReadiness):
            if self._snapshot.readiness.experiment_name:
                self._snapshot = replace(
                    self._snapshot,
                    completed_steps=self._snapshot.completed_steps | {"experiment"},
                )
            evaluated = self._derive(self._snapshot)
            if (
                evaluated.readiness.is_ready
                and self._lifecycle.snapshot().state is RunState.COMPLETED
            ):
                self._lifecycle.mark_idle()
                evaluated = self._derive(evaluated)
            self._snapshot = evaluated
            return CommandResult(
                evaluated.readiness.is_ready,
                evaluated.readiness.guidance,
            )
        if isinstance(command, BeginRun):
            if not self._snapshot.readiness.is_ready:
                return self._record_fault("run-not-ready", self._snapshot.readiness.guidance)
            if self._lifecycle.snapshot().state is RunState.STARTING:
                return CommandResult(True, "Run is already starting.", idempotent=True)
            if self._lifecycle.snapshot().state is not RunState.READY:
                return self._record_fault(
                    "illegal-run-start",
                    f"Cannot start while run lifecycle is {self._lifecycle.snapshot().state.value}.",
                )
            self._lifecycle.begin_start()
            self._snapshot = replace(
                self._snapshot,
                run=replace(self._snapshot.run, outcome_text="Run start requested."),
            )
            return CommandResult(True, "Run is starting.")
        if isinstance(command, ConfirmRunStarted):
            if self._lifecycle.snapshot().state is RunState.RUNNING:
                return CommandResult(True, "Run is already running.", idempotent=True)
            self._lifecycle.mark_running()
            telemetry = tuple(self._telemetry())
            frame = next(self._camera.frames())
            self._snapshot = replace(
                self._snapshot,
                run=replace(
                    self._snapshot.run,
                    telemetry=telemetry,
                    preview=self._preview(frame, "live synthetic frame"),
                    outcome_text="Run started; deterministic preview is active.",
                ),
            )
            return CommandResult(True, "Run started.")
        if isinstance(command, CompleteRun):
            return self._complete_clean()
        if isinstance(command, RequestRunStop):
            state = self._lifecycle.snapshot().state
            if state in {RunState.STOPPING, RunState.COMPLETED}:
                return CommandResult(True, "Run stop was already requested.", idempotent=True)
            self._lifecycle.stop()
            return CommandResult(True, "Ordinary stop requested; awaiting cleanup.")
        if isinstance(command, ConfirmRunStopped):
            state = self._lifecycle.snapshot().state
            if state is RunState.COMPLETED:
                completion = self._lifecycle.snapshot().completion
                return CommandResult(
                    completion is RunCompletion.STOPPED,
                    "Run stop was already finalized.",
                    idempotent=True,
                )
            self._lifecycle.finalize(RunCompletion.STOPPED)
            self._snapshot = replace(
                self._snapshot,
                run=replace(self._snapshot.run, outcome_text="Run stopped by ordinary operator request."),
                completed_steps=self._snapshot.completed_steps | {"live-run"},
            )
            return CommandResult(True, "Run stopped.")
        if isinstance(command, GlobalStop):
            return self._global_stop()
        if isinstance(command, ReportRunFault):
            return self._fault_run(command.code, command.reason)
        if isinstance(command, ReportRunTimeout):
            return self._fault_run("run-timeout", command.reason)
        if isinstance(command, ChooseAnalysisSource):
            self._snapshot = replace(
                self._snapshot,
                analysis=replace(
                    self._snapshot.analysis,
                    source=command.path,
                    progress_percent=0,
                    review="Recorded source selected; analysis has not run.",
                    is_complete=False,
                ),
            )
            return CommandResult(True, f"Selected analysis source {command.path}.")
        if isinstance(command, SetAnalysisMode):
            source = (
                Path("live-demo-capture.mp4")
                if command.mode is AnalysisMode.LIVE_CAPTURE
                else self._snapshot.analysis.source or Path("recorded-demo.mp4")
            )
            self._snapshot = replace(
                self._snapshot,
                analysis=replace(
                    self._snapshot.analysis,
                    mode=command.mode,
                    source=source,
                    progress_percent=0,
                    is_complete=False,
                ),
            )
            return CommandResult(True, f"Analysis mode set to {command.mode.value}.")
        if isinstance(command, RunAnalysis):
            source = self._snapshot.analysis.source or Path("recorded-demo.mp4")
            results = tuple(self._analysis_service.analyze(source, self._geometry_model))
            detected = sum(result.detection.point is not None for result in results)
            self._snapshot = replace(
                self._snapshot,
                analysis=replace(
                    self._snapshot.analysis,
                    progress_percent=100,
                    review=(
                        f"Reviewed {len(results)} frames; {detected} marker positions available; "
                        "results are deterministic."
                    ),
                    is_complete=True,
                ),
                completed_steps=self._snapshot.completed_steps | {"analysis"},
            )
            return CommandResult(True, "Analysis completed.")
        if isinstance(command, ApplySettings):
            density = "compact" if command.compact_mode else "comfortable"
            self._snapshot = replace(
                self._snapshot,
                settings=SettingsSnapshot(
                    profile=command.profile,
                    compact_mode=command.compact_mode,
                    result=(
                        f"{command.profile} profile applied with {density} density; "
                        "no persistence was performed."
                    ),
                ),
            )
            return CommandResult(True, "Settings applied for this session.")
        if isinstance(command, DismissNotification):
            self._snapshot = replace(
                self._snapshot,
                notifications=tuple(
                    notice
                    for notice in self._snapshot.notifications
                    if notice.identifier != command.identifier
                ),
            )
            return CommandResult(True, "Notification dismissed.")
        raise TypeError(f"unsupported application command: {type(command).__name__}")

    def _connect_devices(self) -> CommandResult:
        if self._snapshot.devices.all_connected:
            return CommandResult(True, "Devices are already connected.", idempotent=True)
        devices = replace(
            self._snapshot.devices,
            controller=ConnectionStatus.CONNECTING,
            camera=ConnectionStatus.CONNECTING,
            diagnostic_text="Connecting deterministic controller and camera.",
        )
        self._snapshot = replace(self._snapshot, devices=devices)
        self._serial.connect()
        self._camera.open()
        state = self._lifecycle.snapshot().state
        if state in {RunState.COMPLETED, RunState.FAULT, RunState.IDLE, RunState.READY}:
            self._lifecycle.disconnect()
        if self._lifecycle.snapshot().state is RunState.DISCONNECTED:
            self._lifecycle.connect()
            self._lifecycle.mark_idle()
        self._snapshot = replace(
            self._snapshot,
            devices=DeviceConnectionsSnapshot(
                controller=ConnectionStatus.CONNECTED,
                camera=ConnectionStatus.CONNECTED,
                diagnostic_text="Connected to deterministic fake controller and camera.",
            ),
            faults=(),
            completed_steps=self._snapshot.completed_steps | {"connections"},
        )
        return CommandResult(True, "Devices connected.")

    def _disconnect_devices(self, reason: str) -> CommandResult:
        state = self._lifecycle.snapshot().state
        if state in self._ACTIVE_RUN_STATES:
            if state is not RunState.STOPPING:
                self._lifecycle.stop()
            self._lifecycle.finalize(RunCompletion.FAULTED)
            self._snapshot = replace(
                self._snapshot,
                run=replace(
                    self._snapshot.run,
                    outcome_text="Active run finalized as faulted after device disconnect.",
                ),
                faults=(
                    FaultSnapshot(
                        code="device-disconnected",
                        message=reason,
                        diagnostics=("No hardware safe state is assumed by the presenter.",),
                    ),
                ),
            )
        self._serial.disconnect()
        self._camera.close()
        self._lifecycle.disconnect()
        self._snapshot = replace(
            self._snapshot,
            devices=DeviceConnectionsSnapshot(
                controller=ConnectionStatus.DISCONNECTED,
                camera=ConnectionStatus.DISCONNECTED,
                diagnostic_text=reason,
            ),
        )
        return CommandResult(True, "Devices disconnected.")

    def _complete_clean(self) -> CommandResult:
        state = self._lifecycle.snapshot().state
        if state is RunState.COMPLETED:
            completion = self._lifecycle.snapshot().completion
            return CommandResult(
                completion is RunCompletion.CLEAN,
                "Run completion was already recorded.",
                idempotent=True,
            )
        if state is RunState.RUNNING:
            self._lifecycle.stop()
        self._lifecycle.finalize(RunCompletion.CLEAN)
        self._snapshot = replace(
            self._snapshot,
            run=replace(
                self._snapshot.run,
                outcome_text="Run completed cleanly; no emergency abort was requested.",
            ),
            completed_steps=self._snapshot.completed_steps | {"live-run"},
        )
        return CommandResult(True, "Run completed cleanly.")

    def _global_stop(self) -> CommandResult:
        state = self._lifecycle.snapshot().state
        if state in {RunState.STARTING, RunState.RUNNING}:
            self._lifecycle.stop()
            self._lifecycle.finalize(RunCompletion.ABORTED)
            self._snapshot = replace(
                self._snapshot,
                run=replace(
                    self._snapshot.run,
                    outcome_text="Global STOP acknowledged — active run finalized as aborted.",
                ),
            )
            self._notice("Global STOP acknowledged; run aborted.", NoticeSeverity.ERROR)
            return CommandResult(True, "Global STOP acknowledged; run aborted.")
        if state is RunState.STOPPING:
            self._lifecycle.finalize(RunCompletion.ABORTED)
            self._snapshot = replace(
                self._snapshot,
                run=replace(
                    self._snapshot.run,
                    outcome_text="Global STOP acknowledged while stopping; finalized as aborted.",
                ),
            )
            self._notice("Global STOP acknowledged while stopping.", NoticeSeverity.ERROR)
            return CommandResult(True, "Global STOP finalized the stopping run as aborted.")
        if state is RunState.COMPLETED and self._lifecycle.snapshot().completion is RunCompletion.ABORTED:
            self._notice("Duplicate Global STOP acknowledged; run was already aborted.", NoticeSeverity.INFO)
            return CommandResult(True, "Run was already aborted.", idempotent=True)
        self._notice("Global STOP acknowledged; no active run required abort.", NoticeSeverity.INFO)
        return CommandResult(True, "No active demo run required abort.", idempotent=True)

    def _fault_run(self, code: str, reason: str) -> CommandResult:
        state = self._lifecycle.snapshot().state
        if state in {RunState.STARTING, RunState.RUNNING}:
            self._lifecycle.stop()
            self._lifecycle.finalize(RunCompletion.FAULTED)
        elif state is RunState.STOPPING:
            self._lifecycle.finalize(RunCompletion.FAULTED)
        elif state in {RunState.CONNECTING, RunState.IDLE, RunState.READY}:
            self._lifecycle.mark_fault()
        self._snapshot = replace(
            self._snapshot,
            run=replace(
                self._snapshot.run,
                outcome_text=f"Run finalized fault-safe: {reason}",
            ),
            faults=(
                FaultSnapshot(
                    code=code,
                    message=reason,
                    diagnostics=(
                        "Cleanup/finalization was requested.",
                        "The presenter does not claim a hardware safe state.",
                    ),
                ),
            ),
        )
        self._notice(reason, NoticeSeverity.ERROR)
        return CommandResult(False, reason)

    def _record_fault(self, code: str, message: str) -> CommandResult:
        self._snapshot = replace(
            self._snapshot,
            faults=(FaultSnapshot(code=code, message=message),),
        )
        self._notice(message, NoticeSeverity.ERROR)
        return CommandResult(False, message)

    def _notice(self, message: str, severity: NoticeSeverity) -> None:
        self._notice_identifier += 1
        notices = (
            *self._snapshot.notifications[-19:],
            NotificationSnapshot(self._notice_identifier, message, severity),
        )
        self._snapshot = replace(self._snapshot, notifications=notices)

    def _telemetry(self):
        for item in tuple(self._serial.telemetry())[:8]:
            if item.volts is None:
                continue
            pressure = self._calibration_fit.model.apply(item.volts, require_in_domain=False)
            yield TelemetryPointSnapshot(item.timestamp_seconds, pressure)

    @staticmethod
    def _preview(frame, description: str) -> PreviewFrameSnapshot | None:
        image = frame.image
        shape = getattr(image, "shape", ())
        if len(shape) != 3:
            return None
        height, width, channels = shape
        return PreviewFrameSnapshot(
            width=int(width),
            height=int(height),
            channels=int(channels),
            rgb_bytes=bytes(image.tobytes()),
            frame_index=frame.frame_index,
            frame_count=0,
            description=description,
        )

    def _derive(self, snapshot: ApplicationSnapshot) -> ApplicationSnapshot:
        missing: list[str] = []
        diagnostics: list[str] = []
        if not snapshot.workspace.is_selected:
            missing.append("Choose a workspace")
            diagnostics.append("Workspace: no selection")
        if snapshot.devices.controller is not ConnectionStatus.CONNECTED:
            missing.append("Connect the controller")
            diagnostics.append(f"Controller: {snapshot.devices.controller.value}")
        if snapshot.devices.camera is not ConnectionStatus.CONNECTED:
            missing.append("Connect the camera")
            diagnostics.append(f"Camera: {snapshot.devices.camera.value}")
        if not snapshot.calibration.is_ready:
            missing.append("Fit a calibration")
            diagnostics.append("Calibration: not ready")
        if not snapshot.geometry.is_ready:
            missing.append("Configure video geometry")
            diagnostics.append("Geometry: not ready")
        if not snapshot.readiness.experiment_name.strip():
            missing.append("Enter an experiment name")
            diagnostics.append("Experiment: name missing")
        if snapshot.faults:
            missing.append("Resolve the active fault")
            diagnostics.extend(f"Fault {fault.code}: {fault.message}" for fault in snapshot.faults)

        ready = not missing
        lifecycle = self._lifecycle.snapshot()
        if ready and lifecycle.state is RunState.IDLE:
            self._lifecycle.mark_ready()
            lifecycle = self._lifecycle.snapshot()
        elif not ready and lifecycle.state is RunState.READY:
            self._lifecycle.mark_idle()
            lifecycle = self._lifecycle.snapshot()

        if ready:
            guidance = "Ready: all presenter prerequisites are satisfied."
            next_action = "Go to Live Run and start when the operator is ready."
            diagnostics.append("Readiness is derived from the current application snapshot.")
        else:
            guidance = f"Blocked ! — {missing[0]}."
            next_action = missing[0] + "."
        readiness = replace(
            snapshot.readiness,
            is_ready=ready,
            missing=tuple(missing),
            guidance=guidance,
            next_action=next_action,
            diagnostics=tuple(diagnostics),
        )
        run = replace(
            snapshot.run,
            lifecycle=lifecycle,
            can_start=ready and lifecycle.state is RunState.READY,
            can_global_stop=lifecycle.state in self._ACTIVE_RUN_STATES,
            status_text=_run_label(lifecycle),
        )
        return replace(snapshot, readiness=readiness, run=run)


__all__ = [
    "AnalysisMode",
    "AnalysisSnapshot",
    "ApplicationCommand",
    "ApplicationCommandDispatcher",
    "ApplicationSnapshot",
    "ApplicationStateSource",
    "ApplySettings",
    "BeginRun",
    "CalibrationSnapshot",
    "ChooseAnalysisSource",
    "CollectCalibrationSamples",
    "CommandResult",
    "CompleteRun",
    "ConfigureExperiment",
    "ConfirmRunStarted",
    "ConfirmRunStopped",
    "ConnectDevices",
    "ConnectionStatus",
    "CreateWorkspace",
    "DetectMarker",
    "DeviceConnectionsSnapshot",
    "DismissNotification",
    "DisconnectDevices",
    "EvaluateReadiness",
    "FaultSnapshot",
    "FitCalibration",
    "GeometrySnapshot",
    "GlobalStop",
    "NoticeSeverity",
    "NotificationSnapshot",
    "PresenterSession",
    "PreviewFrameSnapshot",
    "ReadinessSnapshot",
    "ReportRunFault",
    "ReportRunTimeout",
    "RequestDiagnostics",
    "RequestRunStop",
    "RunAnalysis",
    "RunPresenterSnapshot",
    "SelectWorkspace",
    "SetAnalysisMode",
    "SetManualGeometry",
    "SettingsSnapshot",
    "StateStore",
    "Subscription",
    "TelemetryPointSnapshot",
    "WorkflowController",
    "WorkspaceSnapshot",
]
