# -*- coding: utf-8 -*-
"""Master Application Supervisor & Platform Integration Service (M08)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.devices.manager import DeviceManager
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.protocol.builders import (
    create_error_response,
    create_event,
    create_response,
)
from holomed.protocol.models import MessageEnvelope
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ResourceStatus,
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceState
from holomed.platform.auditor import PlatformConsistencyAuditor
from holomed.platform.cycle import CycleCoordinator
from holomed.platform.events import RecordingPlatformEventSink
from holomed.platform.exceptions import (
    PlatformCapacityError,
    PlatformEpochMigrationError,
    PlatformEpochMismatchError,
    PlatformLifecycleError,
    PlatformResourceIntegrityError,
    PlatformSequenceError,
    PlatformShutdownError,
    PlatformValidationError,
)
from holomed.platform.health import HealthAggregator
from holomed.platform.models import (
    AggregateHealthReport,
    CycleSummary,
    SessionContext,
)
from holomed.platform.serialization import serialize_platform_payload
from holomed.platform.session import SessionManager

STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "platform.supervisor",
    "platform.session",
    "platform.cycle",
    "platform.auditor",
    "platform.metrics",
)

PLATFORM_DEPENDENCIES: tuple[str, ...] = (
    "device_manager",
    "vision_service",
    "audio_service",
    "gesture_service",
    "ultron_service",
    "anatomy_service",
    "xr_service",
    "tool_service",
)


class PlatformService(IService):
    """Master Application Supervisor & Platform Integration Service implementing IService."""

    def __init__(
        self,
        services: Optional[Mapping[str, IService]] = None,
        dispatcher: Optional[MessageDispatcher] = None,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._services: dict[str, IService] = dict(services) if services else {}
        self._dispatcher = dispatcher
        self._secret_filter = secret_filter
        self._logger = logger or StructuredLogger("platform_service", secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0

        # Subsystems
        self._session_manager: Optional[SessionManager] = None
        self._cycle_coordinator: Optional[CycleCoordinator] = None
        self._health_aggregator: Optional[HealthAggregator] = None
        self._auditor: Optional[PlatformConsistencyAuditor] = None
        self._event_sink: Optional[RecordingPlatformEventSink] = None

        # Concurrency & Reentrancy Guard (D253)
        self._in_transaction: bool = False

    @property
    def name(self) -> str:
        return "platform_service"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return PLATFORM_DEPENDENCIES

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise PlatformLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def session_manager(self) -> Optional[SessionManager]:
        return self._session_manager

    @property
    def cycle_coordinator(self) -> Optional[CycleCoordinator]:
        return self._cycle_coordinator

    @property
    def event_sink(self) -> Optional[RecordingPlatformEventSink]:
        return self._event_sink

    def register_service(self, name: str, service: IService) -> None:
        """Register an underlying domain service into the supervisor registry."""
        if self._state == ServiceState.STARTED:
            raise PlatformLifecycleError("Cannot register service after platform has STARTED")
        self._services[name] = service

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize supervisor state and acquire 5 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise PlatformLifecycleError(f"Cannot initialize PlatformService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        # Acquire 5 structural handles
        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Instantiate components
        self._session_manager = SessionManager(epoch_id=self._epoch_id)
        self._cycle_coordinator = CycleCoordinator(epoch_id=self._epoch_id)
        self._health_aggregator = HealthAggregator(secret_filter=self._secret_filter)
        self._auditor = PlatformConsistencyAuditor(secret_filter=self._secret_filter)
        self._event_sink = RecordingPlatformEventSink()

        # Register Dispatcher Routes strictly during INITIALIZED
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler(
                "platform.status",
                self.handle_status_query,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "platform.cycle",
                self.handle_cycle_command,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "platform.session.start",
                self.handle_session_start_command,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "platform.session.stop",
                self.handle_session_stop_command,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "platform.audit",
                self.handle_audit_query,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "platform.reset",
                self.handle_reset_command,
                self.name,
            )

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition supervisor to STARTED; acquires zero new resources."""
        if self._state != ServiceState.INITIALIZED:
            raise PlatformLifecycleError(f"Cannot start PlatformService in state {self._state.name}")

        self._state = ServiceState.STARTED
        self._emit_event("platform.started", {"epoch_id": self._epoch_id, "status": "STARTED"})

    def stop(self) -> None:
        """Release all acquired resources and teardown state cleanly."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._in_transaction:
            raise PlatformLifecycleError("Cannot stop PlatformService during active transaction")

        self._in_transaction = True
        failures: list[DeviceShutdownFailureRecord] = []
        try:
            self.clear()

            # Release all 5 structural handles
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
                raise PlatformShutdownError(
                    f"PlatformService teardown encountered {len(failures)} resource failure(s)",
                    failures,
                )

            self._state = ServiceState.STOPPED
        finally:
            self._in_transaction = False

    def health(self) -> ServiceHealth:
        """Evaluate and aggregate health across all underlying services (D254)."""
        now_utc = datetime.now(timezone.utc).isoformat()
        if self._state != ServiceState.STARTED:
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.FAILED if self._state == ServiceState.FAILED else HealthStatus.UNHEALTHY,
                message=f"PlatformService in state {self._state.name}",
                timestamp_utc=now_utc,
            )

        if self._health_aggregator is None:
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                message="Health aggregator uninitialized",
                timestamp_utc=now_utc,
            )

        # Poll underlying services
        service_states = {k: v.state for k, v in self._services.items() if hasattr(v, "state")}
        report = self._health_aggregator.aggregate_health(self._services, service_states, self._epoch_id)

        return ServiceHealth(
            name=self.name,
            status=report.status,
            message=f"Aggregate health: {report.status.name} across {len(self._services)} services",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Public Core API
    # -------------------------------------------------------------------------

    def tick(
        self,
        session_id: str,
        sequence_number: int,
        cycle_params: Optional[Mapping[str, Any]] = None,
    ) -> CycleSummary:
        """Execute one complete synchronous cycle across all domain services (D251, D253)."""
        if self._state != ServiceState.STARTED:
            raise PlatformLifecycleError(f"Cannot execute tick in state {self._state.name}")

        if self._in_transaction:
            raise PlatformLifecycleError("Reentrant call to tick rejected by transaction guard (D253)")

        self._in_transaction = True
        try:
            if self._session_manager is None or self._cycle_coordinator is None:
                raise PlatformResourceIntegrityError("Subsystems uninitialized")

            summary = self._cycle_coordinator.execute_cycle(
                session_manager=self._session_manager,
                session_id=session_id,
                sequence_number=sequence_number,
                services=self._services,
                cycle_params=cycle_params,
            )

            evt_topic = "platform.cycle.completed" if summary.status.value == "COMPLETED" else "platform.cycle.degraded"
            self._emit_event(
                evt_topic,
                {
                    "cycle_id": summary.cycle_id,
                    "session_id": summary.session_id,
                    "sequence_number": summary.sequence_number,
                    "status": summary.status.value,
                    "execution_time_ms": summary.execution_time_ms,
                },
            )
            return summary
        finally:
            self._in_transaction = False

    def start_session(self, session_id: str) -> SessionContext:
        """Start an active clinical session context."""
        if self._state != ServiceState.STARTED:
            raise PlatformLifecycleError(f"Cannot start session in state {self._state.name}")
        if self._session_manager is None:
            raise PlatformResourceIntegrityError("Session manager uninitialized")
        return self._session_manager.start_session(session_id, self._epoch_id)

    def stop_session(self, session_id: str) -> SessionContext:
        """Stop an active clinical session context."""
        if self._session_manager is None:
            raise PlatformResourceIntegrityError("Session manager uninitialized")
        return self._session_manager.stop_session(session_id)

    def evict_session(self, session_id: str) -> bool:
        """Evict session from underlying SessionManager, releasing capacity (M25)."""
        if self._session_manager is None:
            return False
        return self._session_manager.evict_session(session_id)

    def has_session(self, session_id: str) -> bool:
        """Check if session exists in underlying SessionManager."""
        if self._session_manager is None:
            return False
        return self._session_manager.has_session(session_id)

    def migrate_epoch(self, target_epoch_id: int) -> None:

        """Execute atomic-preparation, reverse-order, fail-stop epoch migration (D250)."""
        if self._in_transaction:
            raise PlatformLifecycleError("Cannot migrate epoch during an active transaction")

        self._in_transaction = True
        try:
            # 1. Target validation
            if target_epoch_id != self._epoch_id + 1:
                raise PlatformEpochMismatchError(
                    f"Target epoch {target_epoch_id} must strictly equal current epoch {self._epoch_id} + 1"
                )

            # 2. Reset epoch-aware services in reverse topological order
            epoch_aware_order = ("tool_service", "xr_service", "anatomy_service", "ultron_service")
            for srv_name in epoch_aware_order:
                srv = self._services.get(srv_name)
                if srv is not None and hasattr(srv, "reset"):
                    try:
                        srv.reset(target_epoch_id)
                    except Exception as e:
                        self._state = ServiceState.FAILED
                        self._emit_event(
                            "platform.epoch.migration_failed",
                            {"target_epoch": target_epoch_id, "failed_service": srv_name, "error": str(e)},
                        )
                        raise PlatformEpochMigrationError(
                            f"Epoch migration failed on '{srv_name}': {e}"
                        ) from e

            # 3. Reset perception services without epoch-aware reset
            for srv_name in ("gesture_service", "audio_service", "vision_service"):
                srv = self._services.get(srv_name)
                if srv is not None and hasattr(srv, "clear"):
                    srv.clear()

            # 4. Commit supervisor epoch
            self._epoch_id = target_epoch_id
            if self._session_manager is not None:
                self._session_manager.reset(target_epoch_id)
            if self._cycle_coordinator is not None:
                self._cycle_coordinator.reset(target_epoch_id)

            self._emit_event("platform.epoch.migrated", {"new_epoch_id": self._epoch_id})
        finally:
            self._in_transaction = False

    def audit_platform(self) -> Mapping[str, Any]:
        """Perform whole-system consistency and integration audit."""
        if (
            self._auditor is None
            or self._session_manager is None
            or self._cycle_coordinator is None
        ):
            raise PlatformResourceIntegrityError("Subsystems uninitialized")

        service_states = {k: v.state for k, v in self._services.items() if hasattr(v, "state")}
        return self._auditor.audit_platform(
            services=self._services,
            service_states=service_states,
            session_manager=self._session_manager,
            cycle_coordinator=self._cycle_coordinator,
            epoch_id=self._epoch_id,
        )

    def clear(self) -> None:
        """Clear all active sessions, cycle history, and event sinks."""
        if self._session_manager is not None:
            self._session_manager.clear()
        if self._cycle_coordinator is not None:
            self._cycle_coordinator.clear()
        if self._event_sink is not None:
            self._event_sink.clear()

    # -------------------------------------------------------------------------
    # Dispatcher Handlers
    # -------------------------------------------------------------------------

    def handle_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle platform.status query."""
        h = self.health()
        sessions_count = self._session_manager.session_count if self._session_manager else 0
        cycles_count = len(self._cycle_coordinator.cycle_history) if self._cycle_coordinator else 0

        payload = serialize_platform_payload(
            {
                "service_name": self.name,
                "state": self._state.name,
                "epoch_id": self._epoch_id,
                "aggregate_health": h.status.name,
                "health_message": h.message,
                "active_sessions_count": sessions_count,
                "cycle_history_count": cycles_count,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_cycle_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle platform.cycle command."""
        p = command_envelope.payload
        session_id = p.get("session_id", "default_session")
        sequence_number = p.get("sequence_number", 0)

        try:
            summary = self.tick(
                session_id=str(session_id),
                sequence_number=int(sequence_number),
                cycle_params=p.get("cycle_params"),
            )
            payload = serialize_platform_payload(
                {
                    "cycle_id": summary.cycle_id,
                    "session_id": summary.session_id,
                    "sequence_number": summary.sequence_number,
                    "status": summary.status.value,
                    "execution_time_ms": summary.execution_time_ms,
                    "phases_completed": list(summary.phases_completed),
                    "confidence": summary.confidence,
                    "uncertainty_metric": summary.uncertainty_metric,
                    "is_simulated": summary.is_simulated,
                }
            )
            return create_response(command_envelope, self.name, payload=dict(payload))
        except Exception as e:
            raw_err = str(e)
            redacted_err = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(
                command_envelope,
                self.name,
                error_code=type(e).__name__,
                error_message=redacted_err,
            )

    def handle_session_start_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle platform.session.start command."""
        session_id = command_envelope.payload.get("session_id")
        if not session_id:
            return create_error_response(
                command_envelope,
                self.name,
                error_code="INVALID_SESSION",
                error_message="Missing session_id",
            )
        try:
            ctx = self.start_session(str(session_id))
            payload = serialize_platform_payload(
                {
                    "session_id": ctx.session_id,
                    "epoch_id": ctx.epoch_id,
                    "status": ctx.status.value,
                    "created_timestamp_utc": ctx.created_timestamp_utc,
                }
            )
            return create_response(command_envelope, self.name, payload=dict(payload))
        except Exception as e:
            raw_err = str(e)
            redacted_err = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(
                command_envelope,
                self.name,
                error_code=type(e).__name__,
                error_message=redacted_err,
            )

    def handle_session_stop_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle platform.session.stop command."""
        session_id = command_envelope.payload.get("session_id")
        if not session_id:
            return create_error_response(
                command_envelope,
                self.name,
                error_code="INVALID_SESSION",
                error_message="Missing session_id",
            )
        try:
            ctx = self.stop_session(str(session_id))
            payload = serialize_platform_payload(
                {
                    "session_id": ctx.session_id,
                    "status": ctx.status.value,
                }
            )
            return create_response(command_envelope, self.name, payload=dict(payload))
        except Exception as e:
            raw_err = str(e)
            redacted_err = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(
                command_envelope,
                self.name,
                error_code=type(e).__name__,
                error_message=redacted_err,
            )

    def handle_audit_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle platform.audit query."""
        try:
            report = self.audit_platform()
            payload = serialize_platform_payload(report)
            return create_response(query_envelope, self.name, payload=dict(payload))
        except Exception as e:
            raw_err = str(e)
            redacted_err = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(
                query_envelope,
                self.name,
                error_code=type(e).__name__,
                error_message=redacted_err,
            )

    def handle_reset_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle platform.reset command."""
        target_epoch = command_envelope.payload.get("target_epoch_id")
        if target_epoch is None:
            return create_error_response(
                command_envelope,
                self.name,
                error_code="INVALID_RESET",
                error_message="Missing target_epoch_id",
            )
        try:
            self.migrate_epoch(int(target_epoch))
            payload = serialize_platform_payload(
                {"migration_completed": True, "epoch_id": self._epoch_id}
            )
            return create_response(command_envelope, self.name, payload=dict(payload))
        except Exception as e:
            raw_err = str(e)
            redacted_err = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(
                command_envelope,
                self.name,
                error_code=type(e).__name__,
                error_message=redacted_err,
            )

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit protocol event over dispatcher and record in event sink."""
        frozen_payload = serialize_platform_payload(payload)
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(frozen_payload),
        )

        if self._event_sink is not None:
            try:
                self._event_sink.record_event(env)
            except PlatformCapacityError:
                pass

        if self._dispatcher is not None and getattr(self._dispatcher, "state", None) == DispatcherState.STARTED:
            self._dispatcher.dispatch(env)
