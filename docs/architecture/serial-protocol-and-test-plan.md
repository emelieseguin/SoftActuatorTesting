# Serial controller protocol boundary and test plan

**Status:** Implemented
**Date:** 2026-07-11
**Related todo:** `serial-integration`

## Confirmed legacy facts

- The old GUI enumerates ports with `serial.tools.list_ports.comports()`, opens
  a selected port at 115200 baud with a 0.5 second timeout, and reads lines on
  a background thread.
- Text is decoded as UTF-8 with replacement. The observed command text is
  newline-terminated `CMD:SET CYCLES`, `CMD:SET ON`, `CMD:SET OFF`,
  `CMD:START`, `CMD:STOP`, `CMD:CAL_ON`, and `CMD:CAL_OFF`.
- `--- new run ---` and `--- end run ---` appear in the supplied transcript.
- The old implementation *attempted* to read CSV field three as volts, but its
  field-count check was incorrect and its own comment disagrees with that
  assumption.

## Deliberately unknown

Firmware source, protocol versioning, field order/units, acknowledgements,
error grammar, correlation identifiers, and safety semantics are not in this
repository. Consequently `legacy-field-3-unconfirmed` is an explicit,
configurable parser profile, not a default or an authoritative schema. The
default parser has no configured telemetry fields. This integration sends
legacy command text only; it does not claim a command was acted on or establish
hardware safety limits.

## Design

`infrastructure.serial_adapter.SerialAdapter` exclusively owns an opened
transport, reader thread, close, and join. It exposes a bounded drop-oldest
frame queue, so a slow UI cannot block reads. Reader output consists solely of
immutable typed frames, and UI/application code drains it on its own schedule.
Both source receive time and command-send time are retained. Read/write faults
become `ErrorFrame`s rather than silently ending a worker.

`application.serial_controller.SerialController` is a separate Qt-free
connection/diagnostic presenter state seam. It is intentionally not folded
into the current shared demo `presentation.py`; later composition can bridge
its immutable snapshots without turning the demo workflow into a real-device
owner. The Qt serial panel only renders and dispatches to this controller.

Acknowledgements are opt-in per profile. Profiles with no reliable correlation
will not use acknowledgement waits. For correlated profiles, timed-out
commands leave a tombstone: the next matching uncorrelated acknowledgement is
reported late rather than being allowed to confirm a newer identical command.

## Test plan

Default, hardware-free tests use injected transports and transcript fixtures:

1. Parse every supplied serial fixture, including run markers, unconfigured
   mappings, malformed/short rows, and reader-error sentinels.
2. Assert port refresh, configuration, UTF-8 replacement, queue saturation
   drop-oldest policy, duplicate connect/disconnect, read/write fault reporting,
   deterministic shutdown/join, and no real port construction by default.
3. Assert each legacy command line is newline-encoded exactly; verify
   acknowledgement success, timeout, and stale/late acknowledgement handling.
4. Assert the application controller and Qt panel render connection,
   diagnostics, profile uncertainty, and command results without opening a
   physical port.

Real-port discovery, firmware field mapping, command acceptance, and safety
validation remain hardware-gated work after firmware evidence is available.
