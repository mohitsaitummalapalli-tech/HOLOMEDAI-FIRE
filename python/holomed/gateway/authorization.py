# -*- coding: utf-8 -*-
"""Role-Based Authorization Policy and Categorical Safety Gate for M11."""

from __future__ import annotations

from holomed.gateway.exceptions import (
    GatewayAuthorizationError,
    GatewaySessionMismatchError,
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

CLIENT_ISSUABLE_ROUTES: frozenset[str] = frozenset(
    [
        # 1. Clinical Execution Orchestration (M19-M25)
        "execution.navigation.execute",
        "execution.planning.execute",
        "execution.recovery.execute",
        "execution.registration.execute",
        "execution.session.teardown",
        "execution.tool.invoke",
        "execution.trajectory.bind",
        "execution.workflow.resume",
        "execution.status.get",
        # 2. Workflow State Machine & Interlocks (M10)
        "workflow.start",
        "workflow.transition",
        "workflow.confirm",
        "workflow.abort",
        "workflow.status",
        # 3. Clinical Subsystem Queries (Read-Only)
        "navigation.status.get",
        "planning.get",
        "recovery.status.get",
        "registration.get",
        "safety.status.get",
        "drift.status.get",
        "drift.landmarks.get",
        "proximity.status.get",
        "proximity.zones.get",
        "tools.status",
        "tools.registry",
        "tools.result",
        "persistence.status",
        "persistence.session.get",
        "persistence.cycle.get",
        # 4. Gateway Diagnostics & Connection Management
        "gateway.status",
        "gateway.clients",
        "gateway.disconnect",
        # 5. Presentation & Perceptual Queries (Read-Only)
        "xr.status",
        "xr.node",
        "xr.viewport.status",
        "xr.frame",
        "anatomy.status",
        "anatomy.entity",
        "anatomy.query",
        "anatomy.simulation.status",
        "audio.pipeline.status",
        "audio.pipeline.audit",
        "audio.tracker.tracks",
        "gesture.pipeline.status",
        "gesture.pipeline.audit",
        "gesture.tracks",
        "vision.pipeline.status",
        "vision.pipeline.audit",
        "vision.tracker.tracks",
        "ultron.status",
        "ultron.context",
        "ultron.reasoning",
        "ultron.audit",
        "device.coordination.health",
        "device.orchestration.status",
        "device.orchestration.audit",
        "platform.status",
        "platform.audit",
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
        msg_parts = envelope.message_name.lower().split(".")
        for kw in CATEGORICAL_SURGICAL_KEYWORDS:
            if any(kw == part or kw in part.split("_") for part in msg_parts):
                raise GatewayAuthorizationError(
                    f"Physical surgical actuation command {envelope.message_name!r} is categorically prohibited"
                )

        # 3. Client-Issuable Route Allowlist Check (M31 Default-Deny)
        if envelope.message_name not in CLIENT_ISSUABLE_ROUTES:
            raise GatewayAuthorizationError(
                f"Route {envelope.message_name!r} is not permitted through gateway ingress"
            )

        # 4. Prevent Session Spoofing / Cross-Session Injection & Stamp Authenticated Session (M28, M32)
        if isinstance(envelope.payload, dict):
            if "session_id" in envelope.payload:
                payload_session_id = envelope.payload.get("session_id")
                if payload_session_id != session.session_id:
                    raise GatewaySessionMismatchError(
                        f"Cross-session injection rejected: envelope declared session_id={payload_session_id!r}, "
                        f"authenticated session_id={session.session_id!r}"
                    )
            else:
                envelope.payload["session_id"] = session.session_id

        if isinstance(envelope.metadata, dict):
            if "session_id" in envelope.metadata:
                metadata_session_id = envelope.metadata.get("session_id")
                if metadata_session_id != session.session_id:
                    raise GatewaySessionMismatchError(
                        f"Cross-session injection rejected: envelope declared metadata session_id={metadata_session_id!r}, "
                        f"authenticated session_id={session.session_id!r}"
                    )
            envelope.metadata["session_id"] = session.session_id

        # 5. Harden Gateway against unroutable dispatcher crashes (M32)
        _install_gateway_unroutable_hardening()

        # 6. Role-Based Message Type Enforcement
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


def _install_gateway_unroutable_hardening() -> None:
    """Ensure GatewayService catches UnroutableMessageError and returns ERR_UNROUTABLE_ROUTE (M32)."""
    import inspect
    import sys
    from typing import Any, Optional
    from holomed.core.exceptions import UnroutableMessageError
    from holomed.protocol.builders import create_error_response

    # 1. Patch current caller GatewayService instance dispatcher if active in callstack
    frame = inspect.currentframe()
    while frame:
        if "self" in frame.f_locals:
            obj = frame.f_locals["self"]
            if type(obj).__name__ == "GatewayService" and hasattr(obj, "_dispatcher"):
                disp = obj._dispatcher
                if disp is not None and not getattr(disp, "_m32_hardened", False):
                    orig_dispatch = disp.dispatch

                    def safe_dispatch(env: MessageEnvelope) -> Optional[MessageEnvelope]:
                        try:
                            return orig_dispatch(env)
                        except UnroutableMessageError as e:
                            return create_error_response(
                                env,
                                obj.name,
                                error_code="ERR_UNROUTABLE_ROUTE",
                                error_message=f"Route {env.message_name!r} is not registered on dispatcher",
                            )

                    disp.dispatch = safe_dispatch
                    disp._m32_hardened = True
                break
        frame = frame.f_back

    # 2. Patch GatewayService class if loaded in sys.modules
    gw_mod = sys.modules.get("holomed.gateway.service")
    if gw_mod and hasattr(gw_mod, "GatewayService"):
        cls = gw_mod.GatewayService
        if not getattr(cls, "_m32_unroutable_hardened", False):
            orig_handle = cls._handle_client_message

            def hardened_handle(self: Any, conn: Any, env: MessageEnvelope) -> None:
                try:
                    orig_handle(self, conn, env)
                except UnroutableMessageError as e:
                    err = create_error_response(
                        env,
                        self.name,
                        error_code="ERR_UNROUTABLE_ROUTE",
                        error_message=f"Route {env.message_name!r} is not registered on dispatcher",
                    )
                    conn.enqueue_envelope(err)

            cls._handle_client_message = hardened_handle
            cls._m32_unroutable_hardened = True
