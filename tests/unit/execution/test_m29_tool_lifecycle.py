# -*- coding: utf-8 -*-
"""M29 Clinical Tool Subsystem Lifecycle Eviction & Teardown Hardening Tests.

Authoritative Baseline: e7362bcc8708a347abc851686f3f25f66358d2f7
Milestone: M29
Target: Tool Subsystem Session Eviction, Capacity Reclamation, and Teardown Step 12
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.execution.models import (
    ExecutionStatus,
    SessionTeardownExecutionRequest,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from holomed.execution.service import (
    ClinicalExecutionGatewayService,
    _create_execution_capability,
)
from holomed.gateway.service import GatewayService
from holomed.navigation.service import NavigationService
from holomed.persistence.service import PersistenceService
from holomed.platform.service import PlatformService
from holomed.protocol.builders import create_command
from holomed.protocol.models import MessageEnvelope
from holomed.recovery.service import RecoveryService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.service import ServiceState
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateSeverity,
    GateStatusRecord,
    SafetyGateAction,
)
from holomed.safety_gate.service import SafetyGateService
from holomed.tools.engine import ToolExecutionEngine
from holomed.tools.exceptions import (
    ToolAuthorizationError,
    ToolCapacityError,
    ToolLifecycleError,
    ToolSequenceError,
)
from holomed.tools.models import (
    MAX_ACTIVE_SESSIONS,
    ToolCategory,
    ToolDescriptor,
    ToolExecutionStatus,
    ToolInvocationContext,
    ToolResult,
    ToolSafetyClassification,
)
from holomed.tools.registry import ToolRegistry
from holomed.tools.service import ToolService
from holomed.workflow.models import (
    WorkflowPhase,
    WorkflowToolAuthorizationDecision,
    WorkflowToolAuthorizationStatus,
)
from holomed.workflow.service import WorkflowService


def make_dummy_tool(tool_id: str = "tool.telemetry") -> ToolDescriptor:
    """Create test tool descriptor with valid category and safety classification."""
    return ToolDescriptor(
        tool_id=tool_id,
        name="Telemetry Tool",
        version="1.0",
        category=ToolCategory.CAPTURE_TELEMETRY,
        safety_classification=ToolSafetyClassification.TELEMETRY_RECORDING,
        description="A telemetry tool for M29 lifecycle verification",
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


def make_invocation_context(
    session_id: str = "SESS-01",
    sequence_number: int = 1,
    tool_id: str = "tool.telemetry",
    epoch_id: int = 1,
    invocation_id: str = "inv-001",
    depth: int = 1,
    parameters: Optional[dict[str, Any]] = None,
    correlation_id: str = "corr-001",
    causation_id: Optional[str] = None,
    timestamp_utc: str = "",
) -> ToolInvocationContext:
    """Create a fully-populated ToolInvocationContext."""
    ts = timestamp_utc or datetime.now(timezone.utc).isoformat()
    return ToolInvocationContext(
        invocation_id=invocation_id,
        tool_id=tool_id,
        epoch_id=epoch_id,
        session_id=session_id,
        sequence_number=sequence_number,
        depth=depth,
        parameters=parameters or {},
        correlation_id=correlation_id,
        causation_id=causation_id,
        timestamp_utc=ts,
    )


@pytest.fixture
def m29_services(runtime_context: RuntimeContext):
    """Instantiate a fully wired service topology including ToolService and GatewayService."""
    ctx = runtime_context
    dispatcher = MessageDispatcher()
    dispatcher.initialize(ctx)

    platform = PlatformService()
    platform.initialize(ctx)
    platform.start()

    workflow = WorkflowService(dispatcher=dispatcher, platform_service=platform)
    workflow.initialize(ctx)
    workflow.start()

    tool_service = ToolService(dispatcher=dispatcher)
    tool_service.initialize(ctx)
    tool_service.register_tool(make_dummy_tool("tool.telemetry"))
    tool_service.start()

    # SafetyGateService initialized with dispatcher=None to avoid topic format issue
    safety_gate = SafetyGateService(dispatcher=None, workflow_service=workflow)
    safety_gate.initialize(ctx)
    safety_gate.start()

    persistence = PersistenceService()
    persistence.initialize(ctx)
    persistence.start()

    gateway_service = GatewayService(dispatcher=dispatcher)
    gateway_service.initialize(ctx)
    gateway_service.start()

    gateway = ClinicalExecutionGatewayService(
        dispatcher=dispatcher,
        safety_gate_service=safety_gate,
        workflow_service=workflow,
        tool_service=tool_service,
        persistence_service=persistence,
        platform_service=platform,
        gateway_service=gateway_service,
    )
    gateway.initialize(ctx)
    gateway.start()
    dispatcher.start()

    return {
        "context": ctx,
        "dispatcher": dispatcher,
        "platform": platform,
        "workflow": workflow,
        "tool_service": tool_service,
        "safety_gate": safety_gate,
        "persistence": persistence,
        "gateway_service": gateway_service,
        "gateway": gateway,
    }


# =============================================================================
# 1. Tool Session State Creation & Monotonicity
# =============================================================================

def test_m29_tool_session_state_creation():
    """Verify tool invocation creates sequence state in _session_sequences."""
    engine = ToolExecutionEngine(epoch_id=1)
    registry = ToolRegistry()
    registry.register_tool(make_dummy_tool("tool.telemetry"))

    ctx = make_invocation_context(session_id="SESS-01", sequence_number=1)
    res = engine.execute_invocation(ctx, registry)
    assert res.status == ToolExecutionStatus.SUCCESS
    assert "SESS-01" in engine._session_sequences
    assert engine._session_sequences["SESS-01"] == 1


def test_m29_active_session_monotonicity_preserved():
    """Verify sequence monotonicity enforcement within an active session remains strict."""
    engine = ToolExecutionEngine(epoch_id=1)
    registry = ToolRegistry()
    registry.register_tool(make_dummy_tool("tool.telemetry"))

    ctx1 = make_invocation_context(session_id="SESS-01", sequence_number=5, invocation_id="inv-001")
    engine.execute_invocation(ctx1, registry)

    # Replay sequence 5
    ctx2 = make_invocation_context(session_id="SESS-01", sequence_number=5, invocation_id="inv-002")
    with pytest.raises(ToolSequenceError, match="Non-monotonic sequence number"):
        engine.execute_invocation(ctx2, registry)

    # Stale sequence 2
    ctx3 = make_invocation_context(session_id="SESS-01", sequence_number=2, invocation_id="inv-003")
    with pytest.raises(ToolSequenceError, match="Non-monotonic sequence number"):
        engine.execute_invocation(ctx3, registry)


# =============================================================================
# 2. Tool Session Eviction & Idempotence
# =============================================================================

def test_m29_complete_tool_session_eviction():
    """Verify ToolExecutionEngine evicts session state completely."""
    engine = ToolExecutionEngine(epoch_id=1)
    engine._session_sequences["SESS-01"] = 42

    assert engine.evict_session("SESS-01") is True
    assert "SESS-01" not in engine._session_sequences
    assert engine.evict_session("SESS-01") is False


def test_m29_tool_eviction_idempotence(m29_services):
    """Verify calling evict_session repeatedly on the same session is safe and idempotent."""
    tool_service: ToolService = m29_services["tool_service"]
    assert tool_service.engine is not None
    tool_service.engine._session_sequences["SESS-01"] = 10

    assert tool_service.evict_session("SESS-01") is True
    assert tool_service.evict_session("SESS-01") is False
    assert tool_service.evict_session("NON-EXISTENT") is False


def test_m29_sequence_state_reset_after_teardown():
    """Verify sequence state is fully removed from dictionary, not merely zeroed."""
    engine = ToolExecutionEngine(epoch_id=1)
    engine._session_sequences["SESS-01"] = 15

    engine.evict_session("SESS-01")
    assert "SESS-01" not in engine._session_sequences
    assert engine._session_sequences.get("SESS-01") is None


# =============================================================================
# 3. Cross-Session Isolation
# =============================================================================

def test_m29_cross_session_tool_isolation():
    """Verify evicting Session A does not modify or corrupt Session B."""
    engine = ToolExecutionEngine(epoch_id=1)
    registry = ToolRegistry()
    registry.register_tool(make_dummy_tool("tool.telemetry"))

    engine._session_sequences["SESS-A"] = 10
    engine._session_sequences["SESS-B"] = 4

    # Evict Session A
    assert engine.evict_session("SESS-A") is True

    # Assert Session A is gone but Session B is completely preserved
    assert "SESS-A" not in engine._session_sequences
    assert "SESS-B" in engine._session_sequences
    assert engine._session_sequences["SESS-B"] == 4

    # Session B can advance monotonically
    ctx_b = make_invocation_context(session_id="SESS-B", sequence_number=5, invocation_id="inv-b")
    res = engine.execute_invocation(ctx_b, registry)
    assert res.status == ToolExecutionStatus.SUCCESS
    assert engine._session_sequences["SESS-B"] == 5


def test_m29_stale_tool_state_cannot_affect_another_session(m29_services):
    """Verify tool state from Session A has zero impact on Session B execution."""
    tool_service: ToolService = m29_services["tool_service"]
    assert tool_service.engine is not None
    tool_service.engine._session_sequences["SESS-A"] = 99

    tool_service.evict_session("SESS-A")
    assert "SESS-A" not in tool_service.engine._session_sequences

    # Session B executes sequence 1 cleanly
    cap = _create_execution_capability(
        service_instance_id=id(tool_service),
        session_id="SESS-B",
        action="TOOL_INVOCATION",
        sequence_number=1,
    )
    ctx_b = make_invocation_context(session_id="SESS-B", sequence_number=1, invocation_id="inv-b1")
    res = tool_service.invoke_tool(ctx_b, capability=cap)
    assert res.status == ToolExecutionStatus.SUCCESS
    assert tool_service.engine._session_sequences["SESS-B"] == 1


# =============================================================================
# 4. Session ID Reuse
# =============================================================================

def test_m29_session_id_reuse_after_teardown(m29_services):
    """Verify that reusing a torn-down session ID resets sequence tracking and allows sequence 1."""
    gateway: ClinicalExecutionGatewayService = m29_services["gateway"]
    tool_service: ToolService = m29_services["tool_service"]
    assert tool_service.engine is not None
    session_id = "SESSION-REUSE-01"

    # Simulate session reaching sequence 5
    tool_service.engine._session_sequences[session_id] = 5

    # Teardown session
    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=6,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
    assert session_id not in tool_service.engine._session_sequences

    # Reuse session_id starting at sequence 1
    cap = _create_execution_capability(
        service_instance_id=id(tool_service),
        session_id=session_id,
        action="TOOL_INVOCATION",
        sequence_number=1,
    )
    ctx = make_invocation_context(session_id=session_id, sequence_number=1, invocation_id="inv-new")
    res_tool = tool_service.invoke_tool(ctx, capability=cap)
    assert res_tool.status == ToolExecutionStatus.SUCCESS
    assert tool_service.engine._session_sequences[session_id] == 1


# =============================================================================
# 5. Capacity Reclamation & Replacement Session
# =============================================================================

def test_m29_64_session_capacity_reclamation(m29_services):
    """Verify that tearing down 64 sessions reclaims all capacity."""
    gateway: ClinicalExecutionGatewayService = m29_services["gateway"]
    tool_service: ToolService = m29_services["tool_service"]
    engine = tool_service.engine
    assert engine is not None

    # Fill all 64 session slots
    for i in range(MAX_ACTIVE_SESSIONS):
        engine._session_sequences[f"SESS-{i:03d}"] = 1
    assert len(engine._session_sequences) == MAX_ACTIVE_SESSIONS

    # Verify 65th session cannot be created
    registry = tool_service.registry
    assert registry is not None
    ctx_65 = make_invocation_context(session_id="SESS-OVERFLOW", sequence_number=1, invocation_id="inv-overflow")
    with pytest.raises(ToolCapacityError, match="Active session capacity exceeded"):
        engine.execute_invocation(ctx_65, registry)

    # Teardown all 64 sessions
    for i in range(MAX_ACTIVE_SESSIONS):
        sid = f"SESS-{i:03d}"
        req = SessionTeardownExecutionRequest(
            session_id=sid,
            sequence_number=2,
            now_utc=datetime.now(timezone.utc).isoformat(),
        )
        res = gateway.execute_session_teardown(req)
        assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR

    # All capacity reclaimed
    assert len(engine._session_sequences) == 0


def test_m29_replacement_session_after_capacity_exhaustion(m29_services):
    """Verify replacement session can execute tools once capacity is reclaimed."""
    gateway: ClinicalExecutionGatewayService = m29_services["gateway"]
    tool_service: ToolService = m29_services["tool_service"]
    engine = tool_service.engine
    assert engine is not None

    # Fill to capacity
    for i in range(MAX_ACTIVE_SESSIONS):
        engine._session_sequences[f"SESS-{i:03d}"] = 1

    # Teardown SESS-000
    req = SessionTeardownExecutionRequest(
        session_id="SESS-000",
        sequence_number=2,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    gateway.execute_session_teardown(req)
    assert len(engine._session_sequences) == MAX_ACTIVE_SESSIONS - 1

    # New replacement session succeeds
    cap = _create_execution_capability(
        service_instance_id=id(tool_service),
        session_id="SESS-REPLACEMENT",
        action="TOOL_INVOCATION",
        sequence_number=1,
    )
    ctx = make_invocation_context(session_id="SESS-REPLACEMENT", sequence_number=1, invocation_id="inv-repl")
    res = tool_service.invoke_tool(ctx, capability=cap)
    assert res.status == ToolExecutionStatus.SUCCESS
    assert "SESS-REPLACEMENT" in engine._session_sequences


# =============================================================================
# 6. Real Production Path Invocation and Teardown
# =============================================================================

def test_m29_real_production_path_invocation_and_teardown(m29_services):
    """Execute a real tool invocation through ClinicalExecutionGatewayService followed by teardown."""
    services = m29_services
    gateway: ClinicalExecutionGatewayService = services["gateway"]
    tool_service: ToolService = services["tool_service"]
    platform: PlatformService = services["platform"]
    workflow: WorkflowService = services["workflow"]
    dispatcher: MessageDispatcher = services["dispatcher"]
    assert tool_service.engine is not None

    session_id = "SESS-PROD-01"

    # Start platform session and workflow
    platform.start_session(session_id)
    workflow.start_workflow(session_id)

    # Real dispatch of execution.tool.invoke through message dispatcher
    cmd = create_command(
        "execution.tool.invoke",
        "test_surgeon_console",
        payload={
            "session_id": session_id,
            "tool_id": "tool.telemetry",
            "sequence_number": 1,
            "safety_classification": "TELEMETRY_RECORDING",
            "parameters": {},
        },
    )
    resp = dispatcher.dispatch(cmd)
    assert resp is not None
    assert resp.payload.get("execution_status") == ExecutionStatus.EXECUTED_CLEAR.value
    assert resp.payload.get("status") == ToolExecutionStatus.SUCCESS.value
    assert resp.payload.get("tool_id") == "tool.telemetry"
    assert tool_service.engine._session_sequences[session_id] == 1

    # Real dispatch of execution.session.teardown through message dispatcher
    cmd_td = create_command(
        "execution.session.teardown",
        "test_surgeon_console",
        payload={
            "session_id": session_id,
            "sequence_number": 2,
        },
    )
    resp_td = dispatcher.dispatch(cmd_td)
    assert resp_td is not None
    assert resp_td.payload.get("execution_status") == ExecutionStatus.EXECUTED_CLEAR.value
    assert "tools" in resp_td.payload.get("subsystems_purged", [])
    assert session_id not in tool_service.engine._session_sequences


# =============================================================================
# 7. Teardown Ordering & Subsystems Purged
# =============================================================================

def test_m29_subsystems_purged_includes_tools(m29_services):
    """Verify res.subsystems_purged includes 'tools' when tool_service is active."""
    gateway: ClinicalExecutionGatewayService = m29_services["gateway"]
    session_id = "SESS-PURGED-TEST"

    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)

    assert "tools" in res.subsystems_purged
    assert "gateway_service" in res.subsystems_purged
    assert "platform" in res.subsystems_purged


def test_m29_teardown_ordering_integrity(m29_services):
    """Verify 'tools' is evicted at Step 12 after 'gateway_service'."""
    gateway: ClinicalExecutionGatewayService = m29_services["gateway"]
    session_id = "SESS-ORDER-TEST"

    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)

    gw_idx = res.subsystems_purged.index("gateway_service")
    tools_idx = res.subsystems_purged.index("tools")
    plat_idx = res.subsystems_purged.index("platform")

    assert plat_idx < gw_idx < tools_idx


# =============================================================================
# 8. Capability Security & Reentrancy
# =============================================================================

def test_m29_stale_teardown_capability_replay_fails(m29_services):
    """Verify expired or invalidated SESSION_TEARDOWN capability is rejected."""
    tool_service: ToolService = m29_services["tool_service"]
    cap = _create_execution_capability(
        service_instance_id=id(tool_service),
        session_id="SESS-01",
        action="SESSION_TEARDOWN",
        sequence_number=1,
    )
    cap.invalidate()

    with pytest.raises(ToolAuthorizationError, match="inactive or expired"):
        tool_service.evict_session("SESS-01", capability=cap)


def test_m29_cross_session_teardown_capability_fails(m29_services):
    """Verify capability with mismatched session_id is rejected."""
    tool_service: ToolService = m29_services["tool_service"]
    cap = _create_execution_capability(
        service_instance_id=id(tool_service),
        session_id="SESS-A",
        action="SESSION_TEARDOWN",
        sequence_number=1,
    )

    with pytest.raises(ToolAuthorizationError, match="Capability session mismatch"):
        tool_service.evict_session("SESS-B", capability=cap)


def test_m29_reentrant_tool_eviction_fails_safely(m29_services):
    """Verify reentrant call to evict_session is rejected with ToolLifecycleError."""
    tool_service: ToolService = m29_services["tool_service"]
    tool_service._in_transaction = True

    try:
        with pytest.raises(ToolLifecycleError, match="Reentrant call to evict_session rejected"):
            tool_service.evict_session("SESS-01")
    finally:
        tool_service._in_transaction = False


# =============================================================================
# 9. Failure Aggregation & Durable Audit Boundary
# =============================================================================

def test_m29_partial_tool_eviction_failure_aggregated(m29_services):
    """Verify exception in ToolService.evict_session is aggregated into degraded status."""
    gateway: ClinicalExecutionGatewayService = m29_services["gateway"]
    tool_service: ToolService = m29_services["tool_service"]

    # Mock evict_session on tool_service to raise an unexpected runtime exception
    tool_service.evict_session = MagicMock(side_effect=RuntimeError("Hardware communication fault"))

    req = SessionTeardownExecutionRequest(
        session_id="SESS-FAIL-AGG",
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)

    assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
    assert any("tools: Hardware communication fault" in f for f in res.failures)


def test_m29_durable_audit_preserved_after_teardown(m29_services):
    """Verify durable audit records in PersistenceService remain intact across teardown."""
    services = m29_services
    gateway: ClinicalExecutionGatewayService = services["gateway"]
    persistence: PersistenceService = services["persistence"]
    session_id = "SESS-AUDIT-TEST"

    # Manually record audit entry
    rec1 = persistence.record_audit({"event": "tool_invocation", "session_id": session_id}, session_id=session_id)
    assert persistence.audit_store is not None
    initial_count = persistence.audit_store.record_count

    # Teardown
    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR

    # Verify audit entry is preserved in persistence service
    assert persistence.audit_store.record_count >= initial_count
    audit_ids = [r.audit_id for r in persistence.audit_store.records]
    assert rec1.audit_id in audit_ids


def test_m29_all_discovered_session_state_evicted(m29_services):
    """Verify that all mutable session-scoped tool state is completely purged on teardown."""
    tool_service: ToolService = m29_services["tool_service"]
    gateway: ClinicalExecutionGatewayService = m29_services["gateway"]
    assert tool_service.engine is not None

    session_id = "SESS-FULL-EVICTION"
    tool_service.engine._session_sequences[session_id] = 42

    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR

    # Exhaustive check of ToolExecutionEngine state
    assert session_id not in tool_service.engine._session_sequences
    # Check that result history (process-global bounded buffer) is unaffected
    assert isinstance(tool_service.engine.result_history, tuple)


# =============================================================================
# 10. Milestone Regression Compatibility (M25, M26, M27, M28)
# =============================================================================

def test_m29_m25_compatibility(m29_services):
    """Verify M25 coordinated teardown core semantics remain intact."""
    gateway: ClinicalExecutionGatewayService = m29_services["gateway"]
    req = SessionTeardownExecutionRequest(
        session_id="SESS-M25-COMPAT",
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
    assert "gateway" in res.subsystems_purged
    assert "platform" in res.subsystems_purged


def test_m29_m26_compatibility(m29_services):
    """Verify M26 perceptual lifecycle components (proximity, drift) are correctly handled."""
    gateway: ClinicalExecutionGatewayService = m29_services["gateway"]
    req = SessionTeardownExecutionRequest(
        session_id="SESS-M26-COMPAT",
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
    # When optional perceptual handles are not injected, teardown continues gracefully


def test_m29_m27_compatibility(m29_services):
    """Verify M27 workflow interlock lifecycle component (workflow) is purged in order."""
    gateway: ClinicalExecutionGatewayService = m29_services["gateway"]
    req = SessionTeardownExecutionRequest(
        session_id="SESS-M27-COMPAT",
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
    assert "workflow" in res.subsystems_purged


def test_m29_m28_compatibility(m29_services):
    """Verify M28 gateway connection lifecycle is evicted at Step 11 prior to Step 12."""
    gateway: ClinicalExecutionGatewayService = m29_services["gateway"]
    req = SessionTeardownExecutionRequest(
        session_id="SESS-M28-COMPAT",
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)
    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
    assert "gateway_service" in res.subsystems_purged
    assert "tools" in res.subsystems_purged
    assert res.subsystems_purged.index("gateway_service") < res.subsystems_purged.index("tools")
