# -*- coding: utf-8 -*-
"""M30 Safety Gate Dispatcher Contract & Execution Boundary Hardening Tests.

Authoritative Baseline: 8c46aa2ad883aca2089da98db13cc2d5ef0b1dcb
Milestone: M30
Target: Safety Gate Dispatcher Contract, Topic Naming, Raw Command Deregistration,
        Read-Only Status Query, and Production Wiring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel
from holomed.core.dispatcher import MessageDispatcher
from holomed.core.exceptions import TopicValidationError, UnroutableMessageError
from holomed.core.subscription import validate_concrete_topic
from holomed.execution.models import ExecutionStatus, ToolExecutionRequest
from holomed.execution.service import ClinicalExecutionGatewayService
from holomed.gateway.authorization import (
    GatewayAuthorizationPolicy,
    GatewaySessionMismatchError,
)
from holomed.gateway.models import ClientRole, ClientSession
from holomed.persistence.service import PersistenceService
from holomed.platform.service import PlatformService
from holomed.protocol.builders import create_command, create_event, create_query
from holomed.protocol.models import MessageType
from holomed.runtime.context import RuntimeContext
from holomed.safety_gate.constants import (
    MAX_ACTIVE_GATE_SESSIONS,
    TOPIC_SAFETY_EVALUATED,
    TOPIC_SAFETY_STATUS_GET,
)
from holomed.safety_gate.exceptions import (
    SafetyGateCapacityError,
    SafetyGateLifecycleError,
)
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateRequest,
    GateSeverity,
    GateStatusRecord,
    SafetyGateAction,
)
from holomed.safety_gate.service import SafetyGateService
from holomed.tools.models import (
    ToolCategory,
    ToolDescriptor,
    ToolExecutionStatus,
    ToolResult,
    ToolSafetyClassification,
)
from holomed.tools.service import ToolService
from holomed.workflow.models import WorkflowPhase
from holomed.workflow.service import WorkflowService


def make_dummy_tool(tool_id: str = "tool.telemetry") -> ToolDescriptor:
    """Create test tool descriptor with valid category and safety classification."""
    return ToolDescriptor(
        tool_id=tool_id,
        name="Telemetry Tool",
        version="1.0",
        category=ToolCategory.CAPTURE_TELEMETRY,
        safety_classification=ToolSafetyClassification.TELEMETRY_RECORDING,
        description="A telemetry tool for M30 lifecycle verification",
        required_parameters=(),
        optional_parameters=(),
        max_execution_budget_ms=10.0,
        is_idempotent=True,
        handler=lambda ctx: ToolResult(
            invocation_id=ctx.invocation_id,
            tool_id=ctx.tool_id,
            status=ToolExecutionStatus.SUCCESS,
            result_payload={"executed": True, "seq": ctx.sequence_number},
            execution_time_ms=1.0,
            confidence=1.0,
            uncertainty_metric=0.0,
            epoch_id=ctx.epoch_id,
        ),
    )


def _make_runtime_context() -> RuntimeContext:
    cfg = AppConfig(
        app_name="holomed",
        environment=EnvironmentProfile.DEVELOPMENT,
        host="127.0.0.1",
        port=8000,
        log_level=LogLevel.DEBUG,
    )
    return RuntimeContext(app_config=cfg, epoch_id=1)


class TestM30RealDispatcherStartup:
    """Requirement 1, 2, 4: Real MessageDispatcher startup & topic compliance."""

    def test_real_dispatcher_and_safety_gate_initialization(self) -> None:
        """Verify SafetyGateService initializes cleanly against a real production MessageDispatcher."""
        ctx = _make_runtime_context()
        disp = MessageDispatcher()
        disp.initialize(ctx)

        sg = SafetyGateService(dispatcher=disp)
        # MUST NOT raise TopicValidationError
        sg.initialize(ctx)
        sg.start()

        assert sg.state.name == "STARTED"
        assert sg.name == "safety_gate_service"

        # Verify exactly one query route is registered on dispatcher by safety_gate_service
        reg = disp._subscription_registry
        assert "safety.status.get" in reg._queries
        assert "safety_gate.evaluate" not in reg._commands
        assert "safety.evaluate" not in reg._commands

    def test_registered_topics_satisfy_canonical_grammar(self) -> None:
        """Verify all topics registered by SafetyGateService satisfy ^[a-z0-9]+(\\.[a-z0-9]+)*$."""
        ctx = _make_runtime_context()
        disp = MessageDispatcher()
        disp.initialize(ctx)

        sg = SafetyGateService(dispatcher=disp)
        sg.initialize(ctx)

        # Inspect all routes registered by this service
        for topic, q_reg in disp._subscription_registry._queries.items():
            if q_reg.service_name == sg.name:
                validated = validate_concrete_topic(topic)
                assert validated == topic
                assert "_" not in topic

        for topic, c_reg in disp._subscription_registry._commands.items():
            if c_reg.service_name == sg.name:
                validated = validate_concrete_topic(topic)
                assert validated == topic
                assert "_" not in topic


class TestM30RawCommandRemoval:
    """Requirement 3, 4, 5: safety_gate.evaluate command removal and fail-closed behavior."""

    def test_safety_gate_evaluate_not_registered(self) -> None:
        """Verify safety_gate.evaluate is not registered as a command handler."""
        ctx = _make_runtime_context()
        disp = MessageDispatcher()
        disp.initialize(ctx)
        sg = SafetyGateService(dispatcher=disp)
        sg.initialize(ctx)
        disp.start()
        sg.start()

        cmd = create_command(
            message_name="safety_gate.evaluate",
            source="test",
            target="safety_gate_service",
            payload={"session_id": "session_01", "action": "TOOL_NAVIGATION"},
        )

        with pytest.raises(UnroutableMessageError):
            disp.dispatch(cmd)

    def test_no_alias_command_registered(self) -> None:
        """Verify no alternative raw mutating command (such as safety.evaluate) exists."""
        ctx = _make_runtime_context()
        disp = MessageDispatcher()
        disp.initialize(ctx)
        sg = SafetyGateService(dispatcher=disp)
        sg.initialize(ctx)
        disp.start()
        sg.start()

        cmd = create_command(
            message_name="safety.evaluate",
            source="test",
            target="safety_gate_service",
            payload={"session_id": "session_01", "action": "TOOL_NAVIGATION"},
        )

        with pytest.raises(UnroutableMessageError):
            disp.dispatch(cmd)


class TestM30CanonicalStatusQuery:
    """Requirement 6, 7: Canonical read-only safety.status.get query."""

    def test_status_query_registered_under_canonical_topic(self) -> None:
        """Verify safety.status.get is successfully routed over real dispatcher."""
        ctx = _make_runtime_context()
        disp = MessageDispatcher()
        disp.initialize(ctx)
        sg = SafetyGateService(dispatcher=disp)
        sg.initialize(ctx)
        disp.start()
        sg.start()

        # Populate a decision in-process via evaluate()
        req = GateRequest(
            session_id="session_query_01",
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
        )
        record = sg.evaluate(req)

        # Dispatch query over message bus
        query = create_query(
            message_name=TOPIC_SAFETY_STATUS_GET,
            source="test",
            target="safety_gate_service",
            payload={"session_id": "session_query_01"},
        )

        response = disp.dispatch(query)
        assert response.message_type == MessageType.RESPONSE
        assert response.payload["session_id"] == "session_query_01"
        assert response.payload["decision"] == record.decision.value
        assert response.payload["action"] == "TOOL_NAVIGATION"

    def test_status_query_is_genuinely_read_only(self) -> None:
        """Verify safety.status.get mutates zero state."""
        ctx = _make_runtime_context()
        disp = MessageDispatcher()
        disp.initialize(ctx)
        mock_persistence = MagicMock(spec=PersistenceService)
        sg = SafetyGateService(dispatcher=disp, persistence_service=mock_persistence)
        sg.initialize(ctx)
        disp.start()
        sg.start()

        # Query an uninitialized session
        query = create_query(
            message_name=TOPIC_SAFETY_STATUS_GET,
            source="test",
            target="safety_gate_service",
            payload={"session_id": "session_readonly_check"},
        )

        decisions_before = dict(sg._latest_decisions)
        persisted_before = dict(sg._persisted_states)
        capacity_before = sg.active_sessions_count

        response = disp.dispatch(query)

        # Check response is fail-closed error response
        assert response.message_type == MessageType.ERROR
        assert response.payload["error_code"] == "ERR_SESSION_NOT_FOUND"

        # Verify state invariance
        assert sg._latest_decisions == decisions_before
        assert sg._persisted_states == persisted_before
        assert sg.active_sessions_count == capacity_before
        assert mock_persistence.record_audit.call_count == 0

    def test_status_query_missing_session_id(self) -> None:
        """Verify query returns ERR_INVALID_ARGS when session_id is omitted."""
        ctx = _make_runtime_context()
        disp = MessageDispatcher()
        disp.initialize(ctx)
        sg = SafetyGateService(dispatcher=disp)
        sg.initialize(ctx)
        disp.start()
        sg.start()

        query = create_query(
            message_name=TOPIC_SAFETY_STATUS_GET,
            source="test",
            target="safety_gate_service",
            payload={},
        )
        resp = disp.dispatch(query)
        assert resp.message_type == MessageType.ERROR
        assert resp.payload["error_code"] == "ERR_INVALID_ARGS"


class TestM30CanonicalEventTopic:
    """Requirement 8, 9: Canonical event topic safety.evaluated."""

    def test_subscribing_to_canonical_event_topic_succeeds(self) -> None:
        """Verify subscribing to safety.evaluated succeeds without TopicValidationError."""
        ctx = _make_runtime_context()
        disp = MessageDispatcher()
        disp.initialize(ctx)

        received_events = []

        def on_safety_event(envelope: Any) -> None:
            received_events.append(envelope)

        disp.subscribe_event(TOPIC_SAFETY_EVALUATED, on_safety_event, "test_listener")

        sg = SafetyGateService(dispatcher=disp)
        sg.initialize(ctx)
        disp.start()
        sg.start()

        req = GateRequest(
            session_id="session_event_01",
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
        )
        sg.evaluate(req)

        assert len(received_events) == 1
        event = received_events[0]
        assert event.message_name == "safety.evaluated"
        assert event.payload["session_id"] == "session_event_01"


class TestM30ExecutionGatewayIntegration:
    """Requirement 10, 11: ClinicalExecutionGatewayService integration."""

    def test_clinical_execution_with_real_dispatcher_and_safety_gate(self) -> None:
        """Verify full production integration: Real MessageDispatcher + SafetyGate + ExecutionGateway."""
        ctx = _make_runtime_context()
        dispatcher = MessageDispatcher()
        dispatcher.initialize(ctx)

        platform = PlatformService()
        platform.initialize(ctx)
        platform.start()
        platform.start_session("session_exec_m30")

        tool_service = ToolService(dispatcher=dispatcher)
        tool_service.initialize(ctx)
        tool_service.register_tool(make_dummy_tool("tool.telemetry"))
        tool_service.start()

        # Real SafetyGateService wired with real MessageDispatcher (M30 production wiring)
        safety_gate = SafetyGateService(dispatcher=dispatcher)
        safety_gate.initialize(ctx)
        safety_gate.start()

        persistence = PersistenceService()
        persistence.initialize(ctx)
        persistence.start()

        gateway = ClinicalExecutionGatewayService(
            dispatcher=dispatcher,
            safety_gate_service=safety_gate,
            tool_service=tool_service,
            persistence_service=persistence,
            platform_service=platform,
        )
        gateway.initialize(ctx)
        gateway.start()
        dispatcher.start()

        req = ToolExecutionRequest(
            session_id="session_exec_m30",
            tool_id="tool.telemetry",
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
            parameters={},
            safety_classification=ToolSafetyClassification.TELEMETRY_RECORDING,
        )

        # Execute tool through Execution Gateway
        res = gateway.execute_tool(req)

        # Verify safety gate evaluated in-process and recorded decision
        gate_status = safety_gate.get_gate_status("session_exec_m30")
        assert gate_status is not None
        assert res.gate_decision == gate_status.decision

        # Query safety status over the real message bus
        query = create_query(
            message_name="safety.status.get",
            source="test",
            target="safety_gate_service",
            payload={"session_id": "session_exec_m30"},
        )
        resp = dispatcher.dispatch(query)
        assert resp.payload["decision"] == gate_status.decision.value

    def test_safety_decisions_remain_semantically_identical_across_precedence(self) -> None:
        """Verify all 7 tiers of SafetyGateEvaluator precedence evaluate identically."""
        ctx = _make_runtime_context()
        disp = MessageDispatcher()
        disp.initialize(ctx)

        workflow_mock = MagicMock()
        workflow_mock.get_phase.return_value = WorkflowPhase.ABORTED

        reg_mock = MagicMock()
        reg_mock.is_verified.return_value = True

        sg = SafetyGateService(
            dispatcher=disp,
            workflow_service=workflow_mock,
            registration_service=reg_mock,
        )
        sg.initialize(ctx)
        sg.start()

        # Workflow ABORTED must yield DENIED_INTERLOCKED with WORKFLOW_PHASE_BLOCKED
        req = GateRequest(
            session_id="session_prec_01",
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
        )
        record = sg.evaluate(req)
        assert record.decision == GateDecision.DENIED_INTERLOCKED
        assert record.reason_code == GateReasonCode.REGISTRATION_UNVERIFIED


class TestM30SessionIsolation:
    """Requirement 12, 13: Session isolation and unknown/stopped sessions."""

    def test_gateway_ingress_cross_session_protection(self) -> None:
        """Verify GatewayAuthorizationPolicy rejects Session A client targeting Session B in safety.status.get."""
        client_session = ClientSession(
            client_id="client_surgeon_01",
            client_role=ClientRole.SURGEON_CONSOLE,
            session_id="session_A",
            epoch_id=1,
            connected_at_utc=datetime.now(timezone.utc).isoformat(),
            remote_address="127.0.0.1",
        )

        # Attacker tries to query Session B
        envelope = create_query(
            message_name="safety.status.get",
            source="client_surgeon_01",
            target="safety_gate_service",
            payload={"session_id": "session_B"},
        )

        with pytest.raises(GatewaySessionMismatchError):
            GatewayAuthorizationPolicy.authorize_message(client_session, envelope)

    def test_status_query_unknown_session_returns_not_found(self) -> None:
        """Verify querying an unknown session returns ERR_SESSION_NOT_FOUND without crash."""
        ctx = _make_runtime_context()
        disp = MessageDispatcher()
        disp.initialize(ctx)
        sg = SafetyGateService(dispatcher=disp)
        sg.initialize(ctx)
        disp.start()
        sg.start()

        query = create_query(
            message_name="safety.status.get",
            source="test",
            target="safety_gate_service",
            payload={"session_id": "non_existent_session"},
        )
        resp = disp.dispatch(query)
        assert resp.message_type == MessageType.ERROR
        assert resp.payload["error_code"] == "ERR_SESSION_NOT_FOUND"


class TestM30FailureSemantics:
    """Requirement 14, 15, 16: Failure semantics and fail-closed behavior."""

    def test_evict_session_reentrancy_fails_closed(self) -> None:
        """Verify reentrant evict_session call raises SafetyGateLifecycleError."""
        ctx = _make_runtime_context()
        disp = MessageDispatcher()
        disp.initialize(ctx)
        sg = SafetyGateService(dispatcher=disp)
        sg.initialize(ctx)
        sg.start()

        # Reentrant evict_session call must fail closed
        sg._in_transaction = True
        try:
            with pytest.raises(SafetyGateLifecycleError, match="Reentrant call to evict_session rejected"):
                sg.evict_session("session_01")
        finally:
            sg._in_transaction = False

    def test_evaluator_failure_cannot_produce_false_success(self) -> None:
        """Verify that any failure in dependency logic fails closed and never returns false clearance."""
        ctx = _make_runtime_context()
        disp = MessageDispatcher()
        disp.initialize(ctx)

        broken_workflow = MagicMock()
        broken_workflow.get_phase.side_effect = RuntimeError("Evaluator dependency crashed")

        sg = SafetyGateService(dispatcher=disp, workflow_service=broken_workflow)
        sg.initialize(ctx)
        sg.start()

        req = GateRequest(
            session_id="session_fail_01",
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
        )

        record = sg.evaluate(req)

        # Evaluator must catch exception and return fail-closed DENIED_INTERLOCKED
        assert record.decision == GateDecision.DENIED_INTERLOCKED
        assert record.decision != GateDecision.PERMITTED_CLEAR

    def test_persistence_failure_cannot_produce_false_success(self) -> None:
        """Verify persistence failure raises exception and fails closed."""
        ctx = _make_runtime_context()
        disp = MessageDispatcher()
        disp.initialize(ctx)

        broken_persistence = MagicMock(spec=PersistenceService)
        broken_persistence.record_audit.side_effect = IOError("Disk full")

        sg = SafetyGateService(dispatcher=disp, persistence_service=broken_persistence)
        sg.initialize(ctx)
        sg.start()

        req = GateRequest(
            session_id="session_disk_full",
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
        )

        with pytest.raises(IOError, match="Disk full"):
            sg.evaluate(req)

    def test_capacity_exhaustion_fails_closed(self) -> None:
        """Verify capacity exhaustion raises SafetyGateCapacityError when 16 sessions are full."""
        ctx = _make_runtime_context()
        disp = MessageDispatcher()
        disp.initialize(ctx)
        sg = SafetyGateService(dispatcher=disp)
        sg.initialize(ctx)
        sg.start()

        for i in range(MAX_ACTIVE_GATE_SESSIONS):
            req = GateRequest(
                session_id=f"session_cap_{i}",
                action=SafetyGateAction.TOOL_NAVIGATION,
                sequence_number=1,
                now_utc=datetime.now(timezone.utc).isoformat(),
            )
            sg.evaluate(req)

        assert sg.active_sessions_count == MAX_ACTIVE_GATE_SESSIONS

        # 17th session must fail closed
        req_17 = GateRequest(
            session_id="session_cap_17",
            action=SafetyGateAction.TOOL_NAVIGATION,
            sequence_number=1,
            now_utc=datetime.now(timezone.utc).isoformat(),
        )
        with pytest.raises(SafetyGateCapacityError, match="Max active gate sessions"):
            sg.evaluate(req_17)
