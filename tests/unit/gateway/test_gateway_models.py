# -*- coding: utf-8 -*-
"""Unit Tests for Gateway Models, Client Roles, and Grammar Validation."""

from __future__ import annotations

import pytest

from holomed.gateway.exceptions import GatewayValidationError
from holomed.gateway.models import (
    CLIENT_ID_REGEX,
    MAX_CLIENT_QUEUE,
    MAX_CLIENTS,
    MAX_FRAME_BYTES,
    ClientRole,
    ConnectionState,
    HandshakePayload,
)


def test_client_role_enum_integrity() -> None:
    """Verify all 4 authoritative client roles exist."""
    expected = {
        "XR_DISPLAY",
        "SURGEON_CONSOLE",
        "ASSISTANT_PANEL",
        "READ_ONLY_OBSERVER",
    }
    actual = {r.value for r in ClientRole}
    assert actual == expected


def test_connection_state_enum_integrity() -> None:
    """Verify all connection states exist."""
    expected = {
        "CONNECTING",
        "AUTHENTICATED",
        "ACTIVE",
        "CLOSING",
        "CLOSED",
    }
    actual = {s.value for s in ConnectionState}
    assert actual == expected


def test_handshake_payload_validation() -> None:
    """Verify HandshakePayload validates client_id, session_id, role, and token."""
    hp = HandshakePayload(
        client_id="unity_hmd_01",
        client_role=ClientRole.XR_DISPLAY,
        session_id="surgical_sess_01",
        auth_token="valid_token",
        protocol_version="1.0",
    )
    assert hp.client_id == "unity_hmd_01"

    # Invalid client ID
    with pytest.raises(GatewayValidationError):
        HandshakePayload(
            client_id="invalid/id/with/slashes",
            client_role=ClientRole.XR_DISPLAY,
            session_id="sess_01",
            auth_token="valid",
            protocol_version="1.0",
        )

    # Empty token
    with pytest.raises(GatewayValidationError):
        HandshakePayload(
            client_id="valid_client",
            client_role=ClientRole.XR_DISPLAY,
            session_id="sess_01",
            auth_token="",
            protocol_version="1.0",
        )


def test_constants_and_limits() -> None:
    """Verify mathematical boundaries."""
    assert MAX_CLIENTS == 16
    assert MAX_CLIENT_QUEUE == 64
    assert MAX_FRAME_BYTES == 1048576  # 1 MiB
