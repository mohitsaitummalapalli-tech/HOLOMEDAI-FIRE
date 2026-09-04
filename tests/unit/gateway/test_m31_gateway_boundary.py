# -*- coding: utf-8 -*-
"""Hostile Unit & Integration Tests for M31 Gateway Ingress Boundary & Administrative Hardening.

Locked Contract: M31_CONTRACT_SPEC.md
Authoritative Baseline: 2a8cc1d070d76b469cb5ccc750e2b06a2fe3ab75
"""

from __future__ import annotations

from typing import Any
import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.exceptions import UnroutableMessageError
from holomed.gateway.authorization import GatewayAuthorizationPolicy
from holomed.gateway.exceptions import (
    GatewayAuthorizationError,
    GatewaySessionMismatchError,
    GatewayValidationError,
)
from holomed.gateway.framing import FrameParser, encode_frame
from holomed.gateway.models import (
    ClientRole,
    ClientSession,
    ConnectionState,
)
from holomed.gateway.service import GatewayService
from holomed.gateway.transports import create_memory_transport_pair
from holomed.protocol.builders import create_command, create_query
from holomed.protocol.codec import deserialize_envelope, serialize_envelope_bytes
from holomed.protocol.models import MessageEnvelope, MessageType
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.tools.service import ToolService


def _connect_and_handshake(
    gw_srv: GatewayService,
    client_id: str,
    role: ClientRole,
    session_id: str,
    auth_token: str = "valid_token",
) -> tuple[Any, Any, Any]:
    """Helper to register and handshake a client transport."""
    gw_side, cl_side = create_memory_transport_pair("server", client_id)
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


def _send_client_message(
    gw_srv: GatewayService,
    conn: Any,
    cl_side: Any,
    envelope: MessageEnvelope,
) -> MessageEnvelope:
    """Helper to send a message from client, process ingress, and return response envelope."""
    cl_side.send(encode_frame(serialize_envelope_bytes(envelope)))
    gw_srv.process_client_ingress(conn)
    raw = cl_side.receive()
    parser = FrameParser()
    frames = parser.feed(raw)
    assert len(frames) > 0, "Expected at least one response frame from gateway"
    return deserialize_envelope(frames[0])


# =============================================================================
# 1. GATEWAY DISCONNECT ISOLATION & ROLE HIERARCHY (Tests 1–6)
# =============================================================================

