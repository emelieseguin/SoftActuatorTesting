"""Tests for one-way state binding primitives (snapshots, commands, dispatch)."""

from __future__ import annotations

from dataclasses import dataclass

from soft_actuator_testing.ui.presenters.binding import CommandDispatcher, SnapshotStore, bind_text


@dataclass(frozen=True)
class _Snapshot:
    label: str
    count: int


@dataclass(frozen=True)
class _IncrementCommand:
    amount: int


def test_snapshot_store_notifies_subscribers_only_on_change(qtbot) -> None:
    store = SnapshotStore(_Snapshot("idle", 0))
    seen = []
    store.subscribe(seen.append)

    store.publish(_Snapshot("idle", 0))  # identical snapshot: no notification
    assert seen == []

    store.publish(_Snapshot("running", 1))
    assert seen == [_Snapshot("running", 1)]
    assert store.snapshot == _Snapshot("running", 1)


def test_snapshot_store_unsubscribe_stops_future_notifications(qtbot) -> None:
    store = SnapshotStore(_Snapshot("idle", 0))
    seen = []
    unsubscribe = store.subscribe(seen.append)
    store.publish(_Snapshot("running", 1))
    unsubscribe()
    store.publish(_Snapshot("stopped", 2))
    assert seen == [_Snapshot("running", 1)]


def test_bind_text_renders_initial_value_and_reacts_to_changes(qtbot) -> None:
    store = SnapshotStore(_Snapshot("idle", 0))
    rendered = []
    bind_text(store, rendered.append, lambda snapshot: snapshot.label)
    assert rendered == ["idle"]

    store.publish(_Snapshot("running", 1))
    assert rendered == ["idle", "running"]


def test_bind_text_can_skip_initial_render(qtbot) -> None:
    store = SnapshotStore(_Snapshot("idle", 0))
    rendered = []
    bind_text(store, rendered.append, lambda snapshot: snapshot.label, render_initial=False)
    assert rendered == []
    store.publish(_Snapshot("running", 1))
    assert rendered == ["running"]


def test_command_dispatcher_forwards_commands_and_records_deterministic_history() -> None:
    received = []
    dispatcher: CommandDispatcher[_IncrementCommand] = CommandDispatcher(received.append)

    dispatcher.dispatch(_IncrementCommand(1))
    dispatcher.dispatch(_IncrementCommand(5))

    assert received == [_IncrementCommand(1), _IncrementCommand(5)]
    assert dispatcher.history == (_IncrementCommand(1), _IncrementCommand(5))
