"""Hardware-free coverage for the serial owner and parser."""

from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from time import sleep

import pytest

from soft_actuator_testing.infrastructure.serial_adapter import (
    AcknowledgementFrame,
    CommandState,
    DiagnosticFrame,
    ErrorFrame,
    ParserProfile,
    RunMarkerFrame,
    SerialAdapter,
    SerialConnectionConfig,
    SerialPort,
    SerialTextParser,
    TelemetryFrame,
    legacy_field_three_unconfirmed_profile,
)


class TranscriptTransport:
    def __init__(self, *, fail_reads: bool = False, fail_writes: bool = False) -> None:
        self.is_open = True
        self._lines: Queue[bytes] = Queue()
        self.writes: list[bytes] = []
        self.fail_reads = fail_reads
        self.fail_writes = fail_writes
        self.closed = Event()

    def push(self, line: bytes) -> None:
        self._lines.put(line)

    def readline(self) -> bytes:
        if self.fail_reads:
            raise OSError("simulated disconnect")
        try:
            return self._lines.get(timeout=0.01)
        except Empty:
            return b""

    def write(self, data: bytes) -> int:
        if self.fail_writes:
            raise OSError("simulated write failure")
        self.writes.append(data)
        return len(data)

    def close(self) -> None:
        self.is_open = False
        self.closed.set()


class FakeFactory:
    def __init__(self, transport: TranscriptTransport, ports: tuple[SerialPort, ...] = (SerialPort("FAKE0"),)) -> None:
        self.transport = transport
        self.ports = ports
        self.open_calls = 0
        self.enumerate_calls = 0

    def enumerate_ports(self):
        self.enumerate_calls += 1
        return self.ports

    def open(self, config):
        self.open_calls += 1
        return self.transport


def _adapter(
    transport: TranscriptTransport,
    *,
    profile: ParserProfile | None = None,
    capacity: int = 256,
) -> SerialAdapter:
    adapter = SerialAdapter(FakeFactory(transport), parser=SerialTextParser(profile or legacy_field_three_unconfirmed_profile()))
    assert adapter.connect(SerialConnectionConfig("FAKE0", queue_capacity=capacity))
    return adapter


def _wait_for_frames(adapter: SerialAdapter, minimum: int = 1) -> tuple:
    for _ in range(100):
        frames = adapter.drain_frames()
        if len(frames) >= minimum:
            return frames
        sleep(0.005)
    return ()


def test_parser_reads_every_normal_fixture_line_with_explicit_unconfirmed_mapping() -> None:
    parser = SerialTextParser(legacy_field_three_unconfirmed_profile())
    lines = (Path(__file__).parents[1] / "fixtures/serial/telemetry-normal-with-markers.txt").read_text().splitlines()
    frames = [parser.parse(line) for line in lines]
    assert isinstance(frames[0], RunMarkerFrame) and frames[0].started
    assert [frame.values["volts"] for frame in frames[1:4] if isinstance(frame, TelemetryFrame)] == [0.1, 1.1, 2.1]
    assert isinstance(frames[-1], RunMarkerFrame) and not frames[-1].started


def test_default_parser_never_silently_assigns_legacy_field_three() -> None:
    frame = SerialTextParser().parse("0.000,ignored,0.100")
    assert isinstance(frame, DiagnosticFrame)
    assert frame.diagnostic.code == "telemetry-mapping-unconfigured"


def test_parser_reports_every_malformed_and_short_fixture_line() -> None:
    parser = SerialTextParser(legacy_field_three_unconfirmed_profile())
    lines = (Path(__file__).parents[1] / "fixtures/serial/telemetry-malformed-and-short.txt").read_text().splitlines()
    frames = [parser.parse(line) for line in lines]
    assert all(isinstance(frame, (DiagnosticFrame, ErrorFrame)) for frame in frames)
    assert [frame.diagnostic.code for frame in frames[:3] if isinstance(frame, DiagnosticFrame)] == [
        "telemetry-row-malformed",
        "telemetry-row-too-short",
        "telemetry-value-invalid",
    ]
    assert isinstance(frames[3], ErrorFrame)


