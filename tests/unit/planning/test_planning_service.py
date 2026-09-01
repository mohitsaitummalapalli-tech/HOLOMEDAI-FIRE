# -*- coding: utf-8 -*-
"""Unit and Integration Tests for PlanningService and Dispatcher Routes."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.planning.exceptions import (
    PlanningLifecycleError,
    PlanningLockError,
)
from holomed.planning.models import (
    SurgicalLaterality,
    SurgicalPlanDefinition,
)
from holomed.planning.service import PlanningService
from holomed.protocol.builders import (
    create_command,
    create_query,
)
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState
from holomed.workflow.service import WorkflowService


def test_planning_service_lifecycle_and_resources(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
) -> None:
    """Verify PlanningService lifecycle states and structural handles (4 handles)."""
    srv = PlanningService(secret_filter=secret_filter)
    assert srv.state == ServiceState.UNINITIALIZED
    assert srv.health().status == HealthStatus.UNHEALTHY

    srv.initialize(runtime_context)
    assert srv.state == ServiceState.INITIALIZED
    assert len(srv.resources.outstanding_handles) == 4

    srv.start()
    assert srv.state == ServiceState.STARTED
    assert srv.health().status == HealthStatus.HEALTHY

    srv.stop()
    assert srv.state == ServiceState.STOPPED
    assert len(srv.resources.outstanding_handles) == 0


def test_planning_service_submit_and_lock_immutability(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    sample_plan: SurgicalPlanDefinition,
) -> None:
    """Verify plan locking makes plan permanently immutable (D299, D304)."""
    srv = PlanningService(secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    # 1. Submit draft plan
    submitted = srv.submit_plan(sample_plan, "sess_01")
    assert submitted.plan_id == sample_plan.plan_id
    assert srv.active_plans_count == 1

    # 2. Lock plan
    locked = srv.lock_plan(sample_plan.plan_id)
    assert locked.is_locked is True

    # 3. Attempt to re-submit locked plan -> PlanningLockError
    with pytest.raises(PlanningLockError):
        srv.submit_plan(sample_plan, "sess_01")

    srv.stop()


def test_planning_service_integration_with_workflow_checkpoints(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    sample_plan: SurgicalPlanDefinition,
) -> None:
    """Verify locking a plan registers derived checkpoints into WorkflowService (D296)."""
    wf_srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    wf_srv.initialize(runtime_context)
    wf_srv.start()

    plan_srv = PlanningService(
        dispatcher=message_dispatcher,
        workflow_service=wf_srv,
        secret_filter=secret_filter,
    )
    plan_srv.initialize(runtime_context)
    plan_srv.start()

    plan_srv.submit_plan(sample_plan, "sess_wf_01")
    plan_srv.lock_plan(sample_plan.plan_id)

    # Check that derived checkpoints are registered in WorkflowService
    # Evaluating derived trajectory checkpoint: "chk_traj_traj_cup_reaming"
    interlock = wf_srv.evaluate_checkpoint(
        checkpoint_id="chk_traj_traj_cup_reaming",
        session_id="sess_wf_01",
        measured_confidence=0.90,
        measured_uncertainty=0.10,
        measured_deviation_mm=1.5,  # within 2.0 mm tolerance!
    )
    assert interlock.status is True  # Clear/Safe

    plan_srv.stop()
    wf_srv.stop()


def test_planning_service_dispatcher_routes(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    sample_plan: SurgicalPlanDefinition,
) -> None:
    """Verify planning.submit, planning.get, planning.lock, planning.verify via dispatcher."""
    plan_srv = PlanningService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    plan_srv.initialize(runtime_context)
    message_dispatcher.start()
    plan_srv.start()

    # 1. planning.submit
    submit_payload = {
        "session_id": "sess_disp_01",
        "plan": {
            "plan_id": "plan_disp_01",
            "version": "1.0",
            "case_context": {
                "case_id": "case_d01",
                "patient_hash": "a" * 64,
                "procedure_code": "HIP_REPLACEMENT",
                "laterality": "LEFT",
                "primary_surgeon_id": "surgeon_1",
                "scheduled_date_utc": "2026-09-01T10:00:00Z",
            },
            "trajectories": [
                {
                    "trajectory_id": "t1",
                    "target_structure": "acetabulum",
                    "entry_point_mm": [0.0, 0.0, 0.0],
                    "target_point_mm": [0.0, 0.0, 20.0],
                    "max_lateral_deviation_mm": 2.0,
                    "max_angular_deviation_deg": 3.0,
                    "min_confidence": 0.8,
                    "max_uncertainty": 0.2,
                }
            ],
            "exclusion_zones": [],
        },
    }
    cmd_sub = create_command("planning.submit", "surgeon_console", payload=submit_payload)
    resp_sub = message_dispatcher.dispatch(cmd_sub)
    assert resp_sub.message_type.value == "RESPONSE"
    assert resp_sub.payload["plan_id"] == "plan_disp_01"

    # 2. planning.get
    q_get = create_query("planning.get", "xr_client", payload={"plan_id": "plan_disp_01"})
    resp_get = message_dispatcher.dispatch(q_get)
    assert resp_get.message_type.value == "RESPONSE"
    assert resp_get.payload["case_id"] == "case_d01"
    assert resp_get.payload["is_locked"] is False

    # 3. planning.lock
    cmd_lock = create_command("planning.lock", "surgeon_console", payload={"plan_id": "plan_disp_01"})
    resp_lock = message_dispatcher.dispatch(cmd_lock)
    assert resp_lock.message_type.value == "RESPONSE"
    assert resp_lock.payload["is_locked"] is True

    # 4. planning.verify
    verify_payload = {
        "plan_id": "plan_disp_01",
        "session_id": "sess_disp_01",
        "operator_id": "surgeon_1",
        "patient_hash": "a" * 64,
        "procedure_code": "HIP_REPLACEMENT",
        "laterality": "LEFT",
    }
    cmd_ver = create_command("planning.verify", "surgeon_console", payload=verify_payload)
    resp_ver = message_dispatcher.dispatch(cmd_ver)
    assert resp_ver.message_type.value == "RESPONSE"
    assert resp_ver.payload["verified"] is True

    plan_srv.stop()


def test_reentrancy_guard_in_planning_service(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    sample_plan: SurgicalPlanDefinition,
) -> None:
    """Verify transaction guard prevents reentrant calls."""
    srv = PlanningService(secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    srv._in_transaction = True
    with pytest.raises(PlanningLifecycleError):
        srv.submit_plan(sample_plan, "s1")

    with pytest.raises(PlanningLifecycleError):
        srv.lock_plan("p1")

    with pytest.raises(PlanningLifecycleError):
        srv.stop()

    srv._in_transaction = False
    srv.stop()
