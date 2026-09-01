# -*- coding: utf-8 -*-
"""Adversarial and End-to-End Test Matrix for M12 Planning Subsystem."""

from __future__ import annotations

from dataclasses import replace
import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.planning.exceptions import (
    PlanningCapacityError,
    PlanningLifecycleError,
    PlanningLockError,
    PlanningValidationError,
    PlanningVerificationError,
)
from holomed.planning.geometry import point_to_line_segment_distance
from holomed.planning.models import (
    MAX_ACTIVE_PLANS,
    PatientCaseContext,
    PlanVerificationRecord,
    SafetyExclusionZone,
    SurgicalLaterality,
    SurgicalPlanDefinition,
    TrajectoryPlan,
)
from holomed.planning.service import PlanningService
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus
from holomed.workflow.models import InterlockSeverity
from holomed.workflow.service import WorkflowService


def test_patient_hash_regex_variations(sample_case_context: PatientCaseContext) -> None:
    """Verify strict SHA-256 hex string requirements (lowercase 64-char hex)."""
    # 63 characters -> rejected
    with pytest.raises(PlanningValidationError):
        replace(sample_case_context, patient_hash="a" * 63)

    # 65 characters -> rejected
    with pytest.raises(PlanningValidationError):
        replace(sample_case_context, patient_hash="a" * 65)

    # Non-hex characters -> rejected
    with pytest.raises(PlanningValidationError):
        replace(sample_case_context, patient_hash="g" * 64)

    # Uppercase hex -> rejected (must be normalized lowercase)
    with pytest.raises(PlanningValidationError):
        replace(sample_case_context, patient_hash="A" * 64)


def test_case_id_regex_boundary_checks(sample_case_context: PatientCaseContext) -> None:
    """Verify case_id boundary lengths (64 allowed, 65 rejected)."""
    c64 = replace(sample_case_context, case_id="c" * 64)
    assert c64.case_id == "c" * 64

    with pytest.raises(PlanningValidationError):
        replace(sample_case_context, case_id="c" * 65)


def test_plan_id_regex_boundary_checks(sample_plan: SurgicalPlanDefinition) -> None:
    """Verify plan_id boundary lengths (64 allowed, 65 rejected)."""
    p64 = replace(sample_plan, plan_id="p" * 64)
    assert p64.plan_id == "p" * 64

    with pytest.raises(PlanningValidationError):
        replace(sample_plan, plan_id="p" * 65)


def test_trajectory_length_boundary_checks() -> None:
    """Verify minimum (5.0 mm) and maximum (500.0 mm) trajectory length boundaries."""
    # 4.99 mm -> rejected
    with pytest.raises(PlanningValidationError):
        TrajectoryPlan(
            trajectory_id="t_short",
            target_structure="bone",
            entry_point_mm=(0.0, 0.0, 0.0),
            target_point_mm=(0.0, 0.0, 4.99),
            max_lateral_deviation_mm=1.0,
            max_angular_deviation_deg=1.0,
            min_confidence=0.8,
            max_uncertainty=0.2,
        )

    # Exactly 5.0 mm -> accepted
    t5 = TrajectoryPlan(
        trajectory_id="t_5mm",
        target_structure="bone",
        entry_point_mm=(0.0, 0.0, 0.0),
        target_point_mm=(0.0, 0.0, 5.0),
        max_lateral_deviation_mm=1.0,
        max_angular_deviation_deg=1.0,
        min_confidence=0.8,
        max_uncertainty=0.2,
    )
    assert t5.trajectory_id == "t_5mm"

    # Exactly 500.0 mm -> accepted
    t500 = TrajectoryPlan(
        trajectory_id="t_500mm",
        target_structure="bone",
        entry_point_mm=(0.0, 0.0, 0.0),
        target_point_mm=(0.0, 0.0, 500.0),
        max_lateral_deviation_mm=1.0,
        max_angular_deviation_deg=1.0,
        min_confidence=0.8,
        max_uncertainty=0.2,
    )
    assert t500.trajectory_id == "t_500mm"

    # 500.01 mm -> rejected
    with pytest.raises(PlanningValidationError):
        TrajectoryPlan(
            trajectory_id="t_long",
            target_structure="bone",
            entry_point_mm=(0.0, 0.0, 0.0),
            target_point_mm=(0.0, 0.0, 500.01),
            max_lateral_deviation_mm=1.0,
            max_angular_deviation_deg=1.0,
            min_confidence=0.8,
            max_uncertainty=0.2,
        )


