# -*- coding: utf-8 -*-
"""Unit Tests for M18 SafetyGateEvaluator — Precedence Hierarchy & Action Policies."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from holomed.registration.models import RegistrationState
from holomed.safety_gate.evaluator import SafetyGateEvaluator
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateSeverity,
    SafetyGateAction,
)
from holomed.workflow.models import WorkflowPhase
from tests.unit.safety_gate.conftest import make_gate_request


class TestSafetyGateEvaluatorPrecedence:
    """Tests for precedence hierarchy across subsystems."""

    def test_nominal_all_clear(
        self,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
    ) -> None:
        req = make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION)
        rec = SafetyGateEvaluator.evaluate(
            request=req,
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        assert rec.decision == GateDecision.PERMITTED_CLEAR
        assert rec.severity == GateSeverity.NONE
        assert rec.reason_code == GateReasonCode.NONE

    def test_m15_critical_breach_takes_top_precedence(
        self,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
    ) -> None:
        mock_proximity_service.get_proximity_status.return_value.state.value = "CRITICAL_BREACH"
        # Even if M16 is also in DRIFT_EXCEEDED, M15 CRITICAL_BREACH is top priority
        mock_drift_service.get_drift_status.return_value.state.value = "DRIFT_EXCEEDED"

        req = make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION)
        rec = SafetyGateEvaluator.evaluate(
            request=req,
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        assert rec.decision == GateDecision.DENIED_CRITICAL
        assert rec.severity == GateSeverity.CRITICAL
        assert rec.reason_code == GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH

    def test_m16_drift_exceeded_action_policies(
        self,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
    ) -> None:
        mock_drift_service.get_drift_status.return_value.state.value = "DRIFT_EXCEEDED"

        # Action: TOOL_NAVIGATION -> DENIED
        req_nav = make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION)
        rec_nav = SafetyGateEvaluator.evaluate(
            request=req_nav,
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        assert rec_nav.decision == GateDecision.DENIED_INTERLOCKED
        assert rec_nav.reason_code == GateReasonCode.LANDMARK_DRIFT_EXCEEDED

        # Action: RECOVERY_REORIENTATION -> PERMITTED_WITH_CAUTION (permits recovery)
        req_rec = make_gate_request(action=SafetyGateAction.RECOVERY_REORIENTATION)
        rec_rec = SafetyGateEvaluator.evaluate(
            request=req_rec,
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        assert rec_rec.decision == GateDecision.PERMITTED_WITH_CAUTION
        assert rec_rec.reason_code == GateReasonCode.LANDMARK_DRIFT_EXCEEDED

    def test_m17_recovery_failed_denies_all(
        self,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
    ) -> None:
        mock_recovery_service.get_recovery_status.return_value.state.value = "FAILED"

        req = make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION)
        rec = SafetyGateEvaluator.evaluate(
            request=req,
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        assert rec.decision == GateDecision.DENIED_INTERLOCKED
        assert rec.reason_code == GateReasonCode.SPATIAL_RECOVERY_FAILED

    def test_m16_interlocked_integrity_failure_provenance(
        self,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
    ) -> None:
        mock_drift_service.get_drift_status.return_value.state.value = "INTERLOCKED"

        req = make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION)
        rec = SafetyGateEvaluator.evaluate(
            request=req,
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        assert rec.decision == GateDecision.DENIED_INTERLOCKED
        assert rec.reason_code == GateReasonCode.LANDMARK_INTEGRITY_INTERLOCKED

    def test_comprehensive_action_isolation_matrix(
        self,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
    ) -> None:
        """Matrix A: M16 DRIFT_EXCEEDED isolation."""
        mock_drift_service.get_drift_status.return_value.state.value = "DRIFT_EXCEEDED"
        r_nav = SafetyGateEvaluator.evaluate(
            request=make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION),
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        r_align = SafetyGateEvaluator.evaluate(
            request=make_gate_request(action=SafetyGateAction.TRAJECTORY_ALIGNMENT),
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        r_rec = SafetyGateEvaluator.evaluate(
            request=make_gate_request(action=SafetyGateAction.RECOVERY_REORIENTATION),
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        assert r_nav.decision == GateDecision.DENIED_INTERLOCKED
        assert r_align.decision == GateDecision.DENIED_INTERLOCKED
        assert r_rec.decision == GateDecision.PERMITTED_WITH_CAUTION

        """Matrix B: M13 DRAFT isolation."""
        mock_drift_service.get_drift_status.return_value.state.value = "STABLE"
        mock_registration_service.get_registration.return_value.state = RegistrationState.DRAFT
        r_nav_b = SafetyGateEvaluator.evaluate(
            request=make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION),
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        r_align_b = SafetyGateEvaluator.evaluate(
            request=make_gate_request(action=SafetyGateAction.TRAJECTORY_ALIGNMENT),
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        r_rec_b = SafetyGateEvaluator.evaluate(
            request=make_gate_request(action=SafetyGateAction.RECOVERY_REORIENTATION),
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        assert r_nav_b.decision == GateDecision.DENIED_INTERLOCKED
        assert r_align_b.decision == GateDecision.PERMITTED_WITH_CAUTION
        assert r_rec_b.decision == GateDecision.PERMITTED_CLEAR

        """Matrix C: M15 WARNING isolation."""
        mock_registration_service.get_registration.return_value.state = RegistrationState.VERIFIED
        mock_proximity_service.get_proximity_status.return_value.state.value = "WARNING"
        for act in (SafetyGateAction.TOOL_NAVIGATION, SafetyGateAction.TRAJECTORY_ALIGNMENT, SafetyGateAction.RECOVERY_REORIENTATION):
            r_act = SafetyGateEvaluator.evaluate(
                request=make_gate_request(action=act),
                epoch_id=1,
                workflow_service=mock_workflow_service,
                registration_service=mock_registration_service,
                navigation_service=mock_navigation_service,
                proximity_service=mock_proximity_service,
                drift_service=mock_drift_service,
                recovery_service=mock_recovery_service,
            )
            assert r_act.decision == GateDecision.PERMITTED_WITH_CAUTION
            assert r_act.reason_code == GateReasonCode.APPROACHING_MARGIN

        # WORKFLOW_RESUMPTION is strictly denied under proximity WARNING
        r_res = SafetyGateEvaluator.evaluate(
            request=make_gate_request(action=SafetyGateAction.WORKFLOW_RESUMPTION),
            epoch_id=1,
            workflow_service=mock_workflow_service,
            registration_service=mock_registration_service,
            navigation_service=mock_navigation_service,
            proximity_service=mock_proximity_service,
            drift_service=mock_drift_service,
            recovery_service=mock_recovery_service,
        )
        assert r_res.decision == GateDecision.DENIED_INTERLOCKED

        """Matrix D: M17 FAILED isolation."""
        mock_proximity_service.get_proximity_status.return_value.state.value = "SAFE"
        mock_recovery_service.get_recovery_status.return_value.state.value = "FAILED"
        for act in SafetyGateAction:
            r_act = SafetyGateEvaluator.evaluate(
                request=make_gate_request(action=act),
                epoch_id=1,
                workflow_service=mock_workflow_service,
                registration_service=mock_registration_service,
                navigation_service=mock_navigation_service,
                proximity_service=mock_proximity_service,
                drift_service=mock_drift_service,
                recovery_service=mock_recovery_service,
            )
            assert r_act.decision == GateDecision.DENIED_INTERLOCKED
            assert r_act.reason_code == GateReasonCode.SPATIAL_RECOVERY_FAILED

        """Matrix E: M15 CRITICAL_BREACH isolation."""
        mock_recovery_service.get_recovery_status.return_value.state.value = "IDLE"
        mock_proximity_service.get_proximity_status.return_value.state.value = "CRITICAL_BREACH"
        for act in SafetyGateAction:
            r_act = SafetyGateEvaluator.evaluate(
                request=make_gate_request(action=act),
                epoch_id=1,
                workflow_service=mock_workflow_service,
                registration_service=mock_registration_service,
                navigation_service=mock_navigation_service,
                proximity_service=mock_proximity_service,
                drift_service=mock_drift_service,
                recovery_service=mock_recovery_service,
            )
            assert r_act.decision == GateDecision.DENIED_CRITICAL
            assert r_act.reason_code == GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH

    def test_epoch_mismatch_denies(
        self,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
    ) -> None:
        mock_drift_service.get_drift_status.return_value.epoch_id = 2  # Mismatched runtime epoch 1

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
        assert rec.reason_code == GateReasonCode.RUNTIME_EPOCH_MISMATCH

    def test_warnings_return_permitted_with_caution(
        self,
        mock_workflow_service,
        mock_registration_service,
        mock_navigation_service,
        mock_proximity_service,
        mock_drift_service,
        mock_recovery_service,
    ) -> None:
        mock_proximity_service.get_proximity_status.return_value.state.value = "WARNING"

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
        assert rec.decision == GateDecision.PERMITTED_WITH_CAUTION
        assert rec.severity == GateSeverity.WARNING
        assert rec.reason_code == GateReasonCode.APPROACHING_MARGIN
