# -*- coding: utf-8 -*-
"""Milestone 36 (M36) Hostile Audit & Verification Test Suite.

Verifies end-to-end surgical plan ownership, anti-oracle resolution,
strict mathematical trajectory integrity tolerances, fail-closed subsystem checks,
and durable session teardown isolation across Planning, Registration, Navigation,
Recovery, and Clinical Execution Gateway services.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.execution.exceptions import ExecutionLifecycleError
from holomed.execution.models import (
    ExecutionStatus,
    RecoveryReorientationExecutionRequest,
    RegistrationExecutionRequest,
    TrajectoryBindingExecutionRequest,
)
from holomed.execution.service import (
    ClinicalExecutionGatewayService,
    _create_execution_capability,
)
from holomed.navigation.constants import DEFAULT_PATIENT_TRACKER_FRAME
from holomed.navigation.exceptions import (
    NavigationAuthorizationError,
    NavigationRegistrationMismatchError,
)
from holomed.navigation.models import NavigationState
from holomed.navigation.service import NavigationService
from holomed.planning.exceptions import (
    PlanningAuthorizationError,
    PlanningLockError,
    PlanningValidationError,
)
from holomed.planning.models import (
    TRAJECTORY_ANGULAR_TOLERANCE_DEG,
    TRAJECTORY_CONFIDENCE_TOLERANCE,
    TRAJECTORY_LATERAL_TOLERANCE_MM,
    TRAJECTORY_POINT_TOLERANCE_MM,
    TRAJECTORY_UNCERTAINTY_TOLERANCE,
    PatientCaseContext,
    SurgicalLaterality,
    SurgicalPlanDefinition,
    TrajectoryPlan,
    validate_trajectory_integrity,
)
from holomed.planning.service import PlanningService
from holomed.recovery.exceptions import (
    RecoveryAuthorizationError,
    RecoveryPlanMismatchError,
)
from holomed.recovery.models import (
    RecoveryAuthorization,
    RecoveryState,
    RecoveryStatusRecord,
    StagedRegistrationCandidate,
)
from holomed.recovery.service import RecoveryService
from holomed.registration.exceptions import (
    RegistrationAuthorizationError,
    RegistrationLifecycleError,
    RegistrationPlanMismatchError,
    RegistrationValidationError,
)
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
    RegistrationState,
    RegistrationStatusRecord,
    RigidRegistrationTransform3D,
)
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateSeverity,
    GateStatusRecord,
    SafetyGateAction,
)
from holomed.safety_gate.service import SafetyGateService
from holomed.workflow.models import (
    WorkflowPhase,
    WorkflowToolAuthorizationDecision,
    WorkflowToolAuthorizationStatus,
)
from holomed.workflow.service import WorkflowService


# ---------------------------------------------------------------------------
# Test Helpers & Builders
# ---------------------------------------------------------------------------

def _build_trajectory(
    trajectory_id: str = "traj_01",
    target: Tuple[float, float, float] = (10.0, 20.0, 30.0),
    entry: Tuple[float, float, float] = (15.0, 25.0, 35.0),
    lateral_tol: float = 1.0,
    angular_tol: float = 2.0,
    confidence: float = 0.95,
    uncertainty: float = 0.1,
    target_structure: str = "tumour_core",
) -> TrajectoryPlan:
    """Construct an immutable TrajectoryPlan with complete geometry."""
    return TrajectoryPlan(
        trajectory_id=trajectory_id,
        target_structure=target_structure,
        entry_point_mm=entry,
        target_point_mm=target,
        max_lateral_deviation_mm=lateral_tol,
        max_angular_deviation_deg=angular_tol,
        min_confidence=confidence,
        max_uncertainty=uncertainty,
    )


def _build_sample_cloud() -> FiducialCloud:
    """Build a deterministic fiducial cloud of 4 non-collinear points."""
    pairs = (
        FiducialPointPair(fiducial_id="f1", planned_point_mm=(0.0, 0.0, 0.0), measured_point_mm=(0.0, 0.0, 0.0), label="p1"),
        FiducialPointPair(fiducial_id="f2", planned_point_mm=(100.0, 0.0, 0.0), measured_point_mm=(100.0, 0.0, 0.0), label="p2"),
        FiducialPointPair(fiducial_id="f3", planned_point_mm=(0.0, 100.0, 0.0), measured_point_mm=(0.0, 100.0, 0.0), label="p3"),
        FiducialPointPair(fiducial_id="f4", planned_point_mm=(0.0, 0.0, 100.0), measured_point_mm=(0.0, 0.0, 100.0), label="p4"),
    )
    return FiducialCloud(pairs=pairs)


def _setup_planning_service(context: RuntimeContext, secret_filter: SecretFilter) -> PlanningService:
    srv = PlanningService(secret_filter=secret_filter)
    srv.initialize(context)
    srv.start()
    return srv


def _submit_and_lock_plan(
    srv: PlanningService,
    session_id: str,
    plan_id: str,
    trajectories: Optional[Tuple[TrajectoryPlan, ...]] = None,
) -> SurgicalPlanDefinition:
    """Submit and lock a surgical plan for a session."""
    trajs = trajectories or (_build_trajectory("traj_01"),)
    case_ctx = PatientCaseContext(
        case_id="case-01",
        patient_hash="a" * 64,
        procedure_code="CRAN-01",
        laterality=SurgicalLaterality.LEFT,
        primary_surgeon_id="dr_surgeon",
        scheduled_date_utc="2026-09-05T00:00:00Z",
    )
    plan_def = SurgicalPlanDefinition(
        plan_id=plan_id,
        version="1.0.0",
        case_context=case_ctx,
        trajectories=trajs,
        exclusion_zones=(),
        is_locked=False,
    )
    cap_sub = _create_execution_capability(id(srv), session_id, "PLANNING_COORDINATION", 1)
    srv.submit_plan(plan_def, session_id, capability=cap_sub)
    cap_lock = _create_execution_capability(id(srv), session_id, "PLANNING_COORDINATION", 2)
    return srv.lock_plan(plan_id, capability=cap_lock)


# ---------------------------------------------------------------------------
# Attack Vectors 1-6: Registration Ownership & Anti-Oracle Verification
# ---------------------------------------------------------------------------

class TestM36RegistrationOwnershipAndAntiOracle:
    """Vectors 1-6: RegistrationService plan ownership and anti-oracle checks."""

    def test_vector_01_registration_rejects_foreign_session_plan(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 1: RegistrationService rejects foreign session plan."""
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        plan_a = _submit_and_lock_plan(plan_srv, "session_a", "plan_a")

        reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()

        cap = _create_execution_capability(id(reg_srv), "session_b", "REGISTRATION_ALIGNMENT", 1)
        cloud = _build_sample_cloud()

        # Session B attempts to use Session A's plan
        with pytest.raises(RegistrationPlanMismatchError, match="No surgical plan bound to session"):
            reg_srv.submit_fiducials("session_b", plan_a.plan_id, cloud, capability=cap)

        reg_srv.stop()
        plan_srv.stop()

    def test_vector_02_registration_rejects_unlocked_plan(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 2: RegistrationService rejects unlocked plan."""
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        # Submit but DO NOT lock
        case_ctx = PatientCaseContext(
            case_id="case-draft",
            patient_hash="b" * 64,
            procedure_code="CRAN-02",
            laterality=SurgicalLaterality.RIGHT,
            primary_surgeon_id="dr_surgeon",
            scheduled_date_utc="2026-09-05T00:00:00Z",
        )
        plan_def = SurgicalPlanDefinition(
            plan_id="plan_unlocked",
            version="1.0.0",
            case_context=case_ctx,
            trajectories=(_build_trajectory("traj_01"),),
            exclusion_zones=(),
            is_locked=False,
        )
        cap_sub = _create_execution_capability(id(plan_srv), "session_draft", "PLANNING_COORDINATION", 1)
        plan_srv.submit_plan(plan_def, "session_draft", capability=cap_sub)

        reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()

        cap = _create_execution_capability(id(reg_srv), "session_draft", "REGISTRATION_ALIGNMENT", 1)
        cloud = _build_sample_cloud()

        # Submit fiducials should fail because plan is not locked
        with pytest.raises(RegistrationPlanMismatchError, match="not locked"):
            reg_srv.submit_fiducials("session_draft", "plan_unlocked", cloud, capability=cap)

        reg_srv.stop()
        plan_srv.stop()

    def test_vector_03_registration_rejects_when_planning_service_missing(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 3: RegistrationService fails closed when planning_service is None."""
        reg_srv = RegistrationService(planning_service=None, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()

        cap = _create_execution_capability(id(reg_srv), "session_any", "REGISTRATION_ALIGNMENT", 1)
        cloud = _build_sample_cloud()

        with pytest.raises(RegistrationLifecycleError, match="PlanningService is required"):
            reg_srv.submit_fiducials("session_any", "plan_any", cloud, capability=cap)

        reg_srv.stop()

    def test_vector_04_registration_anti_oracle_does_not_probe_supplied_plan_when_no_session_plan(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 4: Anti-oracle verifies session before caller-supplied plan_id."""
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        plan_victim = _submit_and_lock_plan(plan_srv, "session_victim", "plan_victim")

        reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()

        cap = _create_execution_capability(id(reg_srv), "session_attacker", "REGISTRATION_ALIGNMENT", 1)
        cloud = _build_sample_cloud()

        # Attacker probes whether plan_victim exists by using attacker's session
        with pytest.raises(RegistrationPlanMismatchError) as exc_info:
            reg_srv.submit_fiducials("session_attacker", plan_victim.plan_id, cloud, capability=cap)

        # Message must indicate session has no plan, NOT revealing whether plan_victim exists
        assert "No surgical plan bound to session 'session_attacker'" in str(exc_info.value)

        reg_srv.stop()
        plan_srv.stop()

    def test_vector_05_registration_plan_mismatch_leaves_fiducial_state_untouched(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 5: Registration state remains untouched on plan mismatch."""
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        plan_a = _submit_and_lock_plan(plan_srv, "session_a", "plan_a")

        reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()

        # Valid submission for session_a
        cap1 = _create_execution_capability(id(reg_srv), "session_a", "REGISTRATION_ALIGNMENT", 1)
        cloud = _build_sample_cloud()
        reg_srv.submit_fiducials("session_a", plan_a.plan_id, cloud, capability=cap1)
        assert reg_srv._fiducial_clouds.get("session_a") is not None

        # Attacker tries to submit foreign plan for session_a
        cap2 = _create_execution_capability(id(reg_srv), "session_a", "REGISTRATION_ALIGNMENT", 2)
        tampered_cloud = FiducialCloud(
            pairs=(
                FiducialPointPair(fiducial_id="t1", planned_point_mm=(999.0, 999.0, 999.0), measured_point_mm=(999.0, 999.0, 999.0), label="t1"),
                FiducialPointPair(fiducial_id="t2", planned_point_mm=(100.0, 0.0, 0.0), measured_point_mm=(100.0, 0.0, 0.0), label="t2"),
                FiducialPointPair(fiducial_id="t3", planned_point_mm=(0.0, 100.0, 0.0), measured_point_mm=(0.0, 100.0, 0.0), label="t3"),
                FiducialPointPair(fiducial_id="t4", planned_point_mm=(0.0, 0.0, 100.0), measured_point_mm=(0.0, 0.0, 100.0), label="t4"),
            )
        )
        with pytest.raises(RegistrationPlanMismatchError, match="does not match"):
            reg_srv.submit_fiducials("session_a", "foreign_plan_id", tampered_cloud, capability=cap2)

        # Original fiducial cloud must remain completely unchanged
        current_cloud = reg_srv._fiducial_clouds.get("session_a")
        assert current_cloud is not None
        assert len(current_cloud.pairs) == 4
        assert current_cloud.pairs[0].fiducial_id == "f1"

        reg_srv.stop()
        plan_srv.stop()

    def test_vector_06_registration_solve_rejects_unbound_plan_without_corrupting_state(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 6: solve_registration rejects unbound/mismatched plan with zero side effects."""
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        plan_a = _submit_and_lock_plan(plan_srv, "session_a", "plan_a")

        reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()

        # Session without plan attempts solve
        cap = _create_execution_capability(id(reg_srv), "session_no_plan", "REGISTRATION_ALIGNMENT", 1)
        with pytest.raises(RegistrationPlanMismatchError):
            reg_srv.solve_registration("session_no_plan", plan_a.plan_id, capability=cap)

        assert not reg_srv.is_registration_verified("session_no_plan")

        reg_srv.stop()
        plan_srv.stop()


# ---------------------------------------------------------------------------
# Attack Vectors 7-16: Navigation Trajectory Ownership & Geometric Integrity
# ---------------------------------------------------------------------------

class TestM36NavigationTrajectoryIntegrityAndOwnership:
    """Vectors 7-16: NavigationService trajectory binding and tolerance enforcement."""

    def _setup_nav_test_services(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter, session_id: str, plan_id: str
    ) -> Tuple[PlanningService, RegistrationService, NavigationService, SurgicalPlanDefinition]:
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        traj = _build_trajectory("traj_01")
        plan = _submit_and_lock_plan(plan_srv, session_id, plan_id, trajectories=(traj,))

        # Registration service
        reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()

        # Setup mock registration record to satisfy is_registration_verified
        mock_status = MagicMock()
        mock_status.plan_id = plan_id
        mock_status.transform = RigidRegistrationTransform3D(
            rotation_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            translation_vector_mm=(0.0, 0.0, 0.0),
            source_frame="plan_space",
            target_frame=DEFAULT_PATIENT_TRACKER_FRAME,
            epoch_id=1,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        reg_srv.is_registration_verified = MagicMock(return_value=True)  # type: ignore
        reg_srv.get_registration = MagicMock(return_value=mock_status)  # type: ignore

        # Navigation service with SAME PlanningService
        nav_srv = NavigationService(
            registration_service=reg_srv,
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )
        nav_srv.initialize(runtime_context)
        nav_srv.start()

        return plan_srv, reg_srv, nav_srv, plan

    def test_vector_07_navigation_trajectory_binding_rejects_foreign_session_plan(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 7: Navigation rejects binding for a session without a locked plan."""
        plan_srv, reg_srv, nav_srv, plan = self._setup_nav_test_services(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap = _create_execution_capability(id(nav_srv), "session_02", "TRAJECTORY_ALIGNMENT", 1)
        candidate = _build_trajectory("traj_01")

        with pytest.raises(NavigationRegistrationMismatchError, match="does not have a bound surgical plan"):
            nav_srv.bind_trajectory("session_02", "traj_01", candidate, capability=cap)

        nav_srv.stop()
        reg_srv.stop()
        plan_srv.stop()

    def test_vector_08_navigation_trajectory_binding_rejects_unlocked_plan(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 8: Navigation rejects binding when bound plan is unlocked."""
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        case_ctx = PatientCaseContext(
            case_id="case-draft",
            patient_hash="c" * 64,
            procedure_code="CRAN-03",
            laterality=SurgicalLaterality.BILATERAL,
            primary_surgeon_id="dr_surgeon",
            scheduled_date_utc="2026-09-05T00:00:00Z",
        )
        plan_def = SurgicalPlanDefinition(
            plan_id="plan_unlocked",
            version="1.0.0",
            case_context=case_ctx,
            trajectories=(_build_trajectory("traj_01"),),
            exclusion_zones=(),
            is_locked=False,
        )
        cap_sub = _create_execution_capability(id(plan_srv), "session_01", "PLANNING_COORDINATION", 1)
        plan_srv.submit_plan(plan_def, "session_01", capability=cap_sub)

        reg_srv = MagicMock(spec=RegistrationService)
        reg_srv.is_registration_verified.return_value = True
        reg_srv._planning_service = plan_srv

        nav_srv = NavigationService(
            registration_service=reg_srv,
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )
        nav_srv.initialize(runtime_context)
        nav_srv.start()

        cap = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        candidate = _build_trajectory("traj_01")

        with pytest.raises(NavigationRegistrationMismatchError, match="not locked"):
            nav_srv.bind_trajectory("session_01", "traj_01", candidate, capability=cap)

        nav_srv.stop()
        plan_srv.stop()

    def test_vector_09_navigation_trajectory_binding_rejects_unregistered_plan(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 9: Navigation rejects binding when active registration was solved with different plan."""
        plan_srv, reg_srv, nav_srv, plan = self._setup_nav_test_services(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        mock_status = MagicMock()
        mock_status.plan_id = "plan_99"
        reg_srv.get_registration = MagicMock(return_value=mock_status)  # type: ignore

        cap = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        candidate = _build_trajectory("traj_01")

        with pytest.raises(NavigationRegistrationMismatchError, match="does not match authoritative locked plan"):
            nav_srv.bind_trajectory("session_01", "traj_01", candidate, capability=cap)

        nav_srv.stop()
        reg_srv.stop()
        plan_srv.stop()

    def test_vector_10_navigation_trajectory_binding_rejects_trajectory_not_in_plan(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 10: Navigation rejects trajectory_id not found in authoritative plan."""
        plan_srv, reg_srv, nav_srv, plan = self._setup_nav_test_services(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        candidate = _build_trajectory("unknown_traj")

        with pytest.raises(NavigationRegistrationMismatchError, match="not found in authoritative plan"):
            nav_srv.bind_trajectory("session_01", "unknown_traj", candidate, capability=cap)

        nav_srv.stop()
        reg_srv.stop()
        plan_srv.stop()

    def test_vector_11_navigation_trajectory_binding_rejects_tampered_target_point_mm(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 11: Navigation rejects candidate whose target_point_mm differs by > 1e-6 mm."""
        plan_srv, reg_srv, nav_srv, plan = self._setup_nav_test_services(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        # Perturb target by 0.001 mm (> 1e-6 mm)
        tampered = _build_trajectory("traj_01", target=(10.0, 20.0, 30.001))

        with pytest.raises(NavigationRegistrationMismatchError, match=r"target_point_mm.*exceeds tolerance"):
            nav_srv.bind_trajectory("session_01", "traj_01", tampered, capability=cap)

        nav_srv.stop()
        reg_srv.stop()
        plan_srv.stop()

    def test_vector_12_navigation_trajectory_binding_rejects_tampered_entry_point_mm(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 12: Navigation rejects candidate whose entry_point_mm differs by > 1e-6 mm."""
        plan_srv, reg_srv, nav_srv, plan = self._setup_nav_test_services(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        # Perturb entry by 0.001 mm (> 1e-6 mm)
        tampered = _build_trajectory("traj_01", entry=(15.001, 25.0, 35.0))

        with pytest.raises(NavigationRegistrationMismatchError, match=r"entry_point_mm.*exceeds tolerance"):
            nav_srv.bind_trajectory("session_01", "traj_01", tampered, capability=cap)

        nav_srv.stop()
        reg_srv.stop()
        plan_srv.stop()

    def test_vector_13_navigation_trajectory_binding_rejects_tampered_angles(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 13: Navigation rejects candidate whose angles differ by > 1e-4 deg."""
        plan_srv, reg_srv, nav_srv, plan = self._setup_nav_test_services(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        # Perturb angular tolerance by 0.001 deg (> 1e-4 deg)
        tampered = _build_trajectory("traj_01", angular_tol=2.001)

        with pytest.raises(NavigationRegistrationMismatchError, match=r"max_angular_deviation_deg.*exceeds tolerance"):
            nav_srv.bind_trajectory("session_01", "traj_01", tampered, capability=cap)

        nav_srv.stop()
        reg_srv.stop()
        plan_srv.stop()

    def test_vector_14_navigation_trajectory_binding_rejects_tampered_tolerances(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 14: Navigation rejects candidate whose tolerances or confidence differ by > 1e-6."""
        plan_srv, reg_srv, nav_srv, plan = self._setup_nav_test_services(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        # Perturb lateral tolerance
        tampered = _build_trajectory("traj_01", lateral_tol=1.001)

        with pytest.raises(NavigationRegistrationMismatchError, match=r"max_lateral_deviation_mm.*exceeds tolerance"):
            nav_srv.bind_trajectory("session_01", "traj_01", tampered, capability=cap)

        nav_srv.stop()
        reg_srv.stop()
        plan_srv.stop()

    def test_vector_15_navigation_trajectory_binding_binds_authoritative_instance_not_caller_copy(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 15: Navigation binds authoritative object instance, discarding caller copy."""
        plan_srv, reg_srv, nav_srv, plan = self._setup_nav_test_services(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        # Valid copy
        authoritative = next(t for t in plan.trajectories if t.trajectory_id == "traj_01")
        caller_copy = copy.deepcopy(authoritative)

        nav_srv.bind_trajectory("session_01", "traj_01", caller_copy, sequence_number=1, capability=cap)
        assert nav_srv.get_navigation_status("session_01").state == NavigationState.IDLE

        bound = nav_srv._bound_trajectories.get("session_01")
        assert bound is not None
        assert bound is not caller_copy
        assert nav_srv.get_navigation_status("session_01").trajectory_id == "traj_01"

        nav_srv.stop()
        reg_srv.stop()
        plan_srv.stop()

    def test_vector_16_navigation_trajectory_binding_leaves_navigation_state_untouched_on_failure(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 16: Failure in bind_trajectory leaves active trajectory None and state untouched."""
        plan_srv, reg_srv, nav_srv, plan = self._setup_nav_test_services(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        tampered = _build_trajectory("traj_01", target=(99.0, 99.0, 99.0))

        with pytest.raises(NavigationRegistrationMismatchError):
            nav_srv.bind_trajectory("session_01", "traj_01", tampered, capability=cap)

        assert nav_srv._bound_trajectories.get("session_01") is None
        assert nav_srv.get_navigation_status("session_01").trajectory_id is None

        nav_srv.stop()
        reg_srv.stop()
        plan_srv.stop()


# ---------------------------------------------------------------------------
# Attack Vectors 17-21: Recovery Dual-Checkpoint Plan Ownership & Assertion
# ---------------------------------------------------------------------------

class TestM36RecoveryDualCheckpointPlanOwnership:
    """Vectors 17-21: RecoveryService plan ownership and assertion verification."""

    def _setup_recovery_test_services(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter, session_id: str, plan_id: str
    ) -> Tuple[PlanningService, RecoveryService, SurgicalPlanDefinition]:
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        traj = _build_trajectory("traj_01")
        plan = _submit_and_lock_plan(plan_srv, session_id, plan_id, trajectories=(traj,))

        rec_srv = RecoveryService(planning_service=plan_srv, secret_filter=secret_filter)
        rec_srv.initialize(runtime_context)
        rec_srv.start()

        return plan_srv, rec_srv, plan

    def test_vector_17_recovery_stage_rejects_foreign_session_plan(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 17: Recovery stage_candidate rejects foreign session plan."""
        plan_srv, rec_srv, plan = self._setup_recovery_test_services(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap = _create_execution_capability(id(rec_srv), "session_02", "RECOVERY_REORIENTATION", 1)
        cloud = _build_sample_cloud()

        with pytest.raises(RecoveryPlanMismatchError, match="No surgical plan bound to session"):
            rec_srv.stage_candidate("session_02", "plan_01", cloud, capability=cap)

        rec_srv.stop()
        plan_srv.stop()

    def test_vector_18_recovery_stage_rejects_unlocked_plan(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 18: Recovery stage_candidate rejects unlocked plan."""
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        case_ctx = PatientCaseContext(
            case_id="case-draft",
            patient_hash="d" * 64,
            procedure_code="CRAN-04",
            laterality=SurgicalLaterality.MIDLINE,
            primary_surgeon_id="dr_surgeon",
            scheduled_date_utc="2026-09-05T00:00:00Z",
        )
        plan_def = SurgicalPlanDefinition(
            plan_id="plan_draft",
            version="1.0.0",
            case_context=case_ctx,
            trajectories=(_build_trajectory("traj_01"),),
            exclusion_zones=(),
            is_locked=False,
        )
        cap_sub = _create_execution_capability(id(plan_srv), "session_draft", "PLANNING_COORDINATION", 1)
        plan_srv.submit_plan(plan_def, "session_draft", capability=cap_sub)

        rec_srv = RecoveryService(planning_service=plan_srv, secret_filter=secret_filter)
        rec_srv.initialize(runtime_context)
        rec_srv.start()

        cap = _create_execution_capability(id(rec_srv), "session_draft", "RECOVERY_REORIENTATION", 1)
        cloud = _build_sample_cloud()

        with pytest.raises(RecoveryPlanMismatchError, match="not locked"):
            rec_srv.stage_candidate("session_draft", "plan_draft", cloud, capability=cap)

        rec_srv.stop()
        plan_srv.stop()

    def test_vector_19_recovery_stage_leaves_recovery_state_untouched_on_failure(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 19: Recovery stage failure leaves candidate None and state UNINITIALIZED."""
        plan_srv, rec_srv, plan = self._setup_recovery_test_services(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        cloud = _build_sample_cloud()

        with pytest.raises(RecoveryPlanMismatchError):
            rec_srv.stage_candidate("session_01", "foreign_plan_id", cloud, capability=cap)

        status = rec_srv.get_recovery_status("session_01")
        assert status.state == RecoveryState.IDLE

        rec_srv.stop()
        plan_srv.stop()

    def test_vector_20_recovery_activate_rejects_when_session_plan_unlocked_or_tampered(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 20: Recovery activate_recovery validates plan at Checkpoint 2."""
        plan_srv, rec_srv, plan = self._setup_recovery_test_services(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        cloud = _build_sample_cloud()
        rec_srv.stage_candidate("session_01", "plan_01", cloud, sequence_number=1, capability=cap1)

        auth = RecoveryAuthorization(
            operator_id="surgeon_1",
            rationale="Approved",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            sequence_number=2,
            authorization_reference="AUTH-01",
        )
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (100.0, 0.0, 0.0), (100.1, 0.0, 0.0), sequence_number=2, capability=cap2)

        # Tamper: evict session plan from planning service
        plan_srv.evict_session("session_01")

        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        with pytest.raises(RecoveryPlanMismatchError, match="No surgical plan bound to session"):
            rec_srv.activate_recovery("session_01", sequence_number=3, capability=cap3)

        rec_srv.stop()
        plan_srv.stop()

    def test_vector_21_recovery_activate_verifies_trajectory_assertion_against_authoritative_plan(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 21: Recovery activate_recovery validates trajectory assertion against plan."""
        plan_srv, rec_srv, plan = self._setup_recovery_test_services(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        cloud = _build_sample_cloud()
        rec_srv.stage_candidate("session_01", "plan_01", cloud, sequence_number=1, capability=cap1)

        auth = RecoveryAuthorization(
            operator_id="surgeon_1",
            rationale="Approved",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            sequence_number=2,
            authorization_reference="AUTH-01",
        )
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (100.0, 0.0, 0.0), (100.1, 0.0, 0.0), sequence_number=2, capability=cap2)

        # Tampered trajectory assertion: target point perturbed by 0.001 mm
        tampered_assertion = _build_trajectory("traj_01", target=(10.0, 20.0, 30.001))
        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)

        with pytest.raises(RecoveryPlanMismatchError, match="trajectory integrity assertion failed"):
            rec_srv.activate_recovery(
                "session_01",
                plan_trajectory=tampered_assertion,
                sequence_number=3,
                capability=cap3,
            )

        rec_srv.stop()
        plan_srv.stop()


# ---------------------------------------------------------------------------
# Attack Vectors 22-26: Gateway Ingress Interlocks & Session Lifecycle Isolation
# ---------------------------------------------------------------------------

class TestM36GatewayIngressAndLifecycleIsolation:
    """Vectors 22-26: Gateway ingress plan validation and session teardown isolation."""

    def _setup_gateway_stack(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter, session_id: str, plan_id: str
    ) -> Tuple[ClinicalExecutionGatewayService, PlanningService, SurgicalPlanDefinition]:
        dispatcher = MessageDispatcher()
        dispatcher.initialize(runtime_context)

        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        traj = _build_trajectory("traj_01")
        plan = _submit_and_lock_plan(plan_srv, session_id, plan_id, trajectories=(traj,))

        mock_gate = MagicMock(spec=SafetyGateService)
        mock_gate.evaluate.return_value = GateStatusRecord(
            decision=GateDecision.PERMITTED_CLEAR,
            reason_code=GateReasonCode.NONE,
            severity=GateSeverity.NONE,
            action=SafetyGateAction.TOOL_NAVIGATION,
            evaluated_at_utc=datetime.now(timezone.utc).isoformat(),
            session_id=session_id,
            sequence_number=1,
            subsystem_snapshots=(),
        )

        mock_wf = MagicMock(spec=WorkflowService)
        mock_wf.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
            tool_id="test.tool",
            m07_safety_classification=MagicMock(),
            status=WorkflowToolAuthorizationStatus.PERMITTED,
            workflow_phase=WorkflowPhase.REGISTRATION,
            reason="AUTHORIZED",
            epoch_id=1,
            session_id=session_id,
        )

        reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)

        nav_srv = NavigationService(registration_service=reg_srv, planning_service=plan_srv, secret_filter=secret_filter)
        nav_srv.initialize(runtime_context)

        rec_srv = RecoveryService(planning_service=plan_srv, secret_filter=secret_filter)
        rec_srv.initialize(runtime_context)

        gateway = ClinicalExecutionGatewayService(
            dispatcher=dispatcher,
            safety_gate_service=mock_gate,
            workflow_service=mock_wf,
            registration_service=reg_srv,
            navigation_service=nav_srv,
            recovery_service=rec_srv,
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )
        gateway.initialize(runtime_context)
        dispatcher.start()
        reg_srv.start()
        nav_srv.start()
        rec_srv.start()
        gateway.start()

        return gateway, plan_srv, plan

    def test_vector_22_gateway_registration_ingress_rejects_foreign_or_unlocked_plan(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 22: Gateway execute_registration ingress gate rejects plan mismatch."""
        gateway, plan_srv, plan = self._setup_gateway_stack(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        req = RegistrationExecutionRequest(
            session_id="session_01",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            plan_id="foreign_plan_id",
            operation="SUBMIT",
            cloud=_build_sample_cloud(),
        )
        res = gateway.execute_registration(req)
        assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert res.error_message is not None and "does not match" in res.error_message.lower()

        gateway.stop()
        plan_srv.stop()

    def test_vector_23_gateway_trajectory_binding_ingress_rejects_tampered_trajectory(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 23: Gateway execute_trajectory_binding ingress gate rejects tampered trajectory."""
        gateway, plan_srv, plan = self._setup_gateway_stack(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        # Attacker submits trajectory with target perturbed by 0.001 mm
        tampered = _build_trajectory("traj_01", target=(10.0, 20.0, 30.001))
        req = TrajectoryBindingExecutionRequest(
            session_id="session_01",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            trajectory_id="traj_01",
            plan_trajectory=tampered,
        )
        res = gateway.execute_trajectory_binding(req)
        assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert res.error_message is not None and "target_point_mm" in res.error_message

        gateway.stop()
        plan_srv.stop()

    def test_vector_24_gateway_recovery_reorientation_ingress_rejects_foreign_or_unlocked_plan(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 24: Gateway execute_recovery_reorientation ingress gate rejects foreign plan."""
        gateway, plan_srv, plan = self._setup_gateway_stack(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        req = RecoveryReorientationExecutionRequest(
            session_id="session_01",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            recovery_operation="STAGE",
            plan_id="foreign_plan_id",
            cloud=_build_sample_cloud(),
        )
        res = gateway.execute_recovery_reorientation(req)
        assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert res.error_message is not None and "does not match" in res.error_message.lower()

        gateway.stop()
        plan_srv.stop()

    def test_vector_25_gateway_enforces_planning_service_instance_identity_across_all_subsystems(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 25: Gateway initialize() rejects disparate PlanningService instances."""
        ps1 = _setup_planning_service(runtime_context, secret_filter)
        ps2 = _setup_planning_service(runtime_context, secret_filter)

        reg_srv = RegistrationService(planning_service=ps2, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)

        gateway = ClinicalExecutionGatewayService(
            registration_service=reg_srv,
            planning_service=ps1,
            secret_filter=secret_filter,
        )
        with pytest.raises(ExecutionLifecycleError, match="RegistrationService planning_service instance mismatch with Gateway"):
            gateway.initialize(runtime_context)

        ps1.stop()
        ps2.stop()

    def test_vector_26_teardown_clears_session_plan_binding_enabling_safe_session_reuse(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 26: Session teardown evicts plan binding, preventing contamination on session_id reuse."""
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        plan_1 = _submit_and_lock_plan(plan_srv, "reused_session", "plan_01")
        assert plan_srv.get_plan_for_session("reused_session") is not None

        # Execute session eviction
        evicted = plan_srv.evict_session("reused_session")
        assert evicted is True
        assert plan_srv.get_plan_for_session("reused_session") is None

        # Re-use session with new plan_02
        plan_2 = _submit_and_lock_plan(plan_srv, "reused_session", "plan_02")
        bound_after = plan_srv.get_plan_for_session("reused_session")
        assert bound_after is not None
        assert bound_after.plan_id == "plan_02"
        assert bound_after.plan_id != "plan_01"

        plan_srv.stop()
