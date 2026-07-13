"""Single-owner, threaded serial transport and configurable text protocol parser."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Condition, Event, RLock, Thread
from time import monotonic
from types import MappingProxyType
from typing import Protocol, runtime_checkable


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@runtime_checkable
class SerialTransport(Protocol):
    """Minimal pyserial-shaped transport owned only by :class:`SerialAdapter`."""

    @property
    def is_open(self) -> bool: ...

    def readline(self) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class SerialPort:
    device: str
    description: str = ""
    hardware_id: str = ""


@runtime_checkable
class SerialTransportFactory(Protocol):
    def enumerate_ports(self) -> Sequence[SerialPort]: ...

    def open(self, config: "SerialConnectionConfig") -> SerialTransport: ...


class PySerialTransportFactory:
    """Lazy pyserial factory; importing this module never opens or scans a port."""

    def enumerate_ports(self) -> Sequence[SerialPort]:
        from serial.tools import list_ports

        return tuple(
            SerialPort(device=port.device, description=port.description or "", hardware_id=port.hwid or "")
            for port in list_ports.comports()
        )

    def open(self, config: "SerialConnectionConfig") -> SerialTransport:
        import serial

        return serial.Serial(port=config.port, baudrate=config.baudrate, timeout=config.timeout_seconds)


@dataclass(frozen=True)
class SerialConnectionConfig:
    port: str
    baudrate: int = 115200
    timeout_seconds: float = 0.5
    queue_capacity: int = 256
    shutdown_timeout_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not self.port.strip():
            raise ValueError("A serial port must be selected.")
        if self.baudrate <= 0:
            raise ValueError("baudrate must be positive.")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if self.queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive.")
        if self.shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be positive.")


@dataclass(frozen=True)
class ParserProfile:
    """Text grammar and explicitly configured telemetry mapping.

    ``telemetry_fields`` maps semantic names to zero-based CSV indexes. It is
    empty by default because this repository has no authoritative firmware
    schema. Valid semantic names are ``timestamp_seconds`` and ``volts``.
    """

    name: str = "unconfigured"
    telemetry_fields: Mapping[str, int] = field(default_factory=dict)
    delimiter: str = ","
    new_run_marker: str = "--- new run ---"
    end_run_marker: str = "--- end run ---"
    error_prefixes: tuple[str, ...] = ("__ERROR__", "ERROR:", "ERR:")
    acknowledgement_prefix: str = "ACK:"
    acknowledgements_supported: bool = False
    acknowledgement_ids_supported: bool = False

    def __post_init__(self) -> None:
        unknown = set(self.telemetry_fields) - {"timestamp_seconds", "volts"}
        if unknown:
            raise ValueError(f"Unknown telemetry fields: {sorted(unknown)!r}")
        if any(index < 0 for index in self.telemetry_fields.values()):
            raise ValueError("Telemetry field indexes must be non-negative.")
        if not self.delimiter:
            raise ValueError("delimiter must not be empty.")


def legacy_field_three_unconfirmed_profile() -> ParserProfile:
    """Return the observed legacy mapping, explicitly marked unconfirmed."""

    return ParserProfile(
        name="legacy-field-3-unconfirmed",
        telemetry_fields={"timestamp_seconds": 0, "volts": 2},
        acknowledgements_supported=False,
    )


@dataclass(frozen=True)
class ParseDiagnostic:
    code: str
    message: str
    raw_line: str
    received_at: datetime


@dataclass(frozen=True)
class SerialFrame:
    raw_line: str
    received_at: datetime


@dataclass(frozen=True)
class RunMarkerFrame(SerialFrame):
    started: bool


@dataclass(frozen=True)
class TelemetryFrame(SerialFrame):
    values: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


@dataclass(frozen=True)
class ErrorFrame(SerialFrame):
    message: str
    source: str = "device"


@dataclass(frozen=True)
class AcknowledgementFrame(SerialFrame):
    command: str
    command_id: str | None = None


@dataclass(frozen=True)
class DiagnosticFrame(SerialFrame):
    diagnostic: ParseDiagnostic


@dataclass(frozen=True)
class UnknownFrame(SerialFrame):
    pass


ParsedFrame = RunMarkerFrame | TelemetryFrame | ErrorFrame | AcknowledgementFrame | DiagnosticFrame | UnknownFrame


@dataclass(frozen=True)
class SerialSubscriptionSnapshot:
    """Immutable accounting for one independently consumed frame stream."""

    name: str
    critical: bool
    capacity: int | None
    queued_frames: int
    dropped_frames: int
    closed: bool


@dataclass
class _FrameSubscriber:
    name: str
    capacity: int | None
    critical: bool
    frames: deque[ParsedFrame] = field(default_factory=deque)
    dropped_frames: int = 0
    closed: bool = False


class SerialFrameSubscription:
    """A named, independently drained view of frames from one adapter.

    The adapter owns dispatch and keeps the queue thread-safe. Consumers own
    the subscription lifetime and must close it when their workflow ends.
    """

    def __init__(self, adapter: "SerialAdapter", subscriber: _FrameSubscriber) -> None:
        self._adapter = adapter
        self._subscriber = subscriber

    @property
    def snapshot(self) -> SerialSubscriptionSnapshot:
        return self._adapter._subscription_snapshot(self._subscriber)

    @property
    def name(self) -> str:
        return self._subscriber.name

    @property
    def dropped_frames(self) -> int:
        return self.snapshot.dropped_frames

    @property
    def closed(self) -> bool:
        return self.snapshot.closed

    def drain(self, maximum: int | None = None) -> tuple[ParsedFrame, ...]:
        return self._adapter._drain_subscription(self._subscriber, maximum)

    def close(self) -> None:
        self._adapter._close_subscription(self._subscriber)


class SerialTextParser:
    """Parse one decoded serial line without inventing a firmware schema."""

    def __init__(self, profile: ParserProfile | None = None) -> None:
        self.profile = profile or ParserProfile()

    def parse(self, raw_line: str, received_at: datetime | None = None) -> ParsedFrame:
        timestamp = received_at or _utcnow()
        text = raw_line.strip()
        folded = text.casefold()
        if folded.startswith(self.profile.new_run_marker.casefold()):
            return RunMarkerFrame(raw_line=text, received_at=timestamp, started=True)
        if folded.startswith(self.profile.end_run_marker.casefold()):
            return RunMarkerFrame(raw_line=text, received_at=timestamp, started=False)
        for prefix in self.profile.error_prefixes:
            if folded.startswith(prefix.casefold()):
                return ErrorFrame(
                    raw_line=text,
                    received_at=timestamp,
                    message=text[len(prefix) :].strip() or "Device reported an unspecified serial error.",
                )
        acknowledgement = self._parse_acknowledgement(text, timestamp)
        if acknowledgement is not None:
            return acknowledgement
        if self.profile.delimiter not in text:
            return self._diagnostic(
                "telemetry-row-malformed",
                "Line is neither a known control frame nor a delimited telemetry row.",
                text,
                timestamp,
            )
        if not self.profile.telemetry_fields:
            return self._diagnostic(
                "telemetry-mapping-unconfigured",
                "CSV-like row received, but no telemetry field mapping is configured.",
                text,
                timestamp,
            )
        parts = tuple(field.strip() for field in text.split(self.profile.delimiter))
        configured_highest = max(self.profile.telemetry_fields.values())
        if len(parts) <= configured_highest:
            return self._diagnostic(
                "telemetry-row-too-short",
                f"Row has {len(parts)} fields; configured mapping requires index {configured_highest}.",
                text,
                timestamp,
            )
        values: dict[str, float] = {}
        for name, index in self.profile.telemetry_fields.items():
            try:
                values[name] = float(parts[index])
            except ValueError:
                return self._diagnostic(
                    "telemetry-value-invalid",
                    f"Field {index} configured as {name!r} is not numeric.",
                    text,
                    timestamp,
                )
        return TelemetryFrame(raw_line=text, received_at=timestamp, values=values)

    def _parse_acknowledgement(self, text: str, timestamp: datetime) -> AcknowledgementFrame | None:
        prefix = self.profile.acknowledgement_prefix
        if not self.profile.acknowledgements_supported or not text.casefold().startswith(prefix.casefold()):
            return None
        content = text[len(prefix) :].strip()
        command, separator, command_id = content.partition("#")
        return AcknowledgementFrame(
            raw_line=text,
            received_at=timestamp,
            command=command.strip(),
            command_id=command_id.strip() if separator and command_id.strip() else None,
        )

    @staticmethod
    def _diagnostic(code: str, message: str, raw_line: str, received_at: datetime) -> DiagnosticFrame:
        return DiagnosticFrame(
            raw_line=raw_line,
            received_at=received_at,
            diagnostic=ParseDiagnostic(code=code, message=message, raw_line=raw_line, received_at=received_at),
        )


class CommandState(str, Enum):
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    TIMED_OUT = "timed_out"
    LATE_ACKNOWLEDGEMENT = "late_acknowledgement"
    WRITE_FAILED = "write_failed"


@dataclass(frozen=True)
class CommandReceipt:
    command: str
    command_id: str
    sent_at: datetime
    state: CommandState
    acknowledged_at: datetime | None = None
    detail: str = ""


@dataclass
class _PendingCommand:
    command: str
    command_id: str
    sent_at: datetime
    acknowledgement: AcknowledgementFrame | None = None
    cancelled: bool = False


class SerialAdapter:
    """Own a single transport lifecycle and move parsed frames off the UI thread."""

    def __init__(
        self,
        factory: SerialTransportFactory,
        *,
        parser: SerialTextParser | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._factory = factory
        self._parser = parser or SerialTextParser()
        self._clock = clock
        self._lock = RLock()
        self._acknowledgement = Condition(self._lock)
        self._transport: SerialTransport | None = None
        self._reader: Thread | None = None
        self._stop_reader = Event()
        self._queue_capacity = 1
        self._shutdown_timeout_seconds = 1.0
        self._subscribers: dict[str, _FrameSubscriber] = {}
        self._legacy_subscriber: _FrameSubscriber | None = None
        self._pending: dict[str, _PendingCommand] = {}
        self._timed_out_commands: deque[tuple[str, str]] = deque(maxlen=32)
        self._command_sequence = 0

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._transport is not None and self._transport.is_open

    @property
    def dropped_frames(self) -> int:
        """Drop accounting for deprecated :meth:`drain_frames` callers."""

        with self._lock:
            return self._legacy_subscriber.dropped_frames if self._legacy_subscriber is not None else 0

    @property
    def profile(self) -> ParserProfile:
        return self._parser.profile

    def refresh_ports(self) -> tuple[SerialPort, ...]:
        return tuple(self._factory.enumerate_ports())

    def connect(self, config: SerialConnectionConfig) -> bool:
        with self._lock:
            if self.is_connected:
                return False
            if self._reader is not None and self._reader.is_alive():
                raise RuntimeError("the previous serial reader has not stopped; reconnect is unsafe")
            transport = self._factory.open(config)
            self._transport = transport
            self._queue_capacity = config.queue_capacity
            self._shutdown_timeout_seconds = config.shutdown_timeout_seconds
            self._subscribers.clear()
            self._legacy_subscriber = _FrameSubscriber(
                name="__legacy_drain__",
                capacity=config.queue_capacity,
                critical=False,
            )
            self._subscribers[self._legacy_subscriber.name] = self._legacy_subscriber
            self._stop_reader.clear()
            self._reader = Thread(target=self._read_loop, args=(transport,), name="serial-reader", daemon=False)
            self._reader.start()
            return True

    def disconnect(self, *, timeout_seconds: float | None = None) -> bool:
        with self._lock:
            transport = self._transport
            reader = self._reader
            if transport is None:
                return False
            self._stop_reader.set()
            for pending in self._pending.values():
                pending.cancelled = True
            self._acknowledgement.notify_all()
            self._retire_all_subscribers_locked(
                ErrorFrame(
                    raw_line="",
                    received_at=self._clock(),
                    message="Serial transport disconnected.",
                    source="disconnect",
                )
            )
        close_error: Exception | None = None
        try:
            transport.close()
        except Exception as error:
            close_error = error
            self._offer(
                ErrorFrame(
                    raw_line="",
                    received_at=self._clock(),
                    message=f"Serial close error: {error}",
                    source="close",
                )
            )
        if reader is not None:
            reader.join(timeout_seconds if timeout_seconds is not None else self._shutdown_timeout_seconds)
            if reader.is_alive():
                error = RuntimeError("Serial reader did not stop before the shutdown timeout.")
                self._offer(
                    ErrorFrame(raw_line="", received_at=self._clock(), message=str(error), source="shutdown")
                )
                raise error
        with self._lock:
            if self._transport is transport:
                self._transport = None
            if self._reader is reader:
                self._reader = None
            self._pending.clear()
            self._acknowledgement.notify_all()
        if close_error is not None:
            raise RuntimeError(f"Serial close failed: {close_error}") from close_error
        return True

    def close(self) -> None:
        self.disconnect()

    def drain_frames(self, maximum: int | None = None) -> tuple[ParsedFrame, ...]:
        """Drain the deprecated compatibility stream.

        New application consumers must use :meth:`subscribe_frames`; this
        method deliberately has no role in production workflow consumption.
        """

        with self._lock:
            subscriber = self._legacy_subscriber
        return () if subscriber is None else self._drain_subscription(subscriber, maximum)

    def subscribe_frames(
        self,
        name: str,
        *,
        critical: bool = False,
        capacity: int | None = None,
    ) -> SerialFrameSubscription:
        """Create one named frame stream without competing with other consumers.

        Critical streams are intentionally unbounded and used for active run
        persistence and calibration freshness. Noncritical streams are bounded
        drop-oldest queues with per-subscription accounting.
        """

        normalized = name.strip()
        if not normalized:
            raise ValueError("subscription name must not be empty.")
        if critical and capacity is not None:
            raise ValueError("critical subscriptions are lossless and cannot set a bounded capacity.")
        if not critical:
            capacity = self._queue_capacity if capacity is None else capacity
            if capacity <= 0:
                raise ValueError("subscription capacity must be positive.")
        with self._lock:
            if self._transport is None or not self._transport.is_open:
                raise RuntimeError("Serial transport is not connected.")
            if normalized in self._subscribers:
                raise ValueError(f"serial subscription {normalized!r} already exists.")
            subscriber = _FrameSubscriber(normalized, capacity, critical)
            self._subscribers[normalized] = subscriber
            return SerialFrameSubscription(self, subscriber)

    def _subscription_snapshot(self, subscriber: _FrameSubscriber) -> SerialSubscriptionSnapshot:
        with self._lock:
            return SerialSubscriptionSnapshot(
                subscriber.name,
                subscriber.critical,
                subscriber.capacity,
                len(subscriber.frames),
                subscriber.dropped_frames,
                subscriber.closed,
            )

    def _drain_subscription(
        self,
        subscriber: _FrameSubscriber,
        maximum: int | None,
    ) -> tuple[ParsedFrame, ...]:
        if maximum is not None and maximum < 0:
            raise ValueError("maximum must be non-negative.")
        with self._lock:
            count = len(subscriber.frames) if maximum is None else min(maximum, len(subscriber.frames))
            return tuple(subscriber.frames.popleft() for _ in range(count))

    def _close_subscription(self, subscriber: _FrameSubscriber) -> None:
        with self._lock:
            if subscriber.closed:
                subscriber.frames.clear()
                return
            self._subscribers.pop(subscriber.name, None)
            subscriber.frames.clear()
            subscriber.closed = True
            if subscriber is self._legacy_subscriber:
                self._legacy_subscriber = None

    def send_command(
        self,
        command: str,
        *,
        wait_for_acknowledgement: bool = False,
        acknowledgement_timeout_seconds: float = 1.0,
    ) -> CommandReceipt:
        normalized = command.strip()
        if not normalized:
            raise ValueError("command must not be empty.")
        if acknowledgement_timeout_seconds <= 0:
            raise ValueError("acknowledgement_timeout_seconds must be positive.")
        with self._lock:
            transport = self._transport
            if transport is None or not transport.is_open:
                raise RuntimeError("Serial transport is not connected.")
            self._command_sequence += 1
            command_id = str(self._command_sequence)
            sent_at = self._clock()
            pending = _PendingCommand(command=normalized, command_id=command_id, sent_at=sent_at)
            if wait_for_acknowledgement:
                if not self.profile.acknowledgements_supported:
                    raise ValueError(f"Profile {self.profile.name!r} does not support acknowledgements.")
                self._pending[command_id] = pending
            try:
                transport.write((normalized + "\n").encode("utf-8"))
            except Exception as error:
                self._pending.pop(command_id, None)
                self._offer_locked(
                    ErrorFrame(raw_line=normalized, received_at=self._clock(), message=f"Serial write error: {error}", source="write")
                )
                return CommandReceipt(
                    command=normalized,
                    command_id=command_id,
                    sent_at=sent_at,
                    state=CommandState.WRITE_FAILED,
                    detail=str(error),
                )
            if not wait_for_acknowledgement:
                return CommandReceipt(normalized, command_id, sent_at, CommandState.SENT)
            deadline = monotonic() + acknowledgement_timeout_seconds
            while pending.acknowledgement is None:
                if pending.cancelled:
                    self._pending.pop(command_id, None)
                    return CommandReceipt(
                        normalized,
                        command_id,
                        sent_at,
                        CommandState.WRITE_FAILED,
                        detail="Serial transport disconnected while waiting for acknowledgement.",
                    )
                remaining = deadline - monotonic()
                if remaining <= 0:
                    self._pending.pop(command_id, None)
                    self._timed_out_commands.append((normalized, command_id))
                    return CommandReceipt(
                        normalized,
                        command_id,
                        sent_at,
                        CommandState.TIMED_OUT,
                        detail="No matching acknowledgement arrived before timeout.",
                    )
                self._acknowledgement.wait(remaining)
            acknowledgement = pending.acknowledgement
            self._pending.pop(command_id, None)
            return CommandReceipt(
                normalized,
                command_id,
                sent_at,
                CommandState.ACKNOWLEDGED,
                acknowledged_at=acknowledgement.received_at,
            )

    def _read_loop(self, transport: SerialTransport) -> None:
        while not self._stop_reader.is_set():
            try:
                raw = transport.readline()
            except Exception as error:
                if not self._stop_reader.is_set():
                    self._offer(
                        ErrorFrame(raw_line="", received_at=self._clock(), message=f"Serial read error: {error}", source="read")
                    )
                return
            if not raw:
                continue
            text = raw.decode("utf-8", errors="replace").strip()
            frame = self._parser.parse(text, self._clock())
            with self._lock:
                if self._transport is not transport:
                    return
            self._handle_frame(frame)

    def _handle_frame(self, frame: ParsedFrame) -> None:
        with self._lock:
            if isinstance(frame, AcknowledgementFrame):
                late = self._consume_late_acknowledgement(frame)
                if late is not None:
                    self._offer_locked(late)
                    return
                pending = self._matching_pending(frame)
                if pending is not None:
                    pending.acknowledgement = frame
                    self._acknowledgement.notify_all()
            self._offer_locked(frame)

    def _matching_pending(self, frame: AcknowledgementFrame) -> _PendingCommand | None:
        if frame.command_id is not None:
            return self._pending.get(frame.command_id)
        for pending in self._pending.values():
            if pending.command == frame.command and pending.acknowledgement is None:
                return pending
        return None

    def _consume_late_acknowledgement(self, frame: AcknowledgementFrame) -> ErrorFrame | None:
        if frame.command_id is not None:
            match = next(
                ((command, command_id) for command, command_id in self._timed_out_commands if command_id == frame.command_id),
                None,
            )
        else:
            match = next(((command, command_id) for command, command_id in self._timed_out_commands if command == frame.command), None)
        if match is None:
            return None
        self._timed_out_commands.remove(match)
        command, command_id = match
        return ErrorFrame(
            raw_line=frame.raw_line,
            received_at=frame.received_at,
            message=f"Late acknowledgement for timed-out command {command!r} (id {command_id}) was ignored.",
            source=CommandState.LATE_ACKNOWLEDGEMENT.value,
        )

    def _offer(self, frame: ParsedFrame) -> None:
        with self._lock:
            self._offer_locked(frame)

    def _offer_locked(self, frame: ParsedFrame) -> None:
        for subscriber in tuple(self._subscribers.values()):
            self._offer_to_subscriber_locked(subscriber, frame)

    @staticmethod
    def _offer_to_subscriber_locked(subscriber: _FrameSubscriber, frame: ParsedFrame) -> None:
        if subscriber.closed:
            return
        if subscriber.capacity is not None and len(subscriber.frames) >= subscriber.capacity:
            subscriber.frames.popleft()
            subscriber.dropped_frames += 1
        subscriber.frames.append(frame)

    def _retire_all_subscribers_locked(self, terminal_frame: ErrorFrame) -> None:
        """Deliver a visible terminal event, then detach every active stream."""

        for subscriber in tuple(self._subscribers.values()):
            self._offer_to_subscriber_locked(subscriber, terminal_frame)
            subscriber.closed = True
        self._subscribers.clear()
        self._legacy_subscriber = None
