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


def test_controller_sends_all_legacy_commands_and_retains_sent_timestamp() -> None:
    factory = Factory()
    adapter = SerialAdapter(factory, parser=SerialTextParser(ParserProfile(name="legacy")))
    controller = SerialController(adapter)
    controller.connect(SerialConnectionConfig("FAKE0"))
    controller.set_legacy_parameters(cycles=3, on_milliseconds=6000, off_milliseconds=5000)
    controller.start_legacy_run()
    controller.stop_legacy_run()
    controller.set_legacy_calibration_streaming(True)
    controller.set_legacy_calibration_streaming(False)
    assert factory.transport.writes == [
        b"CMD:SET CYCLES 3\n",
        b"CMD:SET ON 6000\n",
        b"CMD:SET OFF 5000\n",
        b"CMD:START\n",
        b"CMD:STOP\n",
        b"CMD:CAL_ON\n",
        b"CMD:CAL_OFF\n",
    ]
    assert controller.snapshot.last_sent_at is not None
    controller.close()
