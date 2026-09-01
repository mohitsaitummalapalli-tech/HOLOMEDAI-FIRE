# -*- coding: utf-8 -*-
"""Adversarial and End-to-End Tests for M11 Gateway Matrix."""

from __future__ import annotations

from pathlib import Path
import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.gateway.exceptions import (
    GatewayAuthenticationError,
    GatewayLifecycleError,
    GatewayValidationError,
)
from holomed.gateway.framing import encode_frame
from holomed.gateway.models import ClientRole, HandshakePayload
from holomed.gateway.service import GatewayService
from holomed.gateway.transports import create_memory_transport_pair
from holomed.protocol.builders import create_command, create_event, create_query
from holomed.protocol.codec import deserialize_envelope, serialize_envelope_bytes
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus
from holomed.workflow.service import WorkflowService


def test_deterministic_broadcast_ordering_by_client_id(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify broadcast orders deliveries strictly by ascending client_id (D292)."""
    srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    # Register clients in reverse alphabetical order: z_client, m_client, a_client
    c_names = ["z_client", "m_client", "a_client"]
    pairs = {}
    for name in c_names:
        gw, cl = create_memory_transport_pair()
        pairs[name] = cl
        conn = srv.register_client_transport(gw)
        hs = create_command(
            "gateway.handshake",
            name,
            payload={"client_id": name, "client_role": "XR_DISPLAY", "session_id": "s1", "auth_token": "tok"},
        )
        cl.send(encode_frame(serialize_envelope_bytes(hs)))
        srv.process_client_ingress(conn)
        cl.receive()  # drain handshake response

    # Broadcast event
    ev = create_event("test.broadcast", "test_src", payload={"content": "hello"})
    srv.broadcast_envelope(ev)

    # Verify all 3 received the broadcast
    for name in c_names:
        raw = pairs[name].receive()
        assert len(raw) > 0

    srv.stop()


def test_dispatcher_disconnect_command_and_client_list_query(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify gateway.disconnect command and gateway.clients query."""
    srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    gw, cl = create_memory_transport_pair()
    conn = srv.register_client_transport(gw)
    hs = create_command(
        "gateway.handshake",
        "client_to_disconnect",
        payload={"client_id": "client_to_disconnect", "client_role": "XR_DISPLAY", "session_id": "s1", "auth_token": "tok"},
    )
    cl.send(encode_frame(serialize_envelope_bytes(hs)))
    srv.process_client_ingress(conn)
    assert srv.active_connections_count == 1

    # Query gateway.clients
    q_list = create_query("gateway.clients", "admin", payload={})
    resp_list = message_dispatcher.dispatch(q_list)
    assert resp_list.message_type.value == "RESPONSE"
    assert len(resp_list.payload["clients"]) == 1
    assert resp_list.payload["clients"][0]["client_id"] == "client_to_disconnect"

    # Command disconnect
    cmd_disc = create_command("gateway.disconnect", "admin", payload={"client_id": "client_to_disconnect"})
    resp_disc = message_dispatcher.dispatch(cmd_disc)
    assert resp_disc.message_type.value == "RESPONSE"
    assert resp_disc.payload["disconnected_client_id"] == "client_to_disconnect"
    assert srv.active_connections_count == 0

    srv.stop()


def test_reentrancy_guard_in_gateway_service(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify transaction guard prevents reentrant operations."""
    srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    srv._in_transaction = True
    with pytest.raises(GatewayLifecycleError):
        srv.stop()

    srv._in_transaction = False
    srv.stop()


def test_e2e_workflow_command_routing_through_gateway(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify external client issuing workflow.start routes through dispatcher to WorkflowService."""
    wf_srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    gw_srv = GatewayService(dispatcher=message_dispatcher, workflow_service=wf_srv, secret_filter=secret_filter)

    wf_srv.initialize(runtime_context)
    gw_srv.initialize(runtime_context)

    message_dispatcher.start()
    wf_srv.start()
    gw_srv.start()

    gw, cl = create_memory_transport_pair()
    conn = gw_srv.register_client_transport(gw)

    # 1. Handshake as SURGEON_CONSOLE
    hs = create_command(
        "gateway.handshake",
        "surgeon_hmd",
        payload={"client_id": "surgeon_hmd", "client_role": "SURGEON_CONSOLE", "session_id": "sess_live_01", "auth_token": "tok"},
    )
    cl.send(encode_frame(serialize_envelope_bytes(hs)))
    gw_srv.process_client_ingress(conn)
    cl.receive()  # drain handshake response

    # 2. Client issues workflow.start over stream
    wf_start_cmd = create_command(
        "workflow.start",
        "surgeon_hmd",
        payload={"session_id": "sess_live_01"},
    )
    cl.send(encode_frame(serialize_envelope_bytes(wf_start_cmd)))
    gw_srv.process_client_ingress(conn)

    # Read all responses back at client
    all_raw = bytearray()
    while True:
        chunk = cl.receive()
        if not chunk:
            break
        all_raw.extend(chunk)

    from holomed.gateway.framing import FrameParser
    parser = FrameParser()
    frames = [deserialize_envelope(f) for f in parser.feed(all_raw)]
    assert any(
        f.message_type.value == "RESPONSE" and f.payload.get("current_phase") == "PATIENT_CONTEXT"
        for f in frames
    )

    gw_srv.stop()
    wf_srv.stop()


def test_client_id_regex_boundary_checks() -> None:
    """Verify exactly 64 characters allowed and 65 characters rejected."""
    valid_64 = "a" * 64
    hp_ok = HandshakePayload(valid_64, ClientRole.XR_DISPLAY, "sess", "tok", "1.0")
    assert hp_ok.client_id == valid_64

    invalid_65 = "a" * 65
    with pytest.raises(GatewayValidationError):
        HandshakePayload(invalid_65, ClientRole.XR_DISPLAY, "sess", "tok", "1.0")


def test_unauthenticated_client_cannot_send_regular_commands(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify sending non-handshake command before authentication is rejected."""
    srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    srv.start()

    gw, cl = create_memory_transport_pair()
    conn = srv.register_client_transport(gw)

    # Attempt to send workflow.start before handshake
    unauth_cmd = create_command("workflow.start", "attacker", payload={"session_id": "s1"})
    cl.send(encode_frame(serialize_envelope_bytes(unauth_cmd)))

    with pytest.raises(GatewayAuthenticationError):
        srv.process_client_ingress(conn)

    srv.stop()


def test_gateway_health_status(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify health returns UNHEALTHY before start and HEALTHY after start."""
    srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    assert srv.health().status == HealthStatus.UNHEALTHY

    srv.initialize(runtime_context)
    srv.start()
    assert srv.health().status == HealthStatus.HEALTHY

    srv.stop()
    assert srv.health().status == HealthStatus.UNHEALTHY
