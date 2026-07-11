"""Pure tests for the Qt-free presenter state and command boundary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from soft_actuator_testing.application.presentation import (
    AnalysisMode,
    ApplySettings,
    BeginRun,
    ChooseAnalysisSource,
    CompleteRun,
    ConfirmRunStarted,
    ConnectDevices,
    ConnectionStatus,
    DisconnectDevices,
    EvaluateReadiness,
    GlobalStop,
    ReportRunFault,
    ReportRunTimeout,
    RequestRunStop,
    RunAnalysis,
    SetAnalysisMode,
    StateStore,
)
from soft_actuator_testing.domain.run_state import RunCompletion, RunState
from soft_actuator_testing.ui.demo import build_demo_controller


def _ready_controller():
    controller = build_demo_controller()
    controller.dispatch(ConnectDevices())
    controller.dispatch(EvaluateReadiness())
    assert controller.snapshot.run.lifecycle.state is RunState.READY
    return controller


def _running_controller():
    controller = _ready_controller()
    controller.dispatch(BeginRun())
    controller.dispatch(ConfirmRunStarted())
    assert controller.snapshot.run.lifecycle.state is RunState.RUNNING
    return controller


def test_aggregate_snapshot_is_immutable_qt_free_and_contains_every_workflow() -> None:
    snapshot = build_demo_controller().snapshot
    assert snapshot.workspace.is_selected
    assert snapshot.devices.controller is ConnectionStatus.DISCONNECTED
    assert snapshot.calibration.is_ready
    assert snapshot.geometry.is_ready
    assert snapshot.readiness.is_ready is False
    assert snapshot.run.lifecycle.state is RunState.DISCONNECTED
    assert snapshot.analysis.progress_percent == 0
    assert snapshot.settings.profile == "Operator"
    assert snapshot.notifications
    with pytest.raises(FrozenInstanceError):
        snapshot.revision = 99  # type: ignore[misc]


def test_state_store_has_one_current_snapshot_and_idempotent_disposal() -> None:
    store = StateStore("initial")
    seen: list[str] = []
    subscription = store.subscribe(seen.append, emit_current=True)
    store.publish("next")
    subscription.dispose()
    subscription.dispose()
    store.publish("stale")
    assert seen == ["initial", "next"]
    assert store.snapshot == "stale"


def test_command_and_state_propagation_covers_analysis_and_settings() -> None:
    controller = build_demo_controller()
    seen = []
    controller.store.subscribe(seen.append)
    controller.dispatch(ChooseAnalysisSource(Path("/recordings/run.mp4")))
    controller.dispatch(SetAnalysisMode(AnalysisMode.RECORDED_FILE))
    controller.dispatch(RunAnalysis())
    controller.dispatch(ApplySettings("Researcher", True))
    assert seen
    assert controller.snapshot.analysis.is_complete
    assert "Reviewed 6 frames" in controller.snapshot.analysis.review
    assert controller.snapshot.settings.profile == "Researcher"
    assert controller.snapshot.settings.compact_mode


def test_readiness_guidance_names_missing_item_and_next_action() -> None:
    controller = build_demo_controller()
    readiness = controller.snapshot.readiness
    assert not readiness.is_ready
    assert "Connect the controller" in readiness.missing
    assert readiness.guidance.startswith("Blocked")
    assert readiness.next_action == "Connect the controller."
    assert "Controller: disconnected" in readiness.diagnostics
    controller.dispatch(ConnectDevices())
    assert controller.snapshot.readiness.is_ready
    assert controller.snapshot.run.can_start
    assert controller.snapshot.readiness.next_action.startswith("Go to Live Run")


def test_disconnect_during_run_finalizes_faulted_then_reconnects_ready() -> None:
    controller = _running_controller()
    controller.dispatch(DisconnectDevices("Camera link lost."))
    snapshot = controller.snapshot
    assert snapshot.devices.controller is ConnectionStatus.DISCONNECTED
    assert snapshot.run.lifecycle.state is RunState.DISCONNECTED
    assert "faulted" in snapshot.run.outcome_text
    assert snapshot.faults[0].code == "device-disconnected"
    assert not snapshot.readiness.is_ready

    controller.dispatch(ConnectDevices())
    snapshot = controller.snapshot
    assert snapshot.devices.all_connected
    assert snapshot.run.lifecycle.state is RunState.READY
    assert snapshot.faults == ()


def test_global_stop_while_starting_aborts_and_duplicate_is_idempotent() -> None:
    controller = _ready_controller()
    controller.dispatch(BeginRun())
    assert controller.snapshot.run.lifecycle.state is RunState.STARTING
    first = controller.dispatch(GlobalStop())
    first_snapshot = controller.snapshot.run.lifecycle
    second = controller.dispatch(GlobalStop())
    assert first.accepted and not first.idempotent
    assert first_snapshot.state is RunState.COMPLETED
    assert first_snapshot.completion is RunCompletion.ABORTED
    assert second.accepted and second.idempotent
    assert controller.snapshot.run.lifecycle == first_snapshot


def test_global_stop_while_stopping_overrides_no_outcome_and_aborts() -> None:
    controller = _running_controller()
    controller.dispatch(RequestRunStop())
    assert controller.snapshot.run.lifecycle.state is RunState.STOPPING
    result = controller.dispatch(GlobalStop())
    assert result.accepted
    assert controller.snapshot.run.lifecycle.completion is RunCompletion.ABORTED
    assert "while stopping" in controller.snapshot.run.outcome_text


def test_global_stop_disconnected_is_acknowledged_without_inventing_state() -> None:
    controller = build_demo_controller()
    result = controller.dispatch(GlobalStop())
    assert result.accepted and result.idempotent
    assert controller.snapshot.run.lifecycle.state is RunState.DISCONNECTED
    assert controller.snapshot.run.lifecycle.completion is None


def test_ordinary_completion_is_clean_and_distinct_from_emergency_abort() -> None:
    clean = _running_controller()
    clean.dispatch(CompleteRun())
    assert clean.snapshot.run.lifecycle.completion is RunCompletion.CLEAN
    assert "cleanly" in clean.snapshot.run.outcome_text

    aborted = _running_controller()
    aborted.dispatch(GlobalStop())
    assert aborted.snapshot.run.lifecycle.completion is RunCompletion.ABORTED
    assert "Global STOP" in aborted.snapshot.run.outcome_text


def test_explicit_readiness_evaluation_rearms_a_completed_demo_run() -> None:
    controller = _running_controller()
    controller.dispatch(CompleteRun())
    assert controller.snapshot.run.lifecycle.state is RunState.COMPLETED
    controller.dispatch(EvaluateReadiness())
    assert controller.snapshot.run.lifecycle.state is RunState.READY
    assert controller.snapshot.run.can_start


def test_timeout_while_stopping_finalizes_faulted_without_safe_state_claim() -> None:
    controller = _running_controller()
    controller.dispatch(RequestRunStop())
    result = controller.dispatch(ReportRunTimeout())
    assert not result.accepted
    assert controller.snapshot.run.lifecycle.completion is RunCompletion.FAULTED
    assert controller.snapshot.faults[0].code == "run-timeout"
    assert any("does not claim a hardware safe state" in item for item in controller.snapshot.faults[0].diagnostics)


def test_fault_while_ready_uses_lifecycle_fault_and_blocks_readiness() -> None:
    controller = _ready_controller()
    controller.dispatch(ReportRunFault("Controller rejected command.", code="controller-rejected"))
    assert controller.snapshot.run.lifecycle.state is RunState.FAULT
    assert not controller.snapshot.readiness.is_ready
    assert controller.snapshot.faults[0].code == "controller-rejected"
