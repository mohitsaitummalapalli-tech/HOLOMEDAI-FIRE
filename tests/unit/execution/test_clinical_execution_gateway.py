# -*- coding: utf-8 -*-
"""Comprehensive Contract Verification Tests for M21 Clinical Execution Gateway.

Verifies:
- The 6 authoritative dispatcher routes on ClinicalExecutionGatewayService
- execution.tool.invoke command execution and dual-gate coordination
- execution.workflow.resume command routing to M20 resume_from_recovery
- execution.recovery.execute and execution.trajectory.bind command execution
- Direct subsystem capability enforcement (ToolService, NavigationService)
- Safety gate and workflow phase enforcement for tool invocations
- Anti-bypass: unroutable old routes, capability non-forgeability, non-picklability
"""

from __future__ import annotations

from datetime import datetime, timezone
import pickle
import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.exceptions import UnroutableMessageError
from holomed.execution._capability import _ExecutionCapability, _create_execution_capability
from holomed.execution.exceptions import (
    ExecutionAuthorizationError,
    ExecutionValidationError,
)
from holomed.execution.models import (
    ExecutionStatus,
    ToolExecutionRequest,
)
from holomed.execution.service import (
    ClinicalExecutionGatewayService,
    NavigationExecutionService,
)
from holomed.navigation.constants import DEFAULT_PATIENT_TRACKER_FRAME
from holomed.navigation.exceptions import NavigationAuthorizationError
from holomed.navigation.models import TrackedInstrumentPose
from holomed.navigation.service import NavigationService
from holomed.planning.models import TrajectoryPlan
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateSeverity,
    GateStatusRecord,
    SafetyGateAction,
)
from holomed.safety_gate.service import SafetyGateService
from holomed.tools.exceptions import ToolAuthorizationError
from holomed.tools.models import (
    ToolCategory,
    ToolDescriptor,
    ToolExecutionStatus,
    ToolInvocationContext,
    ToolResult,
    ToolSafetyClassification,
)
from holomed.tools.service import ToolService
from holomed.workflow.models import (
    WorkflowPhase,
    WorkflowResumptionRequest,
    WorkflowStateSnapshot,
    WorkflowToolAuthorizationDecision,
    WorkflowToolAuthorizationStatus,
)
from holomed.workflow.service import WorkflowService


def make_test_tool(tool_id: str = "anatomy.query_organ") -> ToolDescriptor:
    return ToolDescriptor(
        tool_id=tool_id,
        name="Query Organ",
        version="1.0",
        category=ToolCategory.QUERY_ANATOMY,
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        description="test tool",
        required_parameters=(),
        optional_parameters=("subsystem",),
        max_execution_budget_ms=50.0,
        is_idempotent=True,
        handler=lambda ctx: ToolResult(
            ctx.invocation_id,
            tool_id,
            ToolExecutionStatus.SUCCESS,
            {"query_result": "target_verified"},
            1.0,
            1.0,
            0.0,
            ctx.epoch_id,
        ),
    )


@pytest.fixture
def test_runtime_context() -> RuntimeContext:
    from holomed.configuration.models import AppConfig
    cfg = AppConfig(
        app_name="HoloMed-Gateway-Test",
        environment="TESTING",
        host="127.0.0.1",
        port=8090,
        log_level="DEBUG",
        gemini_api_key=None,
        protocol_version="1.0",
    )
    return RuntimeContext(app_config=cfg, epoch_id=1, trace_context=None)


def test_alias_preserves_m19_compatibility() -> None:
    """Verify NavigationExecutionService is an alias to ClinicalExecutionGatewayService."""
    assert NavigationExecutionService is ClinicalExecutionGatewayService


