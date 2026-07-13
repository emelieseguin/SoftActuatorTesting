"""Tests for the isolated serial connection/diagnostic presenter state."""

from __future__ import annotations

from queue import Empty, Queue
from time import sleep

from soft_actuator_testing.application.serial_controller import SerialConnectionStatus, SerialController
from soft_actuator_testing.infrastructure.serial_adapter import (
    ParserProfile,
    SerialAdapter,
    SerialConnectionConfig,
    SerialPort,
    SerialTextParser,
    TelemetryFrame,
    legacy_field_three_unconfirmed_profile,
)


class Transport:
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


class Factory:
    def __init__(self) -> None:
        self.transport = Transport()
        self.open_calls = 0
        self.enumerate_calls = 0

    def enumerate_ports(self):
        self.enumerate_calls += 1
        return (SerialPort("FAKE0", "fake serial"),)

    def open(self, config):
        self.open_calls += 1
        return self.transport


def test_default_controller_never_enumerates_or_opens_a_real_port() -> None:
    controller = SerialController()
    assert controller.snapshot.status is SerialConnectionStatus.UNCONFIGURED
    assert controller.refresh_ports() == ()
    assert not controller.connect(SerialConnectionConfig("never-open"))
    assert "no serial adapter" in controller.snapshot.diagnostic_text.lower()


def test_controller_refreshes_connects_polls_and_presents_unconfirmed_mapping() -> None:
    factory = Factory()
    adapter = SerialAdapter(
        factory,
        parser=SerialTextParser(legacy_field_three_unconfirmed_profile()),
    )
    controller = SerialController(adapter)
    assert controller.refresh_ports()[0].device == "FAKE0"
    assert controller.connect(SerialConnectionConfig("FAKE0"))
    factory.transport.lines.put(b"0.1,ignored,2.5\n")
    for _ in range(100):
        if controller.poll():
            break
        sleep(0.005)
    assert controller.snapshot.status is SerialConnectionStatus.CONNECTED
    assert controller.snapshot.profile_is_unconfirmed
    assert "Mapped telemetry" in controller.snapshot.diagnostic_text
    assert controller.snapshot.last_received_at is not None
    assert controller.disconnect()


def test_diagnostics_block_legacy_start_but_retain_safe_legacy_commands() -> None:
    factory = Factory()
    adapter = SerialAdapter(factory, parser=SerialTextParser(ParserProfile(name="legacy")))
    controller = SerialController(adapter)
    controller.connect(SerialConnectionConfig("FAKE0"))
    controller.set_legacy_parameters(cycles=3, on_milliseconds=6000, off_milliseconds=5000)
    assert controller.start_legacy_run() is None
    controller.stop_legacy_run()
    controller.set_legacy_calibration_streaming(True)
    controller.set_legacy_calibration_streaming(False)
    assert factory.transport.writes == [
        b"CMD:SET CYCLES 3\n",
        b"CMD:SET ON 6000\n",
        b"CMD:SET OFF 5000\n",
        b"CMD:STOP\n",
        b"CMD:CAL_ON\n",
        b"CMD:CAL_OFF\n",
    ]
    assert controller.snapshot.last_sent_at is not None
    assert "CMD:START is blocked" in controller.snapshot.diagnostic_text
    controller.close()


def test_diagnostic_polling_and_workflow_subscription_receive_independent_frame_copies() -> None:
    factory = Factory()
    adapter = SerialAdapter(
        factory,
        parser=SerialTextParser(legacy_field_three_unconfirmed_profile()),
    )
    controller = SerialController(adapter)
    controller.connect(SerialConnectionConfig("FAKE0"))
    run_stream = controller.subscribe_frames("test-run-persistence", critical=True)
    factory.transport.lines.put(b"0.1,ignored,2.5\n")

    for _ in range(100):
        if run_stream.snapshot.queued_frames:
            break
        sleep(0.005)

    diagnostics = controller.poll()
    run_frames = run_stream.drain()

    assert any(isinstance(frame, TelemetryFrame) for frame in diagnostics)
    assert any(isinstance(frame, TelemetryFrame) for frame in run_frames)
    run_stream.close()
    controller.close()


def test_bounded_diagnostics_report_their_own_drop_accounting() -> None:
    factory = Factory()
    adapter = SerialAdapter(
        factory,
        parser=SerialTextParser(legacy_field_three_unconfirmed_profile()),
    )
    controller = SerialController(adapter, diagnostics_capacity=2)
    controller.connect(SerialConnectionConfig("FAKE0"))
    for index in range(5):
        factory.transport.lines.put(f"{index},ignored,{index}.0\n".encode())
    for _ in range(100):
        controller.poll()
        if controller.snapshot.dropped_frames >= 3:
            break
        sleep(0.005)

    assert controller.snapshot.dropped_frames >= 3
    assert "Dropped" in controller.snapshot.diagnostic_text
    controller.close()
