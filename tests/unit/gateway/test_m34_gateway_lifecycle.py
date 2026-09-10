# -*- coding: utf-8 -*-
"""M34 Hostile Security & Lifecycle Tests for GatewayService.

Covers:
- Fail-closed caller session validation for gateway.disconnect
- Fail-closed caller session validation for gateway.clients
- Cross-session disconnect rejection on internal dispatcher bus
- Target connection survival upon rejected disconnect
- Multi-tenant client enumeration isolation
- Role hierarchy preservation
"""

from __future__ import annotations

from typing import Any

from holomed.core.dispatcher import MessageDispatcher
from holomed.gateway.framing import encode_frame
from holomed.gateway.models import ClientRole, ConnectionState
from holomed.gateway.service import GatewayService
from holomed.gateway.transports import create_memory_transport_pair
from holomed.protocol.builders import create_command, create_query
from holomed.protocol.codec import serialize_envelope_bytes
from holomed.protocol.models import MessageType
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter


def _setup_active_client(
    srv: GatewayService,
    client_id: str,
    role: ClientRole,
    session_id: str,
) -> tuple[Any, Any]:
    """Helper to establish an authenticated connection in GatewayService."""
    gw_side, cl_side = create_memory_transport_pair()
    conn = srv.register_client_transport(gw_side)
    hs = create_command(
        "gateway.handshake",
        client_id,
        payload={
            "client_id": client_id,
            "client_role": role.value,
            "session_id": session_id,
            "auth_token": f"token-{client_id}",
            "protocol_version": "1.0",
        },
    )
    cl_side.send(encode_frame(serialize_envelope_bytes(hs)))
    srv.process_client_ingress(conn)
    assert conn.state == ConnectionState.ACTIVE
    return conn, cl_side


