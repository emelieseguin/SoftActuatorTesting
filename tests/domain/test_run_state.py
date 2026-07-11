from __future__ import annotations

import pytest

from soft_actuator_testing.domain.errors import StateTransitionError
from soft_actuator_testing.domain.run_state import (
    RunCompletion,
    RunSnapshot,
    RunState,
    finalize_run,
    request_stop,
    transition,
)


def test_legal_run_path_and_stop_finalize_are_idempotent() -> None:
    snapshot = RunSnapshot(RunState.DISCONNECTED)
    for state in (RunState.CONNECTING, RunState.IDLE, RunState.READY, RunState.STARTING, RunState.RUNNING):
        snapshot = transition(snapshot, state).snapshot

    stopping = request_stop(snapshot)
    assert stopping.snapshot.state is RunState.STOPPING
    assert request_stop(stopping.snapshot).idempotent is True

    completed = finalize_run(stopping.snapshot, RunCompletion.STOPPED)
    assert completed.snapshot == RunSnapshot(RunState.COMPLETED, RunCompletion.STOPPED)
    assert finalize_run(completed.snapshot, RunCompletion.STOPPED).idempotent is True


def test_illegal_transition_stop_and_conflicting_finalization_are_errors() -> None:
    with pytest.raises(StateTransitionError, match="cannot transition"):
        transition(RunSnapshot(RunState.DISCONNECTED), RunState.RUNNING)
    with pytest.raises(StateTransitionError, match="cannot stop"):
        request_stop(RunSnapshot(RunState.READY))
    with pytest.raises(StateTransitionError, match="only a stopping"):
        finalize_run(RunSnapshot(RunState.RUNNING), RunCompletion.CLEAN)
    with pytest.raises(StateTransitionError, match="cannot replace"):
        finalize_run(RunSnapshot(RunState.COMPLETED, RunCompletion.CLEAN), RunCompletion.FAULTED)
