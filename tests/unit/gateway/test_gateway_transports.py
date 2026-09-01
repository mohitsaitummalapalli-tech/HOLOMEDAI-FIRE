# -*- coding: utf-8 -*-
"""Unit Tests for MemoryStreamTransport and SocketTransport."""

from __future__ import annotations

import socket
import pytest

from holomed.gateway.exceptions import GatewayTransportError
from holomed.gateway.transports import (
    MemoryStreamTransport,
    SocketTransport,
    create_memory_transport_pair,
)


def test_memory_stream_transport_pair() -> None:
    """Verify bidirectional duplex communication across MemoryStreamTransport pair."""
    gw_side, cl_side = create_memory_transport_pair()

    assert gw_side.is_closed is False
    assert cl_side.is_closed is False

    # Client -> Gateway
    cl_side.send(b"ping from client")
    received_at_gw = gw_side.receive()
    assert received_at_gw == b"ping from client"

    # Gateway -> Client
    gw_side.send(b"pong from gateway")
    received_at_cl = cl_side.receive()
    assert received_at_cl == b"pong from gateway"

    # Close
    gw_side.close()
    assert gw_side.is_closed is True
    assert cl_side.is_closed is True


def test_memory_stream_send_on_closed_raises() -> None:
    """Verify sending on closed transport raises GatewayTransportError."""
    gw_side, cl_side = create_memory_transport_pair()
    gw_side.close()

    with pytest.raises(GatewayTransportError):
        gw_side.send(b"data")


def test_socket_transport_loopback() -> None:
    """Verify real loopback SocketTransport over localhost."""
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    port = server_sock.getsockname()[1]
    server_sock.listen(1)

    client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_sock.connect(("127.0.0.1", port))

    conn_sock, addr = server_sock.accept()

    gw_trans = SocketTransport(conn_sock, remote_address=f"{addr[0]}:{addr[1]}")
    cl_trans = SocketTransport(client_sock, remote_address=f"127.0.0.1:{port}")

    # Send across real TCP socket
    cl_trans.send(b"Hello loopback TCP")
    rec = gw_trans.receive()
    assert rec == b"Hello loopback TCP"

    gw_trans.send(b"Reply over TCP")
    rec_reply = cl_trans.receive()
    assert rec_reply == b"Reply over TCP"

    gw_trans.close()
    cl_trans.close()
    server_sock.close()