class TestM34DisconnectSecurity:
    """Hostile test suite for gateway.disconnect authorization."""

    def test_missing_disconnect_session_fails_closed(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Missing session_id in payload for internal disconnect returns ERR_INVALID_ARGS and leaves target alive."""
        srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        target_conn, _ = _setup_active_client(srv, "surg_target", ClientRole.SURGEON_CONSOLE, "SESSION_A")

        # Attack: internal dispatcher caller with no session_id in payload
        cmd = create_command("gateway.disconnect", "unregistered_sender", payload={"client_id": "surg_target"})
        resp = message_dispatcher.dispatch(cmd)

        assert resp is not None
        assert resp.message_type == MessageType.ERROR
        assert isinstance(resp.payload, dict)
        assert resp.payload["error_code"] == "ERR_INVALID_ARGS"
        assert target_conn.state == ConnectionState.ACTIVE
        assert srv.active_connections_count == 1

        srv.stop()

    def test_none_disconnect_session_fails_closed(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Explicit None session_id returns ERR_INVALID_ARGS and preserves target."""
        srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        target_conn, _ = _setup_active_client(srv, "surg_target", ClientRole.SURGEON_CONSOLE, "SESSION_A")

        cmd = create_command(
            "gateway.disconnect",
            "unregistered_sender",
            payload={"client_id": "surg_target", "session_id": None},
        )
        resp = message_dispatcher.dispatch(cmd)

        assert resp is not None
        assert resp.message_type == MessageType.ERROR
        assert isinstance(resp.payload, dict)
        assert resp.payload["error_code"] == "ERR_INVALID_ARGS"
        assert target_conn.state == ConnectionState.ACTIVE

        srv.stop()

    def test_empty_disconnect_session_fails_closed(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Empty string session_id returns ERR_INVALID_ARGS and preserves target."""
        srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        target_conn, _ = _setup_active_client(srv, "surg_target", ClientRole.SURGEON_CONSOLE, "SESSION_A")

        cmd = create_command(
            "gateway.disconnect",
            "unregistered_sender",
            payload={"client_id": "surg_target", "session_id": ""},
        )
        resp = message_dispatcher.dispatch(cmd)

        assert resp is not None
        assert resp.message_type == MessageType.ERROR
        assert isinstance(resp.payload, dict)
        assert resp.payload["error_code"] == "ERR_INVALID_ARGS"
        assert target_conn.state == ConnectionState.ACTIVE

        srv.stop()

    def test_whitespace_disconnect_session_fails_closed(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Whitespace-only session_id returns ERR_INVALID_ARGS and preserves target."""
        srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        target_conn, _ = _setup_active_client(srv, "surg_target", ClientRole.SURGEON_CONSOLE, "SESSION_A")

        cmd = create_command(
            "gateway.disconnect",
            "unregistered_sender",
            payload={"client_id": "surg_target", "session_id": "   \t  "},
        )
        resp = message_dispatcher.dispatch(cmd)

        assert resp is not None
        assert resp.message_type == MessageType.ERROR
        assert isinstance(resp.payload, dict)
        assert resp.payload["error_code"] == "ERR_INVALID_ARGS"
        assert target_conn.state == ConnectionState.ACTIVE

        srv.stop()

    def test_cross_session_internal_dispatcher_disconnect_fails_closed(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Internal dispatcher message with SESSION_B attempting to disconnect SESSION_A client fails closed."""
        srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        target_conn_a, _ = _setup_active_client(srv, "target_a", ClientRole.SURGEON_CONSOLE, "SESSION_A")
        target_conn_b, _ = _setup_active_client(srv, "target_b", ClientRole.SURGEON_CONSOLE, "SESSION_B")

        # Caller claims SESSION_B but targets client in SESSION_A
        cmd = create_command(
            "gateway.disconnect",
            "internal_worker",
            payload={"client_id": "target_a", "session_id": "SESSION_B"},
        )
        resp = message_dispatcher.dispatch(cmd)

        assert resp is not None
        assert resp.message_type == MessageType.ERROR
        assert isinstance(resp.payload, dict)
        assert resp.payload["error_code"] == "ERR_SESSION_MISMATCH"
        assert target_conn_a.state == ConnectionState.ACTIVE
        assert target_conn_b.state == ConnectionState.ACTIVE
        assert srv.active_connections_count == 2

        srv.stop()

    def test_valid_same_session_disconnect_succeeds(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Valid caller session matching target session executes disconnect cleanly."""
        srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        target_conn, _ = _setup_active_client(srv, "client_to_kill", ClientRole.ASSISTANT_PANEL, "SESSION_A")
        assert srv.active_connections_count == 1

        cmd = create_command(
            "gateway.disconnect",
            "internal_coordinator",
            payload={"client_id": "client_to_kill", "session_id": "SESSION_A", "reason": "Procedure completed"},
        )
        resp = message_dispatcher.dispatch(cmd)

        assert resp is not None
        assert resp.message_type == MessageType.RESPONSE
        assert isinstance(resp.payload, dict)
        assert resp.payload["disconnected_client_id"] == "client_to_kill"
        assert target_conn.state == ConnectionState.CLOSED
        assert srv.active_connections_count == 0

        srv.stop()

    def test_role_hierarchy_remains_enforced(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """ASSISTANT_PANEL cannot disconnect SURGEON_CONSOLE within same session."""
        srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        surg_conn, _ = _setup_active_client(srv, "surg_console", ClientRole.SURGEON_CONSOLE, "SESSION_A")
        asst_conn, _ = _setup_active_client(srv, "asst_panel", ClientRole.ASSISTANT_PANEL, "SESSION_A")

        # asst_panel attempts to disconnect surg_console
        cmd = create_command(
            "gateway.disconnect",
            "asst_panel",
            payload={"client_id": "surg_console", "session_id": "SESSION_A"},
        )
        resp = message_dispatcher.dispatch(cmd)

        assert resp is not None
        assert resp.message_type == MessageType.ERROR
        assert isinstance(resp.payload, dict)
        assert resp.payload["error_code"] == "ERR_AUTHORIZATION_FAILED"
        assert surg_conn.state == ConnectionState.ACTIVE
        assert asst_conn.state == ConnectionState.ACTIVE

        srv.stop()


class TestM34ClientsQuerySecurity:
    """Hostile test suite for gateway.clients query authorization & tenant isolation."""

    def test_missing_clients_query_session_fails_closed(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Querying gateway.clients without session_id fails closed with ERR_INVALID_ARGS."""
        srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        _setup_active_client(srv, "surg_a", ClientRole.SURGEON_CONSOLE, "SESSION_A")
        _setup_active_client(srv, "surg_b", ClientRole.SURGEON_CONSOLE, "SESSION_B")

        q = create_query("gateway.clients", "unregistered_client", payload={})
        resp = message_dispatcher.dispatch(q)

        assert resp is not None
        assert resp.message_type == MessageType.ERROR
        assert isinstance(resp.payload, dict)
        assert resp.payload["error_code"] == "ERR_INVALID_ARGS"

        srv.stop()

    def test_none_clients_query_session_fails_closed(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Querying gateway.clients with session_id=None fails closed with ERR_INVALID_ARGS."""
        srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        _setup_active_client(srv, "surg_a", ClientRole.SURGEON_CONSOLE, "SESSION_A")

        q = create_query("gateway.clients", "unregistered_client", payload={"session_id": None})
        resp = message_dispatcher.dispatch(q)

        assert resp is not None
        assert resp.message_type == MessageType.ERROR
        assert isinstance(resp.payload, dict)
        assert resp.payload["error_code"] == "ERR_INVALID_ARGS"

        srv.stop()

    def test_empty_clients_query_session_fails_closed(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Querying gateway.clients with session_id="" fails closed with ERR_INVALID_ARGS."""
        srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        _setup_active_client(srv, "surg_a", ClientRole.SURGEON_CONSOLE, "SESSION_A")

        q = create_query("gateway.clients", "unregistered_client", payload={"session_id": ""})
        resp = message_dispatcher.dispatch(q)

        assert resp is not None
        assert resp.message_type == MessageType.ERROR
        assert isinstance(resp.payload, dict)
        assert resp.payload["error_code"] == "ERR_INVALID_ARGS"

        srv.stop()

    def test_whitespace_clients_query_session_fails_closed(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Querying gateway.clients with whitespace session_id fails closed with ERR_INVALID_ARGS."""
        srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        _setup_active_client(srv, "surg_a", ClientRole.SURGEON_CONSOLE, "SESSION_A")

        q = create_query("gateway.clients", "unregistered_client", payload={"session_id": "   "})
        resp = message_dispatcher.dispatch(q)

        assert resp is not None
        assert resp.message_type == MessageType.ERROR
        assert isinstance(resp.payload, dict)
        assert resp.payload["error_code"] == "ERR_INVALID_ARGS"

        srv.stop()

    def test_cross_session_clients_query_isolation(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Querying gateway.clients returns strictly clients matching the requested session."""
        srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        # Connect 2 clients in SESSION_A
        _setup_active_client(srv, "surg_a", ClientRole.SURGEON_CONSOLE, "SESSION_A")
        _setup_active_client(srv, "asst_a", ClientRole.ASSISTANT_PANEL, "SESSION_A")

        # Connect 2 clients in SESSION_B
        _setup_active_client(srv, "surg_b", ClientRole.SURGEON_CONSOLE, "SESSION_B")
        _setup_active_client(srv, "xr_b", ClientRole.XR_DISPLAY, "SESSION_B")

        # Query as SESSION_A
        q_a = create_query("gateway.clients", "caller", payload={"session_id": "SESSION_A"})
        resp_a = message_dispatcher.dispatch(q_a)

        assert resp_a is not None
        assert resp_a.message_type == MessageType.RESPONSE
        assert isinstance(resp_a.payload, dict)
        clients_a = resp_a.payload["clients"]
        assert len(clients_a) == 2
        client_ids_a = {c["client_id"] for c in clients_a}
        assert client_ids_a == {"surg_a", "asst_a"}
        # Verify NO SESSION_B client is disclosed
        assert "surg_b" not in client_ids_a
        assert "xr_b" not in client_ids_a

        # Query as SESSION_B
        q_b = create_query("gateway.clients", "caller", payload={"session_id": "SESSION_B"})
        resp_b = message_dispatcher.dispatch(q_b)

        assert resp_b is not None
        assert resp_b.message_type == MessageType.RESPONSE
        assert isinstance(resp_b.payload, dict)
        clients_b = resp_b.payload["clients"]
        assert len(clients_b) == 2
        client_ids_b = {c["client_id"] for c in clients_b}
        assert client_ids_b == {"surg_b", "xr_b"}
        # Verify NO SESSION_A client is disclosed
        assert "surg_a" not in client_ids_b
        assert "asst_a" not in client_ids_b

        srv.stop()

    def test_foreign_session_information_never_appears(
        self,
        runtime_context: RuntimeContext,
        message_dispatcher: MessageDispatcher,
        secret_filter: SecretFilter,
    ) -> None:
        """Querying for a non-existent session returns an empty list, never leaking active foreign clients."""
        srv = GatewayService(dispatcher=message_dispatcher, secret_filter=secret_filter)
        srv.initialize(runtime_context)
        message_dispatcher.start()
        srv.start()

        _setup_active_client(srv, "surg_a", ClientRole.SURGEON_CONSOLE, "SESSION_A")
        _setup_active_client(srv, "surg_b", ClientRole.SURGEON_CONSOLE, "SESSION_B")

        q_c = create_query("gateway.clients", "caller", payload={"session_id": "SESSION_NONEXISTENT"})
        resp_c = message_dispatcher.dispatch(q_c)

        assert resp_c is not None
        assert resp_c.message_type == MessageType.RESPONSE
        assert isinstance(resp_c.payload, dict)
        assert resp_c.payload["clients"] == []

        srv.stop()