def test_trajectory_confidence_and_uncertainty_bounds() -> None:
    """Verify confidence and uncertainty must be in [0.0, 1.0]."""
    # min_confidence > 1.0 -> rejected
    with pytest.raises(PlanningValidationError):
        TrajectoryPlan(
            trajectory_id="t_conf",
            target_structure="bone",
            entry_point_mm=(0.0, 0.0, 0.0),
            target_point_mm=(0.0, 0.0, 10.0),
            max_lateral_deviation_mm=1.0,
            max_angular_deviation_deg=1.0,
            min_confidence=1.05,
            max_uncertainty=0.2,
        )

    # max_uncertainty < 0.0 -> rejected
    with pytest.raises(PlanningValidationError):
        TrajectoryPlan(
            trajectory_id="t_unc",
            target_structure="bone",
            entry_point_mm=(0.0, 0.0, 0.0),
            target_point_mm=(0.0, 0.0, 10.0),
            max_lateral_deviation_mm=1.0,
            max_angular_deviation_deg=1.0,
            min_confidence=0.8,
            max_uncertainty=-0.05,
        )


def test_exclusion_zone_bounds() -> None:
    """Verify exclusion zone radius and clearance must be positive finite floats."""
    # Zero radius -> rejected
    with pytest.raises(PlanningValidationError):
        SafetyExclusionZone(
            zone_id="z_zero",
            anatomical_structure="vessel",
            center_point_mm=(0.0, 0.0, 0.0),
            bounding_radius_mm=0.0,
            min_clearance_mm=2.0,
        )

    # Zero clearance -> rejected
    with pytest.raises(PlanningValidationError):
        SafetyExclusionZone(
            zone_id="z_zero_c",
            anatomical_structure="vessel",
            center_point_mm=(0.0, 0.0, 0.0),
            bounding_radius_mm=5.0,
            min_clearance_mm=0.0,
        )


def test_plan_definition_empty_trajectories_rejected(sample_case_context: PatientCaseContext) -> None:
    """Verify plan must have at least one trajectory."""
    with pytest.raises(PlanningValidationError):
        SurgicalPlanDefinition(
            plan_id="plan_no_traj",
            version="1.0",
            case_context=sample_case_context,
            trajectories=(),
            exclusion_zones=(),
        )


def test_plan_definition_invalid_semver_rejected(
    sample_case_context: PatientCaseContext,
    sample_trajectory: TrajectoryPlan,
) -> None:
    """Verify version must be valid semantic version."""
    with pytest.raises(PlanningValidationError):
        SurgicalPlanDefinition(
            plan_id="plan_bad_ver",
            version="alpha-v1",
            case_context=sample_case_context,
            trajectories=(sample_trajectory,),
            exclusion_zones=(),
        )


