# -*- coding: utf-8 -*-
"""Client Authentication and Handshake Verification for M11."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Container, Optional

from holomed.gateway.exceptions import (
    GatewayAuthenticationError,
    GatewaySessionMismatchError,
    GatewayValidationError,
)
from holomed.gateway.models import (
    CLIENT_ID_REGEX,
    ClientRole,
    ClientSession,
    HandshakePayload,
)
from holomed.protocol.models import MessageEnvelope, MessageType


class GatewayAuthenticator:
    """Validates client identity, authentication tokens, and session bindings."""

    def __init__(self, required_token: Optional[str] = None) -> None:
        self._required_token = required_token

    def authenticate_handshake(
        self,
        envelope: MessageEnvelope,
        active_epoch_id: int,
        known_sessions: Optional[Container[str]] = None,
        remote_address: str = "unknown",
    ) -> ClientSession:
        """Verify handshake message and establish authenticated client session."""
        if envelope.message_name != "gateway.handshake":
            raise GatewayAuthenticationError(
                f"First message must be 'gateway.handshake', got {envelope.message_name!r}"
            )
        if envelope.message_type != MessageType.COMMAND:
            raise GatewayAuthenticationError(
                f"Handshake must be COMMAND message, got {envelope.message_type.value}"
            )

        payload = envelope.payload
        if not isinstance(payload, dict):
            raise GatewayValidationError("Handshake payload must be a JSON dictionary")

        client_id = payload.get("client_id")
        client_role_str = payload.get("client_role")
        session_id = payload.get("session_id")
        auth_token = payload.get("auth_token")
        protocol_version = payload.get("protocol_version", "1.0")

        if not client_id or not isinstance(client_id, str):
            raise GatewayValidationError("Missing or invalid client_id")
        if not CLIENT_ID_REGEX.match(client_id):
            raise GatewayValidationError(f"client_id {client_id!r} violates regex {CLIENT_ID_REGEX.pattern}")

        if not client_role_str or not isinstance(client_role_str, str):
            raise GatewayValidationError("Missing or invalid client_role")
        try:
            client_role = ClientRole(client_role_str)
        except ValueError:
            raise GatewayValidationError(f"Unknown client_role: {client_role_str!r}")

        if not session_id or not isinstance(session_id, str):
            raise GatewayValidationError("Missing or invalid session_id")

        if known_sessions is not None and session_id not in known_sessions:
            raise GatewaySessionMismatchError(f"Requested session_id {session_id!r} is not active")

        if not auth_token or not isinstance(auth_token, str):
            raise GatewayAuthenticationError("Missing or invalid auth_token")

        if self._required_token and auth_token != self._required_token:
            raise GatewayAuthenticationError("Invalid authentication token")

        now_utc = datetime.now(timezone.utc).isoformat()
        return ClientSession(
            client_id=client_id,
            client_role=client_role,
            session_id=session_id,
            epoch_id=active_epoch_id,
            connected_at_utc=now_utc,
            remote_address=remote_address,
        )
