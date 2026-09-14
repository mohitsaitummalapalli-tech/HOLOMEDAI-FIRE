# -*- coding: utf-8 -*-
"""Unit Tests for GatewayService Lifecycle, Routing, and Integration."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.gateway.exceptions import GatewayCapacityError, GatewayLifecycleError
from holomed.gateway.framing import encode_frame
from holomed.gateway.models import ClientRole
from holomed.gateway.service import GatewayService
from holomed.gateway.transports import create_memory_transport_pair
from holomed.protocol.builders import create_command, create_event, create_query, create_response
from holomed.protocol.codec import deserialize_envelope, serialize_envelope_bytes
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState


def test_service_lifecycle_and_structural_resources(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify GatewayService acquires and releases exactly 4 structural handles."""
    srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    assert srv.state == ServiceState.UNINITIALIZED

    srv.initialize(runtime_context)
    assert srv.state == ServiceState.INITIALIZED
    assert len(srv.resources.outstanding_handles) == 4
    expected_handles = {
        "gateway.listener",
        "gateway.registry",
        "gateway.egress",
        "gateway.metrics",
    }
    assert {h.resource_id for h in srv.resources.outstanding_handles} == expected_handles

    message_dispatcher.start()
    srv.start()
    assert srv.state == ServiceState.STARTED
    assert srv.health().status == HealthStatus.HEALTHY

    srv.stop()
    assert srv.state == ServiceState.STOPPED
    assert len(srv.resources.outstanding_handles) == 0


