# -*- coding: utf-8 -*-
"""Unit Tests for GatewayAuthorizationPolicy, Source Verification, and Actuation Prohibition."""

from __future__ import annotations

import pytest

from holomed.gateway.authorization import GatewayAuthorizationPolicy
from holomed.gateway.exceptions import (
    GatewayAuthorizationError,
    GatewayValidationError,
)
from holomed.gateway.models import ClientRole, ClientSession
from holomed.protocol.builders import create_command, create_query


def test_source_spoofing_prevented() -> None:
    """Verify client cannot spoof envelope.source (D284)."""
    session = ClientSession("client_real", ClientRole.SURGEON_CONSOLE, "s1", 1, "now", "addr")
    # Declares source as "platform_service"
    spoofed = create_command("workflow.start", "platform_service", payload={})

    with pytest.raises(GatewayValidationError) as exc_info:
        GatewayAuthorizationPolicy.authorize_message(session, spoofed)

    assert "Source spoofing detected" in str(exc_info.value)


def test_surgical_actuation_categorically_blocked() -> None:
    """Verify physical actuation, cutting, robotics, or energy delivery are blocked (D286)."""
    session = ClientSession("surgeon_console", ClientRole.SURGEON_CONSOLE, "s1", 1, "now", "addr")
    forbidden_commands = [
        "robot.arm.move",
        "tissue.cut",
        "laser.cauterize",
        "actuate.motor",
    ]

    for topic in forbidden_commands:
        cmd = create_command(topic, "surgeon_console", payload={})
        with pytest.raises(GatewayAuthorizationError) as exc_info:
            GatewayAuthorizationPolicy.authorize_message(session, cmd)
        assert "categorically prohibited" in str(exc_info.value)


def test_role_authorization_observer_cannot_send_commands() -> None:
    """Verify READ_ONLY_OBSERVER cannot issue COMMAND messages (D288)."""
    session = ClientSession("obs_01", ClientRole.READ_ONLY_OBSERVER, "s1", 1, "now", "addr")
    cmd = create_command("workflow.start", "obs_01", payload={})

    with pytest.raises(GatewayAuthorizationError):
        GatewayAuthorizationPolicy.authorize_message(session, cmd)

    # But QUERY is permitted
    q = create_query("workflow.status", "obs_01", payload={})
    GatewayAuthorizationPolicy.authorize_message(session, q)


def test_role_authorization_xr_display_cannot_send_commands() -> None:
    """Verify XR_DISPLAY cannot issue COMMAND messages (D288)."""
    session = ClientSession("unity_hmd", ClientRole.XR_DISPLAY, "s1", 1, "now", "addr")
    cmd = create_command("workflow.start", "unity_hmd", payload={})

    with pytest.raises(GatewayAuthorizationError):
        GatewayAuthorizationPolicy.authorize_message(session, cmd)


def test_role_authorization_assistant_cannot_confirm() -> None:
    """Verify ASSISTANT_PANEL cannot issue workflow.confirm."""
    session = ClientSession("asst_01", ClientRole.ASSISTANT_PANEL, "s1", 1, "now", "addr")
    cmd = create_command("workflow.confirm", "asst_01", payload={})

    with pytest.raises(GatewayAuthorizationError):
        GatewayAuthorizationPolicy.authorize_message(session, cmd)


def test_surgeon_console_permitted() -> None:
    """Verify SURGEON_CONSOLE is permitted for non-actuation workflow commands."""
    session = ClientSession("surgeon_01", ClientRole.SURGEON_CONSOLE, "s1", 1, "now", "addr")
    cmd = create_command("workflow.confirm", "surgeon_01", payload={})
    GatewayAuthorizationPolicy.authorize_message(session, cmd)


def test_route_allowlist_blocks_unpermitted_routes() -> None:
    """Verify M31 default-deny: internal, reset, and unknown routes are blocked at gateway ingress."""
    session = ClientSession("surgeon_01", ClientRole.SURGEON_CONSOLE, "s1", 1, "now", "addr")
    forbidden_routes = [
        "platform.reset",
        "platform.cycle",
        "tools.reset",
        "vision.pipeline.reset",
        "xr.reset",
        "ultron.reset",
        "anatomy.reset",
        "audio.pipeline.reset",
        "gesture.pipeline.reset",
        "persistence.replay",
        "drift.evaluate",
        "proximity.evaluate",
        "unknown.bogus.route",
    ]
    for r in forbidden_routes:
        cmd = create_command(r, "surgeon_01", payload={})
        with pytest.raises(GatewayAuthorizationError) as exc_info:
            GatewayAuthorizationPolicy.authorize_message(session, cmd)
        assert "not permitted through gateway ingress" in str(exc_info.value)


def test_route_allowlist_permits_approved_routes() -> None:
    """Verify M31 allowlist allows representative approved client-issuable routes."""
    session = ClientSession("surgeon_01", ClientRole.SURGEON_CONSOLE, "s1", 1, "now", "addr")
    permitted_routes = [
        "execution.navigation.execute",
        "execution.planning.execute",
        "execution.tool.invoke",
        "workflow.start",
        "workflow.status",
        "navigation.status.get",
        "gateway.status",
        "gateway.clients",
        "gateway.disconnect",
    ]
    for r in permitted_routes:
        if r.endswith(".get") or r in ("gateway.status", "gateway.clients", "workflow.status"):
            msg = create_query(r, "surgeon_01", payload={"session_id": "s1"})
        else:
            msg = create_command(r, "surgeon_01", payload={"session_id": "s1"})
        GatewayAuthorizationPolicy.authorize_message(session, msg)
