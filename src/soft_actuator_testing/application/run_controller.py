"""Production, Qt-free orchestration for a cyclic actuator run.

The controller deliberately owns no physical transport.  It composes injected
serial, capture, and artifact collaborators and can therefore be constructed
in a hardware-disconnected application without opening a port or camera.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
import shlex
from threading import Event, RLock, Thread, Timer, current_thread
from time import monotonic
from typing import TYPE_CHECKING, Any, Protocol

from soft_actuator_testing.application.camera_capture import (
    CameraCaptureService,
    CaptureResult,
    CaptureTargetProfile,
    TARGET_4K60,
)
from soft_actuator_testing.domain.calibration import CalibrationFit
from soft_actuator_testing.domain.geometry import VideoGeometry
from soft_actuator_testing.domain.run_state import (
    RunCompletion,
    RunSnapshot,
    RunState,
    finalize_run,
    request_stop,
    transition,
)
from soft_actuator_testing.infrastructure.serial_adapter import (
    CommandState,
    ErrorFrame,
    RunMarkerFrame,
    SerialFrameSubscription,
    TelemetryFrame,
)

if TYPE_CHECKING:
    from soft_actuator_testing.infrastructure.artifact_store import DurableRunArtifacts


@dataclass(frozen=True)
class CyclicRunConfiguration:
    experiment_name: str
    cycles: int
    on_milliseconds: int
    off_milliseconds: int
    workspace: Path | None
    camera_device: str
    calibration: CalibrationFit | None
    geometry: VideoGeometry | None
    camera_profile: CaptureTargetProfile = TARGET_4K60
    record_video: bool = True
    estimated_storage_bytes: int = 0
    timeout_grace_seconds: float = 10.0


@dataclass(frozen=True)
class RunReadiness:
    ready: bool
    failures: tuple[str, ...]
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecordedTelemetry:
    time_s: float
    volts: float
    pressure_kpa: float | None


@dataclass(frozen=True)
class RunCoordinatorSnapshot:
    lifecycle: RunSnapshot
    readiness: RunReadiness
    telemetry: tuple[RecordedTelemetry, ...]
    recording_enabled: bool
    run_id: str | None = None
    diagnostic_text: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunFinalizationResult:
    completion: RunCompletion
    requested_completion: RunCompletion
    lifecycle: RunSnapshot
    manifest_path: Path | None
    video_path: Path | None
    errors: tuple[str, ...]
    idempotent: bool = False

    @property
    def clean(self) -> bool:
        return self.completion is RunCompletion.CLEAN and not self.errors


class RunArtifactStorage(Protocol):
    def preflight_run_storage(self, required_bytes: int = 0) -> None: ...

    def begin_run_artifacts(
        self, *, run_id: str | None = None, software_version: str | None = None
    ) -> DurableRunArtifacts: ...


class LegacySerialRunPort(Protocol):
    @property
    def snapshot(self) -> Any: ...

    @property
    def profile(self) -> Any: ...

    def set_legacy_parameters(
        self, *, cycles: int, on_milliseconds: int, off_milliseconds: int
    ) -> object: ...

    def send_command(self, command: str, **kwargs: object) -> object: ...

    def start_legacy_run(self) -> object: ...

    def stop_legacy_run(self) -> object: ...

    def subscribe_frames(
        self,
        name: str,
        *,
        critical: bool = False,
        capacity: int | None = None,
    ) -> SerialFrameSubscription: ...

    def disconnect(self) -> object: ...


class RunController:
    """Coordinates a run without Qt or implicit hardware construction.

    ``start_async`` is the UI-facing entrypoint.  ``start`` is retained for
    deterministic tests and worker callers; it performs camera readiness
    proof before emitting *any* ``CMD:START`` command.
    """

    _ACTIVE = frozenset({RunState.STARTING, RunState.RUNNING, RunState.STOPPING})

    def __init__(
        self,
        *,
        serial: LegacySerialRunPort | None = None,
        camera: CameraCaptureService | None = None,
        storage: RunArtifactStorage | None = None,
        software_version: str | None = None,
        ui_telemetry_capacity: int = 500,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if ui_telemetry_capacity <= 0:
            raise ValueError("ui_telemetry_capacity must be positive")
        self._serial = serial
        self._camera = camera
        self._storage = storage
        self._software_version = software_version
        self._capacity = ui_telemetry_capacity
        self._clock = clock
        self._lock = RLock()
        self._lifecycle = RunSnapshot(RunState.DISCONNECTED)
        self._readiness = RunReadiness(False, ("Configure a production run.",))
        self._configuration: CyclicRunConfiguration | None = None
        self._artifacts: DurableRunArtifacts | None = None
        self._telemetry: list[RecordedTelemetry] = []
        self._diagnostics: list[str] = []
        self._started_at: datetime | None = None
        self._started_monotonic = 0.0
        self._start_command_sent = False
        self._stop_command_sent = False
        self._camera_started = False
        self._camera_start_attempted = False
        self._capture_result: CaptureResult | None = None
        self._cancel = Event()
        self._telemetry_worker: Thread | None = None
        self._serial_subscription: SerialFrameSubscription | None = None
        self._start_worker: Thread | None = None
        self._final_result: RunFinalizationResult | None = None
        self._finalizing = False
        self._finalized = Event()
        self._generation = 0
        self._finalizing_generation: int | None = None
        self._terminal_reason: str | None = None
        self._requested_completion: RunCompletion | None = None
        self._watchdog: Timer | None = None
        self._finalize_wait_seconds = 5.0

    @property
    def snapshot(self) -> RunCoordinatorSnapshot:
        with self._lock:
            return RunCoordinatorSnapshot(
                lifecycle=self._lifecycle,
                readiness=self._readiness,
                telemetry=tuple(self._telemetry),
                recording_enabled=bool(self._configuration and self._configuration.record_video),
                run_id=self._artifacts.run_id if self._artifacts else None,
                diagnostic_text=tuple(self._diagnostics),
            )

    @property
    def finalization_result(self) -> RunFinalizationResult | None:
        """Expose the immutable finalized recording handoff, if a run ended."""

        with self._lock:
            return self._final_result

    def configure(self, configuration: CyclicRunConfiguration) -> RunReadiness:
        with self._lock:
            if self._lifecycle.state in self._ACTIVE:
                raise RuntimeError("cannot replace run configuration while a run is active")
            self._configuration = configuration
            self._final_result = None
            self._diagnostics = []
        return self.evaluate_readiness()

    def set_storage(self, storage: RunArtifactStorage | None) -> None:
        """Bind the currently opened workspace's artifact store while inactive."""
        with self._lock:
            if self._lifecycle.state in self._ACTIVE:
                raise RuntimeError("cannot replace artifact storage during a run")
            self._storage = storage

    def evaluate_readiness(self) -> RunReadiness:
        with self._lock:
            configuration = self._configuration
        failures: list[str] = []
        diagnostics: list[str] = []
        if configuration is None:
            failures.append("Experiment configuration is required.")
        else:
            self._validate_configuration(configuration, failures)
            self._validate_serial(failures, diagnostics)
            self._validate_storage(configuration, failures)
            self._validate_camera(configuration, failures)
        readiness = RunReadiness(not failures, tuple(failures), tuple(diagnostics))
        with self._lock:
            self._readiness = readiness
            if readiness.ready:
                self._move_to_ready()
            elif self._lifecycle.state is RunState.READY:
                self._lifecycle = transition(self._lifecycle, RunState.IDLE).snapshot
        return readiness

    def start_async(self) -> Thread:
        """Start on a worker so a Qt caller never blocks on capture startup."""

        with self._lock:
            if self._start_worker is not None and self._start_worker.is_alive():
                return self._start_worker
            worker = Thread(target=self._start_async_entry, name="run-controller-start", daemon=True)
            self._start_worker = worker
            worker.start()
            return worker

    def _start_async_entry(self) -> None:
        try:
            self.start()
        except Exception as error:
            # The state/manifest diagnostics are the application error channel;
            # never leak an unhandled worker exception into the UI runtime.
            self._diagnose(f"asynchronous run start ended: {error}")

    def start(self) -> RunCoordinatorSnapshot:
        readiness = self.evaluate_readiness()
        if not readiness.ready:
            raise RuntimeError("Run is not ready: " + " ".join(readiness.failures))
        with self._lock:
            if self._lifecycle.state is RunState.RUNNING:
                return self.snapshot
            if self._lifecycle.state is not RunState.READY:
                raise RuntimeError(f"cannot start from {self._lifecycle.state.value}")
            configuration = self._require_configuration()
            self._lifecycle = transition(self._lifecycle, RunState.STARTING).snapshot
            self._generation += 1
            generation = self._generation
            self._final_result = None
            self._cancel.clear()
            self._finalized.clear()
            self._finalizing = False
            self._finalizing_generation = None
            self._terminal_reason = None
            self._requested_completion = None
            self._start_command_sent = False
            self._stop_command_sent = False
            self._camera_started = False
            self._camera_start_attempted = False
            self._capture_result = None
            self._serial_subscription = None
            self._artifacts = None
            self._telemetry = []
            self._started_at = datetime.now(timezone.utc)
            self._started_monotonic = self._clock()
        camera_acquired = False
        try:
            assert self._storage is not None
            artifacts = self._storage.begin_run_artifacts(software_version=self._software_version)
            self._claim_artifacts(generation, artifacts)
            # Capture startup returns only after negotiated profile, a preview
            # frame, output progress, and a growing recording file are proven.
            if configuration.record_video:
                assert self._camera is not None
                with self._lock:
                    self._camera_start_attempted = True
                try:
                    self._camera.start_capture(
                        artifacts.directory,
                        configuration.camera_device,
                        duration_seconds=None,
                    )
                except Exception:
                    self._remember_camera_result()
                    raise
                camera_acquired = True
                self._ensure_active(generation)
                if not self._camera.health.ready:
                    raise RuntimeError("camera did not report ready recording proof")
                with self._lock:
                    self._ensure_active(generation)
                    self._camera_started = True
            assert self._serial is not None
            with self._lock:
                self._ensure_active(generation)
                self._serial_subscription = self._serial.subscribe_frames(
                    f"run-persistence-{generation}",
                    critical=True,
                )
            # Legacy ordering is intentionally exact.  No acknowledgement wait
            # is requested because the legacy profile has no confirmed ACK model.
            for command in (
                f"CMD:SET CYCLES {configuration.cycles}",
                f"CMD:SET ON {configuration.on_milliseconds}",
                f"CMD:SET OFF {configuration.off_milliseconds}",
                "CMD:START",
            ):
                # Sending and recording command ownership are atomic with
                # finalization. Serial writes do not wait for an ACK under the
                # unconfirmed legacy profile, so this lock remains bounded.
                with self._lock:
                    self._ensure_active(generation)
                    receipt = self._serial.send_command(command)
                    self._require_sent(receipt, command)
                    if command == "CMD:START":
                        self._start_command_sent = True
            with self._lock:
                self._ensure_active(generation)
                self._lifecycle = transition(self._lifecycle, RunState.RUNNING).snapshot
            self._start_telemetry_worker(generation)
            self._start_watchdog(generation, configuration)
            return self.snapshot
        except Exception as error:
            self._diagnose(f"run startup failed: {error}")
            if (
                (camera_acquired or self._camera_start_attempted)
                and not self._camera_started
                and self._camera is not None
                and self._capture_result is None
            ):
                try:
                    self._remember_capture_result(self._camera.stop_capture("cancelled during startup"))
                except Exception as cleanup_error:
                    self._diagnose(f"camera cleanup after cancelled startup failed: {cleanup_error}")
            self._remember_camera_result()
            # A cancellation/finalizer which won the race already owns cleanup.
            if self._is_active_generation(generation):
                self.finalize(RunCompletion.FAULTED, reason="startup failure")
            raise

    def ingest_frames(self, frames: Sequence[object]) -> None:
        """Persist all decoded voltage telemetry; only the UI projection decimates."""

        for frame in frames:
            if isinstance(frame, ErrorFrame):
                self.controller_fault(f"serial {frame.source} fault: {frame.message}")
                return
            if isinstance(frame, RunMarkerFrame) and not frame.started:
                self.finalize(
                    RunCompletion.CLEAN,
                    reason="controller end-run marker",
                    send_stop=False,
                )
                return
            if not isinstance(frame, TelemetryFrame) or "volts" not in frame.values:
                continue
            timestamp = frame.values.get("timestamp_seconds")
            time_s = float(timestamp) if timestamp is not None else self._clock() - self._started_monotonic
            self.record_telemetry(time_s=time_s, volts=float(frame.values["volts"]))

    def record_telemetry(self, *, time_s: float, volts: float) -> RecordedTelemetry:
        if not isfinite(time_s) or not isfinite(volts):
            raise ValueError("telemetry time and volts must be finite")
        with self._lock:
            if self._artifacts is None or self._lifecycle.state not in self._ACTIVE:
                raise RuntimeError("cannot record telemetry outside an active run")
            configuration = self._require_configuration()
            pressure: float | None = None
            if configuration.calibration is None:
                self._diagnose("telemetry persisted raw-only: calibration is unavailable")
            else:
                try:
                    pressure = configuration.calibration.model.apply(volts, require_in_domain=False)
                except Exception as error:
                    self._diagnose(f"telemetry persisted raw-only: calibration failed: {error}")
            sample = RecordedTelemetry(time_s, volts, pressure)
            # This call flushes and fsyncs before any optional UI decimation.
            self._artifacts.append_pressure(time_s=time_s, volts=volts, pressure_kpa=pressure)
            self._telemetry.append(sample)
            if len(self._telemetry) > self._capacity:
                del self._telemetry[: len(self._telemetry) - self._capacity]
            return sample

    def mark_calibration_unavailable(self, reason: str = "calibration became unavailable") -> None:
        """Keep the active run observable by persisting later rows raw-only."""

        with self._lock:
            if self._configuration is None:
                return
            self._configuration = replace(self._configuration, calibration=None)
            self._diagnose(f"telemetry will be persisted raw-only: {reason}")

    def complete(self) -> RunFinalizationResult:
        return self.finalize(RunCompletion.CLEAN, reason="controller completion")

    def stop(self) -> RunFinalizationResult:
        return self.finalize(RunCompletion.STOPPED, reason="operator stop")

    def global_stop(self) -> RunFinalizationResult:
        return self.finalize(RunCompletion.ABORTED, reason="global stop")

    def controller_timeout(self, reason: str = "controller timeout") -> RunFinalizationResult:
        return self.finalize(RunCompletion.FAULTED, reason=reason)

    def controller_fault(self, reason: str) -> RunFinalizationResult:
        return self.finalize(RunCompletion.FAULTED, reason=reason)

    def camera_fault(self, reason: str) -> RunFinalizationResult:
        return self.finalize(RunCompletion.FAULTED, reason=reason)

    def close(self) -> RunFinalizationResult | None:
        """Window-close path: use the same finalizer, then release serial."""

        with self._lock:
            active = self._lifecycle.state in self._ACTIVE
        result = self.finalize(RunCompletion.ABORTED, reason="window close") if active else self._final_result
        if self._serial is not None:
            try:
                self._serial.disconnect()
            except Exception as error:
                self._diagnose(f"serial disconnect during close failed: {error}")
        return result

    def finalize(
        self,
        completion: RunCompletion,
        *,
        reason: str,
        send_stop: bool = True,
    ) -> RunFinalizationResult:
        """Single idempotent cleanup path; every cleanup step is attempted."""

        with self._lock:
            if self._final_result is not None:
                return RunFinalizationResult(
                    **{**self._final_result.__dict__, "idempotent": True}
                )
            if self._lifecycle.state not in self._ACTIVE and self._artifacts is None:
                return RunFinalizationResult(
                    completion,
                    completion,
                    self._lifecycle,
                    None,
                    None,
                    (),
                    idempotent=True,
                )
            if self._finalizing:
                waiter = self._finalized
            else:
                self._finalizing = True
                self._finalizing_generation = self._generation
                self._terminal_reason = reason
                self._requested_completion = completion
                waiter = None
                if self._lifecycle.state in {RunState.STARTING, RunState.RUNNING}:
                    self._lifecycle = request_stop(self._lifecycle).snapshot
        if waiter is not None:
            if not waiter.wait(self._finalize_wait_seconds):
                return RunFinalizationResult(
                    RunCompletion.FAULTED,
                    completion,
                    self._lifecycle,
                    None,
                    None,
                    ("finalizer did not complete before wait timeout",),
                    idempotent=True,
                )
            assert self._final_result is not None
            return RunFinalizationResult(**{**self._final_result.__dict__, "idempotent": True})

        errors: list[str] = []
        capture = self._remember_camera_result()
        video_path: Path | None = capture.video_path if capture is not None else None
        manifest_path: Path | None = None
        try:
            self._cancel.set()
            self._cancel_watchdog()
            worker = self._telemetry_worker
            if worker is not None and worker is not current_thread() and worker.ident is not None:
                worker.join(timeout=2.0)
                if worker.is_alive():
                    errors.append("telemetry worker did not stop before timeout")
            if send_stop and self._start_command_sent and not self._stop_command_sent and self._serial is not None:
                try:
                    receipt = self._serial.stop_legacy_run()
                    self._require_sent(receipt, "CMD:STOP")
                    self._stop_command_sent = True
                except Exception as error:
                    errors.append(f"CMD:STOP failed: {error}")
            if (
                (self._camera_started or self._camera_start_attempted)
                and self._camera is not None
                and capture is None
            ):
                try:
                    capture = self._remember_capture_result(self._camera.stop_capture(reason))
                    video_path = capture.video_path
                except Exception as error:
                    errors.append(f"camera cleanup failed: {error}")
            capture = self._remember_camera_result() or capture
            if capture is not None:
                video_path = capture.video_path
                if not capture.clean:
                    errors.append(capture.error or "camera finalization was not clean")
            final_completion = RunCompletion.FAULTED if errors and completion is not RunCompletion.ABORTED else completion
            payload = self._manifest_payload(final_completion, completion, reason, capture, errors)
            if self._artifacts is not None:
                try:
                    manifest_path = self._artifacts.finalize(payload)
                except Exception as error:
                    errors.append(f"manifest finalization failed: {error}")
                    final_completion = RunCompletion.FAULTED
            with self._lock:
                if self._lifecycle.state is RunState.STOPPING:
                    self._lifecycle = finalize_run(self._lifecycle, final_completion).snapshot
                elif self._lifecycle.state is RunState.COMPLETED:
                    final_completion = self._lifecycle.completion or final_completion
                lifecycle = self._lifecycle
                result = RunFinalizationResult(
                    final_completion,
                    completion,
                    lifecycle,
                    manifest_path,
                    video_path,
                    tuple(errors),
                )
                self._final_result = result
                return result
        finally:
            subscription: SerialFrameSubscription | None = None
            with self._lock:
                subscription = self._serial_subscription
                self._serial_subscription = None
                self._finalizing = False
                self._finalizing_generation = None
                self._finalized.set()
            if subscription is not None:
                subscription.close()

    def _poll_serial(self, generation: int) -> None:
        while not self._cancel.wait(0.02):
            try:
                if not self._is_active_generation(generation):
                    return
                with self._lock:
                    subscription = self._serial_subscription
                if subscription is None:
                    raise RuntimeError("run serial persistence subscription is unavailable")
                self.ingest_frames(subscription.drain())
                self._observe_camera(generation)
            except Exception as error:
                self._diagnose(f"serial telemetry worker fault: {error}")
                self.finalize(RunCompletion.FAULTED, reason="serial telemetry fault")
                return

    def _start_telemetry_worker(self, generation: int) -> None:
        worker = Thread(target=self._poll_serial, args=(generation,), name="run-telemetry", daemon=True)
        self._telemetry_worker = worker
        worker.start()

    def _claim_artifacts(self, generation: int, artifacts: DurableRunArtifacts) -> None:
        with self._lock:
            if self._is_active_generation(generation):
                self._artifacts = artifacts
                return
            final_result = self._final_result
            completion = final_result.completion if final_result is not None else RunCompletion.ABORTED
            requested_completion = self._requested_completion or completion
            reason = self._terminal_reason or "start cancelled while reserving artifacts"
            self._artifacts = artifacts
            capture = self._capture_result
        # Finalization may win while storage reservation blocks.  It could not
        # have written a manifest before a run directory existed, so publish the
        # same complete terminal shape here exactly once rather than leaking a
        # minimally described reservation.
        payload = self._manifest_payload(
            completion,
            requested_completion,
            reason,
            self._remember_camera_result() or capture,
            final_result.errors if final_result is not None else (),
        )
        manifest_path = artifacts.finalize(payload)
        with self._lock:
            if self._generation == generation and self._final_result is not None:
                self._final_result = replace(self._final_result, manifest_path=manifest_path)
        raise RuntimeError("run start was cancelled or superseded")

    def _ensure_active(self, generation: int) -> None:
        if not self._is_active_generation(generation):
            raise RuntimeError("run start was cancelled or superseded")

    def _is_active_generation(self, generation: int) -> bool:
        return (
            self._generation == generation
            and not self._cancel.is_set()
            and self._final_result is None
            and self._lifecycle.state in {RunState.STARTING, RunState.RUNNING}
        )

    @staticmethod
    def _require_sent(receipts: object, operation: str) -> None:
        values = receipts if isinstance(receipts, tuple) else (receipts,)
        if not values:
            raise RuntimeError(f"{operation} returned no command receipt")
        for receipt in values:
            state = getattr(receipt, "state", None)
            if receipt is None or state not in {CommandState.SENT, CommandState.ACKNOWLEDGED}:
                detail = getattr(receipt, "detail", "")
                raise RuntimeError(f"{operation} was not sent successfully: {state or 'no receipt'} {detail}".strip())

    def _start_watchdog(self, generation: int, config: CyclicRunConfiguration) -> None:
        duration = config.cycles * (config.on_milliseconds + config.off_milliseconds) / 1000
        timer = Timer(duration + config.timeout_grace_seconds, self._watchdog_expired, args=(generation,))
        timer.daemon = True
        with self._lock:
            self._cancel_watchdog()
            self._watchdog = timer
        timer.start()

    def _watchdog_expired(self, generation: int) -> None:
        if self._is_active_generation(generation):
            self.controller_timeout("run exceeded expected cycle duration plus grace")

    def _cancel_watchdog(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None

    def _observe_camera(self, generation: int) -> None:
        if not self._camera_started or self._camera is None or not self._is_active_generation(generation):
            return
        result = getattr(self._camera, "result", None)
        if result is not None:
            self.camera_fault(result.error or f"camera ended unexpectedly: {result.completion_reason}")
            return
        health = self._camera.health
        if getattr(health.phase, "value", health.phase) == "fault":
            self.camera_fault("camera reported a capture fault")

    def _move_to_ready(self) -> None:
        if self._lifecycle.state is RunState.DISCONNECTED:
            self._lifecycle = transition(self._lifecycle, RunState.CONNECTING).snapshot
            self._lifecycle = transition(self._lifecycle, RunState.IDLE).snapshot
        elif self._lifecycle.state in {RunState.COMPLETED, RunState.FAULT}:
            self._lifecycle = transition(self._lifecycle, RunState.IDLE).snapshot
        if self._lifecycle.state is RunState.IDLE:
            self._lifecycle = transition(self._lifecycle, RunState.READY).snapshot

    def _validate_configuration(self, config: CyclicRunConfiguration, failures: list[str]) -> None:
        if not config.experiment_name.strip():
            failures.append("Experiment name is required.")
        for name, value in (
            ("cycles", config.cycles),
            ("on timing", config.on_milliseconds),
            ("off timing", config.off_milliseconds),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                failures.append(f"{name.capitalize()} must be a positive integer.")
        if config.calibration is None:
            failures.append("A validated calibration is required; raw-only telemetry cannot start a cyclic run.")
        elif not config.calibration.adequacy.is_adequate:
            failures.append("Calibration fit is not adequate.")
        if config.geometry is None:
            failures.append("Complete video geometry is required.")
        if config.record_video and not config.camera_device.strip():
            failures.append("A camera selection is required.")
        if config.record_video and (
            config.camera_profile.width != TARGET_4K60.width
            or config.camera_profile.height != TARGET_4K60.height
            or config.camera_profile.fps != TARGET_4K60.fps
        ):
            failures.append(f"Camera profile must be {TARGET_4K60.label}.")
        if config.estimated_storage_bytes < 0:
            failures.append("Estimated storage capacity cannot be negative.")
        if not isfinite(config.timeout_grace_seconds) or config.timeout_grace_seconds < 0:
            failures.append("Timeout grace must be a finite non-negative duration.")

    def _validate_serial(self, failures: list[str], diagnostics: list[str]) -> None:
        if self._serial is None:
            failures.append("A configured serial controller is required.")
            return
        snapshot = getattr(self._serial, "snapshot", None)
        status = getattr(snapshot, "status", None)
        if getattr(status, "value", status) != "connected":
            failures.append("Serial controller is not connected.")
        profile = getattr(self._serial, "profile", None)
        profile_name = getattr(profile, "name", "")
        if not profile_name or profile_name == "unconfigured":
            failures.append("A serial telemetry profile is required.")
        elif "unconfirmed" in profile_name:
            diagnostics.append(
                f"Serial profile {profile_name!r} is unconfirmed; commands are sent without ACK claims."
            )

    def _validate_storage(self, config: CyclicRunConfiguration, failures: list[str]) -> None:
        if self._storage is None:
            failures.append("Writable artifact storage is required.")
            return
        if not config.workspace:
            failures.append("A workspace is required.")
            return
        storage_root = getattr(self._storage, "root", None)
        if storage_root is not None and Path(storage_root).resolve() != config.workspace.expanduser().resolve():
            failures.append("Configured workspace does not match artifact storage.")
            return
        try:
            self._storage.preflight_run_storage(config.estimated_storage_bytes)
        except Exception as error:
            failures.append(f"Workspace/storage is not ready: {error}")

    def _validate_camera(self, config: CyclicRunConfiguration, failures: list[str]) -> None:
        if config.record_video and self._camera is None:
            failures.append("Camera capture service is required when recording is enabled.")

    def _manifest_payload(
        self,
        completion: RunCompletion,
        requested_completion: RunCompletion,
        reason: str,
        capture_result: CaptureResult | None,
        errors: Sequence[str],
    ) -> Mapping[str, Any]:
        config = self._configuration
        assert config is not None
        run_id = self._artifacts.run_id if self._artifacts else ""
        output_files = [f"runs/{run_id}/pressure.csv"]
        capture = self._capture_manifest_evidence(config, capture_result, reason)
        final_video = capture["paths"]["final_path"]
        if final_video is not None:
            output_files.append(final_video)
        return {
            "completion": completion.value,
            "requested_completion": requested_completion.value,
            "reason": reason,
            "experiment": {
                "name": config.experiment_name,
                "cycles": config.cycles,
                "on_milliseconds": config.on_milliseconds,
                "off_milliseconds": config.off_milliseconds,
            },
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "recording_enabled": config.record_video,
            "camera": {
                "device": config.camera_device,
                "requested_profile": config.camera_profile.label,
                "finalized_video": final_video,
                "health": capture["health"],
            },
            "capture": capture,
            "pressure_csv": {
                "path": output_files[0],
                "columns": ["schema_version", "artifact_id", "time_s", "volts", "pressure_kPa"],
                "units": {"time_s": "s", "volts": "V", "pressure_kPa": "kPa"},
                "provenance": "decoded serial telemetry; pressure is blank only when calibration is unavailable or fails",
            },
            "serial_profile": getattr(getattr(self._serial, "profile", None), "name", "unknown"),
            "calibration_model_snapshot": self._calibration_snapshot(config.calibration),
            "geometry_model_snapshot": self._geometry_snapshot(config.geometry),
            "platform_provenance": {
                "python_platform": __import__("platform").platform(),
                "software_version": self._software_version,
                "serial_diagnostics": tuple(
                    str(item) for item in getattr(getattr(self._serial, "snapshot", None), "diagnostics", ())
                ),
            },
            "output_files": output_files,
            "warnings": tuple(self._diagnostics),
            "cleanup_errors": tuple(errors),
        }

    def _require_configuration(self) -> CyclicRunConfiguration:
        if self._configuration is None:
            raise RuntimeError("run has not been configured")
        return self._configuration

    def _remember_camera_result(self) -> CaptureResult | None:
        """Cache a terminal camera result exposed by the capture service."""

        if self._camera is None:
            return None
        try:
            result = getattr(self._camera, "result", None)
        except Exception as error:
            self._diagnose(f"could not read camera finalization evidence: {error}")
            return None
        return self._remember_capture_result(result)

    def _remember_capture_result(self, result: CaptureResult | None) -> CaptureResult | None:
        if result is None:
            with self._lock:
                return self._capture_result
        if not isinstance(result, CaptureResult):
            self._diagnose("camera returned an unsupported finalization result; capture evidence is unavailable")
            with self._lock:
                return self._capture_result
        with self._lock:
            if self._capture_result is None:
                self._capture_result = result
            return self._capture_result

    def _capture_manifest_evidence(
        self,
        config: CyclicRunConfiguration,
        result: CaptureResult | None,
        controller_reason: str,
    ) -> Mapping[str, Any]:
        """Serialize only capture facts exposed by the typed application contract."""

        health = result.health if result is not None else self._capture_health()
        evidence = result.evidence if result is not None else None
        profile = health.negotiated_profile if health is not None else None
        preview = health.preview if health is not None else None
        final_path = self._capture_workspace_path(result.video_path) if result is not None else None
        partial_path = (
            self._capture_workspace_path(result.partial_path, require_file=True)
            if result is not None
            else None
        )
        ffmpeg_command = self._capture_command()
        ffmpeg_version = self._capture_optional_text("ffmpeg_version")
        ffmpeg_build = self._capture_optional_text("ffmpeg_build")
        status = self._capture_status(config, result, health, partial_path)
        promoted = evidence.promoted if evidence is not None else None

        return {
            "status": status,
            "requested_target": {
                "width": config.camera_profile.width,
                "height": config.camera_profile.height,
                "fps": config.camera_profile.fps,
                "label": config.camera_profile.label,
            },
            "selected_device": {
                "identifier": config.camera_device or None,
                "backend": self._capture_optional_text("backend"),
                "mode": self._capture_mode(),
            },
            "encoder": None if health is None or not health.encoder else health.encoder,
            "ffmpeg": {
                "command": ffmpeg_command,
                "version": ffmpeg_version,
                "build": ffmpeg_build,
            },
            "startup": {
                "proven": evidence.startup_proven if evidence is not None else None,
                "proof_at": None,
                "components": {
                    "negotiated_profile": profile is not None,
                    "progress": None if health is None else health.frame > 0,
                    "output_file": None if health is None else health.output_bytes > 0,
                    "preview": None if preview is None else preview.produced > 0,
                },
            },
            "negotiated": self._negotiated_profile(profile),
            "progress": {
                "frames": None if health is None else health.frame,
                "fps": None if health is None else health.fps,
                "speed": None if health is None else health.speed,
                "output_time_us": None if health is None else health.output_time_us,
                "file_size_bytes": None if health is None else health.output_bytes,
                "duplicate_frames": None if health is None else health.duplicate_frames,
                "dropped_frames": None if health is None else health.dropped_frames,
                "malformed_progress_lines": None if health is None else health.malformed_progress_lines,
            },
            "preview": {
                "received_frames": None if preview is None else preview.produced,
                "consumed_frames": None if preview is None else preview.consumed,
                "dropped_frames": None if preview is None else preview.replaced_stale,
                "latest_timestamp": None,
                "rate_fps": None,
                "profile": None,
                "maximum_age_seconds": None if preview is None else preview.maximum_age_seconds,
            },
            "health": self._capture_provenance(health),
            "warnings": None if health is None else list(health.warnings),
            "termination": {
                "controller_reason": controller_reason,
                "stop_reason": None if result is None else result.completion_reason,
                "failure": None if result is None or not result.error else result.error,
                "clean": None if result is None else result.clean,
                "cooperative_shutdown": None if evidence is None else evidence.cooperative_shutdown,
                "process_exit_code": None if evidence is None else evidence.process_exit_code,
                "drainers_stopped": None if evidence is None else evidence.drainers_stopped,
                "shutdown_escalated": None if evidence is None else evidence.shutdown_escalated,
            },
            "verification": {
                "readable": None if result is None else result.readable,
                "evidence_readable": None if evidence is None else evidence.verification_readable,
                "duration_seconds": None,
                "frame_count": None,
                "streams": None,
            },
            "paths": {
                "partial_path": partial_path,
                "final_path": final_path,
            },
            "promotion": {
                "promoted": promoted,
                "outcome": self._promotion_outcome(result, partial_path, promoted),
            },
        }

    def _capture_health(self) -> Any | None:
        if self._camera is None:
            return None
        try:
            return self._camera.health
        except Exception as error:
            self._diagnose(f"could not read camera health evidence: {error}")
            return None

    def _capture_workspace_path(self, path: Path | None, *, require_file: bool = False) -> str | None:
        if path is None or self._artifacts is None:
            return None
        candidate = Path(path)
        root = self._artifacts.store.root
        if candidate.is_absolute():
            resolved = candidate.resolve()
        elif candidate.parts and candidate.parts[0] == "runs":
            resolved = (root / candidate).resolve()
        else:
            resolved = (self._artifacts.directory / candidate).resolve()
        try:
            relative_path = resolved.relative_to(root).as_posix()
        except ValueError:
            self._diagnose(f"capture path is outside workspace and cannot be persisted: {path}")
            return None
        return relative_path if not require_file or resolved.is_file() else None

    def _capture_command(self) -> list[str] | None:
        if self._camera is None:
            return None
        value = getattr(self._camera, "ffmpeg_command", None)
        if value is None:
            value = getattr(self._camera, "command", None)
        if isinstance(value, str):
            try:
                command = shlex.split(value)
            except ValueError:
                return None
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            command = [str(part) for part in value]
        else:
            return None
        sanitized: list[str] = []
        redact_next = False
        for index, part in enumerate(command):
            if redact_next:
                sanitized.append("<redacted>")
                redact_next = False
                continue
            sanitized.append(self._sanitize_command_part(part, index))
            redact_next = self._is_sensitive_command_option(part)
        return sanitized

    @staticmethod
    def _is_sensitive_command_option(part: str) -> bool:
        option = part.strip().lstrip("-").casefold().replace("-", "_")
        return option in {
            "password",
            "token",
            "secret",
            "credential",
            "api_key",
            "apikey",
            "key",
            "headers",
            "http_headers",
        }

    def _sanitize_command_part(self, part: str, index: int) -> str:
        value = part.strip()
        lowered = value.casefold()
        if not value:
            return value
        if any(marker in lowered for marker in ("password=", "token=", "secret=", "credential=", "api_key=")):
            key, _, _ = value.partition("=")
            return f"{key}=<redacted>"
        if "$" in value or value.startswith("%"):
            return "<environment-value>"
        if "://" in value or value.casefold().startswith("file:"):
            return "<external-url>"
        if "=" in value:
            key, _, assigned_value = value.partition("=")
            if Path(assigned_value).expanduser().is_absolute():
                return f"{key}=<external-path>"
        path = Path(value).expanduser()
        if path.is_absolute():
            if self._artifacts is not None:
                try:
                    return path.resolve().relative_to(self._artifacts.store.root).as_posix()
                except ValueError:
                    pass
            return path.name if index == 0 else "<external-path>"
        return value

    def _capture_optional_text(self, attribute: str) -> str | None:
        if self._camera is None:
            return None
        value = getattr(self._camera, attribute, None)
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _capture_mode(self) -> Mapping[str, Any] | None:
        if self._camera is None:
            return None
        mode = getattr(self._camera, "input_mode", None)
        if mode is None:
            return None
        values = {
            name: getattr(mode, name, None)
            for name in ("width", "height", "fps", "pixel_format")
        }
        if any(value is None for value in values.values()):
            return None
        return values

    @staticmethod
    def _capture_status(
        config: CyclicRunConfiguration,
        result: CaptureResult | None,
        health: Any | None,
        partial_path: str | None,
    ) -> str:
        if not config.record_video:
            return "disabled"
        if result is not None:
            if result.clean and result.video_path is not None:
                return "completed"
            if partial_path is not None:
                return "retained_partial"
            return "failed"
        if health is None:
            return "unavailable"
        phase = getattr(health.phase, "value", health.phase)
        return "startup_failed" if phase == "fault" else "unavailable"

    @staticmethod
    def _promotion_outcome(
        result: CaptureResult | None,
        partial_path: str | None,
        promoted: bool | None,
    ) -> str:
        if result is None:
            return "not_started"
        if promoted:
            return "promoted"
        if partial_path is not None:
            return "retained_partial"
        return "not_promoted"

    @staticmethod
    def _negotiated_profile(profile: Any | None) -> Mapping[str, Any] | None:
        if profile is None:
            return None
        return {
            "width": profile.width,
            "height": profile.height,
            "fps": profile.fps,
            "pixel_format": profile.pixel_format,
            "codec": profile.codec,
        }

    @staticmethod
    def _capture_provenance(health: Any | None) -> Mapping[str, Any]:
        if health is None:
            return {"enabled": False}
        profile = health.negotiated_profile
        return {
            "enabled": True,
            "phase": getattr(health.phase, "value", str(health.phase)),
            "ready_proof": health.ready,
            "encoder": health.encoder,
            "frame": health.frame,
            "dropped_frames": health.dropped_frames,
            "duplicate_frames": health.duplicate_frames,
            "output_bytes": health.output_bytes,
            "warnings": health.warnings,
            "negotiated_profile": None
            if profile is None
            else {
                "width": profile.width,
                "height": profile.height,
                "fps": profile.fps,
                "pixel_format": profile.pixel_format,
                "codec": profile.codec,
            },
        }

    @staticmethod
    def _calibration_snapshot(calibration: CalibrationFit | None) -> Mapping[str, Any] | None:
        if calibration is None:
            return None
        return {
            "model_type": calibration.model.model_type.value,
            "coefficients": calibration.model.coefficients,
            "input_unit": calibration.model.input_unit.value,
            "output_unit": calibration.model.output_unit.value,
            "adequacy": {
                "sample_count": calibration.adequacy.sample_count,
                "r_squared": calibration.adequacy.r_squared,
                "rmse_kpa": calibration.adequacy.rmse_kpa,
            },
        }

    @staticmethod
    def _geometry_snapshot(geometry: VideoGeometry | None) -> Mapping[str, Any] | None:
        if geometry is None:
            return None
        return {
            "frame_size": {"width": geometry.frame_size.width, "height": geometry.frame_size.height},
            "base_point": {"x": geometry.base_point.x, "y": geometry.base_point.y},
            "initial_tip_point": (
                None
                if geometry.initial_tip_point is None
                else {"x": geometry.initial_tip_point.x, "y": geometry.initial_tip_point.y}
            ),
            "roi": {
                "left": geometry.actuator_roi.left,
                "top": geometry.actuator_roi.top,
                "right": geometry.actuator_roi.right,
                "bottom": geometry.actuator_roi.bottom,
            },
        }

    def _diagnose(self, message: str) -> None:
        with self._lock:
            if message not in self._diagnostics:
                self._diagnostics.append(message)


__all__ = [
    "CyclicRunConfiguration",
    "RecordedTelemetry",
    "RunController",
    "RunCoordinatorSnapshot",
    "RunFinalizationResult",
    "RunReadiness",
]