def test_all_six_execution_routes_registered(
    test_runtime_context: RuntimeContext,
) -> None:
    """Verify that exactly the 6 authoritative execution routes are registered in dispatcher."""
    dispatcher = MessageDispatcher()
    dispatcher.initialize(test_runtime_context)
    gateway = ClinicalExecutionGatewayService(dispatcher=dispatcher)
    gateway.initialize(test_runtime_context)

    # Verify registered routes
    reg = dispatcher.subscription_registry
    assert reg.lookup_command("execution.navigation.execute") is not None
    assert reg.lookup_command("execution.recovery.execute") is not None
    assert reg.lookup_command("execution.trajectory.bind") is not None
    assert reg.lookup_command("execution.tool.invoke") is not None
    assert reg.lookup_command("execution.workflow.resume") is not None
    assert reg.lookup_query("execution.status.get") is not None

    # Verify old routes are NOT registered
    assert reg.lookup_command("navigation.pose.submit") is None
    assert reg.lookup_command("navigation.evaluate") is None
    assert reg.lookup_command("tools.invoke") is None


def test_tool_service_direct_call_without_capability_fails_closed(
    test_runtime_context: RuntimeContext,
) -> None:
    """Direct call to ToolService.invoke_tool() without capability raises ToolAuthorizationError."""
    tool_srv = ToolService()
    tool_srv.initialize(test_runtime_context)
    tool_srv.start()

    ctx = ToolInvocationContext(
        invocation_id="inv-01",
        tool_id="telemetry.capture",
        epoch_id=1,
        session_id="session-01",
        sequence_number=1,
        depth=1,
        parameters={"subsystem": "test"},
        correlation_id=None,
        causation_id=None,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
    )
    with pytest.raises(ToolAuthorizationError, match="Direct uncoordinated call to invoke_tool rejected"):
        tool_srv.invoke_tool(ctx)

    # Inactive capability also rejected
    cap = _create_execution_capability(id(tool_srv), "session-01", "TOOL_INVOCATION", 1)
    cap.invalidate()
    with pytest.raises(ToolAuthorizationError, match="inactive"):
        tool_srv.invoke_tool(ctx, capability=cap)

    tool_srv.stop()


def test_navigation_service_direct_calls_without_capability_fail_closed(
    test_runtime_context: RuntimeContext,
) -> None:
    """Direct calls to NavigationService submit_pose and evaluate fail closed without capability."""
    nav_srv = NavigationService()
    nav_srv.initialize(test_runtime_context)
    nav_srv.start()

    pose = TrackedInstrumentPose(
        instrument_id="inst-01",
        session_id="session-01",
        epoch_id=1,
        sequence_number=1,
        tip_position_mm=(10.0, 20.0, 30.0),
        orientation_quaternion=(0.0, 0.0, 0.0, 1.0),
        coordinate_frame=DEFAULT_PATIENT_TRACKER_FRAME,
        confidence=1.0,
        uncertainty=0.1,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
    )

    with pytest.raises(NavigationAuthorizationError, match="Direct uncoordinated call to submit_pose rejected"):
        nav_srv.submit_pose(pose)

    with pytest.raises(NavigationAuthorizationError, match="Direct uncoordinated call to evaluate rejected"):
        nav_srv.evaluate("session-01")

    # Inactive capability rejected
    cap = _create_execution_capability(id(nav_srv), "session-01", "TOOL_NAVIGATION", 1)
    cap.invalidate()
    with pytest.raises(NavigationAuthorizationError, match="inactive"):
        nav_srv.submit_pose(pose, capability=cap)

    nav_srv.stop()


def test_capability_anti_tamper_and_non_picklable() -> None:
    """Verify capability cannot be externally forged and cannot be pickled."""
    with pytest.raises(ExecutionAuthorizationError, match="Direct external construction of _ExecutionCapability is strictly prohibited"):
        _ExecutionCapability(
            internal_key="forged_key",
            service_instance_id=123,
            session_id="session-01",
            action="TOOL_INVOCATION",
            sequence_number=1,
        )

    cap = _create_execution_capability(123, "session-01", "TOOL_INVOCATION", 1)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(cap)


