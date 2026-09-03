# -*- coding: utf-8 -*-
"""Hostile Unit Tests for M26 Perceptual Monitoring Lifecycle & Session Eviction Hardening.

Authoritative baseline: 16c5121ecaaae714b62ebe8afd763fa36d938de9
Contract: PHASE_26_CONTRACT.md
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock

from holomed.core.dispatcher import MessageDispatcher
from holomed.drift.constants import MAX_ACTIVE_DRIFT_SESSIONS
from holomed.drift.exceptions import DriftCapacityError, DriftLifecycleError
from holomed.drift.models import DriftState, LandmarkDefinition
from holomed.drift.service import DriftService
from holomed.execution.models import (
    ExecutionStatus,
    SessionTeardownExecutionRequest,
    SessionTeardownExecutionResult,
)
from holomed.execution.service import ClinicalExecutionGatewayService
from holomed.navigation.service import NavigationService
from holomed.persistence.service import PersistenceService
from holomed.planning.models import SafetyExclusionZone
from holomed.planning.service import PlanningService
from holomed.platform.service import PlatformService
from holomed.proximity.constants import MAX_ACTIVE_PROXIMITY_SESSIONS
from holomed.proximity.exceptions import ProximityCapacityError, ProximityLifecycleError
from holomed.proximity.models import (
    ProximityEvaluationRecord,
    ProximitySeverity,
    ProximityState,
    ToolClearanceGeometry,
    ZoneBreachRecord,
)
from holomed.proximity.service import ProximityService
from holomed.recovery.service import RecoveryService
from holomed.registration.models import (
    RegistrationState,
    RegistrationStatusRecord,
    RigidRegistrationTransform3D,
)
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.safety_gate.models import GateDecision, GateReasonCode, GateRequest, SafetyGateAction
from holomed.safety_gate.service import SafetyGateService
from holomed.workflow.models import InterlockSeverity
from holomed.workflow.service import WorkflowService


def _create_dummy_zone(zone_id: str = "zone-001") -> SafetyExclusionZone:
    return SafetyExclusionZone(
        zone_id=zone_id,
        anatomical_structure="INTERNAL_CAROTID_ARTERY",
        center_point_mm=(10.0, 20.0, 30.0),
        bounding_radius_mm=5.0,
        min_clearance_mm=2.0,
        severity_if_breached=InterlockSeverity.CRITICAL,
    )


def _create_dummy_geometry(session_id: str, instrument_id: str = "inst-001", seq: int = 1) -> ToolClearanceGeometry:
    return ToolClearanceGeometry(
        instrument_id=instrument_id,
        session_id=session_id,
        epoch_id=1,
        sequence_number=seq,
        tip_position_mm=(10.0, 20.0, 30.0),
        shaft_direction=(0.0, 0.0, 1.0),
        shaft_length_mm=100.0,
        shaft_radius_mm=2.0,
        confidence=0.99,
        tracking_uncertainty_mm=0.2,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        coordinate_frame="PATIENT_TRACKER",
    )


def _create_dummy_landmark(landmark_id: str = "lm-001") -> LandmarkDefinition:
    return LandmarkDefinition(
        landmark_id=landmark_id,
        name="NASION_CHECKPOINT",
        planned_point_mm=(15.0, 25.0, 35.0),
        max_tolerance_mm=1.5,
        min_confidence=0.9,
        max_uncertainty_mm=0.5,
    )


@pytest.fixture
def m26_environment(runtime_context):
    dispatcher = MessageDispatcher()
    dispatcher.initialize(runtime_context)

    platform = PlatformService()
    platform.initialize(runtime_context)
    platform.start()

    workflow = WorkflowService(dispatcher=dispatcher, platform_service=platform)
    workflow.initialize(runtime_context)
    workflow.start()

    planning = PlanningService(dispatcher=dispatcher, workflow_service=workflow)
    planning.initialize(runtime_context)
    planning.start()

    registration = RegistrationService(dispatcher=dispatcher, planning_service=planning)
    registration.initialize(runtime_context)
    registration.start()

    navigation = NavigationService(dispatcher=dispatcher, registration_service=registration)
    navigation.initialize(runtime_context)
    navigation.start()

    recovery = RecoveryService(dispatcher=dispatcher, registration_service=registration)
    recovery.initialize(runtime_context)
    recovery.start()

    proximity = ProximityService(dispatcher=dispatcher, registration_service=registration)
    proximity.initialize(runtime_context)
    proximity.start()

    drift = DriftService(dispatcher=dispatcher, registration_service=registration)
    drift.initialize(runtime_context)
    drift.start()

    safety_gate = SafetyGateService(
        dispatcher=None,
        workflow_service=workflow,
        registration_service=registration,
        navigation_service=navigation,
        recovery_service=recovery,
        proximity_service=proximity,
        drift_service=drift,
    )
    safety_gate.initialize(runtime_context)
    safety_gate.start()

    persistence = PersistenceService()
    persistence.initialize(runtime_context)
    persistence.start()

    gateway = ClinicalExecutionGatewayService(
        dispatcher=dispatcher,
        safety_gate_service=safety_gate,
        workflow_service=workflow,
        navigation_service=navigation,
        persistence_service=persistence,
        recovery_service=recovery,
        registration_service=registration,
        planning_service=planning,
        platform_service=platform,
        proximity_service=proximity,
        drift_service=drift,
    )
    gateway.initialize(runtime_context)
    gateway.start()

    dispatcher.start()

    return {
        "dispatcher": dispatcher,
        "platform": platform,
        "workflow": workflow,
        "planning": planning,
        "registration": registration,
        "navigation": navigation,
        "recovery": recovery,
        "proximity": proximity,
        "drift": drift,
        "safety_gate": safety_gate,
        "persistence": persistence,
        "gateway": gateway,
    }


def _verify_mock_registration(registration_service: RegistrationService, session_id: str):
    """Helper to establish a verified registration record so Proximity & Drift can bind."""
    transform = RigidRegistrationTransform3D(
        rotation_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        translation_vector_mm=(0.0, 0.0, 0.0),
        source_frame="PLAN",
        target_frame="PATIENT",
        epoch_id=1,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    reg_rec = RegistrationStatusRecord(
        session_id=session_id,
        plan_id="plan-001",
        epoch_id=1,
        state=RegistrationState.VERIFIED,
        transform=transform,
        quality_report=None,
        locked=True,
        verified_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    registration_service._registrations[session_id] = reg_rec


# =========================================================================
# 1. Complete M15 ProximityService Eviction Tests
# =========================================================================

def test_m26_proximity_evict_session_direct_purge_all_9_structures(m26_environment):
    prox: ProximityService = m26_environment["proximity"]
    reg: RegistrationService = m26_environment["registration"]
    session_id = "SESS-PROX-01"
    _verify_mock_registration(reg, session_id)

    # Bind zones to populate _session_states, _monitored_zones, _registration_errors, _static_margins
    zone = _create_dummy_zone("zone-001")
    prox.bind_zones(session_id, (zone,), registration_error_mm=0.5, static_margin_mm=1.0)

    # Submit geometry to populate _latest_geometries, _latest_sequences, _active_instruments
    geom = _create_dummy_geometry(session_id, "inst-001", 1)
    prox.submit_geometry(geom)

    # Populate _clearance_history and _latest_evaluations
    prox._clearance_history[(session_id, "zone-001")] = (5.0, datetime.now(timezone.utc).isoformat())
    eval_rec = ProximityEvaluationRecord(
        record_id="REC-001",
        session_id=session_id,
        instrument_id="inst-001",
        epoch_id=1,
        zone_results=(),
        worst_state=ProximityState.CLEAR,
        worst_severity=None,
        is_safe=True,
        evaluated_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    prox._latest_evaluations[session_id] = eval_rec

    # Verify all 9 structures are populated
    assert session_id in prox._session_states
    assert session_id in prox._monitored_zones
    assert session_id in prox._registration_errors
    assert session_id in prox._static_margins
    assert (session_id, "inst-001") in prox._latest_geometries
    assert (session_id, "inst-001") in prox._latest_sequences
    assert session_id in prox._latest_evaluations
    assert session_id in prox._active_instruments
    assert (session_id, "zone-001") in prox._clearance_history

    # Execute eviction
    evicted = prox.evict_session(session_id)
    assert evicted is True

    # Assert all 9 structures are completely purged
    assert session_id not in prox._session_states
    assert session_id not in prox._monitored_zones
    assert session_id not in prox._registration_errors
    assert session_id not in prox._static_margins
    assert (session_id, "inst-001") not in prox._latest_geometries
    assert (session_id, "inst-001") not in prox._latest_sequences
    assert session_id not in prox._latest_evaluations
    assert session_id not in prox._active_instruments
    assert (session_id, "zone-001") not in prox._clearance_history
    assert prox.active_sessions_count == 0


# =========================================================================
# 2. Complete M16 DriftService Eviction Tests
# =========================================================================

def test_m26_drift_evict_session_direct_purge_all_6_structures(m26_environment):
    drift: DriftService = m26_environment["drift"]
    reg: RegistrationService = m26_environment["registration"]
    session_id = "SESS-DRIFT-01"
    _verify_mock_registration(reg, session_id)

    # Bind landmarks to populate _session_states, _landmarks, _verified_landmarks
    lm = _create_dummy_landmark("lm-001")
    drift.bind_landmarks(session_id, (lm,))

    # Populate _latest_sequences, _dwell_buffers, _latest_verifications
    drift._latest_sequences[session_id] = 42
    drift._dwell_buffers[(session_id, "lm-001")] = []
    drift._verified_landmarks[session_id].add("lm-001")

    # Verify all 6 structures are populated
    assert session_id in drift._session_states
    assert session_id in drift._landmarks
    assert session_id in drift._latest_sequences
    assert session_id in drift._verified_landmarks
    assert (session_id, "lm-001") in drift._dwell_buffers

    # Execute eviction
    evicted = drift.evict_session(session_id)
    assert evicted is True

    # Assert all 6 structures are completely purged
    assert session_id not in drift._session_states
    assert session_id not in drift._landmarks
    assert session_id not in drift._latest_sequences
    assert session_id not in drift._verified_landmarks
    assert session_id not in drift._latest_verifications
    assert (session_id, "lm-001") not in drift._dwell_buffers
    assert drift.active_sessions_count == 0


# =========================================================================
# 3. Composite-Key Eviction Surgical Isolation
# =========================================================================

def test_m26_composite_key_surgical_eviction(m26_environment):
    prox: ProximityService = m26_environment["proximity"]
    drift: DriftService = m26_environment["drift"]
    reg: RegistrationService = m26_environment["registration"]

    sess_a = "SESS-COMP-A"
    sess_b = "SESS-COMP-B"
    _verify_mock_registration(reg, sess_a)
    _verify_mock_registration(reg, sess_b)

    # Populate Proximity composite keys for both sessions
    prox.bind_zones(sess_a, (_create_dummy_zone("zone-a"),), 0.5, 1.0)
    prox.bind_zones(sess_b, (_create_dummy_zone("zone-b"),), 0.5, 1.0)
    prox.submit_geometry(_create_dummy_geometry(sess_a, "inst-001", 1))
    prox.submit_geometry(_create_dummy_geometry(sess_b, "inst-002", 1))
    prox._clearance_history[(sess_a, "zone-a")] = (10.0, "ts")
    prox._clearance_history[(sess_b, "zone-b")] = (12.0, "ts")

    # Populate Drift composite keys for both sessions
    drift.bind_landmarks(sess_a, (_create_dummy_landmark("lm-a"),))
    drift.bind_landmarks(sess_b, (_create_dummy_landmark("lm-b"),))
    drift._dwell_buffers[(sess_a, "lm-a")] = []
    drift._dwell_buffers[(sess_b, "lm-b")] = []

    # Evict only Session A
    prox.evict_session(sess_a)
    drift.evict_session(sess_a)

    # Verify Session A composite keys are gone
    assert (sess_a, "inst-001") not in prox._latest_geometries
    assert (sess_a, "inst-001") not in prox._latest_sequences
    assert (sess_a, "zone-a") not in prox._clearance_history
    assert (sess_a, "lm-a") not in drift._dwell_buffers

    # Verify Session B composite keys remain 100% intact
    assert (sess_b, "inst-002") in prox._latest_geometries
    assert (sess_b, "inst-002") in prox._latest_sequences
    assert (sess_b, "zone-b") in prox._clearance_history
    assert (sess_b, "lm-b") in drift._dwell_buffers


# =========================================================================
# 4. Gateway Teardown Production Path (Contract Ordering & Subsystem Reporting)
# =========================================================================

def test_m26_gateway_teardown_production_path(m26_environment):
    gateway: ClinicalExecutionGatewayService = m26_environment["gateway"]
    prox: ProximityService = m26_environment["proximity"]
    drift: DriftService = m26_environment["drift"]
    platform: PlatformService = m26_environment["platform"]
    reg: RegistrationService = m26_environment["registration"]

    session_id = "SESS-TEARDOWN-PROD"
    platform.start_session(session_id)
    _verify_mock_registration(reg, session_id)

    prox.bind_zones(session_id, (_create_dummy_zone("zone-001"),), 0.5, 1.0)
    drift.bind_landmarks(session_id, (_create_dummy_landmark("lm-001"),))

    assert prox.active_sessions_count == 1
    assert drift.active_sessions_count == 1

    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )

    res: SessionTeardownExecutionResult = gateway.execute_session_teardown(req)

    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
    assert "proximity" in res.subsystems_purged
    assert "drift" in res.subsystems_purged
    assert "navigation" in res.subsystems_purged
    assert "platform" in res.subsystems_purged
    assert len(res.failures) == 0

    # Verify order in subsystems_purged matches contract specification
    p_idx = res.subsystems_purged.index("proximity")
    d_idx = res.subsystems_purged.index("drift")
    nav_idx = res.subsystems_purged.index("navigation")
    rec_idx = res.subsystems_purged.index("recovery")

    assert nav_idx < p_idx < d_idx < rec_idx

    # Capacity reclaimed in both services
    assert prox.active_sessions_count == 0
    assert drift.active_sessions_count == 0


# =========================================================================
# 5. 16-Session Drift Capacity Reclamation
# =========================================================================

def test_m26_16_session_drift_capacity_reclaimed(m26_environment):
    gateway: ClinicalExecutionGatewayService = m26_environment["gateway"]
    drift: DriftService = m26_environment["drift"]
    reg: RegistrationService = m26_environment["registration"]
    platform: PlatformService = m26_environment["platform"]

    # Fill and teardown 16 consecutive sessions
    for i in range(16):
        s_id = f"SESS-D-{i:03d}"
        platform.start_session(s_id)
        _verify_mock_registration(reg, s_id)
        drift.bind_landmarks(s_id, (_create_dummy_landmark(f"lm-{i:03d}"),))
        assert drift.active_sessions_count == 1

        req = SessionTeardownExecutionRequest(
            session_id=s_id,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
        )
        res = gateway.execute_session_teardown(req)
        assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
        assert drift.active_sessions_count == 0

    # Attempt Session 17: Must succeed without DriftCapacityError
    s_17 = "SESS-D-017"
    platform.start_session(s_17)
    _verify_mock_registration(reg, s_17)
    drift.bind_landmarks(s_17, (_create_dummy_landmark("lm-017"),))
    assert drift.active_sessions_count == 1
    assert s_17 in drift._session_states


# =========================================================================
# 6. 32-Session Proximity Capacity Reclamation
# =========================================================================

def test_m26_32_session_proximity_capacity_reclaimed(m26_environment):
    gateway: ClinicalExecutionGatewayService = m26_environment["gateway"]
    prox: ProximityService = m26_environment["proximity"]
    reg: RegistrationService = m26_environment["registration"]
    platform: PlatformService = m26_environment["platform"]

    # Run 32 consecutive cycles of bind and teardown
    for i in range(32):
        s_id = f"SESS-P-{i:03d}"
        platform.start_session(s_id)
        _verify_mock_registration(reg, s_id)
        prox.bind_zones(s_id, (_create_dummy_zone(f"zone-{i:03d}"),), 0.5, 1.0)
        assert prox.active_sessions_count == 1

        req = SessionTeardownExecutionRequest(
            session_id=s_id,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
        )
        res = gateway.execute_session_teardown(req)
        assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
        assert prox.active_sessions_count == 0

    # Attempt Session 33: Must succeed without ProximityCapacityError
    s_33 = "SESS-P-033"
    platform.start_session(s_33)
    _verify_mock_registration(reg, s_33)
    prox.bind_zones(s_33, (_create_dummy_zone("zone-033"),), 0.5, 1.0)
    assert prox.active_sessions_count == 1
    assert s_33 in prox._session_states


# =========================================================================
# 7. Stale CRITICAL_BREACH Cannot Contaminate Reused Session
# =========================================================================

def test_m26_stale_critical_breach_cannot_contaminate_reused_session(m26_environment):
    gateway: ClinicalExecutionGatewayService = m26_environment["gateway"]
    prox: ProximityService = m26_environment["proximity"]
    safety_gate: SafetyGateService = m26_environment["safety_gate"]
    reg: RegistrationService = m26_environment["registration"]
    platform: PlatformService = m26_environment["platform"]

    session_id = "SESS-BREACH-REUSE"
    platform.start_session(session_id)
    _verify_mock_registration(reg, session_id)

    # Session 1: Cause an interlocked proximity exclusion zone breach
    prox.bind_zones(session_id, (_create_dummy_zone("zone-001"),), 0.5, 1.0)
    prox._session_states[session_id] = ProximityState.INTERLOCKED

    # Verify safety gate evaluates this as DENIED_CRITICAL
    gate_req = GateRequest(
        session_id=session_id,
        action=SafetyGateAction.TOOL_NAVIGATION,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    status_before = safety_gate.evaluate(gate_req)
    assert status_before.decision == GateDecision.DENIED_CRITICAL
    assert status_before.reason_code == GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH

    # Execute M26 Teardown
    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=2,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR

    # Re-use Session ID for a new clean case
    platform.start_session(session_id)
    _verify_mock_registration(reg, session_id)

    # Evaluate safety gate on the reused session:
    # Proximity has no state for session_id, so Precedence 1 MUST NOT trigger!
    gate_req_reuse = GateRequest(
        session_id=session_id,
        action=SafetyGateAction.TOOL_NAVIGATION,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    status_after = safety_gate.evaluate(gate_req_reuse)
    assert status_after.reason_code != GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH



# =========================================================================
# 8. Stale DRIFT_EXCEEDED Cannot Contaminate Reused Session
# =========================================================================

def test_m26_stale_drift_cannot_contaminate_reused_session(m26_environment):
    gateway: ClinicalExecutionGatewayService = m26_environment["gateway"]
    drift: DriftService = m26_environment["drift"]
    safety_gate: SafetyGateService = m26_environment["safety_gate"]
    reg: RegistrationService = m26_environment["registration"]
    platform: PlatformService = m26_environment["platform"]

    session_id = "SESS-DRIFT-REUSE"
    platform.start_session(session_id)
    _verify_mock_registration(reg, session_id)

    # Session 1: Cause a drift limit exceeded state
    drift.bind_landmarks(session_id, (_create_dummy_landmark("lm-001"),))
    drift._session_states[session_id] = DriftState.DRIFT_EXCEEDED

    # Verify safety gate evaluates this as DENIED_INTERLOCKED
    gate_req = GateRequest(
        session_id=session_id,
        action=SafetyGateAction.TOOL_NAVIGATION,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    status_before = safety_gate.evaluate(gate_req)
    assert status_before.decision == GateDecision.DENIED_INTERLOCKED
    assert status_before.reason_code == GateReasonCode.LANDMARK_DRIFT_EXCEEDED

    # Execute M26 Teardown
    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=2,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR

    # Re-use Session ID
    platform.start_session(session_id)
    _verify_mock_registration(reg, session_id)

    # Evaluate safety gate: Stale drift interlock MUST NOT trigger
    gate_req_reuse = GateRequest(
        session_id=session_id,
        action=SafetyGateAction.TOOL_NAVIGATION,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    status_after = safety_gate.evaluate(gate_req_reuse)
    assert status_after.reason_code != GateReasonCode.LANDMARK_DRIFT_EXCEEDED


# =========================================================================
# 9. Partial Failures & Best-Effort Aggregation
# =========================================================================

def test_m26_partial_proximity_failure_aggregates_and_continues(m26_environment):
    gateway: ClinicalExecutionGatewayService = m26_environment["gateway"]
    prox: ProximityService = m26_environment["proximity"]
    drift: DriftService = m26_environment["drift"]
    platform: PlatformService = m26_environment["platform"]

    session_id = "SESS-PARTIAL-PROX"
    platform.start_session(session_id)

    # Inject mock error on Proximity evict_session
    prox.evict_session = MagicMock(side_effect=RuntimeError("Proximity hardware communication timeout"))

    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)

    assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
    assert any("proximity: Proximity hardware communication timeout" in f for f in res.failures)
    # Remaining stages still executed
    assert "drift" in res.subsystems_purged
    assert "navigation" in res.subsystems_purged
    assert "platform" in res.subsystems_purged


def test_m26_partial_drift_failure_aggregates_and_continues(m26_environment):
    gateway: ClinicalExecutionGatewayService = m26_environment["gateway"]
    drift: DriftService = m26_environment["drift"]
    platform: PlatformService = m26_environment["platform"]

    session_id = "SESS-PARTIAL-DRIFT"
    platform.start_session(session_id)

    # Inject mock error on Drift evict_session
    drift.evict_session = MagicMock(side_effect=RuntimeError("Drift tracker buffer fault"))

    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)

    assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
    assert any("drift: Drift tracker buffer fault" in f for f in res.failures)
    assert "proximity" in res.subsystems_purged
    assert "navigation" in res.subsystems_purged
    assert "platform" in res.subsystems_purged


# =========================================================================
# 10. Reentrancy Guards on Eviction
# =========================================================================

def test_m26_reentrancy_guards_fail_closed(m26_environment):
    prox: ProximityService = m26_environment["proximity"]
    drift: DriftService = m26_environment["drift"]

    session_id = "SESS-REENTRANT"

    prox._in_transaction = True
    with pytest.raises(ProximityLifecycleError, match="Reentrant call to evict_session rejected"):
        prox.evict_session(session_id)
    prox._in_transaction = False

    drift._in_transaction = True
    with pytest.raises(DriftLifecycleError, match="Reentrant call to evict_session rejected"):
        drift.evict_session(session_id)
    drift._in_transaction = False


# =========================================================================
# 11. Nonexistent Eviction Returns False Safely
# =========================================================================

def test_m26_evict_nonexistent_session_returns_false(m26_environment):
    prox: ProximityService = m26_environment["proximity"]
    drift: DriftService = m26_environment["drift"]

    assert prox.evict_session("NONEXISTENT-SESSION") is False
    assert drift.evict_session("NONEXISTENT-SESSION") is False


# =========================================================================
# 12. Rebind Clean State After Teardown
# =========================================================================

def test_m26_rebind_clean_perceptual_state_after_teardown(m26_environment):
    gateway: ClinicalExecutionGatewayService = m26_environment["gateway"]
    prox: ProximityService = m26_environment["proximity"]
    drift: DriftService = m26_environment["drift"]
    reg: RegistrationService = m26_environment["registration"]
    platform: PlatformService = m26_environment["platform"]

    session_id = "SESS-REBIND-CLEAN"
    platform.start_session(session_id)
    _verify_mock_registration(reg, session_id)

    # Initial setup
    prox.bind_zones(session_id, (_create_dummy_zone("zone-001"),), 0.5, 1.0)
    drift.bind_landmarks(session_id, (_create_dummy_landmark("lm-001"),))

    # Teardown
    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
    assert prox.active_sessions_count == 0
    assert drift.active_sessions_count == 0

    # Restart session and rebind new zones and new landmarks
    platform.start_session(session_id)
    _verify_mock_registration(reg, session_id)

    prox.bind_zones(session_id, (_create_dummy_zone("zone-new"),), 0.8, 1.5)
    drift.bind_landmarks(session_id, (_create_dummy_landmark("lm-new"),))

    assert prox.active_sessions_count == 1
    assert drift.active_sessions_count == 1
    assert len(prox.get_monitored_zones(session_id)) == 1
    assert prox.get_monitored_zones(session_id)[0].zone_id == "zone-new"
    assert len(drift.get_bound_landmarks(session_id)) == 1
    assert drift.get_bound_landmarks(session_id)[0].landmark_id == "lm-new"

