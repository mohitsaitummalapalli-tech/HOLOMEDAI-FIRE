# -*- coding: utf-8 -*-
"""External Client Gateway Service Implementing IService (M11)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Container, Dict, List, Mapping, Optional

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.gateway.auth import GatewayAuthenticator
from holomed.gateway.authorization import GatewayAuthorizationPolicy
from holomed.gateway.connection import GatewayConnection
from holomed.gateway.exceptions import (
    GatewayCapacityError,
    GatewayError,
    GatewayLifecycleError,
    GatewayShutdownError,
    GatewayValidationError,
)
from holomed.gateway.models import (
    MAX_CLIENTS,
    MAX_CONNECTIONS_PER_SESSION,
    ClientRole,
    ConnectionState,
)
from holomed.gateway.transports import ITransport
from holomed.protocol.builders import (
    create_error_response,
    create_event,
    create_response,
)
from holomed.protocol.codec import deserialize_envelope
from holomed.protocol.models import MessageEnvelope, MessageType
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceState
from holomed.workflow.service import WorkflowService

STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "gateway.listener",
    "gateway.registry",
    "gateway.egress",
    "gateway.metrics",
)


def _format_error_code(exc_type_name: str) -> str:
    """Format exception name into protocol-compliant ^ERR_[A-Z0-9_]+$ error code."""
    clean = exc_type_name.upper().replace("ERROR", "")
    return f"ERR_{clean}" if not clean.startswith("ERR_") else clean


class GatewayService(IService):
    """External Client Gateway & Protocol Transport Service."""

    def __init__(
        self,
        dispatcher: Optional[MessageDispatcher] = None,
        workflow_service: Optional[WorkflowService] = None,
        secret_filter: Optional[SecretFilter] = None,
        authenticator: Optional[GatewayAuthenticator] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._workflow_service = workflow_service
        self._secret_filter = secret_filter
        self._authenticator = authenticator or GatewayAuthenticator()
        self._logger = logger or StructuredLogger("gateway_service", secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0

        # Registry of active connections: client_id -> GatewayConnection
        self._connections: Dict[str, GatewayConnection] = {}
        # Unauthenticated connections: transport -> GatewayConnection
        self._pending_connections: List[GatewayConnection] = []

        self._in_transaction: bool = False
        self._total_messages_routed: int = 0

    @property
    def name(self) -> str:
        return "gateway_service"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("dispatcher", "workflow_service")

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise GatewayLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def active_connections_count(self) -> int:
        return len(self._connections)

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize gateway service and acquire exactly 4 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise GatewayLifecycleError(f"Cannot initialize GatewayService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        # Acquire 4 structural handles
        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Register Dispatcher Routes strictly during INITIALIZED
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler("gateway.status", self.handle_status_query, self.name)
            self._dispatcher.register_query_handler("gateway.clients", self.handle_clients_query, self.name)
            self._dispatcher.register_command_handler("gateway.disconnect", self.handle_disconnect_command, self.name)

            # Subscribe to presentation frames and workflow events
            self._dispatcher.subscribe_event("xr.presentation.frame", self.handle_presentation_event, self.name)
            self._dispatcher.subscribe_event("workflow.phase.entered", self.handle_workflow_broadcast_event, self.name)
            self._dispatcher.subscribe_event("workflow.confirmation.requested", self.handle_workflow_broadcast_event, self.name)
            self._dispatcher.subscribe_event("workflow.aborted", self.handle_workflow_abort_event, self.name)
            self._dispatcher.subscribe_event("workflow.interlock.tripped", self.handle_workflow_broadcast_event, self.name)

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED. Acquires zero new resources."""
        if self._state != ServiceState.INITIALIZED:
            raise GatewayLifecycleError(f"Cannot start GatewayService in state {self._state.name}")

        self._state = ServiceState.STARTED
        self._emit_event("gateway.started", {"epoch_id": self._epoch_id})

    def stop(self) -> None:
        """Close all connections, clear transient state, and release 4 handles idempotently."""
        if self._state in (ServiceState.STOPPED, getattr(ServiceState, "UNINITIALIZED")):
            return

        if self._in_transaction:
            raise GatewayLifecycleError("Cannot stop GatewayService during an active transaction")

        self._in_transaction = True
        failures: list[DeviceShutdownFailureRecord] = []
        try:
            # Close all active connections
            for client_id, conn in list(self._connections.items()):
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()

            for conn in list(self._pending_connections):
                try:
                    conn.close()
                except Exception:
                    pass
            self._pending_connections.clear()

            if self._resources is not None:
                for handle in list(self._resources.outstanding_handles):
                    try:
                        self._resources.release(handle.resource_id)
                    except Exception as e:
                        self._resources.mark_release_failed(handle.resource_id, str(e))
                        raw_err = str(e)
                        redacted_err = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
                        failures.append(
                            DeviceShutdownFailureRecord(
                                device_id=self.name,
                                error_type=type(e).__name__,
                                error_message=redacted_err,
                                execution_index=len(failures),
                                unreleased_resources=(handle.resource_id,),
                            )
                        )

            if failures:
                self._state = ServiceState.FAILED
                raise GatewayLifecycleError(
                    f"GatewayService teardown encountered {len(failures)} resource failure(s)"
                )

            self._state = ServiceState.STOPPED
        finally:
            self._in_transaction = False

    def health(self) -> ServiceHealth:
        """Evaluate and return in-process health snapshot."""
        now_utc = datetime.now(timezone.utc).isoformat()
        if self._state != ServiceState.STARTED:
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.FAILED if self._state == ServiceState.FAILED else HealthStatus.UNHEALTHY,
                message=f"Service state is {self._state.name}",
                timestamp_utc=now_utc,
            )

        if self._resources is None or len(self._resources.outstanding_handles) != len(STRUCTURAL_RESOURCE_IDS):
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                message="Structural resource tracking desynchronized",
                timestamp_utc=now_utc,
            )

        return ServiceHealth(
            name=self.name,
            status=HealthStatus.HEALTHY,
            message=f"Active clients: {len(self._connections)}, routed messages: {self._total_messages_routed}",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Client Transport Connection & Routing API
    # -------------------------------------------------------------------------

    def register_client_transport(self, transport: ITransport) -> GatewayConnection:
        """Register a new incoming transport stream into the pending connection pool."""
        if self._state != ServiceState.STARTED:
            raise GatewayLifecycleError(f"Cannot register transport in state {self._state.name}")

        if len(self._connections) + len(self._pending_connections) >= MAX_CLIENTS:
            transport.close()
            raise GatewayCapacityError(f"Gateway client capacity limit ({MAX_CLIENTS}) reached")

        conn = GatewayConnection(transport)
        self._pending_connections.append(conn)
        return conn

    def process_client_ingress(self, connection: GatewayConnection) -> int:
        """Read and process incoming framed envelopes from a client connection."""
        if self._state != ServiceState.STARTED:
            raise GatewayLifecycleError("Service is not started")

        raw_frames = connection.read_ingress()
        if not raw_frames:
            return 0

        processed = 0
        for frame_bytes in raw_frames:
            envelope = deserialize_envelope(frame_bytes)

            if connection.state == ConnectionState.CONNECTING:
                # First message must authenticate
                self._handle_handshake(connection, envelope)
            else:
                # Authenticated client message
                self._handle_client_message(connection, envelope)

            processed += 1
            self._total_messages_routed += 1

        # Flush any responses back out to client transport
        connection.flush_egress()
        return processed

    def _handle_handshake(self, connection: GatewayConnection, envelope: MessageEnvelope) -> None:
        """Authenticate client handshake and attach session."""
        session = self._authenticator.authenticate_handshake(
            envelope=envelope,
            active_epoch_id=self._epoch_id,
            remote_address=connection.transport.remote_address,
        )

        # Enforce MAX_CONNECTIONS_PER_SESSION
        session_conns = sum(
            1 for c in self._connections.values() if c.session and c.session.session_id == session.session_id
        )
        if session_conns >= MAX_CONNECTIONS_PER_SESSION:
            connection.close()
            raise GatewayCapacityError(
                f"Session {session.session_id!r} has reached maximum connections ({MAX_CONNECTIONS_PER_SESSION})"
            )

        if session.client_id in self._connections:
            # Displace or reject duplicate client ID
            old_conn = self._connections.pop(session.client_id)
            old_conn.close()

        connection.attach_session(session)
        if connection in self._pending_connections:
            self._pending_connections.remove(connection)
        self._connections[session.client_id] = connection

        # Send Handshake Acceptance Response
        resp = create_response(
            envelope,
            self.name,
            payload={
                "status": "AUTHENTICATED",
                "client_id": session.client_id,
                "client_role": session.client_role.value,
                "session_id": session.session_id,
                "epoch_id": self._epoch_id,
            },
        )
        connection.enqueue_envelope(resp)
        self._emit_event(
            "gateway.client.authenticated",
            {"client_id": session.client_id, "client_role": session.client_role.value, "session_id": session.session_id},
        )

    def _handle_client_message(self, connection: GatewayConnection, envelope: MessageEnvelope) -> None:
        """Authorize and dispatch an authenticated client envelope."""
        assert connection.session is not None
        session = connection.session

        # 1. Enforce Authorization Policy & Medical Safety (D284, D286, D288)
        GatewayAuthorizationPolicy.authorize_message(session, envelope)

        # 2. Dispatch Envelope Synchronously via MessageDispatcher
        if self._dispatcher is not None:
            resp = self._dispatcher.dispatch(envelope)
            if resp is not None:
                connection.enqueue_envelope(resp)
        else:
            err = create_error_response(
                envelope,
                self.name,
                "ERR_DISPATCHER_UNAVAILABLE",
                "MessageDispatcher is not connected to GatewayService",
            )
            connection.enqueue_envelope(err)

    def broadcast_envelope(
        self,
        envelope: MessageEnvelope,
        roles: Optional[Container[ClientRole]] = None,
        session_id: Optional[str] = None,
    ) -> int:
        """Broadcast an envelope to connected clients with deterministic ordering (D292)."""
        delivered = 0
        # Deterministic order: sorted by client_id
        for cid in sorted(self._connections.keys()):
            conn = self._connections[cid]
            if conn.session is None or conn.state != ConnectionState.ACTIVE:
                continue

            if session_id and conn.session.session_id != session_id:
                continue

            if roles and conn.session.client_role not in roles:
                continue

            conn.enqueue_envelope(envelope)
            conn.flush_egress()
            delivered += 1

        return delivered

    def disconnect_client(self, client_id: str, reason: str = "") -> None:
        """Disconnect and unregister an active client connection."""
        if client_id in self._connections:
            conn = self._connections.pop(client_id)
            conn.close()
            self._emit_event("gateway.client.disconnected", {"client_id": client_id, "reason": reason})

    def evict_session(self, session_id: str, capability: Optional[Any] = None) -> bool:
        """Surgically evict all active client connections bound to session_id (M28).

        Closes transports, cleans up connection descriptors, and reclaims capacity
        without affecting connections belonging to other active clinical sessions.
        """
        if not isinstance(session_id, str) or not session_id.strip():
            return False

        if self._in_transaction:
            raise GatewayLifecycleError("Cannot evict session during an active transaction")

        self._in_transaction = True
        evicted = False
        try:
            for cid in list(self._connections.keys()):
                conn = self._connections.get(cid)
                if conn and conn.session and conn.session.session_id == session_id:
                    conn = self._connections.pop(cid)
                    try:
                        conn.close()
                    except Exception:
                        pass
                    evicted = True
                    self._emit_event(
                        "gateway.client.disconnected",
                        {"client_id": cid, "reason": "Session evicted during teardown"},
                    )
            return evicted
        finally:
            self._in_transaction = False

    # -------------------------------------------------------------------------
    # Subscribed Event Handlers
    # -------------------------------------------------------------------------

    def handle_presentation_event(self, envelope: MessageEnvelope) -> None:
        """Egress XR presentation frames strictly to XR_DISPLAY and SURGEON_CONSOLE clients (D282)."""
        allowed_roles = {ClientRole.XR_DISPLAY, ClientRole.SURGEON_CONSOLE}
        session_id = envelope.payload.get("session_id") if isinstance(envelope.payload, dict) else None
        self.broadcast_envelope(envelope, roles=allowed_roles, session_id=session_id)

    def handle_workflow_broadcast_event(self, envelope: MessageEnvelope) -> None:
        """Broadcast workflow status, interlock, and confirmation events to session clients."""
        session_id = envelope.payload.get("session_id")
        self.broadcast_envelope(envelope, session_id=session_id)

    def handle_workflow_abort_event(self, envelope: MessageEnvelope) -> None:
        """Broadcast abort alert and gracefully disconnect session-bound clients (D291)."""
        session_id = envelope.payload.get("session_id")
        self.broadcast_envelope(envelope, session_id=session_id)
        if session_id:
            # Disconnect all clients bound to this aborted session
            for cid in list(self._connections.keys()):
                conn = self._connections.get(cid)
                if conn and conn.session and conn.session.session_id == session_id:
                    self.disconnect_client(cid, reason="Workflow session aborted")

    # -------------------------------------------------------------------------
    # Dispatcher Handlers
    # -------------------------------------------------------------------------

    def handle_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        h = self.health()
        payload = {
            "service_name": self.name,
            "state": self._state.name,
            "health_status": h.status.name,
            "active_clients_count": len(self._connections),
            "pending_clients_count": len(self._pending_connections),
            "total_messages_routed": self._total_messages_routed,
        }
        return create_response(query_envelope, self.name, payload=payload)

    def handle_clients_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        # Resolve caller session context (M31: isolate visibility to caller's session)
        caller_id = query_envelope.source
        caller_conn = self._connections.get(caller_id)
        if caller_conn is not None and caller_conn.session is not None:
            caller_session_id = caller_conn.session.session_id
        else:
            caller_session_id = (
                query_envelope.payload.get("session_id")
                if isinstance(query_envelope.payload, dict)
                else None
            )

        clients = [
            {
                "client_id": conn.client_id,
                "client_role": conn.client_role.value if conn.client_role else None,
                "session_id": conn.session.session_id if conn.session else None,
                "queue_depth": conn.queue_depth,
            }
            for cid, conn in sorted(self._connections.items())
            if conn.session is not None
            and (caller_session_id is None or conn.session.session_id == caller_session_id)
        ]
        return create_response(query_envelope, self.name, payload={"clients": clients})

    def handle_disconnect_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        payload = command_envelope.payload if isinstance(command_envelope.payload, dict) else {}
        client_id = payload.get("client_id")
        reason = payload.get("reason", "Operator disconnect command")
        if not client_id or not isinstance(client_id, str) or not client_id.strip():
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing client_id")

        target_conn = self._connections.get(str(client_id))
        if target_conn is None:
            return create_error_response(
                command_envelope, self.name, "ERR_CLIENT_NOT_FOUND", f"Target client {client_id!r} not found"
            )

        if target_conn.session is None:
            return create_error_response(
                command_envelope, self.name, "ERR_INVALID_ARGS", f"Target client {client_id!r} lacks active session"
            )

        # Resolve caller context
        caller_id = command_envelope.source
        caller_conn = self._connections.get(caller_id)
        if caller_conn is not None and caller_conn.session is not None:
            caller_session_id = caller_conn.session.session_id
            caller_role = caller_conn.session.client_role
        else:
            caller_session_id = payload.get("session_id")
            caller_role = None

        target_session_id = target_conn.session.session_id

        # M31 Invariant A.1: Cross-session disconnect rejected
        if caller_session_id is not None and target_session_id != caller_session_id:
            return create_error_response(
                command_envelope,
                self.name,
                "ERR_SESSION_MISMATCH",
                f"Cross-session disconnect rejected: target {client_id!r} belongs to session {target_session_id!r}, "
                f"caller belongs to session {caller_session_id!r}",
            )

        # M31 Invariant A.2: Role hierarchy (ASSISTANT_PANEL cannot disconnect SURGEON_CONSOLE)
        if (
            caller_role == ClientRole.ASSISTANT_PANEL
            and target_conn.session.client_role == ClientRole.SURGEON_CONSOLE
            and caller_id != client_id
        ):
            return create_error_response(
                command_envelope,
                self.name,
                "ERR_AUTHORIZATION_FAILED",
                "ASSISTANT_PANEL cannot disconnect SURGEON_CONSOLE",
            )

        # All authorization passed; execute disconnect
        self.disconnect_client(str(client_id), str(reason))
        return create_response(command_envelope, self.name, payload={"disconnected_client_id": client_id})

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit protocol event over dispatcher."""
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(payload),
        )
        if self._dispatcher is not None and getattr(self._dispatcher, "state", None) == DispatcherState.STARTED:
            self._dispatcher.dispatch(env)
