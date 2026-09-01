# -*- coding: utf-8 -*-
"""HoloMed AI External Client Gateway & Protocol Transport Foundation (M11)."""

from __future__ import annotations

from holomed.gateway.auth import GatewayAuthenticator
from holomed.gateway.authorization import GatewayAuthorizationPolicy
from holomed.gateway.connection import GatewayConnection
from holomed.gateway.exceptions import (
    GatewayAuthenticationError,
    GatewayAuthorizationError,
    GatewayCapacityError,
    GatewayEpochMismatchError,
    GatewayError,
    GatewayFramingError,
    GatewayLifecycleError,
    GatewayQueueOverflowError,
    GatewaySessionMismatchError,
    GatewayShutdownError,
    GatewayTransportError,
    GatewayValidationError,
)
from holomed.gateway.framing import (
    HEADER_SIZE,
    FrameParser,
    encode_frame,
)
from holomed.gateway.models import (
    CLIENT_ID_REGEX,
    CLIENT_TIMEOUT_SECONDS,
    MAX_AUTH_ATTEMPTS,
    MAX_CLIENT_ID_LENGTH,
    MAX_CLIENT_QUEUE,
    MAX_CLIENTS,
    MAX_CONNECTIONS_PER_SESSION,
    MAX_FRAME_BYTES,
    MAX_HANDSHAKE_BYTES,
    SESSION_ID_REGEX,
    ClientQueueStats,
    ClientRole,
    ClientSession,
    ConnectionState,
    FramePriority,
    GatewayHealthStatus,
    HandshakePayload,
)
from holomed.gateway.service import GatewayService
from holomed.gateway.transports import (
    ITransport,
    MemoryStreamTransport,
    SocketTransport,
    create_memory_transport_pair,
)

__all__ = [
    # Exceptions
    "GatewayError",
    "GatewayLifecycleError",
    "GatewayShutdownError",
    "GatewayValidationError",
    "GatewayCapacityError",
    "GatewayTransportError",
    "GatewayFramingError",
    "GatewayAuthenticationError",
    "GatewayAuthorizationError",
    "GatewayQueueOverflowError",
    "GatewaySessionMismatchError",
    "GatewayEpochMismatchError",
    # Enums
    "ClientRole",
    "ConnectionState",
    "FramePriority",
    "GatewayHealthStatus",
    # Models
    "HandshakePayload",
    "ClientSession",
    "ClientQueueStats",
    # Constants
    "MAX_CLIENTS",
    "MAX_CONNECTIONS_PER_SESSION",
    "MAX_CLIENT_QUEUE",
    "MAX_FRAME_BYTES",
    "MAX_HANDSHAKE_BYTES",
    "MAX_CLIENT_ID_LENGTH",
    "CLIENT_TIMEOUT_SECONDS",
    "MAX_AUTH_ATTEMPTS",
    "HEADER_SIZE",
    "CLIENT_ID_REGEX",
    "SESSION_ID_REGEX",
    # Framing
    "encode_frame",
    "FrameParser",
    # Transports
    "ITransport",
    "MemoryStreamTransport",
    "SocketTransport",
    "create_memory_transport_pair",
    # Components
    "GatewayConnection",
    "GatewayAuthenticator",
    "GatewayAuthorizationPolicy",
    "GatewayService",
]
