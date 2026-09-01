# -*- coding: utf-8 -*-
"""Unit Tests for GatewayAuthenticator and Handshake Verification."""

from __future__ import annotations

import pytest

from holomed.gateway.auth import GatewayAuthenticator
from holomed.gateway.exceptions import (
    GatewayAuthenticationError,
    GatewaySessionMismatchError,
    GatewayValidationError,
)
from holomed.gateway.models import ClientRole
from holomed.protocol.builders import create_command, create_event


def test_handshake_successful_authentication() -> None:
    """Verify valid handshake produces an authenticated ClientSession."""
    auth = GatewayAuthenticator(required_token="secure_token_123")
    cmd = create_command(
        "gateway.handshake",
        "client_01",
        payload={
            "client_id": "client_01",
            "client_role": "SURGEON_CONSOLE",
            "session_id": "sess_surgical_01",
            "auth_token": "secure_token_123",
            "protocol_version": "1.0",
        },
    )
    session = auth.authenticate_handshake(cmd, active_epoch_id=1, remote_address="127.0.0.1:5000")
    assert session.client_id == "client_01"
    assert session.client_role == ClientRole.SURGEON_CONSOLE
    assert session.session_id == "sess_surgical_01"
    assert session.epoch_id == 1


def test_handshake_invalid_token_rejected() -> None:
    """Verify mismatched token raises GatewayAuthenticationError."""
    auth = GatewayAuthenticator(required_token="correct_token")
    cmd = create_command(
        "gateway.handshake",
        "client_01",
        payload={
            "client_id": "client_01",
            "client_role": "XR_DISPLAY",
            "session_id": "sess_01",
            "auth_token": "wrong_token",
            "protocol_version": "1.0",
        },
    )
    with pytest.raises(GatewayAuthenticationError):
        auth.authenticate_handshake(cmd, active_epoch_id=1)


def test_handshake_non_handshake_topic_rejected() -> None:
    """Verify sending non-handshake command as first message is rejected."""
    auth = GatewayAuthenticator()
    cmd = create_command("other.command", "client_01", payload={})
    with pytest.raises(GatewayAuthenticationError):
        auth.authenticate_handshake(cmd, active_epoch_id=1)


def test_handshake_session_mismatch_rejected() -> None:
    """Verify requesting an inactive session raises GatewaySessionMismatchError."""
    auth = GatewayAuthenticator()
    cmd = create_command(
        "gateway.handshake",
        "client_01",
        payload={
            "client_id": "client_01",
            "client_role": "XR_DISPLAY",
            "session_id": "inactive_session",
            "auth_token": "any_token",
            "protocol_version": "1.0",
        },
    )
    with pytest.raises(GatewaySessionMismatchError):
        auth.authenticate_handshake(cmd, active_epoch_id=1, known_sessions={"active_session_01"})
