# -*- coding: utf-8 -*-
"""M20 Unit Tests for M18 SafetyGate WORKFLOW_RESUMPTION Action.

Verifies the 14 locked safety invariants for workflow resumption authorization:
1. Nominal all-clear permit
2. M17 not ACTIVATED
3. M16 drift not STABLE
4. M15 proximity breach
5. M15 proximity warning
6. M13 registration not VERIFIED
7. M10 workflow not RECOVERY_REQUIRED
8. Revision mismatch: request vs M17
9. Revision mismatch: M16 vs M17
10. Revision mismatch: M15 vs M17
11. Revision mismatch: M13 vs M17
12. Freshness violation: M16 evaluated before M17
13. Freshness violation: M15 evaluated before M17
14. Session mismatch across subsystems
"""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from holomed.registration.models import RegistrationState
from holomed.safety_gate.evaluator import SafetyGateEvaluator
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateRequest,
    GateSeverity,
    SafetyGateAction,
)
from holomed.workflow.models import WorkflowPhase


def _make_resumption_mocks(
    session_id: str = "session-01",
    recovery_revision: int = 2,
    base_ts: str = "2026-09-02T12:00:00.000000+00:00",
    fresh_ts: str = "2026-09-02T12:00:01.000000+00:00",
):
    """Factory creating nominal mock services configured for WORKFLOW_RESUMPTION."""
    # M10 Workflow: currently RECOVERY_REQUIRED
    wf_svc = MagicMock()
    wf_snap = MagicMock()
    wf_snap.session_id = session_id
    wf_snap.current_phase = WorkflowPhase.RECOVERY_REQUIRED
    wf_snap.is_terminal = False
    wf_svc.get_workflow_state.return_value = wf_snap

    # M17 Spatial Recovery: ACTIVATED with active recovery_revision
    rec_svc = MagicMock()
    rec_stat = MagicMock()
    rec_stat.session_id = session_id
    rec_stat.state.value = "ACTIVATED"
    rec_stat.registration_revision = recovery_revision
    rec_stat.updated_at_utc = base_ts
    rec_svc.get_recovery_status.return_value = rec_stat

    # M16 Drift: STABLE, revision matches M17, evaluated after M17
    drift_svc = MagicMock()
    drift_stat = MagicMock()
    drift_stat.session_id = session_id
    drift_stat.state.value = "STABLE"
    drift_stat.epoch_id = 1
    drift_stat.registration_revision = recovery_revision
    drift_stat.updated_at_utc = fresh_ts
    drift_stat.last_verification.evaluated_at_utc = fresh_ts
    drift_stat.bound_landmark_count = 4
    drift_svc.get_drift_status.return_value = drift_stat

    # M15 Proximity: SAFE, revision matches M17, evaluated after M17
    prox_svc = MagicMock()
    prox_stat = MagicMock()
    prox_stat.session_id = session_id
    prox_stat.state.value = "SAFE"
    prox_stat.epoch_id = 1
    prox_stat.registration_revision = recovery_revision
    prox_stat.updated_at_utc = fresh_ts
    prox_stat.last_evaluation.evaluated_at_utc = fresh_ts
    prox_stat.monitored_zone_count = 2
    prox_svc.get_proximity_status.return_value = prox_stat

    # M13 Registration: VERIFIED, revision matches M17
    reg_svc = MagicMock()
    reg_rec = MagicMock()
    reg_rec.session_id = session_id
    reg_rec.state = RegistrationState.VERIFIED
    reg_rec.epoch_id = 1
    reg_rec.revision = recovery_revision
    reg_rec.verified_at_utc = fresh_ts
    reg_rec.locked = True
    reg_svc.get_registration.return_value = reg_rec

    # M14 Navigation: IDLE or TRACKING
    nav_svc = MagicMock()
    nav_stat = MagicMock()
    nav_stat.session_id = session_id
    nav_stat.state.value = "IDLE"
    nav_stat.epoch_id = 1
    nav_stat.has_bound_trajectory = True
    nav_svc.get_navigation_status.return_value = nav_stat

    return wf_svc, reg_svc, nav_svc, prox_svc, drift_svc, rec_svc


def _eval(req: GateRequest, mocks, epoch_id: int = 1):
    wf_svc, reg_svc, nav_svc, prox_svc, drift_svc, rec_svc = mocks
    return SafetyGateEvaluator.evaluate(
        request=req,
        epoch_id=epoch_id,
        workflow_service=wf_svc,
        registration_service=reg_svc,
        navigation_service=nav_svc,
        proximity_service=prox_svc,
        drift_service=drift_svc,
        recovery_service=rec_svc,
    )


