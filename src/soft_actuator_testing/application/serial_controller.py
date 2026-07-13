"""Qt-free connection and diagnostic state for the real serial adapter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from threading import RLock

from soft_actuator_testing.infrastructure.serial_adapter import (
    AcknowledgementFrame,
    CommandReceipt,
    DiagnosticFrame,
    ErrorFrame,
    ParsedFrame,
    ParserProfile,
    RunMarkerFrame,
    SerialAdapter,
    SerialConnectionConfig,
    SerialFrameSubscription,
    SerialPort,
    TelemetryFrame,
    UnknownFrame,
    legacy_field_three_unconfirmed_profile,
)


class SerialConnectionStatus(str, Enum):
    UNCONFIGURED = "unconfigured"
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    FAULT = "fault"


@dataclass(frozen=True)
class SerialDiagnostic:
    received_at: datetime | None
    category: str
    message: str


@dataclass(frozen=True)
class SerialConnectionSnapshot:
    status: SerialConnectionStatus
    profile_name: str
    profile_is_unconfirmed: bool
    config: SerialConnectionConfig | None
    ports: tuple[SerialPort, ...]
    diagnostics: tuple[SerialDiagnostic, ...]
    dropped_frames: int
    last_sent_at: datetime | None = None
    last_received_at: datetime | None = None
    last_command: CommandReceipt | None = None

    @property
    def diagnostic_text(self) -> str:
        return "\n".join(
            f"{entry.received_at.isoformat() if entry.received_at else 'local'} [{entry.category}] {entry.message}"
            for entry in self.diagnostics
        )


class SerialController:
    """Application seam for serial configuration and frame diagnostics.

    Constructing the default controller does not instantiate a pyserial factory
    or enumerate/open a physical port. A caller must inject an adapter before a
    real connection is possible.
    """

    def __init__(
        self,
        adapter: SerialAdapter | None = None,
        *,
        profile: ParserProfile | None = None,
        diagnostics_capacity: int = 100,
    ) -> None:
        if diagnostics_capacity <= 0:
            raise ValueError("diagnostics_capacity must be positive.")
        self._adapter = adapter
        self._profile = profile or (adapter.profile if adapter is not None else ParserProfile())
        self._diagnostics_capacity = diagnostics_capacity
        self._lock = RLock()
        self._diagnostic_subscription: SerialFrameSubscription | None = None
        status = SerialConnectionStatus.DISCONNECTED if adapter is not None else SerialConnectionStatus.UNCONFIGURED
        initial_diagnostic = (
            "No serial adapter is configured; this screen will not access a physical port."
            if adapter is None
            else f"Serial adapter ready with profile {self._profile.name!r}; no port is open."
        )
        self._snapshot = SerialConnectionSnapshot(
            status=status,
            profile_name=self._profile.name,
            profile_is_unconfirmed=self._profile.name == legacy_field_three_unconfirmed_profile().name,
            config=None,
            ports=(),
            diagnostics=(SerialDiagnostic(None, "connection", initial_diagnostic),),
            dropped_frames=0,
        )
        self._listeners: list[Callable[[SerialConnectionSnapshot], None]] = []

    @property
    def snapshot(self) -> SerialConnectionSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def profile(self) -> ParserProfile:
        return self._profile

    def subscribe(self, listener: Callable[[SerialConnectionSnapshot], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)
            snapshot = self._snapshot
        listener(snapshot)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def refresh_ports(self) -> tuple[SerialPort, ...]:
        if self._adapter is None:
            return self._publish(
                replace(
                    self._snapshot,
                    diagnostics=self._append("connection", "Port refresh skipped: no serial adapter is configured."),
                )
            ).ports
        try:
            ports = self._adapter.refresh_ports()
        except Exception as error:
            self._publish(
                replace(
                    self._snapshot,
                    status=SerialConnectionStatus.FAULT,
                    diagnostics=self._append("port-enumeration", f"Port enumeration failed: {error}"),
                )
            )
            return ()
        self._publish(
            replace(
                self._snapshot,
                status=SerialConnectionStatus.DISCONNECTED if not self._adapter.is_connected else SerialConnectionStatus.CONNECTED,
                ports=ports,
                diagnostics=self._append("port-enumeration", f"Found {len(ports)} serial port(s)."),
            )
        )
        return ports

    def connect(self, config: SerialConnectionConfig) -> bool:
        if self._adapter is None:
            self._publish(
                replace(
                    self._snapshot,
                    diagnostics=self._append("connection", "Connect skipped: no serial adapter is configured."),
                )
            )
            return False
        try:
            opened = self._adapter.connect(config)
        except Exception as error:
            self._publish(
                replace(
                    self._snapshot,
                    status=SerialConnectionStatus.FAULT,
                    config=config,
                    diagnostics=self._append("connection", f"Unable to open {config.port}: {error}"),
                )
            )
            return False
        self._ensure_diagnostic_subscription()
        self._publish(
            replace(
                self._snapshot,
                status=SerialConnectionStatus.CONNECTED,
                config=config,
                diagnostics=self._append(
                    "connection",
                    f"{'Opened' if opened else 'Already connected to'} {config.port} at {config.baudrate} baud.",
                ),
            )
        )
        return opened

    def disconnect(self) -> bool:
        if self._adapter is None:
            return False
        try:
            disconnected = self._adapter.disconnect()
        except Exception as error:
            self._publish(
                replace(
                    self._snapshot,
                    status=SerialConnectionStatus.FAULT,
                    diagnostics=self._append("disconnect", f"Serial disconnect failed: {error}"),
                )
            )
            return False
        self._publish(
            replace(
                self._snapshot,
                status=SerialConnectionStatus.DISCONNECTED,
                diagnostics=self._append(
                    "connection",
                    "Serial adapter disconnected." if disconnected else "Serial adapter was already disconnected.",
                ),
            )
        )
        return disconnected

    def close(self) -> None:
        self.disconnect()

    def poll(self, maximum: int | None = None) -> tuple[ParsedFrame, ...]:
        if self._adapter is None:
            return ()
        subscription = self._diagnostic_subscription
        if subscription is None:
            return ()
        frames = subscription.drain(maximum)
        snapshot = self.snapshot
        if not frames and snapshot.dropped_frames == subscription.dropped_frames:
            return ()
        diagnostics = snapshot.diagnostics
        last_received = snapshot.last_received_at
        for frame in frames:
            diagnostics = self._append_frame(diagnostics, frame)
            last_received = frame.received_at
        dropped_frames = subscription.dropped_frames
        if dropped_frames > snapshot.dropped_frames:
            diagnostics = (
                diagnostics
                + (
                    SerialDiagnostic(
                        None,
                        "diagnostic-overflow",
                        f"Dropped {dropped_frames - snapshot.dropped_frames} frame(s) from the bounded diagnostics queue "
                        f"({dropped_frames} total).",
                    ),
                )
            )[-self._diagnostics_capacity :]
        self._publish(
            replace(
                snapshot,
                diagnostics=diagnostics,
                dropped_frames=dropped_frames,
                last_received_at=last_received,
                status=SerialConnectionStatus.FAULT
                if any(isinstance(frame, ErrorFrame) and frame.source in {"read", "write", "shutdown", "close"} for frame in frames)
                else snapshot.status,
            )
        )
        return frames

    def subscribe_frames(
        self,
        name: str,
        *,
        critical: bool = False,
        capacity: int | None = None,
    ) -> SerialFrameSubscription:
        """Create an independent workflow stream from the one serial reader."""

        if self._adapter is None:
            raise RuntimeError("No serial adapter is configured.")
        return self._adapter.subscribe_frames(name, critical=critical, capacity=capacity)

    def send_command(
        self,
        command: str,
        *,
        wait_for_acknowledgement: bool = False,
        acknowledgement_timeout_seconds: float = 1.0,
    ) -> CommandReceipt | None:
        if self._adapter is None:
            self._publish(
                replace(self._snapshot, diagnostics=self._append("command", f"Not sent {command!r}: no serial adapter is configured."))
            )
            return None
        try:
            receipt = self._adapter.send_command(
                command,
                wait_for_acknowledgement=wait_for_acknowledgement,
                acknowledgement_timeout_seconds=acknowledgement_timeout_seconds,
            )
        except Exception as error:
            self._publish(
                replace(
                    self._snapshot,
                    status=SerialConnectionStatus.FAULT,
                    diagnostics=self._append("command", f"Command {command!r} failed: {error}"),
                )
            )
            return None
        self._publish(
            replace(
                self._snapshot,
                last_sent_at=receipt.sent_at,
                last_command=receipt,
                diagnostics=self._append("command", f"{receipt.command} → {receipt.state.value}"),
            )
        )
        return receipt

    def set_legacy_parameters(self, *, cycles: int, on_milliseconds: int, off_milliseconds: int) -> tuple[CommandReceipt | None, ...]:
        if cycles < 1 or on_milliseconds < 1 or off_milliseconds < 1:
            raise ValueError("Legacy cycles and on/off durations must be positive.")
        return (
            self.send_command(f"CMD:SET CYCLES {cycles}"),
            self.send_command(f"CMD:SET ON {on_milliseconds}"),
            self.send_command(f"CMD:SET OFF {off_milliseconds}"),
        )

    def start_legacy_run(self) -> CommandReceipt | None:
        return self.send_diagnostic_command("CMD:START")

    def stop_legacy_run(self) -> CommandReceipt | None:
        return self.send_command("CMD:STOP")

    def set_legacy_calibration_streaming(self, enabled: bool) -> CommandReceipt | None:
        return self.send_command("CMD:CAL_ON" if enabled else "CMD:CAL_OFF")

    def send_diagnostic_command(
        self,
        command: str,
        *,
        wait_for_acknowledgement: bool = False,
        acknowledgement_timeout_seconds: float = 1.0,
    ) -> CommandReceipt | None:
        """Send a safe diagnostic command without bypassing run readiness."""

        if self._is_unsafe_diagnostic_run_start(command):
            self._publish(
                replace(
                    self.snapshot,
                    diagnostics=self._append(
                        "command-blocked",
                        "CMD:START is blocked in diagnostics. Use Live Run after RunController readiness and camera proof.",
                    ),
                )
            )
            return None
        return self.send_command(
            command,
            wait_for_acknowledgement=wait_for_acknowledgement,
            acknowledgement_timeout_seconds=acknowledgement_timeout_seconds,
        )

    @staticmethod
    def _is_unsafe_diagnostic_run_start(command: str) -> bool:
        normalized = " ".join(command.upper().replace("_", " ").split())
        return normalized == "START" or normalized.startswith("CMD:START") or normalized.startswith("CMD:RUN START")

    def _ensure_diagnostic_subscription(self) -> None:
        if self._adapter is None:
            return
        current = self._diagnostic_subscription
        if current is not None and not current.closed:
            return
        self._diagnostic_subscription = self._adapter.subscribe_frames(
            "diagnostics",
            capacity=self._diagnostics_capacity,
        )

    def _append(self, category: str, message: str, received_at: datetime | None = None) -> tuple[SerialDiagnostic, ...]:
        return (self._snapshot.diagnostics + (SerialDiagnostic(received_at, category, message),))[-self._diagnostics_capacity :]

    def _append_frame(
        self, diagnostics: tuple[SerialDiagnostic, ...], frame: ParsedFrame
    ) -> tuple[SerialDiagnostic, ...]:
        if isinstance(frame, RunMarkerFrame):
            category, message = "run-marker", "Run started marker received." if frame.started else "Run ended marker received."
        elif isinstance(frame, TelemetryFrame):
            category, message = "telemetry", f"Mapped telemetry: {dict(frame.values)!r}"
        elif isinstance(frame, DiagnosticFrame):
            category, message = "parse", f"{frame.diagnostic.code}: {frame.diagnostic.message}"
        elif isinstance(frame, ErrorFrame):
            category, message = frame.source, frame.message
        elif isinstance(frame, AcknowledgementFrame):
            category, message = "acknowledgement", f"Acknowledged {frame.command!r}."
        elif isinstance(frame, UnknownFrame):
            category, message = "unrecognized", frame.raw_line
        else:  # pragma: no cover - retained for future frame extensions
            category, message = "frame", repr(frame)
        return (diagnostics + (SerialDiagnostic(frame.received_at, category, message),))[-self._diagnostics_capacity :]

    def _publish(self, snapshot: SerialConnectionSnapshot) -> SerialConnectionSnapshot:
        with self._lock:
            self._snapshot = snapshot
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener(snapshot)
        return snapshot
