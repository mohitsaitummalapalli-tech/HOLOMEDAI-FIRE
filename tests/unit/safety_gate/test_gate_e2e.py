# -*- coding: utf-8 -*-
"""End-to-End Tests for M18 Safety Gate — Dynamic Intraoperative Multi-Service Workflow."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from holomed.runtime.service import ServiceState
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    SafetyGateAction,
)
from holomed.safety_gate.service import SafetyGateService
from tests.unit.safety_gate.conftest import make_gate_request


class TestSafetyGateE2E:
    """Dynamic multi-service clinical lifecycle scenario."""

    def test_full_intraoperative_lifecycle_with_safety_gate(
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
        """Full scenario:
        1. All systems nominal -> PERMITTED_CLEAR
        2. Proximity warning -> PERMITTED_WITH_CAUTION (APPROACHING_MARGIN)
        3. Landmark drift exceeded -> DENIED_INTERLOCKED for navigation
        4. Reorientation requested -> PERMITTED_WITH_CAUTION for recovery
        5. Recovery completes -> PERMITTED_CLEAR for navigation
        """
        gate_svc = SafetyGateService(
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
        gate_svc.initialize(runtime_context)
        gate_svc.start()
        assert gate_svc.state == ServiceState.STARTED

        # Phase 1: All nominal
        r1 = gate_svc.evaluate(make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION, sequence_number=1))
        assert r1.decision == GateDecision.PERMITTED_CLEAR

        # Phase 2: Proximity Warning
        mock_proximity_service.get_proximity_status.return_value.state.value = "WARNING"
        r2 = gate_svc.evaluate(make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION, sequence_number=2))
        assert r2.decision == GateDecision.PERMITTED_WITH_CAUTION
        assert r2.reason_code == GateReasonCode.APPROACHING_MARGIN

        # Phase 3: Landmark drift exceeded -> Navigation blocked
        mock_proximity_service.get_proximity_status.return_value.state.value = "SAFE"
        mock_drift_service.get_drift_status.return_value.state.value = "DRIFT_EXCEEDED"
        r3 = gate_svc.evaluate(make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION, sequence_number=3))
        assert r3.decision == GateDecision.DENIED_INTERLOCKED
        assert r3.reason_code == GateReasonCode.LANDMARK_DRIFT_EXCEEDED

        # Phase 4: Recovery requested -> Permitted for recovery specifically
        r4 = gate_svc.evaluate(make_gate_request(action=SafetyGateAction.RECOVERY_REORIENTATION, sequence_number=4))
        assert r4.decision == GateDecision.PERMITTED_WITH_CAUTION

        # Phase 5: Recovery completes -> All nominal again
        mock_drift_service.get_drift_status.return_value.state.value = "READY"
        mock_recovery_service.get_recovery_status.return_value.state.value = "ACTIVATED"
        r5 = gate_svc.evaluate(make_gate_request(action=SafetyGateAction.TOOL_NAVIGATION, sequence_number=5))
        assert r5.decision == GateDecision.PERMITTED_CLEAR

        # Clean teardown
        gate_svc.stop()
        assert gate_svc.state == ServiceState.STOPPED
        assert gate_svc.resources.is_empty
