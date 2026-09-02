# -*- coding: utf-8 -*-
"""Adversarial and End-to-End Test Matrix for M13 Registration Subsystem."""

from __future__ import annotations

from dataclasses import replace
import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.exceptions import UnroutableMessageError
from holomed.execution._capability import _create_execution_capability
from holomed.planning.models import SurgicalPlanDefinition
from holomed.planning.service import PlanningService
from holomed.protocol.builders import create_command, create_query
from holomed.registration.constants import MAX_ACTIVE_REGISTRATIONS
from holomed.registration.exceptions import (
    RegistrationCapacityError,
    RegistrationDegeneracyError,
    RegistrationValidationError,
    RegistrationVerificationError,
)
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
    RegistrationState,
    RigidRegistrationTransform3D,
)
from holomed.registration.service import RegistrationService
from holomed.registration.solver import HornRigidRegistrationSolver
from holomed.registration.transforms import (
    compose_transforms,
    transform_point,
    transform_trajectory,
)
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter


def _make_reg_cap(srv: RegistrationService, session_id: str, seq: int = 1, action: str = "REGISTRATION_ALIGNMENT"):
    return _create_execution_capability(
        service_instance_id=id(srv),
        session_id=session_id,
        action=action,
        sequence_number=seq,
    )


def test_repeated_identical_coordinates_rejected() -> None:
    """Verify identical coordinates in cloud are rejected by minimum spacing check."""
    p1 = FiducialPointPair("f1", (10.0, 10.0, 10.0), (10.0, 10.0, 10.0), "p1")
    p2 = FiducialPointPair("f2", (10.0, 10.0, 10.0), (50.0, 50.0, 50.0), "p2")
    p3 = FiducialPointPair("f3", (0.0, 50.0, 0.0), (0.0, 50.0, 0.0), "p3")

    with pytest.raises(RegistrationValidationError):
        FiducialCloud(pairs=(p1, p2, p3))


def test_nearly_collinear_points_rejected() -> None:
    """Verify points with triangle area < 10 mm^2 are rejected as degenerate (D313)."""
    # p1 at (0,0,0), p2 at (100,0,0), p3 at (50, 0.05, 0) -> Area = 2.5 mm^2 < 10.0 mm^2
    p1 = FiducialPointPair("f1", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), "p1")
    p2 = FiducialPointPair("f2", (100.0, 0.0, 0.0), (100.0, 0.0, 0.0), "p2")
    p3 = FiducialPointPair("f3", (50.0, 0.05, 0.0), (50.0, 0.05, 0.0), "p3")

    with pytest.raises(RegistrationDegeneracyError):
        FiducialCloud(pairs=(p1, p2, p3))


def test_extreme_but_valid_coordinates_boundary() -> None:
    """Verify coordinates at exactly +/- 2000 mm are accepted."""
    p1 = FiducialPointPair("f1", (-2000.0, 0.0, 0.0), (0.0, 0.0, 0.0), "p1")
    p2 = FiducialPointPair("f2", (2000.0, 0.0, 0.0), (2000.0, 0.0, 0.0), "p2")
    p3 = FiducialPointPair("f3", (0.0, 2000.0, 0.0), (0.0, 2000.0, 0.0), "p3")
    cloud = FiducialCloud(pairs=(p1, p2, p3))
    assert len(cloud.pairs) == 3


