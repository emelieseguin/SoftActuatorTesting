"""Qt adapters for the application-owned state and command seams."""

from __future__ import annotations

from collections.abc import Callable
from types import MethodType
from typing import Generic, TypeVar
from weakref import WeakMethod, ref

from PySide6.QtCore import QObject
from shiboken6 import isValid

from soft_actuator_testing.application.presentation import StateStore, Subscription

SnapshotT = TypeVar("SnapshotT")
CommandT = TypeVar("CommandT")
Unsubscribe = Callable[[], None]


class SnapshotStore(StateStore[SnapshotT]):
    """Compatibility name for the Qt-free application :class:`StateStore`."""

    def __init__(self, initial: SnapshotT, parent: QObject | None = None) -> None:
        del parent
        super().__init__(initial)


class CommandDispatcher(Generic[CommandT]):
    """Small UI-side adapter around a typed application command handler."""

    def __init__(self, handler: Callable[[CommandT], object]) -> None:
        self._handler = handler
        self._history: list[CommandT] = []

    def dispatch(self, command: CommandT) -> object:
        self._history.append(command)
        return self._handler(command)

    @property
    def history(self) -> tuple[CommandT, ...]:
        return tuple(self._history)


def bind_view(
    owner: QObject,
    store: StateStore[SnapshotT],
    callback: Callable[[SnapshotT], None],
    *,
    render_initial: bool = True,
) -> Subscription:
    """Bind a view without retaining it or updating it after Qt destruction."""

    owner_reference = ref(owner)
    weak_callback = WeakMethod(callback) if isinstance(callback, MethodType) else None
    strong_callback = None if weak_callback is not None else callback
    subscription_holder: list[Subscription] = []

    def apply(snapshot: SnapshotT) -> None:
        current_owner = owner_reference()
        current_callback = weak_callback() if weak_callback is not None else strong_callback
        if current_owner is None or current_callback is None or not isValid(current_owner):
            if subscription_holder:
                subscription_holder[0].dispose()
            return
        current_callback(snapshot)

    subscription = store.subscribe(apply)
    subscription_holder.append(subscription)
    owner.destroyed.connect(subscription.dispose)
    if render_initial:
        apply(store.snapshot)
    return subscription


def bind_text(
    store: StateStore[SnapshotT],
    setter: Callable[[str], None],
    selector: Callable[[SnapshotT], str],
    *,
    render_initial: bool = True,
) -> Unsubscribe:
    """Bind text for non-view-specific call sites; return an unsubscribe."""

    def apply(snapshot: SnapshotT) -> None:
        setter(selector(snapshot))

    if render_initial:
        apply(store.snapshot)
    return store.subscribe(apply).dispose


__all__ = [
    "CommandDispatcher",
    "SnapshotStore",
    "Unsubscribe",
    "bind_text",
    "bind_view",
]