def test_legacy_command_fixture_is_newline_encoded_exactly() -> None:
    transport = TranscriptTransport()
    adapter = _adapter(transport)
    commands = (Path(__file__).parents[1] / "fixtures/serial/command-lines.txt").read_text().splitlines()
    for command in commands:
        assert adapter.send_command(command).state is CommandState.SENT
    assert transport.writes == [(command + "\n").encode() for command in commands]
    adapter.disconnect()


def test_port_refresh_duplicate_connection_and_disconnect_are_idempotent() -> None:
    transport = TranscriptTransport()
    factory = FakeFactory(transport)
    adapter = SerialAdapter(factory)
    assert adapter.refresh_ports() == (SerialPort("FAKE0"),)
    config = SerialConnectionConfig("FAKE0")
    assert adapter.connect(config)
    assert not adapter.connect(config)
    assert adapter.disconnect()
    assert not adapter.disconnect()
    assert factory.open_calls == 1


def test_reader_replaces_invalid_utf8_and_uses_drop_oldest_bounded_queue() -> None:
    transport = TranscriptTransport()
    adapter = _adapter(transport, capacity=2)
    for index in range(5):
        transport.push(f"{index},ignored,{index}.0\n".encode())
    transport.push(b"\xff,ignored,9.0\n")
    for _ in range(100):
        if adapter.dropped_frames >= 4:
            break
        sleep(0.005)
    frames = adapter.drain_frames()
    assert adapter.dropped_frames >= 4
    assert len(frames) == 2
    assert any("\ufffd" in frame.raw_line for frame in frames)
    adapter.disconnect()


def test_read_and_write_failures_are_typed_frames() -> None:
    read_adapter = _adapter(TranscriptTransport(fail_reads=True))
    frames = _wait_for_frames(read_adapter)
    assert isinstance(frames[0], ErrorFrame) and frames[0].source == "read"
    read_adapter.disconnect()

    write_adapter = _adapter(TranscriptTransport(fail_writes=True))
    receipt = write_adapter.send_command("CMD:START")
    assert receipt.state is CommandState.WRITE_FAILED
    frames = _wait_for_frames(write_adapter)
    assert isinstance(frames[0], ErrorFrame) and frames[0].source == "write"
    write_adapter.disconnect()


def test_acknowledgement_success_timeout_and_stale_acknowledgement() -> None:
    profile = ParserProfile(name="ack-test", acknowledgements_supported=True)
    transport = TranscriptTransport()
    adapter = _adapter(transport, profile=profile)
    result: list = []

    worker = Thread(target=lambda: result.append(adapter.send_command("CMD:START", wait_for_acknowledgement=True)))
    worker.start()
    for _ in range(100):
        if transport.writes:
            break
        sleep(0.005)
    transport.push(b"ACK: CMD:START\n")
    worker.join(1)
    assert result[0].state is CommandState.ACKNOWLEDGED

    timed_out = adapter.send_command("CMD:START", wait_for_acknowledgement=True, acknowledgement_timeout_seconds=0.01)
    assert timed_out.state is CommandState.TIMED_OUT
    next_result: list = []
    worker = Thread(
        target=lambda: next_result.append(
            adapter.send_command("CMD:START", wait_for_acknowledgement=True, acknowledgement_timeout_seconds=0.05)
        )
    )
    worker.start()
    sleep(0.005)
    transport.push(b"ACK: CMD:START\n")
    worker.join(1)
    assert next_result[0].state is CommandState.TIMED_OUT
    assert any(
        isinstance(frame, ErrorFrame) and frame.source == CommandState.LATE_ACKNOWLEDGEMENT.value
        for frame in _wait_for_frames(adapter)
    )
    adapter.disconnect()