def test_determinant_drift_protection() -> None:
    """Verify transform rejects determinant deviation from +1.0."""
    with pytest.raises(RegistrationValidationError) as exc:
        RigidRegistrationTransform3D(
            rotation_matrix=((1.01, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            translation_vector_mm=(0.0, 0.0, 0.0),
            source_frame="plan_frame",
            target_frame="patient_tracker_frame",
            epoch_id=1,
            created_at_utc="2026-09-01T12:00:00Z",
        )
    assert "orthonormal" in str(exc.value) or "determinant" in str(exc.value)


def test_orthonormality_drift_protection() -> None:
    """Verify transform rejects non-orthogonal rows."""
    with pytest.raises(RegistrationValidationError):
        RigidRegistrationTransform3D(
            rotation_matrix=((1.0, 0.1, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            translation_vector_mm=(0.0, 0.0, 0.0),
            source_frame="plan_frame",
            target_frame="patient_tracker_frame",
            epoch_id=1,
            created_at_utc="2026-09-01T12:00:00Z",
        )


def test_transform_composition_associativity() -> None:
    """Verify transform composition is associative: T3(T2(T1(p))) == (T3 * T2 * T1)(p)."""
    t1 = RigidRegistrationTransform3D(
        rotation_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        translation_vector_mm=(5.0, 0.0, 0.0),
        source_frame="F0",
        target_frame="F1",
        epoch_id=1,
        created_at_utc="2026-09-01T12:00:00Z",
    )
    t2 = RigidRegistrationTransform3D(
        rotation_matrix=((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        translation_vector_mm=(0.0, 10.0, 0.0),
        source_frame="F1",
        target_frame="F2",
        epoch_id=1,
        created_at_utc="2026-09-01T12:00:00Z",
    )
    t3 = RigidRegistrationTransform3D(
        rotation_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        translation_vector_mm=(0.0, 0.0, 15.0),
        source_frame="F2",
        target_frame="F3",
        epoch_id=1,
        created_at_utc="2026-09-01T12:00:00Z",
    )

    t12_3 = compose_transforms(compose_transforms(t1, t2), t3)
    t1_23 = compose_transforms(t1, compose_transforms(t2, t3))

    p = (1.0, 2.0, 3.0)
    p1 = transform_point(t12_3, p)
    p2 = transform_point(t1_23, p)

    for i in range(3):
        assert pytest.approx(p1[i], abs=1e-5) == p2[i]


def test_replacing_fiducials_resets_state_to_draft(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    planning_service: PlanningService,
    sample_locked_plan: SurgicalPlanDefinition,
    sample_cloud: FiducialCloud,
) -> None:
    """Verify re-submitting fiducials after SOLVED resets state to DRAFT."""
    srv = RegistrationService(planning_service=planning_service, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    cap = _make_reg_cap(srv, "s1", seq=1)
    srv.submit_fiducials("s1", sample_locked_plan.plan_id, sample_cloud, capability=cap, sequence_number=1)
    srv.solve_registration("s1", sample_locked_plan.plan_id, capability=cap, sequence_number=1)
    assert srv.get_registration("s1").state == RegistrationState.SOLVED

    # Re-submit fiducials
    srv.submit_fiducials("s1", sample_locked_plan.plan_id, sample_cloud, capability=cap, sequence_number=1)
    assert srv.get_registration("s1").state == RegistrationState.DRAFT

    cap.invalidate()
    srv.stop()


def test_capacity_exhaustion_max_active_registrations(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    planning_service: PlanningService,
    sample_locked_plan: SurgicalPlanDefinition,
    sample_cloud: FiducialCloud,
) -> None:
    """Verify service rejects registrations beyond MAX_ACTIVE_REGISTRATIONS (16)."""
    srv = RegistrationService(planning_service=planning_service, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    for i in range(MAX_ACTIVE_REGISTRATIONS):
        sid = f"sess_{i:02d}"
        cap = _make_reg_cap(srv, sid, seq=1)
        srv.submit_fiducials(sid, sample_locked_plan.plan_id, sample_cloud, capability=cap, sequence_number=1)
        cap.invalidate()

    cap_overflow = _make_reg_cap(srv, "sess_overflow", seq=1)
    with pytest.raises(RegistrationCapacityError):
        srv.submit_fiducials("sess_overflow", sample_locked_plan.plan_id, sample_cloud, capability=cap_overflow, sequence_number=1)
    cap_overflow.invalidate()

    srv.stop()


def test_e2e_complete_registration_and_trajectory_transformation(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    planning_service: PlanningService,
    sample_locked_plan: SurgicalPlanDefinition,
    sample_cloud: FiducialCloud,
) -> None:
    """Verify complete end-to-end flow: fiducials -> solve -> verify -> transform trajectory."""
    srv = RegistrationService(planning_service=planning_service, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    cap = _make_reg_cap(srv, "sess_e2e", seq=1)

    # 1. Submit and solve
    srv.submit_fiducials("sess_e2e", sample_locked_plan.plan_id, sample_cloud, capability=cap, sequence_number=1)
    rec_solved = srv.solve_registration("sess_e2e", sample_locked_plan.plan_id, capability=cap, sequence_number=1)
    assert rec_solved.state == RegistrationState.SOLVED
    assert rec_solved.transform is not None

    # 2. Verify
    snap = srv.verify_registration(
        session_id="sess_e2e",
        operator_id="operator_01",
        checkpoint_plan_mm=(0.0, 0.0, 0.0),
        checkpoint_measured_mm=(10.0, 20.0, 30.0),
        capability=cap,
        sequence_number=1,
    )
    assert snap.passed is True
    assert srv.is_registration_verified("sess_e2e") is True

    # 3. Transform M12 TrajectoryPlan using verified transform
    traj = sample_locked_plan.trajectories[0]
    traj_physical = transform_trajectory(rec_solved.transform, traj)

    assert pytest.approx(traj_physical.entry_point_mm[0], abs=1e-4) == 20.0
    assert pytest.approx(traj_physical.entry_point_mm[1], abs=1e-4) == 30.0
    assert pytest.approx(traj_physical.entry_point_mm[2], abs=1e-4) == 40.0

    cap.invalidate()
    srv.stop()


def test_epoch_and_session_binding(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    planning_service: PlanningService,
    sample_locked_plan: SurgicalPlanDefinition,
    sample_cloud: FiducialCloud,
) -> None:
    """Verify registration binds strictly to active session and epoch."""
    srv = RegistrationService(planning_service=planning_service, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    cap = _make_reg_cap(srv, "sess_epoch", seq=1)
    rec = srv.submit_fiducials("sess_epoch", sample_locked_plan.plan_id, sample_cloud, capability=cap, sequence_number=1)
    assert rec.epoch_id == runtime_context.epoch_id
    assert rec.session_id == "sess_epoch"
    cap.invalidate()

    cap_unk = _make_reg_cap(srv, "unknown_sess", seq=1)
    with pytest.raises(RegistrationValidationError):
        srv.verify_registration("unknown_sess", "op", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), capability=cap_unk, sequence_number=1)
    cap_unk.invalidate()

    srv.stop()


def test_secret_filter_redaction_on_registration_errors(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    message_dispatcher: MessageDispatcher,
) -> None:
    """Verify sensitive tokens are redacted in registration error responses."""
    srv = RegistrationService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    # Query for non-existent session containing a secret token
    q = create_query(
        "registration.get",
        "surgeon_console",
        payload={"session_id": "TOP_SECRET_PATIENT_TOKEN"},
    )
    resp = message_dispatcher.dispatch(q)
    assert resp.message_type.value == "ERROR"
    assert "TOP_SECRET_PATIENT_TOKEN" not in resp.payload["error_message"]

    srv.stop()


def test_fail_closed_behavior_when_registration_unverified(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    planning_service: PlanningService,
    sample_locked_plan: SurgicalPlanDefinition,
    sample_cloud: FiducialCloud,
) -> None:
    """Verify that is_registration_verified returns False until explicit verification passes."""
    srv = RegistrationService(planning_service=planning_service, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    cap = _make_reg_cap(srv, "sess_gate", seq=1)
    srv.submit_fiducials("sess_gate", sample_locked_plan.plan_id, sample_cloud, capability=cap, sequence_number=1)
    assert srv.is_registration_verified("sess_gate") is False

    srv.solve_registration("sess_gate", sample_locked_plan.plan_id, capability=cap, sequence_number=1)
    assert srv.is_registration_verified("sess_gate") is False

    srv.verify_registration("sess_gate", "op", (0.0, 0.0, 0.0), (10.0, 20.0, 30.0), capability=cap, sequence_number=1)
    assert srv.is_registration_verified("sess_gate") is True

    cap.invalidate()
    srv.stop()


def test_negative_coordinates_preservation(sample_locked_plan: SurgicalPlanDefinition) -> None:
    """Verify negative coordinate quadrants are correctly handled in registration solver."""
    pairs = (
        FiducialPointPair("f1", (-100.0, -100.0, -100.0), (0.0, 0.0, 0.0), "p1"),
        FiducialPointPair("f2", (-50.0, -100.0, -100.0), (50.0, 0.0, 0.0), "p2"),
        FiducialPointPair("f3", (-100.0, -50.0, -100.0), (0.0, 50.0, 0.0), "p3"),
    )
    cloud = FiducialCloud(pairs=pairs)
    t = HornRigidRegistrationSolver.solve(cloud)
    assert pytest.approx(t.translation_vector_mm[0], abs=1e-4) == 100.0
    assert pytest.approx(t.translation_vector_mm[1], abs=1e-4) == 100.0
    assert pytest.approx(t.translation_vector_mm[2], abs=1e-4) == 100.0


def test_verify_command_unroutable(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
) -> None:
    """Verify registration.verify is unroutable on dispatcher in M23."""
    srv = RegistrationService(dispatcher=message_dispatcher)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    cmd = create_command("registration.verify", "console", payload={"session_id": "s1"})
    with pytest.raises(UnroutableMessageError):
        message_dispatcher.dispatch(cmd)

    srv.stop()


def test_submit_command_unroutable(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
) -> None:
    """Verify registration.submit is unroutable on dispatcher in M23."""
    srv = RegistrationService(dispatcher=message_dispatcher)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    cmd = create_command("registration.submit", "console", payload={"session_id": "s1"})
    with pytest.raises(UnroutableMessageError):
        message_dispatcher.dispatch(cmd)

    srv.stop()
