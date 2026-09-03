# -*- coding: utf-8 -*-
"""Hostile Unit & Integration Tests for M28 Gateway Ingress Security & Connection Lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.execution.models import (
    ExecutionStatus,
    SessionTeardownExecutionRequest,
)
from holomed.execution.service import ClinicalExecutionGatewayService
from holomed.gateway.authorization import GatewayAuthorizationPolicy
from holomed.gateway.exceptions import (
    GatewayCapacityError,
    GatewayLifecycleError,
    GatewaySessionMismatchError,
    GatewayValidationError,
)
from holomed.gateway.framing import FrameParser, encode_frame
from holomed.gateway.models import (
    MAX_CONNECTIONS_PER_SESSION,
    ClientRole,
    ClientSession,
    ConnectionState,
)
from holomed.gateway.service import GatewayService
from holomed.gateway.transports import create_memory_transport_pair
from holomed.platform.models import SessionStatus
from holomed.platform.service import PlatformService
from holomed.protocol.builders import create_command, create_event, create_query
from holomed.protocol.codec import deserialize_envelope, serialize_envelope_bytes
from holomed.protocol.models import MessageEnvelope
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.workflow.models import WorkflowPhase
from holomed.workflow.service import WorkflowService


def _connect_and_handshake(
    gw_srv: GatewayService,
    client_id: str,
    role: ClientRole,
    session_id: str,
    auth_token: str = "valid_token",
) -> tuple[Any, Any, Any]:
    """Helper to register and handshake a client transport."""
    gw_side, cl_side = create_memory_transport_pair()
    conn = gw_srv.register_client_transport(gw_side)

    hs = create_command(
        "gateway.handshake",
        client_id,
        payload={
            "client_id": client_id,
            "client_role": role.value,
            "session_id": session_id,
            "auth_token": auth_token,
            "protocol_version": "1.0",
        },
    )
    cl_side.send(encode_frame(serialize_envelope_bytes(hs)))
    gw_srv.process_client_ingress(conn)
    cl_side.receive()  # drain handshake response
    return gw_side, cl_side, conn


# =============================================================================
# 1. SECURITY: Cross-Session Spoofing & Route Ingress Invariants (Tests 1–8)
# =============================================================================

def test_cross_session_payload_injection_rejected(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """1. Verify client authenticated for Session A sending payload targeting Session B is rejected."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    _, cl_side, conn = _connect_and_handshake(
        gw_srv, "client_a", ClientRole.SURGEON_CONSOLE, "SESSION_A"
    )

    # Attempt cross-session command with session_id="SESSION_B"
    spoofed = create_command(
        "workflow.transition",
        "client_a",
        payload={"session_id": "SESSION_B", "target_phase": "ABORTED", "sequence_number": 1},
    )
    cl_side.send(encode_frame(serialize_envelope_bytes(spoofed)))

    with pytest.raises(GatewaySessionMismatchError) as exc_info:
        gw_srv.process_client_ingress(conn)

    assert "Cross-session injection rejected" in str(exc_info.value)
    assert "SESSION_B" in str(exc_info.value)
    assert "SESSION_A" in str(exc_info.value)
    gw_srv.stop()


