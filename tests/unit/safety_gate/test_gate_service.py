# -*- coding: utf-8 -*-
"""Unit Tests for M18 SafetyGateService — Lifecycle, Handles, Routes & Persistence Deduplication."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from holomed.protocol.builders import create_command, create_query
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState
from holomed.safety_gate.constants import STRUCTURAL_RESOURCE_IDS
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateSeverity,
    SafetyGateAction,
)
from holomed.safety_gate.service import SafetyGateService
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


class TestSafetyGateServiceLifecycle:
    """Tests for 4-handle lifecycle and health."""

    def test_initialize_acquires_exactly_4_structural_handles(
        self, mock_dispatcher, secret_filter, logger, runtime_context
    ) -> None:
        svc = SafetyGateService(
            dispatcher=mock_dispatcher,
            secret_filter=secret_filter,
            logger=logger,
        )
        assert svc.state == ServiceState.UNINITIALIZED

        svc.initialize(runtime_context)
        assert svc.state == ServiceState.INITIALIZED
        assert len(svc.resources.outstanding_handles) == 4
        assert {h.resource_id for h in svc.resources.outstanding_handles} == set(STRUCTURAL_RESOURCE_IDS)

    def test_start_and_stop_releases_handles_idempotently(
        self, mock_dispatcher, secret_filter, logger, runtime_context
    ) -> None:
        svc = SafetyGateService(
            dispatcher=mock_dispatcher,
            secret_filter=secret_filter,
            logger=logger,
        )
        svc.initialize(runtime_context)
        svc.start()
        assert svc.state == ServiceState.STARTED

        svc.stop()
        assert svc.state == ServiceState.STOPPED
        assert svc.resources.is_empty

        # Idempotent stop
        svc.stop()
        assert svc.state == ServiceState.STOPPED

    def test_health_reporting(
        self, mock_dispatcher, secret_filter, logger, runtime_context
    ) -> None:
        svc = SafetyGateService(
            dispatcher=mock_dispatcher,
            secret_filter=secret_filter,
            logger=logger,
        )
        assert svc.health().status == HealthStatus.UNHEALTHY

        svc.initialize(runtime_context)
        svc.start()
        assert svc.health().status == HealthStatus.HEALTHY


class TestSafetyGatePersistenceDeduplication:
    """Tests confirming persistence on transitions and suppression on identical frames."""

    def test_repeated_identical_evaluations_persisted_only_once(
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

        req1 = make_gate_request(sequence_number=1)
        req2 = make_gate_request(sequence_number=2)
        req3 = make_gate_request(sequence_number=3)

        mock_persistence_service.record_audit.reset_mock()

        # Frame 1: First evaluation -> Persisted
        svc.evaluate(req1)
        assert mock_persistence_service.record_audit.call_count == 1

        # Frame 2: Identical state -> Suppressed
        svc.evaluate(req2)
        assert mock_persistence_service.record_audit.call_count == 1

        # Frame 3: Identical state -> Suppressed
        svc.evaluate(req3)
        assert mock_persistence_service.record_audit.call_count == 1

        # Frame 4: State transition to Warning -> Persisted!
        mock_proximity_service.get_proximity_status.return_value.state.value = "WARNING"
        req4 = make_gate_request(sequence_number=4)
        svc.evaluate(req4)
        assert mock_persistence_service.record_audit.call_count == 2


class TestSafetyGateDispatcherRoutes:
    """Tests for dispatcher commands and queries."""

    def test_evaluate_command_route(
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

        cmd = create_command(
            message_name="safety_gate.evaluate",
            source="test",
            target="safety_gate_service",
            payload={
                "session_id": "session-01",
                "action": "TOOL_NAVIGATION",
                "sequence_number": 1,
            },
        )
        resp = svc.handle_evaluate_command(cmd)
        assert resp.payload["decision"] == "PERMITTED_CLEAR"

        # Query route
        qry = create_query(
            message_name="safety_gate.status.get",
            source="test",
            target="safety_gate_service",
            payload={"session_id": "session-01"},
        )
        q_resp = svc.handle_get_status_query(qry)
        assert q_resp.payload["decision"] == "PERMITTED_CLEAR"
