"""Pure run lifecycle state transitions and idempotent completion semantics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .errors import ErrorCode, StateTransitionError


class RunState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    IDLE = "idle"
    READY = "ready"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAULT = "fault"


class RunCompletion(str, Enum):
    CLEAN = "clean"
    STOPPED = "stopped"
    ABORTED = "aborted"
    FAULTED = "faulted"


LEGAL_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.DISCONNECTED: frozenset({RunState.CONNECTING}),
    RunState.CONNECTING: frozenset({RunState.IDLE, RunState.FAULT}),
    RunState.IDLE: frozenset({RunState.READY, RunState.DISCONNECTED, RunState.FAULT}),
    RunState.READY: frozenset({RunState.STARTING, RunState.IDLE, RunState.FAULT}),
    RunState.STARTING: frozenset({RunState.RUNNING, RunState.STOPPING, RunState.FAULT}),
    RunState.RUNNING: frozenset({RunState.STOPPING, RunState.FAULT}),
    RunState.STOPPING: frozenset({RunState.COMPLETED, RunState.FAULT}),
    RunState.COMPLETED: frozenset({RunState.IDLE, RunState.DISCONNECTED}),
    RunState.FAULT: frozenset({RunState.IDLE, RunState.DISCONNECTED}),
}


@dataclass(frozen=True)
class RunSnapshot:
    state: RunState
    completion: RunCompletion | None = None

    def __post_init__(self) -> None:
        if self.state is RunState.COMPLETED and self.completion is None:
            raise StateTransitionError(
                ErrorCode.ILLEGAL_TRANSITION,
                "completed runs require a completion state",
                "completion",
            )
        if self.state is not RunState.COMPLETED and self.completion is not None:
            raise StateTransitionError(
                ErrorCode.ILLEGAL_TRANSITION,
                "only completed runs may have a completion state",
                "completion",
            )


@dataclass(frozen=True)
class TransitionResult:
    snapshot: RunSnapshot
    idempotent: bool = False


def transition(snapshot: RunSnapshot, target: RunState) -> TransitionResult:
    """Make one legal transition, preserving terminal completion metadata."""

    if target not in LEGAL_TRANSITIONS[snapshot.state]:
        raise StateTransitionError(
            ErrorCode.ILLEGAL_TRANSITION,
            f"cannot transition from {snapshot.state.value} to {target.value}",
            "run.state",
        )
    return TransitionResult(RunSnapshot(target))


def request_stop(snapshot: RunSnapshot) -> TransitionResult:
    """Request a stop once; repeated stop requests after stopping are no-ops."""

    if snapshot.state in {RunState.STOPPING, RunState.COMPLETED}:
        return TransitionResult(snapshot, idempotent=True)
    if snapshot.state not in {RunState.STARTING, RunState.RUNNING}:
        raise StateTransitionError(
            ErrorCode.ILLEGAL_TRANSITION,
            f"cannot stop a run in {snapshot.state.value}",
            "run.state",
        )
    return TransitionResult(RunSnapshot(RunState.STOPPING))


def finalize_run(snapshot: RunSnapshot, completion: RunCompletion) -> TransitionResult:
    """Record exactly one completion outcome after cleanup has finished."""

    if snapshot.state is RunState.COMPLETED:
        if snapshot.completion is completion:
            return TransitionResult(snapshot, idempotent=True)
        raise StateTransitionError(
            ErrorCode.ILLEGAL_TRANSITION,
            "cannot replace an existing completion outcome",
            "completion",
        )
    if snapshot.state is not RunState.STOPPING:
        raise StateTransitionError(
            ErrorCode.ILLEGAL_TRANSITION,
            "only a stopping run can be finalized",
            "run.state",
        )
    return TransitionResult(RunSnapshot(RunState.COMPLETED, completion))