def test_service_plan_capacity_limit(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    sample_plan: SurgicalPlanDefinition,
) -> None:
    """Verify service rejects more than MAX_ACTIVE_PLANS (16) plans."""
    srv = PlanningService(secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    for i in range(MAX_ACTIVE_PLANS):
        p = replace(sample_plan, plan_id=f"plan_{i:02d}")
        srv.submit_plan(p, f"session_{i:02d}")

    # 17th plan -> PlanningCapacityError
    p_overflow = replace(sample_plan, plan_id="plan_overflow")
    with pytest.raises(PlanningCapacityError):
        srv.submit_plan(p_overflow, "session_overflow")

    srv.stop()


def test_plan_locking_idempotency(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    sample_plan: SurgicalPlanDefinition,
) -> None:
    """Verify locking an already locked plan is idempotent."""
    srv = PlanningService(secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    srv.submit_plan(sample_plan, "s1")
    l1 = srv.lock_plan(sample_plan.plan_id)
    assert l1.is_locked is True

    l2 = srv.lock_plan(sample_plan.plan_id)
    assert l2.is_locked is True

    srv.stop()


def test_plan_retrieval_helpers(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    sample_plan: SurgicalPlanDefinition,
) -> None:
    """Verify get_plan and get_plan_for_session helpers."""
    srv = PlanningService(secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    srv.submit_plan(sample_plan, "session_42")

    # Found by plan_id
    p = srv.get_plan(sample_plan.plan_id)
    assert p.plan_id == sample_plan.plan_id

    # Found by session_id
    p_sess = srv.get_plan_for_session("session_42")
    assert p_sess is not None
    assert p_sess.plan_id == sample_plan.plan_id

    # Unknown session -> None
    assert srv.get_plan_for_session("unknown_session") is None

    # Unknown plan_id -> PlanningValidationError
    with pytest.raises(PlanningValidationError):
        srv.get_plan("unknown_plan_id")

    srv.stop()


def test_service_uninitialized_and_stopped_health(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
) -> None:
    """Verify health transitions across lifecycle."""
    srv = PlanningService(secret_filter=secret_filter)
    assert srv.health().status == HealthStatus.UNHEALTHY

    srv.initialize(runtime_context)
    assert srv.health().status == HealthStatus.UNHEALTHY

    srv.start()
    assert srv.health().status == HealthStatus.HEALTHY

    srv.stop()
    assert srv.health().status == HealthStatus.UNHEALTHY


def test_e2e_plan_creation_locking_checkpoint_registration_and_workflow(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    sample_plan: SurgicalPlanDefinition,
) -> None:
    """Verify complete end-to-end flow from planning to workflow interlock validation."""
    wf = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    wf.initialize(runtime_context)
    wf.start()

    plan_srv = PlanningService(dispatcher=message_dispatcher, workflow_service=wf, secret_filter=secret_filter)
    plan_srv.initialize(runtime_context)
    plan_srv.start()

    # 1. Submit and lock plan
    plan_srv.submit_plan(sample_plan, "sess_e2e")
    plan_srv.lock_plan(sample_plan.plan_id)

    # 2. Verify plan
    record = plan_srv.verify_plan(
        plan_id=sample_plan.plan_id,
        session_id="sess_e2e",
        operator_id="operator_01",
        patient_hash=sample_plan.case_context.patient_hash,
        procedure_code="THA_POSTERIOR_APPROACH",
        laterality=SurgicalLaterality.RIGHT,
    )
    assert record.verified is True

    # 3. Start workflow session
    sess = wf.start_workflow("sess_e2e")
    assert sess.session_id == "sess_e2e"

    # 4. Evaluate exclusion zone breach -> trips interlock!
    interlock = wf.evaluate_checkpoint(
        checkpoint_id="chk_zone_zone_sciatic_nerve",
        session_id="sess_e2e",
        measured_confidence=0.95,
        measured_uncertainty=0.05,
        measured_deviation_mm=6.0,  # exceeds 5.0 mm min clearance -> unsafe!
    )
    assert interlock.status is False  # Tripped / Unsafe

    plan_srv.stop()
    wf.stop()


def test_plan_verification_record_immutability(sample_plan: SurgicalPlanDefinition) -> None:
    """Verify PlanVerificationRecord is frozen dataclass."""
    rec = PlanVerificationRecord(
        verification_id="v1",
        plan_id=sample_plan.plan_id,
        session_id="s1",
        epoch_id=1,
        verified=True,
        patient_identity_matched=True,
        procedure_matched=True,
        laterality_matched=True,
        verifier_operator_id="op1",
        timestamp_utc="2026-09-01T12:00:00Z",
        details={},
    )
    with pytest.raises(AttributeError):
        rec.verified = False  # type: ignore


def test_trajectory_angular_deviation_negative_or_zero_rejected() -> None:
    """Verify max_angular_deviation_deg must be strictly positive."""
    with pytest.raises(PlanningValidationError):
        TrajectoryPlan(
            trajectory_id="t_deg",
            target_structure="femur",
            entry_point_mm=(0.0, 0.0, 0.0),
            target_point_mm=(0.0, 0.0, 20.0),
            max_lateral_deviation_mm=1.0,
            max_angular_deviation_deg=0.0,
            min_confidence=0.8,
            max_uncertainty=0.2,
        )


def test_point_to_line_segment_degenerate_zero_length_line() -> None:
    """Verify distance computation when segment start and end are coincident."""
    dist = point_to_line_segment_distance((3.0, 4.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    assert dist == 5.0


def test_planning_service_stopped_state_operations_rejected(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    sample_plan: SurgicalPlanDefinition,
) -> None:
    """Verify operations in STOPPED state raise PlanningLifecycleError."""
    srv = PlanningService(secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()
    srv.stop()

    with pytest.raises(PlanningLifecycleError):
        srv.submit_plan(sample_plan, "s1")

    with pytest.raises(PlanningLifecycleError):
        srv.lock_plan("p1")


def test_dispatcher_invalid_args_return_error(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify missing arguments in command return protocol ERROR response."""
    plan_srv = PlanningService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    plan_srv.initialize(runtime_context)
    message_dispatcher.start()
    plan_srv.start()

    # submit without plan payload
    cmd = create_command("planning.submit", "test", payload={})
    resp = message_dispatcher.dispatch(cmd)
    assert resp.message_type.value == "ERROR"
    assert resp.payload["error_code"] == "ERR_INVALID_ARGS"

    plan_srv.stop()


def test_plan_verification_with_case_sensitive_patient_hash_normalized(
    sample_plan: SurgicalPlanDefinition,
) -> None:
    """Verify verification engine normalizes patient hash casing during comparison."""
    locked_plan = replace(sample_plan, is_locked=True)
    srv = PlanningService()
    srv._epoch_id = 1
    srv._state = getattr(srv, "_state").STARTED
    srv._plans[locked_plan.plan_id] = locked_plan

    # Report hash in uppercase
    rec = srv.verify_plan(
        plan_id=locked_plan.plan_id,
        session_id="s1",
        operator_id="op1",
        patient_hash=locked_plan.case_context.patient_hash.upper(),
        procedure_code="THA_POSTERIOR_APPROACH",
        laterality=SurgicalLaterality.RIGHT,
    )
    assert rec.verified is True