def test_client_connect_handshake_and_command_routing(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify end-to-end client connection, handshake, and command execution."""
    srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    # Create client transport
    gw_side, cl_side = create_memory_transport_pair()
    conn = srv.register_client_transport(gw_side)

    # 1. Send Handshake
    hs_cmd = create_command(
        "gateway.handshake",
        "client_01",
        payload={
            "client_id": "client_01",
            "client_role": "SURGEON_CONSOLE",
            "session_id": "sess_01",
            "auth_token": "token",
            "protocol_version": "1.0",
        },
    )
    cl_side.send(encode_frame(serialize_envelope_bytes(hs_cmd)))
    processed = srv.process_client_ingress(conn)
    assert processed == 1
    assert conn.client_id == "client_01"

    # Read Handshake Response at client
    raw_resp = cl_side.receive()
    from holomed.gateway.framing import FrameParser
    parser = FrameParser()
    resp_env = deserialize_envelope(parser.feed(raw_resp)[0])
    assert resp_env.message_type.value == "RESPONSE"
    assert resp_env.payload["status"] == "AUTHENTICATED"

    # 2. Client queries gateway.status
    q = create_query("gateway.status", "client_01", payload={})
    cl_side.send(encode_frame(serialize_envelope_bytes(q)))
    srv.process_client_ingress(conn)

    raw_q_resp = cl_side.receive()
    q_resp = deserialize_envelope(parser.feed(raw_q_resp)[0])
    assert q_resp.message_type.value == "RESPONSE"
    assert q_resp.payload["active_clients_count"] == 1

    srv.stop()


def test_xr_presentation_frame_routed_to_display_clients(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify XR presentation frames are delivered to XR_DISPLAY clients (D282)."""
    srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    gw_side, cl_side = create_memory_transport_pair()
    conn = srv.register_client_transport(gw_side)

    # Handshake as XR_DISPLAY
    hs = create_command(
        "gateway.handshake",
        "unity_quest_01",
        payload={
            "client_id": "unity_quest_01",
            "client_role": "XR_DISPLAY",
            "session_id": "sess_01",
            "auth_token": "tok",
            "protocol_version": "1.0",
        },
    )
    cl_side.send(encode_frame(serialize_envelope_bytes(hs)))
    srv.process_client_ingress(conn)
    # Drain handshake response
    cl_side.receive()

    # Simulate M06 XR presentation frame emitted over dispatcher
    frame_env = create_event(
        "xr.presentation.frame",
        "xr_service",
        target="unity_client",
        payload={"scene": "femur_model", "confidence": 0.98},
    )
    message_dispatcher.dispatch(frame_env)

    # Verify frame reached client transport
    raw_frame = cl_side.receive()
    from holomed.gateway.framing import FrameParser
    parser = FrameParser()
    parsed = deserialize_envelope(parser.feed(raw_frame)[0])
    assert parsed.message_name == "xr.presentation.frame"
    assert parsed.payload["confidence"] == 0.98

    srv.stop()


def test_workflow_abort_broadcast_and_disconnect(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify workflow.aborted event disconnects session-bound clients (D291)."""
    srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    gw_side, cl_side = create_memory_transport_pair()
    conn = srv.register_client_transport(gw_side)

    hs = create_command(
        "gateway.handshake",
        "client_sess_02",
        payload={
            "client_id": "client_sess_02",
            "client_role": "SURGEON_CONSOLE",
            "session_id": "sess_to_abort",
            "auth_token": "tok",
            "protocol_version": "1.0",
        },
    )
    cl_side.send(encode_frame(serialize_envelope_bytes(hs)))
    srv.process_client_ingress(conn)
    assert srv.active_connections_count == 1

    # Emit workflow.aborted for this session
    abort_env = create_event(
        "workflow.aborted",
        "workflow_service",
        payload={"session_id": "sess_to_abort", "reason": "Emergency stop"},
    )
    message_dispatcher.dispatch(abort_env)

    # Client was disconnected
    assert srv.active_connections_count == 0
    assert conn.state.value == "CLOSED"

    srv.stop()


def test_capacity_max_connections_per_session(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify session is limited to MAX_CONNECTIONS_PER_SESSION = 4."""
    srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    # Connect 4 clients to sess_cap
    for i in range(4):
        gw, cl = create_memory_transport_pair()
        c = srv.register_client_transport(gw)
        hs = create_command(
            "gateway.handshake",
            f"c_{i}",
            payload={"client_id": f"c_{i}", "client_role": "XR_DISPLAY", "session_id": "sess_cap", "auth_token": "tok"},
        )
        cl.send(encode_frame(serialize_envelope_bytes(hs)))
        srv.process_client_ingress(c)

    assert srv.active_connections_count == 4

    # 5th client for same session fails
    gw5, cl5 = create_memory_transport_pair()
    c5 = srv.register_client_transport(gw5)
    hs5 = create_command(
        "gateway.handshake",
        "c_5",
        payload={"client_id": "c_5", "client_role": "XR_DISPLAY", "session_id": "sess_cap", "auth_token": "tok"},
    )
    cl5.send(encode_frame(serialize_envelope_bytes(hs5)))
    with pytest.raises(GatewayCapacityError):
        srv.process_client_ingress(c5)

    srv.stop()


def test_m45_event_cleanup(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify M45 Event Cleanup for evicted and stopped platform sessions."""
    srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    # Connect client A to sess_a
    gw_a, cl_a = create_memory_transport_pair()
    c_a = srv.register_client_transport(gw_a)
    hs_a = create_command("gateway.handshake", "client_a", payload={"client_id": "client_a", "client_role": "XR_DISPLAY", "session_id": "sess_a", "auth_token": "tok"})
    cl_a.send(encode_frame(serialize_envelope_bytes(hs_a)))
    srv.process_client_ingress(c_a)

    # Connect client B to sess_b
    gw_b, cl_b = create_memory_transport_pair()
    c_b = srv.register_client_transport(gw_b)
    hs_b = create_command("gateway.handshake", "client_b", payload={"client_id": "client_b", "client_role": "XR_DISPLAY", "session_id": "sess_b", "auth_token": "tok"})
    cl_b.send(encode_frame(serialize_envelope_bytes(hs_b)))
    srv.process_client_ingress(c_b)

    assert srv.active_connections_count == 2

    # Emit platform.session.evicted for sess_a
    evict_env = create_event("platform.session.evicted", "platform", payload={"session_id": "sess_a"})
    message_dispatcher.dispatch(evict_env)

    # Client A should be disconnected, Client B untouched
    assert srv.active_connections_count == 1
    assert c_a.state.value == "CLOSED"
    assert c_b.state.value == "ACTIVE"

    # Emit platform.session.stopped for sess_b
    stop_env = create_event("platform.session.stopped", "platform", payload={"session_id": "sess_b"})
    message_dispatcher.dispatch(stop_env)

    assert srv.active_connections_count == 0
    assert c_b.state.value == "CLOSED"

    srv.stop()


def test_m45_synchronous_ingress_safety(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify M45 Synchronous Ingress Safety blocks non-ACTIVE sessions before cleanup."""
    srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    gw, cl = create_memory_transport_pair()
    c = srv.register_client_transport(gw)
    
    # Handshake with a session name that trigger 'ACTIVE' first, then we'll change our mind?
    # Wait, if we use 'stop_sess_c' it will fail the handshake!
    # So we must handshake with 'sess_c', then change the connection.session in the test to 'stop_sess_c'!
    hs = create_command("gateway.handshake", "client_c", payload={"client_id": "client_c", "client_role": "XR_DISPLAY", "session_id": "sess_c", "auth_token": "tok"})
    cl.send(encode_frame(serialize_envelope_bytes(hs)))
    srv.process_client_ingress(c)

    cl.receive() # drain handshake response

    # Simulate persistence returning non-ACTIVE (e.g. STOPPED)
    # Since we use conftest patch, we can just alter the session ID internally:
    import dataclasses
    c._session = dataclasses.replace(c.session, session_id="stop_sess_c")

    # Send a query
    q = create_query("gateway.status", "client_c", payload={})
    cl.send(encode_frame(serialize_envelope_bytes(q)))
    srv.process_client_ingress(c)

    raw_resp = cl.receive()
    from holomed.gateway.framing import FrameParser
    parser = FrameParser()
    resp = deserialize_envelope(parser.feed(raw_resp)[0])
    
    assert resp.message_type.value == "ERROR"
    assert "error_code" in resp.payload
    assert resp.payload["error_code"] == "ERR_SESSION_INVALID"
    
    srv.stop()
