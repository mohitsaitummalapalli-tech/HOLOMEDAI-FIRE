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
from holomed.protocol.builders import create_command, create_event, create_query
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
