# -*- coding: utf-8 -*-
"""Strongly Typed Data Models, Enums, and Constants for M11 Gateway."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping, Optional

from holomed.gateway.exceptions import GatewayValidationError

# Canonical Constants
MAX_CLIENTS: int = 16
MAX_CONNECTIONS_PER_SESSION: int = 4
MAX_CLIENT_QUEUE: int = 64
MAX_FRAME_BYTES: int = 1048576  # 1 MiB (strict M00.2 limit)
MAX_HANDSHAKE_BYTES: int = 4096  # 4 KiB
MAX_CLIENT_ID_LENGTH: int = 64
CLIENT_TIMEOUT_SECONDS: float = 30.0
MAX_AUTH_ATTEMPTS: int = 3

CLIENT_ID_REGEX = re.compile(r"^[a-z0-9_-]{1,64}$")
SESSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class ClientRole(str, Enum):
    """Authoritative client capabilities for connected external runtimes."""

    XR_DISPLAY = "XR_DISPLAY"
    SURGEON_CONSOLE = "SURGEON_CONSOLE"
    ASSISTANT_PANEL = "ASSISTANT_PANEL"
    READ_ONLY_OBSERVER = "READ_ONLY_OBSERVER"


class ConnectionState(str, Enum):
    """Lifecycle progression states for a client transport connection."""

    CONNECTING = "CONNECTING"
    AUTHENTICATED = "AUTHENTICATED"
    ACTIVE = "ACTIVE"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"


class FramePriority(str, Enum):
    """Priority classification for egress backpressure eviction."""

    CRITICAL_RELIABLE = "CRITICAL_RELIABLE"
    TRANSIENT_VISUAL = "TRANSIENT_VISUAL"


class GatewayHealthStatus(str, Enum):
    """Health classification for GatewayService."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    FAILED = "FAILED"


@dataclass(frozen=True)
class HandshakePayload:
    """Validated parameters received in a gateway.handshake command."""

    client_id: str
    client_role: ClientRole
    session_id: str
    auth_token: str
    protocol_version: str

    def __post_init__(self) -> None:
        if not CLIENT_ID_REGEX.match(self.client_id):
            raise GatewayValidationError(f"Invalid client_id format: {self.client_id!r}")
        if not SESSION_ID_REGEX.match(self.session_id):
            raise GatewayValidationError(f"Invalid session_id format: {self.session_id!r}")
        if not isinstance(self.client_role, ClientRole):
            raise GatewayValidationError(f"Invalid client_role: {self.client_role!r}")
        if not self.auth_token:
            raise GatewayValidationError("auth_token cannot be empty")


@dataclass(frozen=True)
class ClientSession:
    """Immutable descriptor of an authenticated, active client connection."""

    client_id: str
    client_role: ClientRole
    session_id: str
    epoch_id: int
    connected_at_utc: str
    remote_address: str


@dataclass(frozen=True)
class ClientQueueStats:
    """Telemetry metrics tracking per-client queue utilization."""

    client_id: str
    queue_depth: int
    enqueued_total: int
    dropped_frames_total: int
    critical_messages_total: int
