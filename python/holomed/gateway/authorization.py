# -*- coding: utf-8 -*-
"""Role-Based Authorization Policy and Categorical Safety Gate for M11."""

from __future__ import annotations

from holomed.gateway.exceptions import (
    GatewayAuthorizationError,
    GatewayValidationError,
)
from holomed.gateway.models import ClientRole, ClientSession
from holomed.protocol.models import MessageEnvelope, MessageType

CATEGORICAL_SURGICAL_KEYWORDS: frozenset[str] = frozenset(
    [
        "robot",
        "actuate",
        "cut",
        "tissue",
        "cauterize",
        "energy",
        "ablate",
    ]
)


class GatewayAuthorizationPolicy:
    """Enforces role capabilities, source identity verification, and non-actuation safety."""

    @staticmethod
    def authorize_message(session: ClientSession, envelope: MessageEnvelope) -> None:
        """Validate client capability and ensure message does not violate safety boundaries."""
        # 1. Prevent Source Spoofing (D284)
        if envelope.source != session.client_id:
            raise GatewayValidationError(
                f"Source spoofing detected: envelope declared source={envelope.source!r}, authenticated={session.client_id!r}"
            )

        # 2. Categorical Surgical Actuation Block (D286)
        msg_name_lower = envelope.message_name.lower()
        for kw in CATEGORICAL_SURGICAL_KEYWORDS:
            if kw in msg_name_lower:
                raise GatewayAuthorizationError(
                    f"Physical surgical actuation command {envelope.message_name!r} is categorically prohibited"
                )

        # 3. Role-Based Message Type Enforcement
        role = session.client_role

        if role == ClientRole.READ_ONLY_OBSERVER:
            if envelope.message_type != MessageType.QUERY:
                raise GatewayAuthorizationError(
                    f"READ_ONLY_OBSERVER is restricted to QUERY messages, cannot issue {envelope.message_type.value}"
                )

        elif role == ClientRole.XR_DISPLAY:
            if envelope.message_type != MessageType.QUERY:
                raise GatewayAuthorizationError(
                    f"XR_DISPLAY is restricted to QUERY messages, cannot issue {envelope.message_type.value}"
                )

        elif role == ClientRole.ASSISTANT_PANEL:
            if envelope.message_name == "workflow.confirm":
                raise GatewayAuthorizationError(
                    "ASSISTANT_PANEL cannot issue human confirmation commands; requires SURGEON_CONSOLE"
                )

        elif role == ClientRole.SURGEON_CONSOLE:
            # Full operational authority within permitted non-actuation workflow
            pass