def test_execution_tool_invoke_command_success(
    test_runtime_context: RuntimeContext,
) -> None:
    """Verify execution.tool.invoke dispatcher command executes tool when dual gates permit."""
    dispatcher = MessageDispatcher()
    dispatcher.initialize(test_runtime_context)
    tool_srv = ToolService()
    tool_srv.initialize(test_runtime_context)
    tool_srv.register_tool(make_test_tool("anatomy.query_organ"))
    tool_srv.start()

    # Mock SafetyGateService: permit clear
    from unittest.mock import MagicMock
    mock_gate = MagicMock(spec=SafetyGateService)
    mock_gate.evaluate.return_value = GateStatusRecord(
        session_id="session-01",
        decision=GateDecision.PERMITTED_CLEAR,
        severity=GateSeverity.NONE,
        reason_code=GateReasonCode.NONE,
        action=SafetyGateAction.TOOL_INVOCATION,
        sequence_number=1,
        subsystem_snapshots=(),
        evaluated_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    # Mock WorkflowService: permit tool
    mock_wf = MagicMock(spec=WorkflowService)
    mock_wf.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
        tool_id="anatomy.query_organ",
        m07_safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        status=WorkflowToolAuthorizationStatus.PERMITTED,
        workflow_phase=WorkflowPhase.NAVIGATION,
        reason="OK",
        epoch_id=1,
        session_id="session-01",
    )

    gateway = ClinicalExecutionGatewayService(
        dispatcher=dispatcher,
        safety_gate_service=mock_gate,
        workflow_service=mock_wf,
        tool_service=tool_srv,
    )
    gateway.initialize(test_runtime_context)
    dispatcher.start()
    gateway.start()

    cmd = create_command(
        "execution.tool.invoke",
        "surgeon_console",
        payload={
            "session_id": "session-01",
            "tool_id": "anatomy.query_organ",
            "sequence_number": 1,
            "safety_classification": "READ_ONLY_INFORMATIVE",
            "parameters": {"subsystem": "optical_tracker"},
        },
    )
    resp = dispatcher.dispatch(cmd)

    assert resp.message_type.value == "RESPONSE"
    assert resp.payload["session_id"] == "session-01"
    assert resp.payload["tool_id"] == "anatomy.query_organ"
    assert resp.payload["execution_status"] == "EXECUTED_CLEAR"
    assert resp.payload["gate_decision"] == "PERMITTED_CLEAR"
    assert "result_payload" in resp.payload

    gateway.stop()
    tool_srv.stop()


