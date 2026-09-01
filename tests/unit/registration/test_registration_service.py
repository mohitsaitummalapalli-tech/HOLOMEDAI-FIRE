# -*- coding: utf-8 -*-
"""Unit and Integration Tests for RegistrationService and Dispatcher Routes."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.planning.models import SurgicalPlanDefinition
from holomed.planning.service import PlanningService
from holomed.protocol.builders import create_command, create_query
from holomed.registration.constants import MAX_ACTIVE_REGISTRATIONS
from holomed.registration.exceptions import (
    RegistrationAccuracyError,
    RegistrationCapacityError,
    RegistrationLifecycleError,
    RegistrationPlanMismatchError,
    RegistrationVerificationError,
)
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
    RegistrationState,
)
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState


def test_registration_service_lifecycle_and_resources(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
) -> None:
    """Verify RegistrationService lifecycle and 4 structural handles."""
    srv = RegistrationService(secret_filter=secret_filter)
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


def test_registration_service_submit_and_solve_success(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    planning_service: PlanningService,
    sample_locked_plan: SurgicalPlanDefinition,
    sample_cloud: FiducialCloud,
) -> None:
    """Verify full submit -> solve flow results in state SOLVED."""
    srv = RegistrationService(planning_service=planning_service, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    # 1. Submit fiducials
    rec_draft = srv.submit_fiducials("sess_reg_01", sample_locked_plan.plan_id, sample_cloud)
    assert rec_draft.state == RegistrationState.DRAFT
    assert srv.is_registration_verified("sess_reg_01") is False

    # 2. Solve registration
    rec_solved = srv.solve_registration("sess_reg_01", sample_locked_plan.plan_id)
    assert rec_solved.state == RegistrationState.SOLVED
    assert rec_solved.transform is not None
    # State SOLVED is not yet VERIFIED! (Cannot navigate yet)
    assert srv.is_registration_verified("sess_reg_01") is False

    srv.stop()


def test_registration_service_solve_fails_if_fre_exceeds_threshold(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    planning_service: PlanningService,
    sample_locked_plan: SurgicalPlanDefinition,
) -> None:
    """Verify solver transitions to FAILED and raises error if FRE > 1.5 mm (D312)."""
    srv = RegistrationService(planning_service=planning_service, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    # Create 4 points with a large perturbation on one point so FRE > 1.5 mm
    noisy_pairs = (
        FiducialPointPair("f1", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), "p1"),
        FiducialPointPair("f2", (50.0, 0.0, 0.0), (50.0, 0.0, 0.0), "p2"),
        FiducialPointPair("f3", (0.0, 50.0, 0.0), (0.0, 50.0, 0.0), "p3"),
        FiducialPointPair("f4", (50.0, 50.0, 0.0), (50.0, 50.0, 10.0), "p4"),
    )
    cloud = FiducialCloud(pairs=noisy_pairs)

    srv.submit_fiducials("sess_noisy", sample_locked_plan.plan_id, cloud)

    with pytest.raises(RegistrationAccuracyError) as exc:
        srv.solve_registration("sess_noisy", sample_locked_plan.plan_id)
    assert "exceeds limit" in str(exc.value)

    reg = srv.get_registration("sess_noisy")
    assert reg is not None
    assert reg.state == RegistrationState.FAILED

    srv.stop()


def test_registration_service_verify_and_drift_detection(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    planning_service: PlanningService,
    sample_locked_plan: SurgicalPlanDefinition,
    sample_cloud: FiducialCloud,
) -> None:
    """Verify registration verification and drift detection (D314)."""
    srv = RegistrationService(planning_service=planning_service, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    srv.submit_fiducials("sess_drift", sample_locked_plan.plan_id, sample_cloud)
    srv.solve_registration("sess_drift", sample_locked_plan.plan_id)

    # 1. Successful verification with measured checkpoint matching transform (t is +10 X, +20 Y, +30 Z)
    # Plan checkpoint at (0, 0, 0) transforms to (10, 20, 30)
    snap = srv.verify_registration(
        session_id="sess_drift",
        operator_id="surgeon_01",
        checkpoint_plan_mm=(0.0, 0.0, 0.0),
        checkpoint_measured_mm=(10.2, 20.1, 30.1),  # Drift = ~0.24 mm (within 1.5 mm limit)
    )
    assert snap.passed is True
    assert srv.is_registration_verified("sess_drift") is True

    # 2. Drift detected: physical checkpoint moved by 3 mm -> INVALIDATED!
    with pytest.raises(RegistrationVerificationError) as exc:
        srv.verify_registration(
            session_id="sess_drift",
            operator_id="surgeon_01",
            checkpoint_plan_mm=(0.0, 0.0, 0.0),
            checkpoint_measured_mm=(13.0, 20.0, 30.0),  # 3 mm drift!
        )
    assert "drift" in str(exc.value)

    reg = srv.get_registration("sess_drift")
    assert reg is not None
    assert reg.state == RegistrationState.INVALIDATED
    assert reg.transform is None  # Transform dropped!
    assert srv.is_registration_verified("sess_drift") is False

    srv.stop()


def test_registration_service_requires_locked_plan(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    planning_service: PlanningService,
    sample_cloud: FiducialCloud,
) -> None:
    """Verify registration rejects unlocked plan (D315)."""
    srv = RegistrationService(planning_service=planning_service, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    # Submit an unlocked plan into planning service
    from holomed.planning.models import PatientCaseContext, SurgicalLaterality, SurgicalPlanDefinition
    ctx = PatientCaseContext("c_unlocked", "c" * 64, "PROC", SurgicalLaterality.LEFT, "dr", "2026-09-01T00:00:00Z")
    unlocked_plan = SurgicalPlanDefinition(
        plan_id="plan_unlocked_01",
        version="1.0",
        case_context=ctx,
        trajectories=planning_service.get_plan("plan_reg_01").trajectories,
        exclusion_zones=(),
        is_locked=False,
    )
    planning_service.submit_plan(unlocked_plan, "sess_u")

    with pytest.raises(RegistrationPlanMismatchError):
        srv.submit_fiducials("sess_reg_unlocked", "plan_unlocked_01", sample_cloud)

    srv.stop()


def test_registration_dispatcher_routes(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    planning_service: PlanningService,
    sample_locked_plan: SurgicalPlanDefinition,
) -> None:
    """Verify registration.submit, solve, get, verify dispatcher routes."""
    srv = RegistrationService(
        dispatcher=message_dispatcher,
        planning_service=planning_service,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    # 1. registration.submit
    submit_payload = {
        "session_id": "sess_disp",
        "plan_id": sample_locked_plan.plan_id,
        "fiducials": [
            {
                "fiducial_id": "f1",
                "planned_point_mm": [0.0, 0.0, 0.0],
                "measured_point_mm": [10.0, 20.0, 30.0],
                "label": "l1",
            },
            {
                "fiducial_id": "f2",
                "planned_point_mm": [50.0, 0.0, 0.0],
                "measured_point_mm": [60.0, 20.0, 30.0],
                "label": "l2",
            },
            {
                "fiducial_id": "f3",
                "planned_point_mm": [0.0, 50.0, 0.0],
                "measured_point_mm": [10.0, 70.0, 30.0],
                "label": "l3",
            },
        ],
    }
    cmd_sub = create_command("registration.submit", "surgeon_console", payload=submit_payload)
    resp_sub = message_dispatcher.dispatch(cmd_sub)
    assert resp_sub.message_type.value == "RESPONSE"
    assert resp_sub.payload["point_count"] == 3

    # 2. registration.solve
    cmd_solve = create_command(
        "registration.solve",
        "surgeon_console",
        payload={"session_id": "sess_disp", "plan_id": sample_locked_plan.plan_id},
    )
    resp_solve = message_dispatcher.dispatch(cmd_solve)
    assert resp_solve.message_type.value == "RESPONSE"
    assert resp_solve.payload["state"] == "SOLVED"

    # 3. registration.get
    q_get = create_query("registration.get", "xr_client", payload={"session_id": "sess_disp"})
    resp_get = message_dispatcher.dispatch(q_get)
    assert resp_get.message_type.value == "RESPONSE"
    assert resp_get.payload["state"] == "SOLVED"
    assert "transform" in resp_get.payload

    # 4. registration.verify
    cmd_ver = create_command(
        "registration.verify",
        "surgeon_console",
        payload={
            "session_id": "sess_disp",
            "operator_id": "surgeon_console",
            "checkpoint_plan_mm": [0.0, 0.0, 0.0],
            "checkpoint_measured_mm": [10.0, 20.0, 30.0],
        },
    )
    resp_ver = message_dispatcher.dispatch(cmd_ver)
    assert resp_ver.message_type.value == "RESPONSE"
    assert resp_ver.payload["verified"] is True

    srv.stop()


def test_registration_service_reentrancy_guard(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    sample_locked_plan: SurgicalPlanDefinition,
    sample_cloud: FiducialCloud,
) -> None:
    """Verify transaction guard prevents reentrant calls."""
    srv = RegistrationService(secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    srv._in_transaction = True
    with pytest.raises(RegistrationLifecycleError):
        srv.submit_fiducials("s1", sample_locked_plan.plan_id, sample_cloud)

    with pytest.raises(RegistrationLifecycleError):
        srv.solve_registration("s1", sample_locked_plan.plan_id)

    with pytest.raises(RegistrationLifecycleError):
        srv.stop()

    srv._in_transaction = False
    srv.stop()