def test_identical_in_flight_commands_each_receive_one_uncorrelated_acknowledgement() -> None:
    profile = ParserProfile(name="ack-test", acknowledgements_supported=True)
    transport = TranscriptTransport()
    adapter = _adapter(transport, profile=profile)
    results: list = []
    workers = [
        Thread(
            target=lambda: results.append(
                adapter.send_command("CMD:START", wait_for_acknowledgement=True)
            )
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for _ in range(100):
        if len(transport.writes) == 2:
            break
        sleep(0.005)

    transport.push(b"ACK: CMD:START\n")
    transport.push(b"ACK: CMD:START\n")
    for worker in workers:
        worker.join(1)

    assert len(results) == 2
    assert all(result.state is CommandState.ACKNOWLEDGED for result in results)
    adapter.disconnect()


def test_disconnect_closes_and_joins_reader() -> None:
    transport = TranscriptTransport()
    adapter = _adapter(transport)
    reader = adapter._reader
    assert reader is not None and reader.is_alive()
    assert adapter.disconnect(timeout_seconds=0.5)
    assert transport.closed.is_set()
    assert not reader.is_alive()


def test_disconnect_failure_does_not_allow_a_second_reader_to_replace_a_stuck_owner() -> None:
    class BlockingTransport(TranscriptTransport):
        def __init__(self) -> None:
            super().__init__()
            self.entered = Event()
            self.release = Event()

        def readline(self) -> bytes:
            self.entered.set()
            assert self.release.wait(1)
            return b""

    transport = BlockingTransport()
    adapter = _adapter(transport)
    assert transport.entered.wait(1)

    with pytest.raises(RuntimeError, match="did not stop"):
        adapter.disconnect(timeout_seconds=0.01)
    with pytest.raises(RuntimeError, match="previous serial reader"):
        adapter.connect(SerialConnectionConfig("FAKE1"))

    transport.release.set()
    adapter.disconnect(timeout_seconds=0.5)


def test_disconnect_wakes_a_pending_acknowledgement_wait_with_a_fault_receipt() -> None:
    profile = ParserProfile(name="ack-test", acknowledgements_supported=True)
    transport = TranscriptTransport()
    adapter = _adapter(transport, profile=profile)
    receipts = []
    waiting = Thread(
        target=lambda: receipts.append(
            adapter.send_command("CMD:START", wait_for_acknowledgement=True, acknowledgement_timeout_seconds=1)
        )
    )
    waiting.start()
    for _ in range(100):
        if transport.writes:
            break
        sleep(0.005)

    adapter.disconnect()
    waiting.join(0.5)

    assert not waiting.is_alive()
    assert receipts[0].state is CommandState.WRITE_FAILED
    assert "disconnected" in receipts[0].detail


def test_fanout_keeps_critical_run_frames_when_bounded_diagnostics_overflow() -> None:
    transport = TranscriptTransport()
    adapter = _adapter(transport, capacity=2)
    diagnostics = adapter.subscribe_frames("diagnostics", capacity=2)
    run = adapter.subscribe_frames("run-persistence", critical=True)

    for index in range(10):
        transport.push(f"{index},ignored,{index}.0\n".encode())

    for _ in range(100):
        if run.snapshot.queued_frames == 10:
            break
        sleep(0.005)

    assert run.snapshot.capacity is None
    assert run.snapshot.dropped_frames == 0
    assert diagnostics.snapshot.dropped_frames >= 8
    assert len(run.drain()) == 10
    assert len(diagnostics.drain()) == 2
    run.close()
    diagnostics.close()
    adapter.disconnect()


def test_disconnect_retires_streams_with_visible_fault_and_reconnect_allows_fresh_subscriptions() -> None:
    first = TranscriptTransport()
    second = TranscriptTransport()

    class ReconnectingFactory(FakeFactory):
        def __init__(self) -> None:
            super().__init__(first)
            self.transports = [first, second]

        def open(self, config):
            del config
            self.open_calls += 1
            return self.transports.pop(0)

    adapter = SerialAdapter(
        ReconnectingFactory(),
        parser=SerialTextParser(legacy_field_three_unconfirmed_profile()),
    )
    assert adapter.connect(SerialConnectionConfig("FAKE0"))
    first_run = adapter.subscribe_frames("run-persistence", critical=True)
    assert adapter.disconnect()

    terminal = first_run.drain()
    assert first_run.closed
    assert len(terminal) == 1
    assert isinstance(terminal[0], ErrorFrame) and terminal[0].source == "disconnect"

    assert adapter.connect(SerialConnectionConfig("FAKE0"))
    second_run = adapter.subscribe_frames("run-persistence", critical=True)
    second.push(b"0.1,ignored,3.5\n")
    for _ in range(100):
        if second_run.snapshot.queued_frames:
            break
        sleep(0.005)
    assert isinstance(second_run.drain()[0], TelemetryFrame)
    second_run.close()
    adapter.disconnect()