def test_spoofed_workflow_transition_cannot_mutate_b(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """2. Real ingress execution test: spoofed workflow.transition fails before reaching WorkflowService."""
    wf_srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv = GatewayService(dispatcher=message_dispatcher, workflow_service=wf_srv, secret_filter=secret_filter)

    wf_srv.initialize(runtime_context)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    wf_srv.start()
    gw_srv.start()

    # Start Session B workflow
    wf_srv.start_workflow("SESSION_B")
    snap_before = wf_srv.get_workflow_state("SESSION_B")
    assert snap_before.current_phase == WorkflowPhase.PATIENT_CONTEXT

    # Client A authenticates for Session A
    _, cl_side, conn = _connect_and_handshake(
        gw_srv, "console_a", ClientRole.SURGEON_CONSOLE, "SESSION_A"
    )

    # Client A attempts to force Session B into ABORTED
    spoofed = create_command(
        "workflow.transition",
        "console_a",
        payload={"session_id": "SESSION_B", "target_phase": "ABORTED", "sequence_number": 1},
    )
    cl_side.send(encode_frame(serialize_envelope_bytes(spoofed)))

    with pytest.raises(GatewaySessionMismatchError):
        gw_srv.process_client_ingress(conn)

    # Verify Session B phase is UNTOUCHED
    snap_after = wf_srv.get_workflow_state("SESSION_B")
    assert snap_after.current_phase == WorkflowPhase.PATIENT_CONTEXT

    gw_srv.stop()
    wf_srv.stop()


def test_spoofed_execution_tool_invoke_cannot_mutate_b(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """3. Real ingress execution test: spoofed execution.tool.invoke rejected at perimeter."""
    exec_srv = ClinicalExecutionGatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)

    exec_srv.initialize(runtime_context)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    exec_srv.start()
    gw_srv.start()

    _, cl_side, conn = _connect_and_handshake(
        gw_srv, "console_a", ClientRole.SURGEON_CONSOLE, "SESSION_A"
    )

    # Attempt to invoke tool on Session B
    spoofed = create_command(
        "execution.tool.invoke",
        "console_a",
        payload={
            "session_id": "SESSION_B",
            "tool_id": "biopsy.needle.gauge",
            "sequence_number": 1,
            "safety_classification": "STANDARD",
        },
    )
    cl_side.send(encode_frame(serialize_envelope_bytes(spoofed)))

    with pytest.raises(GatewaySessionMismatchError):
        gw_srv.process_client_ingress(conn)

    # Verify execution service has zero results for Session B
    assert exec_srv.get_execution_status("SESSION_B") is None

    gw_srv.stop()
    exec_srv.stop()


def test_spoofed_execution_session_teardown_cannot_destroy_b(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """4. Real ingress execution test: spoofed execution.session.teardown cannot destroy Session B."""
    plat_srv = PlatformService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    wf_srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    exec_srv = ClinicalExecutionGatewayService(
        dispatcher=message_dispatcher,
        workflow_service=wf_srv,
        platform_service=plat_srv,
        secret_filter=secret_filter,
    )
    gw_srv = GatewayService(dispatcher=message_dispatcher, workflow_service=wf_srv, secret_filter=secret_filter)

    plat_srv.initialize(runtime_context)
    wf_srv.initialize(runtime_context)
    exec_srv.initialize(runtime_context)
    gw_srv.initialize(runtime_context)

    message_dispatcher.start()
    plat_srv.start()
    wf_srv.start()
    exec_srv.start()
    gw_srv.start()

    # Active Session B
    plat_srv.start_session("SESSION_B")
    wf_srv.start_workflow("SESSION_B")
    assert plat_srv.has_session("SESSION_B") is True
    assert plat_srv.session_manager.get_session("SESSION_B").status == SessionStatus.ACTIVE
    assert wf_srv.get_workflow_state("SESSION_B").current_phase == WorkflowPhase.PATIENT_CONTEXT

    # Attacker connects for Session A
    _, cl_side, conn = _connect_and_handshake(
        gw_srv, "attacker_console", ClientRole.SURGEON_CONSOLE, "SESSION_A"
    )

    # Attacker sends teardown command targeting SESSION_B
    attack_env = create_command(
        "execution.session.teardown",
        "attacker_console",
        payload={"session_id": "SESSION_B", "sequence_number": 0},
    )
    cl_side.send(encode_frame(serialize_envelope_bytes(attack_env)))

    with pytest.raises(GatewaySessionMismatchError):
        gw_srv.process_client_ingress(conn)

    # Verify Session B is 100% active and intact
    assert plat_srv.has_session("SESSION_B") is True
    assert plat_srv.session_manager.get_session("SESSION_B").status == SessionStatus.ACTIVE
    assert wf_srv.get_workflow_state("SESSION_B").current_phase == WorkflowPhase.PATIENT_CONTEXT

    gw_srv.stop()
    exec_srv.stop()
    wf_srv.stop()
    plat_srv.stop()


def test_same_session_payload_allowed(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """5. Verify compliant client specifying payload['session_id'] == session.session_id succeeds."""
    wf_srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv = GatewayService(dispatcher=message_dispatcher, workflow_service=wf_srv, secret_filter=secret_filter)

    wf_srv.initialize(runtime_context)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    wf_srv.start()
    gw_srv.start()

    _, cl_side, conn = _connect_and_handshake(
        gw_srv, "console_a", ClientRole.SURGEON_CONSOLE, "SESSION_A"
    )

    # Valid command matching bound session
    cmd = create_command(
        "workflow.start",
        "console_a",
        payload={"session_id": "SESSION_A"},
    )
    cl_side.send(encode_frame(serialize_envelope_bytes(cmd)))
    processed = gw_srv.process_client_ingress(conn)
    assert processed == 1

    # Verify workflow started
    snap = wf_srv.get_workflow_state("SESSION_A")
    assert snap.current_phase == WorkflowPhase.PATIENT_CONTEXT

    gw_srv.stop()
    wf_srv.stop()


def test_omitted_session_id_on_global_routes(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """6. Verify global routes omitting session_id pass gateway authorization transparently."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    _, cl_side, conn = _connect_and_handshake(
        gw_srv, "client_status", ClientRole.SURGEON_CONSOLE, "SESSION_A"
    )

    q = create_query("gateway.status", "client_status", payload={})
    cl_side.send(encode_frame(serialize_envelope_bytes(q)))
    processed = gw_srv.process_client_ingress(conn)
    assert processed == 1

    raw_resp = cl_side.receive()
    parser = FrameParser()
    resp = deserialize_envelope(parser.feed(raw_resp)[0])
    assert resp.message_type.value == "RESPONSE"
    assert resp.payload["service_name"] == "gateway_service"

    gw_srv.stop()


def test_malformed_and_none_session_id_handling() -> None:
    """7. Verify malformed or None session_id in payload triggers session mismatch."""
    session = ClientSession("c1", ClientRole.SURGEON_CONSOLE, "SESSION_A", 1, "now", "addr")

    # Mismatched None
    env_none = create_command("workflow.start", "c1", payload={"session_id": None})
    with pytest.raises(GatewaySessionMismatchError):
        GatewayAuthorizationPolicy.authorize_message(session, env_none)

    # Mismatched empty string
    env_empty = create_command("workflow.start", "c1", payload={"session_id": ""})
    with pytest.raises(GatewaySessionMismatchError):
        GatewayAuthorizationPolicy.authorize_message(session, env_empty)

    # Mismatched integer
    env_int = create_command("workflow.start", "c1", payload={"session_id": 12345})
    with pytest.raises(GatewaySessionMismatchError):
        GatewayAuthorizationPolicy.authorize_message(session, env_int)


def test_cross_session_replay_fails_closed(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """8. Verify intercepted valid envelope from Session A cannot be replayed against Session B connection."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    # Client B connects for Session B
    _, cl_b, conn_b = _connect_and_handshake(
        gw_srv, "client_b", ClientRole.SURGEON_CONSOLE, "SESSION_B"
    )

    # Attacker replays envelope originally minted for Session A
    replayed_env = create_command(
        "workflow.transition",
        "client_b",
        payload={"session_id": "SESSION_A", "target_phase": "ABORTED", "sequence_number": 1},
    )
    cl_b.send(encode_frame(serialize_envelope_bytes(replayed_env)))

    with pytest.raises(GatewaySessionMismatchError):
        gw_srv.process_client_ingress(conn_b)

    gw_srv.stop()


# =============================================================================
# 2. CONNECTION LIFECYCLE & CAPACITY (Tests 9–15)
# =============================================================================

def test_connection_capacity_limit_enforced(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """9. Verify exactly 4 connections allowed per session; 5th raises GatewayCapacityError."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    # Open 4 connections for SESSION_CAP
    for i in range(MAX_CONNECTIONS_PER_SESSION):
        _connect_and_handshake(gw_srv, f"client_cap_{i}", ClientRole.SURGEON_CONSOLE, "SESSION_CAP")

    assert gw_srv.active_connections_count == 4

    # 5th connection must fail with GatewayCapacityError
    gw_side, cl_side = create_memory_transport_pair()
    conn = gw_srv.register_client_transport(gw_side)
    hs = create_command(
        "gateway.handshake",
        "client_cap_overflow",
        payload={
            "client_id": "client_cap_overflow",
            "client_role": "SURGEON_CONSOLE",
            "session_id": "SESSION_CAP",
            "auth_token": "valid_token",
            "protocol_version": "1.0",
        },
    )
    cl_side.send(encode_frame(serialize_envelope_bytes(hs)))

    with pytest.raises(GatewayCapacityError) as exc_info:
        gw_srv.process_client_ingress(conn)

    assert "maximum connections" in str(exc_info.value)
    gw_srv.stop()


def test_session_eviction_closes_all_session_connections(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """10. Verify calling evict_session() surgically purges all connections bound to session_id."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    _, _, conn_a1 = _connect_and_handshake(gw_srv, "c_a1", ClientRole.SURGEON_CONSOLE, "SESSION_A")
    _, _, conn_a2 = _connect_and_handshake(gw_srv, "c_a2", ClientRole.XR_DISPLAY, "SESSION_A")
    assert gw_srv.active_connections_count == 2

    # Evict Session A
    evicted = gw_srv.evict_session("SESSION_A")
    assert evicted is True
    assert gw_srv.active_connections_count == 0
    assert conn_a1.state == ConnectionState.CLOSED
    assert conn_a2.state == ConnectionState.CLOSED

    gw_srv.stop()


def test_session_eviction_preserves_foreign_connections(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """11. Verify evicting Session A preserves Session B connections untouched."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    _, _, conn_a = _connect_and_handshake(gw_srv, "c_a", ClientRole.SURGEON_CONSOLE, "SESSION_A")
    _, _, conn_b = _connect_and_handshake(gw_srv, "c_b", ClientRole.SURGEON_CONSOLE, "SESSION_B")
    assert gw_srv.active_connections_count == 2

    # Evict Session A
    gw_srv.evict_session("SESSION_A")

    # Session A is closed, Session B remains ACTIVE
    assert conn_a.state == ConnectionState.CLOSED
    assert conn_b.state == ConnectionState.ACTIVE
    assert gw_srv.active_connections_count == 1
    assert "c_b" in gw_srv._connections

    gw_srv.stop()


def test_capacity_reclaimed_after_eviction(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """12. Verify eviction reclaims capacity: 4 connections -> evict -> 4 new connections succeed."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    # Max out capacity
    for i in range(MAX_CONNECTIONS_PER_SESSION):
        _connect_and_handshake(gw_srv, f"c_reuse_{i}", ClientRole.SURGEON_CONSOLE, "SESSION_REUSE")

    assert gw_srv.active_connections_count == 4

    # Evict session
    gw_srv.evict_session("SESSION_REUSE")
    assert gw_srv.active_connections_count == 0

    # Can connect 4 new connections again cleanly
    for i in range(MAX_CONNECTIONS_PER_SESSION):
        _connect_and_handshake(gw_srv, f"c_reuse_new_{i}", ClientRole.SURGEON_CONSOLE, "SESSION_REUSE")

    assert gw_srv.active_connections_count == 4
    gw_srv.stop()


def test_evicted_connection_cannot_receive_xr_frames(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """13. Verify stale/evicted connection cannot receive XR presentation frames."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    _, cl_a, _ = _connect_and_handshake(gw_srv, "hmd_a", ClientRole.XR_DISPLAY, "SESSION_A")
    _, cl_b, _ = _connect_and_handshake(gw_srv, "hmd_b", ClientRole.XR_DISPLAY, "SESSION_B")

    # Evict Session A
    gw_srv.evict_session("SESSION_A")

    # Broadcast presentation frame for Session B
    frame_env = create_event(
        "xr.presentation.frame",
        "xr_service",
        payload={"session_id": "SESSION_B", "frame_idx": 42},
    )
    gw_srv.handle_presentation_event(frame_env)

    # Client B receives frame
    raw_b = cl_b.receive()
    assert len(raw_b) > 0

    # Client A receives nothing (transport closed / disconnected)
    raw_a = cl_a.receive()
    assert len(raw_a) == 0

    gw_srv.stop()


def test_evicted_connection_cannot_receive_workflow_broadcasts(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """14. Verify evicted connection cannot receive workflow broadcasts upon session ID reuse."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    _, cl_stale, _ = _connect_and_handshake(gw_srv, "console_stale", ClientRole.SURGEON_CONSOLE, "SESSION_X")

    # Teardown / evict Session X
    gw_srv.evict_session("SESSION_X")

    # Connect new client for reused Session X
    _, cl_new, _ = _connect_and_handshake(gw_srv, "console_new", ClientRole.SURGEON_CONSOLE, "SESSION_X")

    # Broadcast workflow event to Session X
    wf_ev = create_event(
        "workflow.phase.entered",
        "workflow_service",
        payload={"session_id": "SESSION_X", "phase": "NAVIGATION_ACTIVE"},
    )
    gw_srv.handle_workflow_broadcast_event(wf_ev)

    # New client receives event
    raw_new = cl_new.receive()
    assert len(raw_new) > 0

    # Stale evicted client receives nothing
    raw_stale = cl_stale.receive()
    assert len(raw_stale) == 0

    gw_srv.stop()


def test_evicted_connection_cannot_submit_commands(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """15. Verify evicted connection transport is closed and cannot submit commands."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    _, cl_side, conn = _connect_and_handshake(gw_srv, "c_evict", ClientRole.SURGEON_CONSOLE, "SESSION_A")

    gw_srv.evict_session("SESSION_A")
    assert conn.state == ConnectionState.CLOSED

    # Attempt to send command through closed transport raises GatewayTransportError
    from holomed.gateway.exceptions import GatewayTransportError
    cmd = create_query("gateway.status", "c_evict", payload={})
    with pytest.raises(GatewayTransportError):
        cl_side.send(encode_frame(serialize_envelope_bytes(cmd)))

    processed = gw_srv.process_client_ingress(conn)
    # Transport is closed; 0 processed
    assert processed == 0

    gw_srv.stop()


# =============================================================================
# 3. TEARDOWN INTEGRATION & FAILURE AGGREGATION (Tests 16–18)
# =============================================================================

def test_teardown_step11_gateway_integration(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """16. Verify execute_session_teardown() invokes Step 11 Gateway eviction."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    exec_srv = ClinicalExecutionGatewayService(
        dispatcher=message_dispatcher,
        gateway_service=gw_srv,
        secret_filter=secret_filter,
    )

    gw_srv.initialize(runtime_context)
    exec_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()
    exec_srv.start()

    # Client connected for SESSION_TD
    _, _, conn = _connect_and_handshake(gw_srv, "c_td", ClientRole.SURGEON_CONSOLE, "SESSION_TD")
    assert gw_srv.active_connections_count == 1

    # Execute teardown via ClinicalExecutionGatewayService
    req = SessionTeardownExecutionRequest(
        session_id="SESSION_TD",
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = exec_srv.execute_session_teardown(req)

    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
    assert "gateway_service" in res.subsystems_purged
    assert gw_srv.active_connections_count == 0
    assert conn.state == ConnectionState.CLOSED

    gw_srv.stop()
    exec_srv.stop()


def test_teardown_gateway_failure_aggregation(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """17. Verify exception in gateway eviction is aggregated into failures list without halting teardown."""
    class FailingGatewayService:
        def evict_session(self, session_id: str, cap: Any = None) -> bool:
            raise RuntimeError("Simulated gateway transport hardware failure")

    failing_gw = FailingGatewayService()
    plat_srv = PlatformService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    exec_srv = ClinicalExecutionGatewayService(
        dispatcher=message_dispatcher,
        platform_service=plat_srv,
        gateway_service=failing_gw,
        secret_filter=secret_filter,
    )

    plat_srv.initialize(runtime_context)
    exec_srv.initialize(runtime_context)
    message_dispatcher.start()
    plat_srv.start()
    exec_srv.start()

    plat_srv.start_session("SESSION_FAIL")

    req = SessionTeardownExecutionRequest(
        session_id="SESSION_FAIL",
        sequence_number=1,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = exec_srv.execute_session_teardown(req)

    # Teardown marks degraded/failed status and aggregates failure
    assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
    assert any("gateway_service: Simulated gateway transport hardware failure" in f for f in res.failures)
    # Platform was still evicted
    assert "platform" in res.subsystems_purged

    exec_srv.stop()
    plat_srv.stop()


def test_reentrant_gateway_eviction_fails_safely(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """18. Verify reentrant call to evict_session() is rejected by transaction guard."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    gw_srv._in_transaction = True
    with pytest.raises(GatewayLifecycleError) as exc_info:
        gw_srv.evict_session("SESSION_A")

    assert "Cannot evict session during an active transaction" in str(exc_info.value)
    gw_srv._in_transaction = False
    gw_srv.stop()