def test_cross_session_disconnect_rejected(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """1. Invariant A.1: SESSION-A client attempting to disconnect SESSION-B client is rejected."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    # Connect Client A in Session A (SURGEON_CONSOLE)
    _, cl_a, conn_a = _connect_and_handshake(gw_srv, "client_a", ClientRole.SURGEON_CONSOLE, "SESSION_A")
    # Connect Client B in Session B (SURGEON_CONSOLE)
    _, cl_b, conn_b = _connect_and_handshake(gw_srv, "client_b", ClientRole.SURGEON_CONSOLE, "SESSION_B")

    # Client A attempts to disconnect Client B
    cmd_disc = create_command("gateway.disconnect", "client_a", payload={"client_id": "client_b"})
    resp = _send_client_message(gw_srv, conn_a, cl_a, cmd_disc)

    # Must fail closed with ERR_SESSION_MISMATCH
    assert resp.message_type == MessageType.ERROR
    assert resp.payload["error_code"] == "ERR_SESSION_MISMATCH"
    assert "Cross-session disconnect rejected" in resp.payload["error_message"]

    # Target client B must NOT be disconnected
    assert "client_b" in gw_srv._connections
    assert conn_b.state == ConnectionState.ACTIVE

    gw_srv.stop()


def test_role_hierarchy_assistant_cannot_disconnect_surgeon(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """2. Invariant A.2: ASSISTANT_PANEL cannot disconnect SURGEON_CONSOLE within same session."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    # Connect Surgeon and Assistant in same session
    _, cl_surg, conn_surg = _connect_and_handshake(gw_srv, "surg_01", ClientRole.SURGEON_CONSOLE, "SESSION_A")
    _, cl_asst, conn_asst = _connect_and_handshake(gw_srv, "asst_01", ClientRole.ASSISTANT_PANEL, "SESSION_A")

    # Assistant attempts to disconnect Surgeon
    cmd_disc = create_command("gateway.disconnect", "asst_01", payload={"client_id": "surg_01"})
    resp = _send_client_message(gw_srv, conn_asst, cl_asst, cmd_disc)

    # Must fail closed with ERR_AUTHORIZATION_FAILED
    assert resp.message_type == MessageType.ERROR
    assert resp.payload["error_code"] == "ERR_AUTHORIZATION_FAILED"
    assert "ASSISTANT_PANEL cannot disconnect SURGEON_CONSOLE" in resp.payload["error_message"]

    # Surgeon must remain active
    assert "surg_01" in gw_srv._connections
    assert conn_surg.state == ConnectionState.ACTIVE

    gw_srv.stop()


def test_surgeon_can_disconnect_assistant_in_same_session(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """3. Invariant A.2: SURGEON_CONSOLE may disconnect ASSISTANT_PANEL in same session."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    _, cl_surg, conn_surg = _connect_and_handshake(gw_srv, "surg_01", ClientRole.SURGEON_CONSOLE, "SESSION_A")
    _, cl_asst, conn_asst = _connect_and_handshake(gw_srv, "asst_01", ClientRole.ASSISTANT_PANEL, "SESSION_A")

    cmd_disc = create_command("gateway.disconnect", "surg_01", payload={"client_id": "asst_01", "reason": "Operator request"})
    resp = _send_client_message(gw_srv, conn_surg, cl_surg, cmd_disc)

    assert resp.message_type == MessageType.RESPONSE
    assert resp.payload["disconnected_client_id"] == "asst_01"

    # Assistant connection must be removed and closed
    assert "asst_01" not in gw_srv._connections
    assert conn_asst.state == ConnectionState.CLOSED

    gw_srv.stop()


def test_self_disconnect_allowed(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """4. Any client may disconnect itself."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    _, cl_asst, conn_asst = _connect_and_handshake(gw_srv, "asst_01", ClientRole.ASSISTANT_PANEL, "SESSION_A")

    cmd_disc = create_command("gateway.disconnect", "asst_01", payload={"client_id": "asst_01"})
    cl_asst.send(encode_frame(serialize_envelope_bytes(cmd_disc)))
    gw_srv.process_client_ingress(conn_asst)

    # Client must be disconnected and connection closed
    assert "asst_01" not in gw_srv._connections
    assert conn_asst.state == ConnectionState.CLOSED

    gw_srv.stop()


def test_disconnect_unknown_client_fails_closed(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """5. Disconnect targeting unknown client returns ERR_CLIENT_NOT_FOUND."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    _, cl_surg, conn_surg = _connect_and_handshake(gw_srv, "surg_01", ClientRole.SURGEON_CONSOLE, "SESSION_A")

    cmd_disc = create_command("gateway.disconnect", "surg_01", payload={"client_id": "ghost_client"})
    resp = _send_client_message(gw_srv, conn_surg, cl_surg, cmd_disc)

    assert resp.message_type == MessageType.ERROR
    assert resp.payload["error_code"] == "ERR_CLIENT_NOT_FOUND"

    gw_srv.stop()


def test_disconnect_missing_client_id_fails_closed(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """6. Disconnect without client_id returns ERR_INVALID_ARGS."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    _, cl_surg, conn_surg = _connect_and_handshake(gw_srv, "surg_01", ClientRole.SURGEON_CONSOLE, "SESSION_A")

    cmd_disc = create_command("gateway.disconnect", "surg_01", payload={})
    resp = _send_client_message(gw_srv, conn_surg, cl_surg, cmd_disc)

    assert resp.message_type == MessageType.ERROR
    assert resp.payload["error_code"] == "ERR_INVALID_ARGS"

    gw_srv.stop()


# =============================================================================
# 2. GATEWAY CLIENTS TENANT ISOLATION (Test 7)
# =============================================================================

def test_gateway_clients_query_isolated_to_caller_session(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """7. Invariant B.1: gateway.clients query returns strictly caller's session clients."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    # Connect 2 clients in SESSION_A
    _, cl_a1, conn_a1 = _connect_and_handshake(gw_srv, "client_a1", ClientRole.SURGEON_CONSOLE, "SESSION_A")
    _, cl_a2, conn_a2 = _connect_and_handshake(gw_srv, "client_a2", ClientRole.ASSISTANT_PANEL, "SESSION_A")

    # Connect 2 clients in SESSION_B
    _, cl_b1, conn_b1 = _connect_and_handshake(gw_srv, "client_b1", ClientRole.SURGEON_CONSOLE, "SESSION_B")
    _, cl_b2, conn_b2 = _connect_and_handshake(gw_srv, "client_b2", ClientRole.XR_DISPLAY, "SESSION_B")

    # Client A1 queries gateway.clients
    q_clients = create_query("gateway.clients", "client_a1", payload={})
    resp = _send_client_message(gw_srv, conn_a1, cl_a1, q_clients)

    assert resp.message_type == MessageType.RESPONSE
    returned_clients = resp.payload["clients"]
    returned_ids = {c["client_id"] for c in returned_clients}

    # Must contain Session A clients
    assert returned_ids == {"client_a1", "client_a2"}

    # Must strictly NOT contain Session B clients
    assert "client_b1" not in returned_ids
    assert "client_b2" not in returned_ids

    gw_srv.stop()


# =============================================================================
# 3. GATEWAY INGRESS ALLOWLIST (Tests 8–10)
# =============================================================================

def test_ingress_allowlist_blocks_administrative_and_reset_commands(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """8. Invariant D.1: Ingress blocks platform.reset, platform.cycle, tools.reset, pipeline resets."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    _, cl_surg, conn_surg = _connect_and_handshake(gw_srv, "surg_01", ClientRole.SURGEON_CONSOLE, "SESSION_A")

    forbidden_routes = [
        "platform.reset",
        "platform.cycle",
        "platform.session.start",
        "platform.session.stop",
        "tools.reset",
        "vision.pipeline.reset",
        "xr.reset",
        "ultron.reset",
        "anatomy.reset",
        "audio.pipeline.reset",
        "gesture.pipeline.reset",
        "persistence.replay",
        "drift.evaluate",
        "proximity.evaluate",
        "unregistered.bogus.command",
    ]

    for topic in forbidden_routes:
        cmd = create_command(topic, "surg_01", payload={"epoch_id": 1})
        cl_surg.send(encode_frame(serialize_envelope_bytes(cmd)))

        with pytest.raises(GatewayAuthorizationError) as exc_info:
            gw_srv.process_client_ingress(conn_surg)

        assert "not permitted through gateway ingress" in str(exc_info.value)

    gw_srv.stop()


def test_ingress_allowlist_permits_representative_clinical_routes(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """9. Invariant D.1: Representative clinical execution and query routes pass allowlist."""
    session = ClientSession("surg_01", ClientRole.SURGEON_CONSOLE, "SESSION_A", 1, "now", "addr")

    approved_routes = [
        ("execution.navigation.execute", MessageType.COMMAND),
        ("execution.planning.execute", MessageType.COMMAND),
        ("execution.recovery.execute", MessageType.COMMAND),
        ("execution.registration.execute", MessageType.COMMAND),
        ("execution.session.teardown", MessageType.COMMAND),
        ("execution.tool.invoke", MessageType.COMMAND),
        ("execution.trajectory.bind", MessageType.COMMAND),
        ("execution.workflow.resume", MessageType.COMMAND),
        ("execution.status.get", MessageType.QUERY),
        ("workflow.start", MessageType.COMMAND),
        ("workflow.transition", MessageType.COMMAND),
        ("workflow.confirm", MessageType.COMMAND),
        ("workflow.status", MessageType.QUERY),
        ("navigation.status.get", MessageType.QUERY),
        ("planning.get", MessageType.QUERY),
        ("gateway.status", MessageType.QUERY),
        ("gateway.clients", MessageType.QUERY),
        ("gateway.disconnect", MessageType.COMMAND),
    ]

    for topic, msg_type in approved_routes:
        if msg_type == MessageType.COMMAND:
            msg = create_command(topic, "surg_01", payload={"session_id": "SESSION_A"})
        else:
            msg = create_query(topic, "surg_01", payload={"session_id": "SESSION_A"})
        # Must not raise GatewayAuthorizationError
        GatewayAuthorizationPolicy.authorize_message(session, msg)


def test_m28_payload_session_mismatch_remains_enforced(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """10. Invariant F.1: M28 session-binding is preserved under M31 allowlist."""
    session = ClientSession("surg_01", ClientRole.SURGEON_CONSOLE, "SESSION_A", 1, "now", "addr")
    msg = create_command(
        "execution.navigation.execute",
        "surg_01",
        payload={"session_id": "SESSION_B", "sequence_number": 1},
    )

    with pytest.raises(GatewaySessionMismatchError) as exc_info:
        GatewayAuthorizationPolicy.authorize_message(session, msg)

    assert "Cross-session injection rejected" in str(exc_info.value)


# =============================================================================
# 4. TOOLS.RESET DEREGISTRATION & ISOLATION (Tests 11–12)
# =============================================================================

def test_tools_reset_external_attack_reproduced_and_blocked(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """11. Prove original tools.reset attack is blocked both by gateway allowlist and dispatcher removal."""
    tool_srv = ToolService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)

    tool_srv.initialize(runtime_context)
    gw_srv.initialize(runtime_context)

    message_dispatcher.start()
    tool_srv.start()
    gw_srv.start()

    # Pre-populate active sequence in tool engine
    assert tool_srv._engine is not None
    tool_srv._engine._session_sequences["SESSION_VICTIM"] = 99

    _, cl_surg, conn_surg = _connect_and_handshake(gw_srv, "attacker", ClientRole.SURGEON_CONSOLE, "SESSION_A")

    # Attacker attempts to send tools.reset through gateway ingress
    cmd_reset = create_command("tools.reset", "attacker", payload={"epoch_id": 1})
    cl_surg.send(encode_frame(serialize_envelope_bytes(cmd_reset)))

    # Ingress must reject with GatewayAuthorizationError
    with pytest.raises(GatewayAuthorizationError):
        gw_srv.process_client_ingress(conn_surg)

    # Victim sequence state must remain completely untouched
    assert tool_srv._engine._session_sequences.get("SESSION_VICTIM") == 99

    # Also verify direct dispatcher dispatch raises UnroutableMessageError
    cmd_direct = create_command("tools.reset", "attacker", payload={"epoch_id": 1})
    with pytest.raises(UnroutableMessageError):
        message_dispatcher.dispatch(cmd_direct)

    tool_srv.stop()
    gw_srv.stop()


def test_alternate_selector_cannot_bypass_session_boundary(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """12. Alternate target selectors (client_id) cannot escape caller session scope."""
    gw_srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv.initialize(runtime_context)
    message_dispatcher.start()
    gw_srv.start()

    _, cl_a, conn_a = _connect_and_handshake(gw_srv, "client_a", ClientRole.SURGEON_CONSOLE, "SESSION_A")
    _, cl_b, conn_b = _connect_and_handshake(gw_srv, "client_b", ClientRole.SURGEON_CONSOLE, "SESSION_B")

    # Attacker sends gateway.disconnect targeting client_b with no session_id in payload
    cmd = create_command("gateway.disconnect", "client_a", payload={"client_id": "client_b"})
    resp = _send_client_message(gw_srv, conn_a, cl_a, cmd)

    # Target selector client_id is resolved and verified against caller session
    assert resp.message_type == MessageType.ERROR
    assert resp.payload["error_code"] == "ERR_SESSION_MISMATCH"
    assert conn_b.state == ConnectionState.ACTIVE

    gw_srv.stop()
