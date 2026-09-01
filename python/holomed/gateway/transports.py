# -*- coding: utf-8 -*-
"""Stream and Network Transport Implementations for M11 Gateway."""

from __future__ import annotations

from abc import ABC, abstractmethod
import collections
import socket
from typing import Optional, Tuple

from holomed.gateway.exceptions import GatewayTransportError


class ITransport(ABC):
    """Abstract interface for bidirectional byte stream transport."""

    @abstractmethod
    def send(self, data: bytes) -> None:
        """Send raw bytes over the transport."""
        raise NotImplementedError

    @abstractmethod
    def receive(self, max_bytes: int = 65536) -> bytes:
        """Receive raw bytes from the transport. Returns empty bytes on EOF/closed."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Close the transport connection."""
        raise NotImplementedError

    @property
    @abstractmethod
    def is_closed(self) -> bool:
        """Check if transport is closed."""
        raise NotImplementedError

    @property
    @abstractmethod
    def remote_address(self) -> str:
        """Return remote peer address representation."""
        raise NotImplementedError


class MemoryStreamTransport(ITransport):
    """In-memory duplex byte-stream transport for deterministic test isolation."""

    def __init__(self, remote_address: str = "memory://client") -> None:
        self._remote_address = remote_address
        self._inbox: collections.deque[bytes] = collections.deque()
        self._peer: Optional[MemoryStreamTransport] = None
        self._closed: bool = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def remote_address(self) -> str:
        return self._remote_address

    def send(self, data: bytes) -> None:
        if self._closed:
            raise GatewayTransportError("Cannot send on closed MemoryStreamTransport")
        if self._peer is None or self._peer.is_closed:
            raise GatewayTransportError("Peer is disconnected")
        self._peer._deliver(data)

    def receive(self, max_bytes: int = 65536) -> bytes:
        if self._closed or not self._inbox:
            return b""
        chunk = self._inbox.popleft()
        if len(chunk) > max_bytes:
            # Requeue remaining
            self._inbox.appendleft(chunk[max_bytes:])
            return chunk[:max_bytes]
        return chunk

    def _deliver(self, data: bytes) -> None:
        if not self._closed and data:
            self._inbox.append(bytes(data))

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._inbox.clear()
            if self._peer and not self._peer._closed:
                self._peer.close()


def create_memory_transport_pair(
    gateway_address: str = "memory://server",
    client_address: str = "memory://client",
) -> Tuple[MemoryStreamTransport, MemoryStreamTransport]:
    """Create a linked pair of bidirectional in-memory transports."""
    t_gateway = MemoryStreamTransport(remote_address=client_address)
    t_client = MemoryStreamTransport(remote_address=gateway_address)
    t_gateway._peer = t_client
    t_client._peer = t_gateway
    return t_gateway, t_client


class SocketTransport(ITransport):
    """Standard library TCP socket transport."""

    def __init__(self, sock: socket.socket, remote_address: str) -> None:
        self._socket = sock
        self._remote_address = remote_address
        self._closed = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def remote_address(self) -> str:
        return self._remote_address

    def send(self, data: bytes) -> None:
        if self._closed:
            raise GatewayTransportError("Cannot send on closed SocketTransport")
        try:
            self._socket.sendall(data)
        except OSError as e:
            self.close()
            raise GatewayTransportError(f"Socket send failed: {e}") from e

    def receive(self, max_bytes: int = 65536) -> bytes:
        if self._closed:
            return b""
        try:
            chunk = self._socket.recv(max_bytes)
            if not chunk:
                self.close()
                return b""
            return chunk
        except OSError as e:
            self.close()
            raise GatewayTransportError(f"Socket recv failed: {e}") from e

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._socket.close()
            except OSError:
                pass
