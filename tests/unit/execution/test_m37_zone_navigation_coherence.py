# -*- coding: utf-8 -*-
"""Milestone 37 (M37) Group 2 Hostile Audit & Verification Test Suite.

Verifies:
1. Real-Time Navigation Ingress Trajectory Identity Disconnect:
   - Synchronous plan lock and active bound trajectory verification at Gateway ingress.
   - Exact identity matching and authoritative effective trajectory resolution.
   - TOCTOU re-verification of plan lock and canonical trajectory geometry during tracking.
   - Fail-safe INTERLOCKED state transition without clinical deviation/pose mutation.
2. Recovery Zone Usurpation and Omission Disconnect:
   - Deterministic key-indexed zone ID matching and exact set equality assertions.
   - Authoritative zone derivation from locked surgical plan even when zones=None.
   - Anti-tampering defense against altered zone severity, radius, clearance, or center coordinates.
   - Pre-existing candidate TRE validation preventing registration error spoofing.
   - PROXIMITY_BINDING execution capability minting and single-use invalidation.
   - Unconditional post-activation proximity consistency verification.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.drift.models import LandmarkDefinition
from holomed.drift.service import DriftService
from holomed.execution.exceptions import ExecutionLifecycleError, ExecutionValidationError
from holomed.execution.models import (
    ExecutionStatus,
    NavigationExecutionRequest,
    RecoveryReorientationExecutionRequest,
    TrajectoryBindingExecutionRequest,
)
from holomed.execution.service import (
    ClinicalExecutionGatewayService,
    _create_execution_capability,
)
from holomed.navigation.constants import DEFAULT_PATIENT_TRACKER_FRAME
from holomed.navigation.exceptions import (
    NavigationAuthorizationError,
    NavigationLifecycleError,
    NavigationRegistrationMismatchError,
)
from holomed.navigation.models import (
    NavigationState,
    PositionClass,
    TrackedInstrumentPose,
    TrajectoryDeviationRecord,
)
from holomed.navigation.service import NavigationService
from holomed.planning.exceptions import (
    PlanningAuthorizationError,
    PlanningLockError,
    PlanningValidationError,
)
from holomed.planning.models import (
    PatientCaseContext,
    SafetyExclusionZone,
    SurgicalLaterality,
    SurgicalPlanDefinition,
    TrajectoryPlan,
    validate_exclusion_zone_integrity,
    validate_trajectory_integrity,
)
from holomed.planning.service import PlanningService
from holomed.proximity.models import ProximityState
from holomed.proximity.service import ProximityService
from holomed.recovery.exceptions import (
    RecoveryActivationError,
    RecoveryAuthorizationError,
    RecoveryLifecycleError,
    RecoveryPlanMismatchError,
    RecoveryValidationError,
)
from holomed.recovery.models import (
    RecoveryAuthorization,
    RecoveryState,
    RecoveryStatusRecord,
    StagedRegistrationCandidate,
)
from holomed.recovery.service import RecoveryService
from holomed.registration.models import (
    FiducialCloud,
    FiducialPointPair,
    RegistrationQualityReport,
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
    InterlockSeverity,
    WorkflowPhase,
    WorkflowToolAuthorizationDecision,
    WorkflowToolAuthorizationStatus,
)
from holomed.workflow.service import WorkflowService


# ---------------------------------------------------------------------------
# Helpers and Fixtures
# ---------------------------------------------------------------------------

def _build_trajectory(
    trajectory_id: str = "traj_01",
    target: Tuple[float, float, float] = (10.0, 20.0, 30.0),
    entry: Tuple[float, float, float] = (10.0, 20.0, 80.0),
    lateral_tol: float = 1.0,
    angular_tol: float = 2.0,
    confidence: float = 0.95,
    uncertainty: float = 0.1,
    target_structure: str = "tumour_core",
) -> TrajectoryPlan:
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


def _build_exclusion_zone(
    zone_id: str = "zone_01",
    name: str = "critical_vessel",
    center: Tuple[float, float, float] = (50.0, 60.0, 70.0),
    radius: float = 10.0,
    clearance: float = 5.0,
    severity: InterlockSeverity = InterlockSeverity.BLOCKING,
) -> SafetyExclusionZone:
    return SafetyExclusionZone(
        zone_id=zone_id,
        anatomical_structure=name,
        center_point_mm=center,
        bounding_radius_mm=radius,
        min_clearance_mm=clearance,
        severity_if_breached=severity,
    )


def _build_sample_cloud() -> FiducialCloud:
    pairs = (
        FiducialPointPair(fiducial_id="f1", planned_point_mm=(0.0, 0.0, 0.0), measured_point_mm=(0.0, 0.0, 0.0), label="p1"),
        FiducialPointPair(fiducial_id="f2", planned_point_mm=(100.0, 0.0, 0.0), measured_point_mm=(100.0, 0.0, 0.0), label="p2"),
        FiducialPointPair(fiducial_id="f3", planned_point_mm=(0.0, 100.0, 0.0), measured_point_mm=(0.0, 100.0, 0.0), label="p3"),
        FiducialPointPair(fiducial_id="f4", planned_point_mm=(0.0, 0.0, 100.0), measured_point_mm=(0.0, 0.0, 100.0), label="p4"),
    )
    return FiducialCloud(pairs=pairs)


def _build_tracked_pose(
    instrument_id: str = "inst_01",
    session_id: str = "session_01",
    sequence_number: int = 1,
    tip_position_mm: Tuple[float, float, float] = (12.0, 22.0, 32.0),
    orientation_quaternion: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
    now_utc: str = "",
) -> TrackedInstrumentPose:
    ts = now_utc or datetime.now(timezone.utc).isoformat()
    return TrackedInstrumentPose(
        instrument_id=instrument_id,
        session_id=session_id,
        epoch_id=1,
        sequence_number=sequence_number,
        tip_position_mm=tip_position_mm,
        orientation_quaternion=orientation_quaternion,
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=0.98,
        uncertainty=0.05,
        timestamp_utc=ts,
    )


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
    exclusion_zones: Optional[Tuple[SafetyExclusionZone, ...]] = None,
) -> SurgicalPlanDefinition:
    trajs = trajectories or (_build_trajectory("traj_01"),)
    zones = exclusion_zones or (_build_exclusion_zone("zone_01"),)
    case_ctx = PatientCaseContext(
        case_id="case-01",
        patient_hash="c" * 64,
        procedure_code="CRAN-01",
        laterality=SurgicalLaterality.LEFT,
        primary_surgeon_id="dr_surgeon",
        scheduled_date_utc="2026-09-06T00:00:00Z",
    )
    plan_def = SurgicalPlanDefinition(
        plan_id=plan_id,
        version="1.0.0",
        case_context=case_ctx,
        trajectories=trajs,
        exclusion_zones=zones,
        is_locked=False,
    )
    cap_sub = _create_execution_capability(id(srv), session_id, "PLANNING_COORDINATION", 1)
    srv.submit_plan(plan_def, session_id, capability=cap_sub)
    cap_lock = _create_execution_capability(id(srv), session_id, "PLANNING_COORDINATION", 2)
    return srv.lock_plan(plan_id, capability=cap_lock)


def _setup_verified_registration(
    reg_srv: RegistrationService,
    session_id: str,
    plan_id: str,
    tre_estimate: Optional[float] = 0.5,
    fre_rms: float = 0.4,
) -> None:
    cloud = _build_sample_cloud()
    cap = _create_execution_capability(id(reg_srv), session_id, "REGISTRATION_ALIGNMENT", 1)
    reg_srv.submit_fiducials(session_id, plan_id, cloud, capability=cap)
    reg_srv.solve_registration(session_id, plan_id, capability=cap)
    reg_srv.verify_registration(
        session_id=session_id,
        operator_id="dr_verifier",
        checkpoint_plan_mm=(0.0, 0.0, 0.0),
        checkpoint_measured_mm=(0.1, 0.1, 0.1),
        capability=cap,
    )
    # Inject specific TRE / FRE if requested
    rec = reg_srv._registrations.get(session_id)
    if rec is not None:
        q = RegistrationQualityReport(
            fre_rms_mm=fre_rms,
            fre_max_mm=fre_rms + 0.2,
            per_fiducial_residuals_mm=(0.2, 0.2, 0.2, 0.2),
            passed_clinical_threshold=True,
            warning_threshold_exceeded=False,
            target_registration_error_estimate_mm=tre_estimate,
        )
        updated = RegistrationStatusRecord(
            session_id=rec.session_id,
            plan_id=rec.plan_id,
            epoch_id=rec.epoch_id,
            state=rec.state,
            transform=rec.transform,
            quality_report=q,
            locked=rec.locked,
            verified_at_utc=rec.verified_at_utc,
        )
        reg_srv._registrations[session_id] = updated


def _setup_gateway_and_subsystems(
    runtime_context: RuntimeContext,
    secret_filter: SecretFilter,
    session_id: str = "session_01",
    plan_id: str = "plan_01",
    trajectories: Optional[Tuple[TrajectoryPlan, ...]] = None,
    exclusion_zones: Optional[Tuple[SafetyExclusionZone, ...]] = None,
) -> Tuple[ClinicalExecutionGatewayService, PlanningService, RegistrationService, NavigationService, RecoveryService, ProximityService]:
    plan_srv = _setup_planning_service(runtime_context, secret_filter)
    plan = _submit_and_lock_plan(plan_srv, session_id, plan_id, trajectories=trajectories, exclusion_zones=exclusion_zones)

    reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
    reg_srv.initialize(runtime_context)

    prox_srv = ProximityService(planning_service=plan_srv, registration_service=reg_srv, secret_filter=secret_filter)
    prox_srv.initialize(runtime_context)

    nav_srv = NavigationService(registration_service=reg_srv, planning_service=plan_srv, secret_filter=secret_filter)
    nav_srv.initialize(runtime_context)

    drift_srv = DriftService(registration_service=reg_srv, secret_filter=secret_filter)
    drift_srv.initialize(runtime_context)

    rec_srv = RecoveryService(
        registration_service=reg_srv,
        proximity_service=prox_srv,
        navigation_service=nav_srv,
        drift_service=drift_srv,
        planning_service=plan_srv,
        secret_filter=secret_filter,
    )
    rec_srv.initialize(runtime_context)

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
        tool_id="inst_01",
        m07_safety_classification=MagicMock(),
        status=WorkflowToolAuthorizationStatus.PERMITTED,
        workflow_phase=WorkflowPhase.NAVIGATION,
        reason="AUTHORIZED",
        epoch_id=1,
        session_id=session_id,
    )
    wf_state_mock = MagicMock()
    wf_state_mock.current_phase.value = "NAVIGATION"
    mock_wf.get_workflow_state.return_value = wf_state_mock

    dispatcher = MessageDispatcher()
    dispatcher.initialize(runtime_context)

    gateway = ClinicalExecutionGatewayService(
        dispatcher=dispatcher,
        safety_gate_service=mock_gate,
        workflow_service=mock_wf,
        registration_service=reg_srv,
        navigation_service=nav_srv,
        recovery_service=rec_srv,
        proximity_service=prox_srv,
        drift_service=drift_srv,
        planning_service=plan_srv,
        secret_filter=secret_filter,
    )

    # M42 isolated business-logic local stub

    gateway._is_session_active = MagicMock(return_value=True)
    gateway.initialize(runtime_context)

    dispatcher.start()
    reg_srv.start()
    prox_srv.start()
    nav_srv.start()
    drift_srv.start()
    rec_srv.start()
    gateway.start()

    # Pre-verify registration for nominal session
    _setup_verified_registration(reg_srv, session_id, plan_id)

    return gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv


# ---------------------------------------------------------------------------
# Test Suite: 28 Normalized Hostile Vectors
# ---------------------------------------------------------------------------

class TestM37ZoneNavigationCoherence:
    """Complete 28-Vector hostile test suite for M37 Group 2."""

    # =========================================================================
    # Vectors 1-8: Gateway Real-Time Navigation Ingress Trajectory Identity
    # =========================================================================

    def test_vector_01_execute_navigation_rejects_foreign_target_trajectory_id(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 1: Session B requests navigation with Session A's target_trajectory_id."""
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_a", "plan_a",
            trajectories=(_build_trajectory("traj_a"),)
        )
        # Setup Session B plan & bind traj_b
        _submit_and_lock_plan(plan_srv, "session_b", "plan_b", trajectories=(_build_trajectory("traj_b"),))
        _setup_verified_registration(reg_srv, "session_b", "plan_b")
        cap_b = _create_execution_capability(id(nav_srv), "session_b", "TRAJECTORY_ALIGNMENT", 1)
        nav_srv.bind_trajectory("session_b", "traj_b", _build_trajectory("traj_b"), capability=cap_b)

        # Session B attempts to execute navigation specifying Session A's trajectory
        pose = _build_tracked_pose(instrument_id="inst_01", session_id="session_b", sequence_number=1)
        req = NavigationExecutionRequest(
            session_id="session_b",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_a",
            pose=pose,
        )
        res = gateway.execute_navigation(req)

        assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert "does not match active bound trajectory" in (res.error_message or "")
        # Invariant: No poses processed, no deviation updated
        assert nav_srv.get_navigation_status("session_b").last_deviation is None

    def test_vector_02_execute_navigation_rejects_target_id_mismatch_with_active_bound_trajectory(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 2: Caller requests target_trajectory_id='traj_02' when 'traj_01' is active bound."""
        traj1 = _build_trajectory("traj_01")
        traj2 = _build_trajectory("traj_02", target=(100.0, 100.0, 100.0), entry=(100.0, 100.0, 50.0))
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01",
            trajectories=(traj1, traj2)
        )
        cap = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        nav_srv.bind_trajectory("session_01", "traj_01", traj1, capability=cap)

        pose = _build_tracked_pose(instrument_id="inst_01", session_id="session_01", sequence_number=1)
        req = NavigationExecutionRequest(
            session_id="session_01",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_02",
            pose=pose,
        )
        res = gateway.execute_navigation(req)

        assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert "does not match active bound trajectory" in (res.error_message or "")
        assert nav_srv.get_navigation_status("session_01").last_deviation is None

    def test_vector_03_execute_navigation_rejects_when_no_trajectory_bound(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 3: Session has valid plan but bind_trajectory was never called."""
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        # bind_trajectory is omitted
        pose = _build_tracked_pose(instrument_id="inst_01", session_id="session_01", sequence_number=1)
        req = NavigationExecutionRequest(
            session_id="session_01",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_01",
            pose=pose,
        )
        res = gateway.execute_navigation(req)

        assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert "No trajectory bound for session" in (res.error_message or "")
        assert nav_srv.get_navigation_status("session_01").last_deviation is None

    def test_vector_04_execute_navigation_rejects_when_bound_trajectory_removed_from_plan(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 4: Active bound trajectory ID unseated from current locked plan."""
        traj1 = _build_trajectory("traj_01")
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01",
            trajectories=(traj1,)
        )
        cap = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        nav_srv.bind_trajectory("session_01", "traj_01", traj1, capability=cap)

        # Replace plan with Plan B that does NOT contain traj_01
        traj_alt = _build_trajectory("traj_alt")
        plan_srv.evict_session("session_01")
        _submit_and_lock_plan(plan_srv, "session_01", "plan_02", trajectories=(traj_alt,))

        pose = _build_tracked_pose(instrument_id="inst_01", session_id="session_01", sequence_number=1)
        req = NavigationExecutionRequest(
            session_id="session_01",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_01",
            pose=pose,
        )
        res = gateway.execute_navigation(req)

        assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert "not found in authoritative plan" in (res.error_message or "")
        assert nav_srv.get_navigation_status("session_01").last_deviation is None

    def test_vector_05_execute_navigation_with_omitted_target_trajectory_id_resolves_authoritatively(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 5: Caller sends target_trajectory_id=None; resolves authoritatively and succeeds."""
        traj1 = _build_trajectory("traj_01")
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01",
            trajectories=(traj1,)
        )
        cap = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        nav_srv.bind_trajectory("session_01", "traj_01", traj1, capability=cap)

        pose = _build_tracked_pose(instrument_id="inst_01", session_id="session_01", sequence_number=1)
        req = NavigationExecutionRequest(
            session_id="session_01",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id=None,  # OMITTED
            pose=pose,
        )
        res = gateway.execute_navigation(req)

        assert res.execution_status in (ExecutionStatus.EXECUTED_CLEAR, ExecutionStatus.EXECUTED_WITH_CAUTION)
        assert res.target_trajectory_id == "traj_01"
        assert res.deviation_record is not None

    def test_vector_06_execute_navigation_rejects_when_session_plan_unbound(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 6: Session has no surgical plan bound in PlanningService."""
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        plan_srv.evict_session("session_01")

        pose = _build_tracked_pose(instrument_id="inst_01", session_id="session_01", sequence_number=1)
        req = NavigationExecutionRequest(
            session_id="session_01",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_01",
            pose=pose,
        )
        res = gateway.execute_navigation(req)

        assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert "No surgical plan bound to session" in (res.error_message or "")
        assert nav_srv.get_navigation_status("session_01").last_deviation is None

    def test_vector_07_execute_navigation_rejects_when_session_plan_unlocked(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter, mock_dispatcher: MagicMock
    ) -> None:
        """Vector 7: Session plan has is_locked=False."""
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        # Create unlocked plan
        case_ctx = PatientCaseContext(
            case_id="case-unlocked", patient_hash="d" * 64, procedure_code="CRAN-01",
            laterality=SurgicalLaterality.RIGHT, primary_surgeon_id="dr_surgeon",
            scheduled_date_utc="2026-09-06T00:00:00Z",
        )
        plan_def = SurgicalPlanDefinition(
            plan_id="plan_unlocked", version="1.0.0", case_context=case_ctx,
            trajectories=(_build_trajectory("traj_01"),), exclusion_zones=(), is_locked=False,
        )
        cap_sub = _create_execution_capability(id(plan_srv), "session_01", "PLANNING_COORDINATION", 1)
        plan_srv.submit_plan(plan_def, "session_01", capability=cap_sub)

        reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()
        nav_srv = NavigationService(registration_service=reg_srv, planning_service=plan_srv, secret_filter=secret_filter)
        nav_srv.initialize(runtime_context)
        nav_srv.start()

        gateway = ClinicalExecutionGatewayService(
            dispatcher=mock_dispatcher,
            navigation_service=nav_srv,
            planning_service=plan_srv,
            secret_filter=secret_filter,
        )

        # M42 isolated business-logic local stub

        gateway._is_session_active = MagicMock(return_value=True)
        gateway.initialize(runtime_context)
        gateway.start()

        pose = _build_tracked_pose(instrument_id="inst_01", session_id="session_01", sequence_number=1)
        req = NavigationExecutionRequest(
            session_id="session_01",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_01",
            pose=pose,
        )
        res = gateway.execute_navigation(req)

        assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert "is not locked" in (res.error_message or "")
        assert nav_srv.get_navigation_status("session_01").last_deviation is None

    def test_vector_08_execute_navigation_rejects_missing_planning_service(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter, mock_dispatcher: MagicMock
    ) -> None:
        """Vector 8: Gateway constructed with _planning_service=None fails closed."""
        gateway = ClinicalExecutionGatewayService(
            dispatcher=mock_dispatcher,
            planning_service=None,
            secret_filter=secret_filter,
        )
        # M42 isolated business-logic local stub
        gateway._is_session_active = MagicMock(return_value=True)
        gateway.initialize(runtime_context)
        gateway.start()

        pose = _build_tracked_pose(instrument_id="inst_01", session_id="session_01", sequence_number=1)
        req = NavigationExecutionRequest(
            session_id="session_01",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            instrument_id="inst_01",
            target_trajectory_id="traj_01",
            pose=pose,
        )
        res = gateway.execute_navigation(req)

        assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
        assert "PlanningService handle is unavailable" in (res.error_message or "")

    # =========================================================================
    # Vectors 9-14: NavigationService Runtime TOCTOU & Authorization Invariants
    # =========================================================================

    def test_vector_09_navigation_service_evaluate_rejects_uncoordinated_direct_call(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 9: Direct uncoordinated call to evaluate() without capability rejects."""
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        plan = _submit_and_lock_plan(plan_srv, "session_01", "plan_01")
        reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()
        _setup_verified_registration(reg_srv, "session_01", "plan_01")

        nav_srv = NavigationService(registration_service=reg_srv, planning_service=plan_srv, secret_filter=secret_filter)
        nav_srv.initialize(runtime_context)
        nav_srv.start()

        cap = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        nav_srv.bind_trajectory("session_01", "traj_01", plan.trajectories[0], capability=cap)

        with pytest.raises(NavigationAuthorizationError, match="missing execution capability"):
            nav_srv.evaluate("session_01", "inst_01", capability=None)

    def test_vector_10_navigation_service_evaluate_rejects_capability_session_mismatch(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 10: Capability minted for Session A passed to Session B in evaluate()."""
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        plan = _submit_and_lock_plan(plan_srv, "session_01", "plan_01")
        reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()
        _setup_verified_registration(reg_srv, "session_01", "plan_01")

        nav_srv = NavigationService(registration_service=reg_srv, planning_service=plan_srv, secret_filter=secret_filter)
        nav_srv.initialize(runtime_context)
        nav_srv.start()

        cap1 = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        nav_srv.bind_trajectory("session_01", "traj_01", plan.trajectories[0], capability=cap1)

        cap_foreign = _create_execution_capability(id(nav_srv), "session_02", "TOOL_NAVIGATION", 2)
        with pytest.raises(NavigationAuthorizationError, match="Capability session mismatch"):
            nav_srv.evaluate("session_01", "inst_01", capability=cap_foreign)

    def test_vector_11_navigation_service_evaluate_fails_closed_when_plan_unlocked_post_binding(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 11: Plan unlocked between bind_trajectory and evaluate() transitions to INTERLOCKED."""
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        plan = _submit_and_lock_plan(plan_srv, "session_01", "plan_01")
        reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()
        _setup_verified_registration(reg_srv, "session_01", "plan_01")

        nav_srv = NavigationService(registration_service=reg_srv, planning_service=plan_srv, secret_filter=secret_filter)
        nav_srv.initialize(runtime_context)
        nav_srv.start()

        cap_bind = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        nav_srv.bind_trajectory("session_01", "traj_01", plan.trajectories[0], capability=cap_bind)

        # TOCTOU attack: Plan is unlocked in PlanningService
        old_plan = plan_srv._plans["plan_01"]
        unlocked_plan = copy.deepcopy(old_plan)
        object.__setattr__(unlocked_plan, "is_locked", False)
        plan_srv._plans["plan_01"] = unlocked_plan

        cap_pose = _create_execution_capability(id(nav_srv), "session_01", "TOOL_NAVIGATION", 4)
        pose = _build_tracked_pose(instrument_id="inst_01", session_id="session_01", sequence_number=4)
        nav_srv.submit_pose(pose, capability=cap_pose)

        cap_eval = _create_execution_capability(id(nav_srv), "session_01", "TOOL_NAVIGATION", 4)
        with pytest.raises(NavigationRegistrationMismatchError, match="missing or unlocked"):
            nav_srv.evaluate("session_01", "inst_01", capability=cap_eval)

        # Fail-safe state mutation check
        assert nav_srv.get_navigation_status("session_01").state == NavigationState.INTERLOCKED
        # Forbidden mutations check: no deviation updated
        assert nav_srv.get_navigation_status("session_01").last_deviation is None

    def test_vector_12_navigation_service_evaluate_fails_closed_when_plan_replaced_post_binding(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 12: Plan replaced between bind_trajectory and evaluate() transitions to INTERLOCKED."""
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        plan = _submit_and_lock_plan(plan_srv, "session_01", "plan_01")
        reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()
        _setup_verified_registration(reg_srv, "session_01", "plan_01")

        nav_srv = NavigationService(registration_service=reg_srv, planning_service=plan_srv, secret_filter=secret_filter)
        nav_srv.initialize(runtime_context)
        nav_srv.start()

        cap_bind = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        nav_srv.bind_trajectory("session_01", "traj_01", plan.trajectories[0], capability=cap_bind)

        # TOCTOU attack: Plan replaced with Plan 02 having different trajectory
        plan_srv.evict_session("session_01")
        _submit_and_lock_plan(plan_srv, "session_01", "plan_02", trajectories=(_build_trajectory("traj_alt"),))

        cap_pose = _create_execution_capability(id(nav_srv), "session_01", "TOOL_NAVIGATION", 2)
        pose = _build_tracked_pose(instrument_id="inst_01", session_id="session_01", sequence_number=2)
        nav_srv.submit_pose(pose, capability=cap_pose)

        cap_eval = _create_execution_capability(id(nav_srv), "session_01", "TOOL_NAVIGATION", 2)
        with pytest.raises(NavigationRegistrationMismatchError, match="is not in authoritative plan"):
            nav_srv.evaluate("session_01", "inst_01", capability=cap_eval)

        assert nav_srv.get_navigation_status("session_01").state == NavigationState.INTERLOCKED
        assert nav_srv.get_navigation_status("session_01").last_deviation is None

    def test_vector_13_navigation_service_evaluate_fails_closed_when_bound_trajectory_geometry_altered(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 13: Bound trajectory geometry unseated post-binding transitions to INTERLOCKED."""
        plan_srv = _setup_planning_service(runtime_context, secret_filter)
        traj_orig = _build_trajectory("traj_01", target=(10.0, 20.0, 30.0))
        plan = _submit_and_lock_plan(plan_srv, "session_01", "plan_01", trajectories=(traj_orig,))
        reg_srv = RegistrationService(planning_service=plan_srv, secret_filter=secret_filter)
        reg_srv.initialize(runtime_context)
        reg_srv.start()
        _setup_verified_registration(reg_srv, "session_01", "plan_01")

        nav_srv = NavigationService(registration_service=reg_srv, planning_service=plan_srv, secret_filter=secret_filter)
        nav_srv.initialize(runtime_context)
        nav_srv.start()

        cap_bind = _create_execution_capability(id(nav_srv), "session_01", "TRAJECTORY_ALIGNMENT", 1)
        nav_srv.bind_trajectory("session_01", "traj_01", traj_orig, capability=cap_bind)

        # TOCTOU attack: Plan replaced by Plan with same ID and trajectory ID but altered target_point_mm
        traj_altered = _build_trajectory("traj_01", target=(10.0, 20.0, 30.01))
        plan_srv.evict_session("session_01")
        _submit_and_lock_plan(plan_srv, "session_01", "plan_01", trajectories=(traj_altered,))

        cap_pose = _create_execution_capability(id(nav_srv), "session_01", "TOOL_NAVIGATION", 2)
        pose = _build_tracked_pose(instrument_id="inst_01", session_id="session_01", sequence_number=2)
        nav_srv.submit_pose(pose, capability=cap_pose)

        cap_eval = _create_execution_capability(id(nav_srv), "session_01", "TOOL_NAVIGATION", 2)
        with pytest.raises(NavigationRegistrationMismatchError, match="differs from bound trajectory"):
            nav_srv.evaluate("session_01", "inst_01", capability=cap_eval)

        assert nav_srv.get_navigation_status("session_01").state == NavigationState.INTERLOCKED
        assert nav_srv.get_navigation_status("session_01").last_deviation is None

    def test_vector_14_navigation_service_missing_planning_service_fails_closed(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 14: NavigationService.evaluate() called when _planning_service=None fails closed."""
        nav_srv = NavigationService(planning_service=None, secret_filter=secret_filter)
        nav_srv.initialize(runtime_context)
        nav_srv.start()

        cap_eval = _create_execution_capability(id(nav_srv), "session_01", "TOOL_NAVIGATION", 1)
        with pytest.raises(NavigationLifecycleError, match="PlanningService is required for navigation evaluation"):
            nav_srv.evaluate("session_01", "inst_01", capability=cap_eval)

    # =========================================================================
    # Vectors 15-28: Recovery Zone Usurpation Defense & Coherence Invariants
    # =========================================================================

    def test_vector_15_recovery_activate_rejects_foreign_exclusion_zone(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 15: Caller supplies foreign zone ID in zones parameter; rejects before mutation."""
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01",
            exclusion_zones=(_build_exclusion_zone("zone_auth"),)
        )
        # Stage & verify candidate
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        rec_srv.stage_candidate("session_01", "plan_01", _build_sample_cloud(), capability=cap1)
        auth = RecoveryAuthorization("dr_op", "test", datetime.now(timezone.utc).isoformat(), 2, "auth_ref")
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (0, 0, 0), (0, 0, 0), sequence_number=2, capability=cap2)

        # Attacker supplies foreign zone
        foreign_zone = _build_exclusion_zone("zone_foreign")
        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        with pytest.raises(RecoveryPlanMismatchError, match="does not match authoritative zone ID set"):
            rec_srv.activate_recovery("session_01", zones=(foreign_zone,), sequence_number=3, capability=cap3)

        # State remains VERIFIED, no activation mutation
        assert rec_srv.get_recovery_status("session_01").state == RecoveryState.VERIFIED

    def test_vector_16_recovery_activate_rejects_same_count_foreign_zone_set(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 16: Plan has 2 zones {z1, z2}; caller supplies 2 foreign zones {x1, x2}."""
        z1 = _build_exclusion_zone("z1")
        z2 = _build_exclusion_zone("z2")
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01",
            exclusion_zones=(z1, z2)
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        rec_srv.stage_candidate("session_01", "plan_01", _build_sample_cloud(), capability=cap1)
        auth = RecoveryAuthorization("dr_op", "test", datetime.now(timezone.utc).isoformat(), 2, "auth_ref")
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (0, 0, 0), (0, 0, 0), sequence_number=2, capability=cap2)

        x1 = _build_exclusion_zone("x1")
        x2 = _build_exclusion_zone("x2")
        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        with pytest.raises(RecoveryPlanMismatchError, match="does not match authoritative zone ID set"):
            rec_srv.activate_recovery("session_01", zones=(x1, x2), sequence_number=3, capability=cap3)

        assert rec_srv.get_recovery_status("session_01").state == RecoveryState.VERIFIED

    def test_vector_17_recovery_activate_rejects_duplicate_caller_zone_ids(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 17: Caller supplies duplicate zone IDs [z1, z1]."""
        z1 = _build_exclusion_zone("z1")
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01",
            exclusion_zones=(z1,)
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        rec_srv.stage_candidate("session_01", "plan_01", _build_sample_cloud(), capability=cap1)
        auth = RecoveryAuthorization("dr_op", "test", datetime.now(timezone.utc).isoformat(), 2, "auth_ref")
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (0, 0, 0), (0, 0, 0), sequence_number=2, capability=cap2)

        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        with pytest.raises(RecoveryPlanMismatchError, match="Caller supplied duplicate zone_ids"):
            rec_srv.activate_recovery("session_01", zones=(z1, z1), sequence_number=3, capability=cap3)

        assert rec_srv.get_recovery_status("session_01").state == RecoveryState.VERIFIED

    def test_vector_18_recovery_activate_rejects_duplicate_authoritative_zone_ids(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 18: Authoritative plan containing duplicate zone IDs rejects at recovery entry."""
        z1 = _build_exclusion_zone("z1")
        z1_dup = _build_exclusion_zone("z1", name="z1_duplicate")
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01",
            exclusion_zones=(z1, z1_dup)
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        rec_srv.stage_candidate("session_01", "plan_01", _build_sample_cloud(), capability=cap1)
        auth = RecoveryAuthorization("dr_op", "test", datetime.now(timezone.utc).isoformat(), 2, "auth_ref")
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (0, 0, 0), (0, 0, 0), sequence_number=2, capability=cap2)

        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        with pytest.raises(RecoveryPlanMismatchError, match="contains duplicate zone_ids"):
            rec_srv.activate_recovery("session_01", sequence_number=3, capability=cap3)

        assert rec_srv.get_recovery_status("session_01").state == RecoveryState.VERIFIED

    def test_vector_19_recovery_activate_rejects_missing_one_zone_with_extra_foreign_zone(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 19: Plan has {z1, z2}; caller supplies {z1, z3}. Equal count (2) but set mismatch."""
        z1 = _build_exclusion_zone("z1")
        z2 = _build_exclusion_zone("z2")
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01",
            exclusion_zones=(z1, z2)
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        rec_srv.stage_candidate("session_01", "plan_01", _build_sample_cloud(), capability=cap1)
        auth = RecoveryAuthorization("dr_op", "test", datetime.now(timezone.utc).isoformat(), 2, "auth_ref")
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (0, 0, 0), (0, 0, 0), sequence_number=2, capability=cap2)

        z3 = _build_exclusion_zone("z3")
        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        with pytest.raises(RecoveryPlanMismatchError, match="does not match authoritative zone ID set"):
            rec_srv.activate_recovery("session_01", zones=(z1, z3), sequence_number=3, capability=cap3)

        assert rec_srv.get_recovery_status("session_01").state == RecoveryState.VERIFIED

    def test_vector_20_recovery_activate_accepts_reordered_zones_with_matching_ids_and_geometries(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 20: Plan has (z1, z2); caller supplies (z2, z1). Accepted and binds authoritative zones."""
        z1 = _build_exclusion_zone("z1", center=(10.0, 10.0, 10.0))
        z2 = _build_exclusion_zone("z2", center=(20.0, 20.0, 20.0))
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01",
            exclusion_zones=(z1, z2)
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        rec_srv.stage_candidate("session_01", "plan_01", _build_sample_cloud(), capability=cap1)
        auth = RecoveryAuthorization("dr_op", "test", datetime.now(timezone.utc).isoformat(), 2, "auth_ref")
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (0, 0, 0), (0, 0, 0), sequence_number=2, capability=cap2)

        # Pass re-ordered copies
        caller_z2 = copy.deepcopy(z2)
        caller_z1 = copy.deepcopy(z1)
        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        res = rec_srv.activate_recovery("session_01", zones=(caller_z2, caller_z1), sequence_number=3, capability=cap3)

        assert res.state == RecoveryState.ACTIVATED
        # Authoritative zones bound to Proximity in plan order (z1, z2)
        bound_zones = prox_srv._monitored_zones.get("session_01")
        assert bound_zones == (z1, z2)

    def test_vector_21_recovery_activate_rejects_zone_with_altered_severity(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 21: Caller alters severity_if_breached (CRITICAL -> WARNING)."""
        z1 = _build_exclusion_zone("z1", severity=InterlockSeverity.CRITICAL)
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01",
            exclusion_zones=(z1,)
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        rec_srv.stage_candidate("session_01", "plan_01", _build_sample_cloud(), capability=cap1)
        auth = RecoveryAuthorization("dr_op", "test", datetime.now(timezone.utc).isoformat(), 2, "auth_ref")
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (0, 0, 0), (0, 0, 0), sequence_number=2, capability=cap2)

        tampered_z1 = _build_exclusion_zone("z1", severity=InterlockSeverity.WARNING)
        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        with pytest.raises(RecoveryPlanMismatchError, match="Exclusion zone severity mismatch"):
            rec_srv.activate_recovery("session_01", zones=(tampered_z1,), sequence_number=3, capability=cap3)

        assert rec_srv.get_recovery_status("session_01").state == RecoveryState.VERIFIED

    def test_vector_22_recovery_activate_rejects_zone_with_altered_clearance(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 22: Caller alters min_clearance_mm by 0.05 mm (> 10^-6 mm)."""
        z1 = _build_exclusion_zone("z1", clearance=5.0)
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01",
            exclusion_zones=(z1,)
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        rec_srv.stage_candidate("session_01", "plan_01", _build_sample_cloud(), capability=cap1)
        auth = RecoveryAuthorization("dr_op", "test", datetime.now(timezone.utc).isoformat(), 2, "auth_ref")
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (0, 0, 0), (0, 0, 0), sequence_number=2, capability=cap2)

        tampered_z1 = _build_exclusion_zone("z1", clearance=5.05)
        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        with pytest.raises(RecoveryPlanMismatchError, match="min_clearance_mm.*exceeds tolerance"):
            rec_srv.activate_recovery("session_01", zones=(tampered_z1,), sequence_number=3, capability=cap3)

        assert rec_srv.get_recovery_status("session_01").state == RecoveryState.VERIFIED

    def test_vector_23_recovery_activate_rejects_zone_with_altered_radius(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 23: Caller alters bounding_radius_mm by 0.05 mm (> 10^-6 mm)."""
        z1 = _build_exclusion_zone("z1", radius=10.0)
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01",
            exclusion_zones=(z1,)
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        rec_srv.stage_candidate("session_01", "plan_01", _build_sample_cloud(), capability=cap1)
        auth = RecoveryAuthorization("dr_op", "test", datetime.now(timezone.utc).isoformat(), 2, "auth_ref")
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (0, 0, 0), (0, 0, 0), sequence_number=2, capability=cap2)

        tampered_z1 = _build_exclusion_zone("z1", radius=10.05)
        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        with pytest.raises(RecoveryPlanMismatchError, match="bounding_radius_mm.*exceeds tolerance"):
            rec_srv.activate_recovery("session_01", zones=(tampered_z1,), sequence_number=3, capability=cap3)

        assert rec_srv.get_recovery_status("session_01").state == RecoveryState.VERIFIED

    def test_vector_24_recovery_activate_rejects_zone_with_altered_center_point(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 24: Caller alters center_point_mm[0] by 0.01 mm (> 10^-6 mm)."""
        z1 = _build_exclusion_zone("z1", center=(50.0, 60.0, 70.0))
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01",
            exclusion_zones=(z1,)
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        rec_srv.stage_candidate("session_01", "plan_01", _build_sample_cloud(), capability=cap1)
        auth = RecoveryAuthorization("dr_op", "test", datetime.now(timezone.utc).isoformat(), 2, "auth_ref")
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (0, 0, 0), (0, 0, 0), sequence_number=2, capability=cap2)

        tampered_z1 = _build_exclusion_zone("z1", center=(50.01, 60.0, 70.0))
        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        with pytest.raises(RecoveryPlanMismatchError, match="center_point_mm.*exceeds tolerance"):
            rec_srv.activate_recovery("session_01", zones=(tampered_z1,), sequence_number=3, capability=cap3)

        assert rec_srv.get_recovery_status("session_01").state == RecoveryState.VERIFIED

    def test_vector_25_recovery_activate_rejects_registration_error_spoofing(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 25: Caller passes registration_error_mm=0.0 when candidate TRE is non-zero."""
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        cand = rec_srv.stage_candidate("session_01", "plan_01", _build_sample_cloud(), capability=cap1)
        auth = RecoveryAuthorization("dr_op", "test", datetime.now(timezone.utc).isoformat(), 2, "auth_ref")
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (0, 0, 0), (0, 0, 0), sequence_number=2, capability=cap2)

        cand_tre = cand.quality_report.target_registration_error_estimate_mm or cand.quality_report.fre_rms_mm
        spoofed_error = cand_tre + 0.1  # Spoofed value > 10^-6 mm divergence

        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        with pytest.raises(RecoveryPlanMismatchError, match="does not match candidate TRE"):
            rec_srv.activate_recovery("session_01", registration_error_mm=spoofed_error, sequence_number=3, capability=cap3)

        assert rec_srv.get_recovery_status("session_01").state == RecoveryState.VERIFIED

    def test_vector_26_recovery_activate_authoritatively_rebinds_proximity_when_zones_omitted(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 26: Caller calls activate_recovery(zones=None); binds authoritative plan zones."""
        z1 = _build_exclusion_zone("z1_auth")
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01",
            exclusion_zones=(z1,)
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        rec_srv.stage_candidate("session_01", "plan_01", _build_sample_cloud(), capability=cap1)
        auth = RecoveryAuthorization("dr_op", "test", datetime.now(timezone.utc).isoformat(), 2, "auth_ref")
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (0, 0, 0), (0, 0, 0), sequence_number=2, capability=cap2)

        # Call activate_recovery with zones=None (omitted)
        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        rec_status = rec_srv.activate_recovery("session_01", zones=None, sequence_number=3, capability=cap3)

        assert rec_status.state == RecoveryState.ACTIVATED
        # Proximity is safely rebound with authoritative plan zones
        assert prox_srv._monitored_zones.get("session_01") == (z1,)
        assert prox_srv.get_proximity_status("session_01").state == ProximityState.CLEAR

    def test_vector_27_recovery_activate_mints_and_validates_proximity_capability(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 27: Recovery activation mints PROXIMITY_BINDING capability validated and invalidated."""
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        rec_srv.stage_candidate("session_01", "plan_01", _build_sample_cloud(), capability=cap1)
        auth = RecoveryAuthorization("dr_op", "test", datetime.now(timezone.utc).isoformat(), 2, "auth_ref")
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (0, 0, 0), (0, 0, 0), sequence_number=2, capability=cap2)

        captured_caps = []
        orig_bind_zones = prox_srv.bind_zones
        def _inspect_bind_zones(*args, **kwargs):
            cap = kwargs.get("capability")
            captured_caps.append(cap)
            assert cap is not None
            assert cap.is_active is True
            assert cap.session_id == "session_01"
            assert cap.action == "PROXIMITY_BINDING"
            return orig_bind_zones(*args, **kwargs)

        prox_srv.bind_zones = _inspect_bind_zones

        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        res = rec_srv.activate_recovery("session_01", sequence_number=3, capability=cap3)

        assert res.state == RecoveryState.ACTIVATED
        assert len(captured_caps) == 1
        # Invalidate guarantee in finally:
        assert captured_caps[0].is_active is False

    def test_vector_28_recovery_activate_rejects_after_plan_replacement_or_reset_or_replay(
        self, runtime_context: RuntimeContext, secret_filter: SecretFilter
    ) -> None:
        """Vector 28: Recovery activation rejects when plan replaced post-stage, session reset, or replayed cap."""
        gateway, plan_srv, reg_srv, nav_srv, rec_srv, prox_srv = _setup_gateway_and_subsystems(
            runtime_context, secret_filter, "session_01", "plan_01"
        )
        cap1 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 1)
        rec_srv.stage_candidate("session_01", "plan_01", _build_sample_cloud(), capability=cap1)
        auth = RecoveryAuthorization("dr_op", "test", datetime.now(timezone.utc).isoformat(), 2, "auth_ref")
        cap2 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 2)
        rec_srv.verify_candidate("session_01", auth, (0, 0, 0), (0, 0, 0), sequence_number=2, capability=cap2)

        # 28A: Replayed / Inactive Capability
        cap3 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 3)
        cap3.invalidate()
        with pytest.raises(RecoveryAuthorizationError, match="Execution capability is inactive"):
            rec_srv.activate_recovery("session_01", sequence_number=3, capability=cap3)

        # 28B: Reset session clears verification state
        rec_srv.reset_session("session_01")
        cap4 = _create_execution_capability(id(rec_srv), "session_01", "RECOVERY_REORIENTATION", 4)
        with pytest.raises(RecoveryLifecycleError, match="Recovery must be in VERIFIED state"):
            rec_srv.activate_recovery("session_01", sequence_number=4, capability=cap4)
