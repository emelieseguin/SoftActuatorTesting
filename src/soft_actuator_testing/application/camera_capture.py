"""Qt-free camera capture state, coordination, and presenter contracts."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from threading import Lock, RLock, Thread, Timer
from time import monotonic
from typing import Generic, Protocol, TypeVar, runtime_checkable

from .presentation import StateStore


class CapturePhase(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    READY = "ready"
    RECORDING = "recording"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAULT = "fault"


@dataclass(frozen=True)
class CaptureTargetProfile:
    width: int
    height: int
    fps: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0 or self.fps <= 0:
            raise ValueError("capture dimensions and frame rate must be positive")

    @property
    def label(self) -> str:
        return f"{self.width}x{self.height}@{self.fps}"


TARGET_4K60 = CaptureTargetProfile(width=3840, height=2160, fps=60)


@dataclass(frozen=True)
class CameraMode:
    width: int
    height: int
    fps: float
    pixel_format: str

    def matches(self, target: CaptureTargetProfile = TARGET_4K60) -> bool:
        return (
            self.width == target.width
            and self.height == target.height
            and abs(self.fps - target.fps) < 0.01
        )


@dataclass(frozen=True)
class CameraDevice:
    identifier: str
    name: str
    backend: str
    modes: tuple[CameraMode, ...] = ()


@dataclass(frozen=True)
class NegotiatedCaptureProfile:
    width: int
    height: int
    fps: float
    pixel_format: str
    codec: str

    def verify(
        self,
        target: CaptureTargetProfile = TARGET_4K60,
        *,
        expected_pixel_format: str | None = None,
    ) -> None:
        if (
            self.width != target.width
            or self.height != target.height
            or abs(self.fps - target.fps) >= 0.01
        ):
            raise ValueError(
                f"negotiated {self.width}x{self.height}@{self.fps:g}, "
                f"required {target.label}"
            )
        if (
            expected_pixel_format
            and self.pixel_format.casefold() != expected_pixel_format.casefold()
            and self.codec.casefold() != expected_pixel_format.casefold()
        ):
            raise ValueError(
                f"negotiated codec/pixel format {self.codec!r}/{self.pixel_format!r}, "
                f"required input format {expected_pixel_format!r}"
            )


@dataclass(frozen=True)
class PreviewFrame:
    index: int
    width: int
    height: int
    rgb_bytes: bytes
    captured_monotonic: float

    def __post_init__(self) -> None:
        expected = self.width * self.height * 3
        if len(self.rgb_bytes) != expected:
            raise ValueError(f"RGB frame has {len(self.rgb_bytes)} bytes; expected {expected}")


@dataclass(frozen=True)
class LatestFrameStats:
    produced: int = 0
    consumed: int = 0
    replaced_stale: int = 0
    maximum_age_seconds: float = 0.0


FrameT = TypeVar("FrameT")


class LatestFrameChannel(Generic[FrameT]):
    """A one-slot channel: producers replace stale data instead of blocking."""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._lock = Lock()
        self._value: FrameT | None = None
        self._published_at = 0.0
        self._stats = LatestFrameStats()

    def publish(self, value: FrameT) -> None:
        with self._lock:
            replaced = self._value is not None
            self._value = value
            self._published_at = self._clock()
            self._stats = replace(
                self._stats,
                produced=self._stats.produced + 1,
                replaced_stale=self._stats.replaced_stale + int(replaced),
            )

    def consume_latest(self) -> FrameT | None:
        with self._lock:
            if self._value is None:
                return None
            value = self._value
            self._value = None
            age = max(0.0, self._clock() - self._published_at)
            self._stats = replace(
                self._stats,
                consumed=self._stats.consumed + 1,
                maximum_age_seconds=max(self._stats.maximum_age_seconds, age),
            )
            return value

    @property
    def stats(self) -> LatestFrameStats:
        with self._lock:
            return self._stats


@dataclass(frozen=True)
class CaptureHealth:
    phase: CapturePhase = CapturePhase.IDLE
    frame: int = 0
    fps: float = 0.0
    speed: float = 0.0
    output_time_us: int = 0
    output_bytes: int = 0
    duplicate_frames: int = 0
    dropped_frames: int = 0
    malformed_progress_lines: int = 0
    negotiated_profile: NegotiatedCaptureProfile | None = None
    encoder: str = ""
    preview: LatestFrameStats = LatestFrameStats()
    warnings: tuple[str, ...] = ()
    ready: bool = False
    clean: bool = True


@dataclass(frozen=True)
class CaptureResult:
    completion_reason: str
    video_path: Path | None
    partial_path: Path
    readable: bool
    clean: bool
    health: CaptureHealth
    error: str = ""


class CaptureError(RuntimeError):
    pass


@runtime_checkable
class CameraDeviceSource(Protocol):
    def devices(self) -> Sequence[CameraDevice]: ...


@runtime_checkable
class CameraCaptureBackend(Protocol):
    @property
    def frame_channel(self) -> LatestFrameChannel[PreviewFrame]: ...

    @property
    def health(self) -> CaptureHealth: ...

    @property
    def result(self) -> CaptureResult | None: ...

    def start(
        self,
        output_directory: Path,
        device_identifier: str,
        *,
        readiness_timeout: float,
    ) -> None: ...

    def stop(self, reason: str = "operator", *, timeout: float | None = None) -> CaptureResult: ...

    def close(self, *, timeout: float | None = None) -> CaptureResult | None: ...


class CameraCaptureService:
    """Application-owned timed-capture facade over one infrastructure owner."""

    def __init__(
        self,
        backend: CameraCaptureBackend,
        *,
        storage_preflight: Callable[[Path, float | None], object] | None = None,
    ) -> None:
        self._backend = backend
        self._storage_preflight = storage_preflight
        self._lock = RLock()
        self._timer: Timer | None = None
        self._generation = 0
        self._start_in_progress = False

    @property
    def frame_channel(self) -> LatestFrameChannel[PreviewFrame]:
        return self._backend.frame_channel

    @property
    def health(self) -> CaptureHealth:
        return self._backend.health

    @property
    def result(self) -> CaptureResult | None:
        return self._backend.result

    def start_capture(
        self,
        output_directory: Path,
        device_identifier: str,
        *,
        duration_seconds: float | None = None,
        readiness_timeout: float = 10.0,
    ) -> None:
        if duration_seconds is not None and duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        with self._lock:
            if self._start_in_progress or self._backend.health.phase in {
                CapturePhase.STARTING,
                CapturePhase.READY,
                CapturePhase.RECORDING,
                CapturePhase.STOPPING,
            }:
                raise CaptureError("camera capture is already active")
            self._cancel_timer()
            self._generation += 1
            generation = self._generation
            self._start_in_progress = True
        try:
            if self._storage_preflight is not None:
                self._storage_preflight(Path(output_directory), duration_seconds)
            self._backend.start(
                output_directory,
                device_identifier,
                readiness_timeout=readiness_timeout,
            )
        finally:
            with self._lock:
                self._start_in_progress = False
        if duration_seconds is not None:
            timer: Timer
            timer = Timer(
                duration_seconds,
                lambda: self._timed_stop(timer, generation),
            )
            timer.daemon = True
            with self._lock:
                if generation != self._generation:
                    return
                self._timer = timer
            timer.start()

    def stop_capture(
        self,
        reason: str = "operator",
        *,
        timeout: float | None = None,
    ) -> CaptureResult:
        with self._lock:
            self._generation += 1
            self._cancel_timer()
        return self._backend.stop(reason, timeout=timeout)

    def close(self, *, timeout: float | None = None) -> CaptureResult | None:
        with self._lock:
            self._generation += 1
            self._cancel_timer()
        return self._backend.close(timeout=timeout)

    def _timed_stop(self, timer: Timer, generation: int) -> None:
        with self._lock:
            if self._timer is not timer or self._generation != generation:
                return
            self._timer = None
        self._backend.stop("duration")

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


@dataclass(frozen=True)
class CameraPanelSnapshot:
    devices: tuple[CameraDevice, ...] = ()
    selected_device: str = ""
    target_profile: CaptureTargetProfile = TARGET_4K60
    health: CaptureHealth = CaptureHealth()
    preview: PreviewFrame | None = None
    status_text: str = "No camera selected."
    can_start: bool = False
    can_stop: bool = False
    error: str = ""


class CameraPanelPresenter:
    """Presenter for a reusable panel; all blocking capture calls use workers."""

    def __init__(
        self,
        devices: CameraDeviceSource,
        capture: CameraCaptureService,
    ) -> None:
        self._devices = devices
        self._capture = capture
        self._operation_lock = Lock()
        self._state_lock = RLock()
        self.state = StateStore(CameraPanelSnapshot())

    def refresh_devices(self) -> None:
        try:
            devices = tuple(self._devices.devices())
        except Exception as exc:
            self._publish(error=str(exc), status_text="Camera discovery failed.")
            return
        current = self.state.snapshot.selected_device
        selected = current if any(device.identifier == current for device in devices) else ""
        if not selected and devices:
            selected = devices[0].identifier
        self._publish(
            devices=devices,
            selected_device=selected,
            can_start=bool(selected),
            status_text=f"{len(devices)} camera(s) available." if devices else "No cameras found.",
            error="",
        )

    def select_device(self, identifier: str) -> None:
        if not any(device.identifier == identifier for device in self.state.snapshot.devices):
            self._publish(error=f"Unknown camera {identifier!r}.")
            return
        self._publish(selected_device=identifier, can_start=True, error="")

    def start_capture(
        self,
        output_directory: Path,
        *,
        duration_seconds: float | None = None,
        readiness_timeout: float = 10.0,
    ) -> None:
        selected = self.state.snapshot.selected_device
        if not selected:
            self._publish(error="Select a camera before capture.")
            return
        self._publish(
            health=replace(self._capture.health, phase=CapturePhase.STARTING),
            can_start=False,
            can_stop=True,
            status_text="Starting camera…",
            error="",
        )

        def run() -> None:
            if not self._operation_lock.acquire(blocking=False):
                return
            try:
                self._capture.start_capture(
                    output_directory,
                    selected,
                    duration_seconds=duration_seconds,
                    readiness_timeout=readiness_timeout,
                )
                self.refresh_status()
            except Exception as exc:
                result = self._capture.result
                if result is not None and not result.error:
                    self.refresh_status()
                    return
                self._publish(
                    health=replace(self._capture.health, phase=CapturePhase.FAULT),
                    can_start=True,
                    can_stop=False,
                    status_text="Camera startup failed.",
                    error=str(exc),
                )
            finally:
                self._operation_lock.release()

        Thread(target=run, name="camera-panel-start", daemon=True).start()

    def stop_capture(self, reason: str = "operator") -> None:
        self._publish(can_stop=False, status_text="Stopping camera…")

        def run() -> None:
            try:
                self._capture.stop_capture(reason)
                self.refresh_status()
            except Exception as exc:
                self._publish(error=str(exc), status_text="Camera cleanup failed.")

        Thread(target=run, name="camera-panel-stop", daemon=True).start()

    def refresh_status(self) -> None:
        health = self._capture.health
        preview = self._capture.frame_channel.consume_latest()
        active = health.phase in {
            CapturePhase.STARTING,
            CapturePhase.READY,
            CapturePhase.RECORDING,
            CapturePhase.STOPPING,
        }
        text = (
            f"{health.phase.value}: frame {health.frame}, "
            f"dropped {health.dropped_frames}, {health.output_bytes} bytes"
        )
        self._publish(
            health=health,
            preview=preview if preview is not None else self.state.snapshot.preview,
            status_text=text,
            can_start=bool(self.state.snapshot.selected_device) and not active,
            can_stop=active,
        )

    def close(self, *, timeout: float = 10.0) -> bool:
        try:
            self._capture.close(timeout=timeout)
        except Exception as exc:
            self._publish(error=str(exc), status_text="Camera cleanup failed.")
            return False
        self.refresh_status()
        return True

    def _publish(self, **changes: object) -> None:
        with self._state_lock:
            self.state.publish(replace(self.state.snapshot, **changes))


__all__ = [
    "CameraCaptureBackend",
    "CameraCaptureService",
    "CameraDevice",
    "CameraDeviceSource",
    "CameraMode",
    "CameraPanelPresenter",
    "CameraPanelSnapshot",
    "CaptureError",
    "CaptureHealth",
    "CapturePhase",
    "CaptureResult",
    "CaptureTargetProfile",
    "LatestFrameChannel",
    "LatestFrameStats",
    "NegotiatedCaptureProfile",
    "PreviewFrame",
    "TARGET_4K60",
]