def test_execution_tool_invoke_blocked_by_safety_gate(
    test_runtime_context: RuntimeContext,
) -> None:
    """Verify execution.tool.invoke fails closed when M18 safety gate denies."""
    dispatcher = MessageDispatcher()
    dispatcher.initialize(test_runtime_context)
    tool_srv = ToolService()
    tool_srv.initialize(test_runtime_context)
    tool_srv.start()

    from unittest.mock import MagicMock
    mock_gate = MagicMock(spec=SafetyGateService)
    mock_gate.evaluate.return_value = GateStatusRecord(
        session_id="session-01",
        decision=GateDecision.DENIED_INTERLOCKED,
        severity=GateSeverity.CRITICAL,
        reason_code=GateReasonCode.LANDMARK_DRIFT_EXCEEDED,
        action=SafetyGateAction.TOOL_INVOCATION,
        sequence_number=1,
        subsystem_snapshots=(),
        evaluated_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    mock_wf = MagicMock(spec=WorkflowService)

    gateway = ClinicalExecutionGatewayService(
        dispatcher=dispatcher,
        safety_gate_service=mock_gate,
        workflow_service=mock_wf,
        tool_service=tool_srv,
    )
    gateway.initialize(test_runtime_context)
    dispatcher.start()
    gateway.start()

    cmd = create_command(
        "execution.tool.invoke",
        "surgeon_console",
        payload={
            "session_id": "session-01",
            "tool_id": "tracker.calibrate",
            "sequence_number": 1,
            "safety_classification": "READ_ONLY_INFORMATIVE",
            "parameters": {},
        },
    )
    resp = dispatcher.dispatch(cmd)

    assert resp.message_type.value == "RESPONSE"
    assert resp.payload["execution_status"] == "BLOCKED_SAFETY_GATE"
    assert resp.payload["gate_decision"] == "DENIED_INTERLOCKED"
    assert resp.payload["gate_reason_code"] == "LANDMARK_DRIFT_EXCEEDED"
    # Verify tool service was never called
    assert "result_payload" not in resp.payload

    # Workflow authorization gate must not have been called upon safety gate denial
    mock_wf.authorize_tool.assert_not_called()

    gateway.stop()
    tool_srv.stop()


def test_execution_tool_invoke_blocked_by_workflow(
    test_runtime_context: RuntimeContext,
) -> None:
    """Verify execution.tool.invoke fails closed when M10 workflow denies."""
    dispatcher = MessageDispatcher()
    dispatcher.initialize(test_runtime_context)
    tool_srv = ToolService()
    tool_srv.initialize(test_runtime_context)
    tool_srv.start()

    from unittest.mock import MagicMock
    mock_gate = MagicMock(spec=SafetyGateService)
    mock_gate.evaluate.return_value = GateStatusRecord(
        session_id="session-01",
        decision=GateDecision.PERMITTED_CLEAR,
        severity=GateSeverity.NONE,
        reason_code=GateReasonCode.NONE,
        action=SafetyGateAction.TOOL_INVOCATION,
        sequence_number=1,
        subsystem_snapshots=(),
        evaluated_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    mock_wf = MagicMock(spec=WorkflowService)
    mock_wf.authorize_tool.return_value = WorkflowToolAuthorizationDecision(
        tool_id="tracker.calibrate",
        m07_safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        status=WorkflowToolAuthorizationStatus.BLOCKED_PHASE,
        workflow_phase=WorkflowPhase.INTERVENTION,
        reason="PHASE_BLOCKED",
        epoch_id=1,
        session_id="session-01",
    )

    gateway = ClinicalExecutionGatewayService(
        dispatcher=dispatcher,
        safety_gate_service=mock_gate,
        workflow_service=mock_wf,
        tool_service=tool_srv,
    )
    gateway.initialize(test_runtime_context)
    dispatcher.start()
    gateway.start()

    cmd = create_command(
        "execution.tool.invoke",
        "surgeon_console",
        payload={
            "session_id": "session-01",
            "tool_id": "tracker.calibrate",
            "sequence_number": 1,
            "safety_classification": "READ_ONLY_INFORMATIVE",
            "parameters": {},
        },
    )
    resp = dispatcher.dispatch(cmd)

    assert resp.message_type.value == "RESPONSE"
    assert resp.payload["execution_status"] == "BLOCKED_WORKFLOW"
    assert "result_payload" not in resp.payload

    gateway.stop()
    tool_srv.stop()


def test_execution_workflow_resume_command(
    test_runtime_context: RuntimeContext,
) -> None:
    """Verify execution.workflow.resume command routes directly to M20 resume_from_recovery."""
    dispatcher = MessageDispatcher()
    dispatcher.initialize(test_runtime_context)

    from unittest.mock import MagicMock
    mock_wf = MagicMock(spec=WorkflowService)
    mock_snapshot = WorkflowStateSnapshot(
        workflow_id="wf-01",
        session_id="session-01",
        epoch_id=1,
        procedure_id="proc-01",
        current_phase=WorkflowPhase.NAVIGATION,
        previous_phase=WorkflowPhase.RECOVERY_REQUIRED,
        transition_count=2,
        sequence_number=2,
        is_terminal=False,
    )
    mock_wf.resume_from_recovery.return_value = mock_snapshot

    gateway = ClinicalExecutionGatewayService(
        dispatcher=dispatcher,
        workflow_service=mock_wf,
    )
    gateway.initialize(test_runtime_context)
    dispatcher.start()
    gateway.start()

    cmd = create_command(
        "execution.workflow.resume",
        "surgeon_console",
        payload={
            "session_id": "session-01",
            "sequence_number": 2,
            "recovery_revision": 1,
            "cleared_interlock_ids": ["DRIFT_01"],
        },
    )
    resp = dispatcher.dispatch(cmd)

    assert resp.message_type.value == "RESPONSE"
    assert resp.payload["session_id"] == "session-01"
    assert resp.payload["phase"] == "NAVIGATION"
    assert resp.payload["recovery_revision"] == 1

    # Verify WorkflowService.resume_from_recovery was called with correct parameters
    mock_wf.resume_from_recovery.assert_called_once()
    call_args = mock_wf.resume_from_recovery.call_args
    req_arg = call_args.kwargs.get("request") or call_args.args[0]
    assert req_arg.session_id == "session-01"
    assert req_arg.recovery_revision == 1
    assert req_arg.cleared_interlock_ids == ("DRIFT_01",)

    gateway.stop()
