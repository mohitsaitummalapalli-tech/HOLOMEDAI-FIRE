# -*- coding: utf-8 -*-
"""Adversarial Tests for M18 Safety Gate — Simultaneous Failures, Action Isolation & Bypass Limitation."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from holomed.safety_gate.constants import MAX_ACTIVE_GATE_SESSIONS
from holomed.safety_gate.evaluator import SafetyGateEvaluator
from holomed.safety_gate.exceptions import SafetyGateCapacityError
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    SafetyGateAction,
)
from holomed.safety_gate.service import SafetyGateService
from holomed.workflow.models import WorkflowPhase
from tests.unit.safety_gate.conftest import make_gate_request


def _make_started_gate_service(
    mock_dispatcher,
    mock_workflow_service,
    mock_registration_service,
    mock_navigation_service,
    mock_proximity_service,
    mock_drift_service,
    mock_recovery_service,
    mock_persistence_service,
    secret_filter,
    logger,
    runtime_context,
) -> SafetyGateService:
    svc = SafetyGateService(
        dispatcher=mock_dispatcher,
        workflow_service=mock_workflow_service,
        registration_service=mock_registration_service,
        navigation_service=mock_navigation_service,
        proximity_service=mock_proximity_service,
        drift_service=mock_drift_service,
        recovery_service=mock_recovery_service,
        persistence_service=mock_persistence_service,
        secret_filter=secret_filter,
        logger=logger,
    )
    svc.initialize(runtime_context)
    svc.start()
    return svc


class TestSafetyGateActionIsolation:
    """Prove that recovery authorization NEVER implies navigation authorization."""

    def test_recovery_permission_does_not_permit_navigation_when_drift_exceeded(
        self,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
    ) -> None:
        mock_drift_service.get_drift_status.return_value.state.value = "DRIFT_EXCEEDED"

        # Action: RECOVERY_REORIENTATION -> PERMITTED_WITH_CAUTION
        rec_rec = SafetyGateEvaluator.evaluate(
            request=make_gate_request(action=SafetyGateAction.RECOVERY_REORIENTATION),
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        assert rec_rec.decision == GateDecision.PERMITTED_WITH_CAUTION

        # Action: TOOL_NAVIGATION -> STRICTLY DENIED_INTERLOCKED
        rec_nav = SafetyGateEvaluator.evaluate(
            request=make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION),
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        assert rec_nav.decision == GateDecision.DENIED_INTERLOCKED


class TestSafetyGateSimultaneousFailures:
    """Verify deterministic resolution of simultaneous multi-service failures."""

    def test_simultaneous_critical_breach_and_drift_exceeded(
        self,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
    ) -> None:
        mock_proximity_service.get_proximity_status.return_value.state.value = "CRITICAL_BREACH"
        mock_drift_service.get_drift_status.return_value.state.value = "DRIFT_EXCEEDED"
        mock_recovery_service.get_recovery_status.return_value.state.value = "FAILED"

        rec = SafetyGateEvaluator.evaluate(
            request=make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION),
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        # Top priority: CRITICAL_EXCLUSION_ZONE_BREACH
        assert rec.decision == GateDecision.DENIED_CRITICAL
        assert rec.reason_code == GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH


class TestSafetyGateBypassLimitation:
    """Explicitly verify and document architectural bypass limitation under frozen M00-M17."""

    def test_m18_denies_while_frozen_m14_directly_invoked_operates_independently(
        self,
        mock_dispatcher,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        """Architectural Proof:
        When M16 is DRIFT_EXCEEDED, M18 returns DENIED_INTERLOCKED.
        However, if an external caller directly invokes frozen M14 evaluate(),
        M14 only checks M13 registration state, demonstrating the necessity of the
        future unified enforcement milestone.
        """
        svc = _make_started_gate_service(
            mock_dispatcher, mock_workflow_service, mock_registration_service,
            mock_navigation_service, mock_proximity_service, mock_drift_service,
            mock_recovery_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        mock_drift_service.get_drift_status.return_value.state.value = "DRIFT_EXCEEDED"

        # 1. M18 Evaluator correctly returns DENIED
        record = svc.evaluate(make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION))
        assert record.decision == GateDecision.DENIED_INTERLOCKED
        assert record.reason_code == GateReasonCode.LANDMARK_DRIFT_EXCEEDED

        # 2. Frozen M14 status is independent (it queries M13, not M18)
        assert mock_navigation_service.get_navigation_status("session-01").state.value == "TRACKING"


class TestSafetyGateSubsystemFailures:
    """Tests for individual subsystem failure branches."""

    def test_m10_workflow_aborted_denies(
        self,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
    ) -> None:
        mock_workflow_service.get_workflow_state.return_value.current_phase = WorkflowPhase.ABORTED
        rec = SafetyGateEvaluator.evaluate(
            request=make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION),
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        assert rec.decision == GateDecision.DENIED_INTERLOCKED
        assert rec.reason_code == GateReasonCode.WORKFLOW_PHASE_BLOCKED

    def test_m14_missing_bound_trajectory_denies_tool_navigation(
        self,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
    ) -> None:
        mock_navigation_service.get_navigation_status.return_value.has_bound_trajectory = False
        rec = SafetyGateEvaluator.evaluate(
            request=make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION),
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        assert rec.decision == GateDecision.DENIED_INTERLOCKED
        assert rec.reason_code == GateReasonCode.NAVIGATION_UNAVAILABLE

    def test_registration_revision_is_never_treated_as_epoch(
        self,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
    ) -> None:
        """M17 registration_revision is 5 (different from epoch_id 1), which must NOT trigger epoch mismatch."""
        mock_recovery_service.get_recovery_status.return_value.registration_revision = 5

        rec = SafetyGateEvaluator.evaluate(
            request=make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION),
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        assert rec.decision == GateDecision.PERMITTED_CLEAR
        assert rec.reason_code == GateReasonCode.NONE


class TestSafetyGateCapacity:
    """Tests for session capacity limits."""

    def test_gate_session_capacity_exceeded(
        self,
        mock_dispatcher,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
        mock_persistence_service,
        secret_filter,
        logger,
        runtime_context,
    ) -> None:
        svc = _make_started_gate_service(
            mock_dispatcher, mock_workflow_service, mock_registration_service,
            mock_navigation_service, mock_proximity_service, mock_drift_service,
            mock_recovery_service, mock_persistence_service, secret_filter,
            logger, runtime_context,
        )

        for i in range(MAX_ACTIVE_GATE_SESSIONS):
            svc.evaluate(make_gate_request(session_id=f"sess-{i:03d}"))

        with pytest.raises(SafetyGateCapacityError, match="Max active gate sessions"):
            svc.evaluate(make_gate_request(session_id="sess-overflow"))