class TestSafetyGateWorkflowResumption:
    """Test suite for SafetyGateAction.WORKFLOW_RESUMPTION evaluation rules."""

    def test_01_nominal_all_clear_permit(self) -> None:
        mocks = _make_resumption_mocks()
        req = GateRequest(
            session_id="session-01",
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=1,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=2,
        )
        rec = _eval(req, mocks)
        assert rec.decision == GateDecision.PERMITTED_CLEAR
        assert rec.severity == GateSeverity.NONE
        assert rec.reason_code == GateReasonCode.NONE
        assert rec.action == SafetyGateAction.WORKFLOW_RESUMPTION

    def test_02_m17_not_activated_denies(self) -> None:
        for invalid_state in ("IDLE", "FAILED", "BLOCKED", "COMPLETED"):
            mocks = _make_resumption_mocks()
            mocks[5].get_recovery_status.return_value.state.value = invalid_state
            req = GateRequest(
                session_id="session-01",
                action=SafetyGateAction.WORKFLOW_RESUMPTION,
                sequence_number=1,
                now_utc="2026-09-02T12:00:02.000000+00:00",
                recovery_revision=2,
            )
            rec = _eval(req, mocks)
            assert rec.decision == GateDecision.DENIED_INTERLOCKED
            assert rec.reason_code == GateReasonCode.SPATIAL_RECOVERY_FAILED

    def test_03_m16_drift_not_stable_denies(self) -> None:
        for invalid_state in ("READY", "DRIFT_EXCEEDED", "INTERLOCKED", "UNKNOWN"):
            mocks = _make_resumption_mocks()
            mocks[4].get_drift_status.return_value.state.value = invalid_state
            req = GateRequest(
                session_id="session-01",
                action=SafetyGateAction.WORKFLOW_RESUMPTION,
                sequence_number=1,
                now_utc="2026-09-02T12:00:02.000000+00:00",
                recovery_revision=2,
            )
            rec = _eval(req, mocks)
            assert rec.decision in (GateDecision.DENIED_INTERLOCKED, GateDecision.DENIED_CRITICAL)
            assert rec.reason_code in (
                GateReasonCode.LANDMARK_DRIFT_EXCEEDED,
                GateReasonCode.LANDMARK_INTEGRITY_INTERLOCKED,
            )

    def test_04_m15_proximity_critical_breach_denies_critical(self) -> None:
        mocks = _make_resumption_mocks()
        mocks[3].get_proximity_status.return_value.state.value = "CRITICAL_BREACH"
        req = GateRequest(
            session_id="session-01",
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=1,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=2,
        )
        rec = _eval(req, mocks)
        assert rec.decision == GateDecision.DENIED_CRITICAL
        assert rec.severity == GateSeverity.CRITICAL
        assert rec.reason_code == GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH

    def test_05_m15_proximity_warning_denies_interlocked(self) -> None:
        mocks = _make_resumption_mocks()
        mocks[3].get_proximity_status.return_value.state.value = "WARNING"
        req = GateRequest(
            session_id="session-01",
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=1,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=2,
        )
        rec = _eval(req, mocks)
        assert rec.decision == GateDecision.DENIED_INTERLOCKED
        assert rec.reason_code == GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH

    def test_06_m13_registration_not_verified_denies(self) -> None:
        for invalid_state in ("DRAFT", "PENDING", "FAILED", "SUSPENDED"):
            mocks = _make_resumption_mocks()
            mocks[1].get_registration.return_value.state = MagicMock()
            mocks[1].get_registration.return_value.state.value = invalid_state
            req = GateRequest(
                session_id="session-01",
                action=SafetyGateAction.WORKFLOW_RESUMPTION,
                sequence_number=1,
                now_utc="2026-09-02T12:00:02.000000+00:00",
                recovery_revision=2,
            )
            rec = _eval(req, mocks)
            assert rec.decision == GateDecision.DENIED_INTERLOCKED
            assert rec.reason_code == GateReasonCode.REGISTRATION_UNVERIFIED

    def test_07_m10_workflow_not_recovery_required_denies(self) -> None:
        for phase in (WorkflowPhase.NAVIGATION, WorkflowPhase.PRE_PROCEDURE_PLANNING, WorkflowPhase.ABORTED):
            mocks = _make_resumption_mocks()
            mocks[0].get_workflow_state.return_value.current_phase = phase
            mocks[0].get_workflow_state.return_value.is_terminal = (phase == WorkflowPhase.ABORTED)
            req = GateRequest(
                session_id="session-01",
                action=SafetyGateAction.WORKFLOW_RESUMPTION,
                sequence_number=1,
                now_utc="2026-09-02T12:00:02.000000+00:00",
                recovery_revision=2,
            )
            rec = _eval(req, mocks)
            assert rec.decision == GateDecision.DENIED_INTERLOCKED
            assert rec.reason_code == GateReasonCode.WORKFLOW_PHASE_BLOCKED

    def test_08_revision_mismatch_request_vs_m17_denies(self) -> None:
        mocks = _make_resumption_mocks(recovery_revision=2)
        req = GateRequest(
            session_id="session-01",
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=1,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=3,  # Mismatched from M17 (revision=2)
        )
        rec = _eval(req, mocks)
        assert rec.decision == GateDecision.DENIED_INTERLOCKED
        assert rec.reason_code == GateReasonCode.SPATIAL_RECOVERY_FAILED

    def test_09_revision_mismatch_m16_vs_m17_denies(self) -> None:
        mocks = _make_resumption_mocks(recovery_revision=2)
        # M16 has stale revision 1
        mocks[4].get_drift_status.return_value.registration_revision = 1
        req = GateRequest(
            session_id="session-01",
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=1,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=2,
        )
        rec = _eval(req, mocks)
        assert rec.decision == GateDecision.DENIED_INTERLOCKED
        assert rec.reason_code == GateReasonCode.LANDMARK_DRIFT_EXCEEDED

    def test_10_revision_mismatch_m15_vs_m17_denies(self) -> None:
        mocks = _make_resumption_mocks(recovery_revision=2)
        # M15 has stale revision 1
        mocks[3].get_proximity_status.return_value.registration_revision = 1
        req = GateRequest(
            session_id="session-01",
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=1,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=2,
        )
        rec = _eval(req, mocks)
        assert rec.decision == GateDecision.DENIED_INTERLOCKED
        assert rec.reason_code == GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH

    def test_11_revision_mismatch_m13_vs_m17_denies(self) -> None:
        mocks = _make_resumption_mocks(recovery_revision=2)
        # M13 has stale revision 1
        mocks[1].get_registration.return_value.revision = 1
        req = GateRequest(
            session_id="session-01",
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=1,
            now_utc="2026-09-02T12:00:02.000000+00:00",
            recovery_revision=2,
        )
        rec = _eval(req, mocks)
        assert rec.decision == GateDecision.DENIED_INTERLOCKED
        assert rec.reason_code == GateReasonCode.REGISTRATION_UNVERIFIED

    def test_12_freshness_violation_m16_before_m17_denies(self) -> None:
        mocks = _make_resumption_mocks(
            base_ts="2026-09-02T12:00:05.000000+00:00",
            fresh_ts="2026-09-02T12:00:06.000000+00:00",
        )
        # M16 evaluated before M17 was updated
        mocks[4].get_drift_status.return_value.last_verification.evaluated_at_utc = "2026-09-02T12:00:01.000000+00:00"
        mocks[4].get_drift_status.return_value.updated_at_utc = "2026-09-02T12:00:01.000000+00:00"
        req = GateRequest(
            session_id="session-01",
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=1,
            now_utc="2026-09-02T12:00:06.000000+00:00",
            recovery_revision=2,
        )
        rec = _eval(req, mocks)
        assert rec.decision == GateDecision.DENIED_INTERLOCKED
        assert rec.reason_code == GateReasonCode.LANDMARK_DRIFT_EXCEEDED

    def test_13_freshness_violation_m15_before_m17_denies(self) -> None:
        mocks = _make_resumption_mocks(
            base_ts="2026-09-02T12:00:05.000000+00:00",
            fresh_ts="2026-09-02T12:00:06.000000+00:00",
        )
        # Override M15 to be evaluated before M17
        mocks[3].get_proximity_status.return_value.last_evaluation.evaluated_at_utc = "2026-09-02T12:00:01.000000+00:00"
        mocks[3].get_proximity_status.return_value.updated_at_utc = "2026-09-02T12:00:01.000000+00:00"
        req = GateRequest(
            session_id="session-01",
            action=SafetyGateAction.WORKFLOW_RESUMPTION,
            sequence_number=1,
            now_utc="2026-09-02T12:00:07.000000+00:00",
            recovery_revision=2,
        )
        rec = _eval(req, mocks)
        assert rec.decision == GateDecision.DENIED_INTERLOCKED
        assert rec.reason_code == GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH

    def test_14_session_mismatch_across_subsystems_denies(self) -> None:
        subsystem_indices = [
            (3, "session_id", "alien-session"),  # M15
            (4, "session_id", "alien-session"),  # M16
            (5, "session_id", "alien-session"),  # M17
            (1, "session_id", "alien-session"),  # M13
            (0, "session_id", "alien-session"),  # M10
            (2, "session_id", "alien-session"),  # M14
        ]
        for idx, attr, val in subsystem_indices:
            mocks = _make_resumption_mocks()
            if idx == 0:
                mocks[0].get_workflow_state.return_value.session_id = val
            elif idx == 1:
                mocks[1].get_registration.return_value.session_id = val
            elif idx == 2:
                mocks[2].get_navigation_status.return_value.session_id = val
            elif idx == 3:
                mocks[3].get_proximity_status.return_value.session_id = val
            elif idx == 4:
                mocks[4].get_drift_status.return_value.session_id = val
            elif idx == 5:
                mocks[5].get_recovery_status.return_value.session_id = val

            req = GateRequest(
                session_id="session-01",
                action=SafetyGateAction.WORKFLOW_RESUMPTION,
                sequence_number=1,
                now_utc="2026-09-02T12:00:02.000000+00:00",
                recovery_revision=2,
            )
            rec = _eval(req, mocks)
            assert rec.decision == GateDecision.DENIED_INTERLOCKED
            assert rec.reason_code == GateReasonCode.SESSION_MISMATCH
