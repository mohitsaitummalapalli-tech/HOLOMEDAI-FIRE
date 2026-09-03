# -*- coding: utf-8 -*-
"""Unified Cross-Service Safety Gate Service Implementing IService for M18."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.drift.service import DriftService
from holomed.navigation.service import NavigationService
from holomed.persistence.service import PersistenceService
from holomed.protocol.builders import (
    create_error_response,
    create_event,
    create_response,
)
from holomed.protocol.models import MessageEnvelope
from holomed.proximity.service import ProximityService
from holomed.recovery.service import RecoveryService
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceState
from holomed.safety_gate.constants import (
    MAX_ACTIVE_GATE_SESSIONS,
    SERVICE_NAME,
    STRUCTURAL_RESOURCE_IDS,
    TOPIC_SAFETY_EVALUATED,
    TOPIC_SAFETY_STATUS_GET,
)
from holomed.safety_gate.evaluator import SafetyGateEvaluator
from holomed.safety_gate.exceptions import (
    SafetyGateCapacityError,
    SafetyGateLifecycleError,
    SafetyGateShutdownError,
    SafetyGateValidationError,
)
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateRequest,
    GateStatusRecord,
    SafetyGateAction,
)
from holomed.workflow.service import WorkflowService


def _format_error_code(exc_type_name: str) -> str:
    """Format exception class name into protocol-compliant ^ERR_[A-Z0-9_]+$ error code."""
    clean = exc_type_name.upper().replace("ERROR", "")
    return f"ERR_{clean}" if not clean.startswith("ERR_") else clean


class SafetyGateService(IService):
    """Unified Cross-Service Navigation Safety Authorization Gate Service."""

    def __init__(
        self,
        dispatcher: Optional[MessageDispatcher] = None,
        workflow_service: Optional[WorkflowService] = None,
        registration_service: Optional[RegistrationService] = None,
        navigation_service: Optional[NavigationService] = None,
        proximity_service: Optional[ProximityService] = None,
        drift_service: Optional[DriftService] = None,
        recovery_service: Optional[RecoveryService] = None,
        persistence_service: Optional[PersistenceService] = None,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._workflow_service = workflow_service
        self._registration_service = registration_service
        self._navigation_service = navigation_service
        self._proximity_service = proximity_service
        self._drift_service = drift_service
        self._recovery_service = recovery_service
        self._persistence_service = persistence_service
        self._secret_filter = secret_filter
        self._logger = logger or StructuredLogger(SERVICE_NAME, secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0

        # Session decision cache
        self._latest_decisions: Dict[str, GateStatusRecord] = {}
        # session_id -> (last_decision, last_reason_code) for persistence deduplication
        self._persisted_states: Dict[str, Tuple[GateDecision, GateReasonCode]] = {}

        self._in_transaction: bool = False

    @property
    def name(self) -> str:
        return SERVICE_NAME

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("dispatcher",)

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise SafetyGateLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def active_sessions_count(self) -> int:
        return len(self._latest_decisions)

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize safety gate service and acquire 4 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise SafetyGateLifecycleError(f"Cannot initialize SafetyGateService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Register Dispatcher Routes (M30: Query only, raw evaluate command removed)
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler(TOPIC_SAFETY_STATUS_GET, self.handle_get_status_query, self.name)

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED."""
        if self._state != ServiceState.INITIALIZED:
            raise SafetyGateLifecycleError(f"Cannot start SafetyGateService in state {self._state.name}")

        self._state = ServiceState.STARTED
        self._emit_event("safety_gate.started", {"epoch_id": self._epoch_id, "state": ServiceState.STARTED.name})

    def stop(self) -> None:
        """Stop safety gate, clear cached states, and release 4 handles idempotently."""
        if self._state in (ServiceState.STOPPED, getattr(ServiceState, "UNINITIALIZED")):
            return

        if self._in_transaction:
            raise SafetyGateLifecycleError("Cannot stop SafetyGateService during an active transaction")

        self._in_transaction = True
        failures: list[DeviceShutdownFailureRecord] = []
        try:
            self.clear()

            if self._resources is not None:
                for handle in list(self._resources.outstanding_handles):
                    try:
                        self._resources.release(handle.resource_id)
                    except Exception as e:
                        self._resources.mark_release_failed(handle.resource_id, str(e))
                        raw_err = str(e)
                        redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
                        failures.append(
                            DeviceShutdownFailureRecord(
                                device_id=self.name,
                                error_type=type(e).__name__,
                                error_message=redacted,
                                execution_index=len(failures),
                                unreleased_resources=(handle.resource_id,),
                            )
                        )

            if failures:
                self._state = ServiceState.FAILED
                raise SafetyGateShutdownError(
                    f"SafetyGateService teardown encountered {len(failures)} resource failure(s)",
                    failures,
                )

            self._state = ServiceState.STOPPED
        finally:
            self._in_transaction = False

    def health(self) -> ServiceHealth:
        """Return in-process health snapshot."""
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
            message=f"Active gate sessions: {len(self._latest_decisions)}",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Core Safety Gate Evaluation
    # -------------------------------------------------------------------------

    def evaluate(self, request: GateRequest) -> GateStatusRecord:
        """Synchronously evaluate cross-service safety evidence and return canonical decision."""
        if self._state != ServiceState.STARTED:
            raise SafetyGateLifecycleError(f"Cannot evaluate safety gate in state {self._state.name}")
        if self._in_transaction:
            raise SafetyGateLifecycleError("Reentrant call to evaluate rejected")

        self._in_transaction = True
        try:
            session_id = request.session_id
            if session_id not in self._latest_decisions and len(self._latest_decisions) >= MAX_ACTIVE_GATE_SESSIONS:
                raise SafetyGateCapacityError(f"Max active gate sessions ({MAX_ACTIVE_GATE_SESSIONS}) exceeded")

            # Execute pure cross-service logic
            decision_record = SafetyGateEvaluator.evaluate(
                request=request,
                epoch_id=self._epoch_id,
                workflow_service=self._workflow_service,
                registration_service=self._registration_service,
                navigation_service=self._navigation_service,
                proximity_service=self._proximity_service,
                drift_service=self._drift_service,
                recovery_service=self._recovery_service,
            )

            # Check persistence deduplication policy:
            # Persist on:
            # 1. State transition (decision changed)
            # 2. Reason change under denial
            # 3. First occurrence
            current_signature = (decision_record.decision, decision_record.reason_code)
            last_signature = self._persisted_states.get(session_id)

            should_persist = last_signature is None or last_signature != current_signature

            if should_persist:
                self._persisted_states[session_id] = current_signature
                if self._persistence_service is not None:
                    audit_payload = {
                        "session_id": session_id,
                        "decision": decision_record.decision.value,
                        "severity": decision_record.severity.value,
                        "reason_code": decision_record.reason_code.value,
                        "action": decision_record.action.value,
                        "sequence_number": decision_record.sequence_number,
                        "epoch_id": self._epoch_id,
                        "evaluated_at_utc": decision_record.evaluated_at_utc,
                    }
                    self._persistence_service.record_audit(audit_payload, session_id=session_id)

                self._emit_event(
                    TOPIC_SAFETY_EVALUATED,
                    {
                        "session_id": session_id,
                        "decision": decision_record.decision.value,
                        "severity": decision_record.severity.value,
                        "reason_code": decision_record.reason_code.value,
                        "action": decision_record.action.value,
                        "epoch_id": self._epoch_id,
                    },
                )

            self._latest_decisions[session_id] = decision_record
            return decision_record
        finally:
            self._in_transaction = False

    def get_gate_status(self, session_id: str) -> Optional[GateStatusRecord]:
        """Query latest cached safety gate decision for a session."""
        return self._latest_decisions.get(session_id)

    def evict_session(self, session_id: str, capability: Optional[Any] = None) -> bool:
        """Evict session-scoped safety gate decisions and persistence signatures, releasing capacity (M25)."""
        if self._in_transaction:
            raise SafetyGateLifecycleError("Reentrant call to evict_session rejected")
        evicted = False
        if session_id in self._latest_decisions:
            del self._latest_decisions[session_id]
            evicted = True
        if session_id in self._persisted_states:
            del self._persisted_states[session_id]
            evicted = True
        return evicted

    def clear(self) -> None:
        """Clear transient session tracking cache."""
        self._latest_decisions.clear()
        self._persisted_states.clear()


    # -------------------------------------------------------------------------
    # Helper & Dispatcher Protocol Handlers
    # -------------------------------------------------------------------------

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit protocol event over message dispatcher."""
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(payload),
        )
        if self._dispatcher is not None and getattr(self._dispatcher, "state", None) == DispatcherState.STARTED:
            self._dispatcher.dispatch(env)

    def handle_evaluate_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle safety_gate.evaluate command."""
        payload = command_envelope.payload
        session_id = payload.get("session_id")
        action_str = payload.get("action")
        seq = payload.get("sequence_number", 1)
        now_utc = payload.get("now_utc") or datetime.now(timezone.utc).isoformat()
        inst_id = payload.get("instrument_id")
        traj_id = payload.get("target_trajectory_id")

        if not session_id or not action_str:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id or action")

        try:
            action = SafetyGateAction(action_str)
            req = GateRequest(
                session_id=str(session_id),
                action=action,
                sequence_number=int(seq),
                now_utc=str(now_utc),
                instrument_id=str(inst_id) if inst_id else None,
                target_trajectory_id=str(traj_id) if traj_id else None,
            )
            record = self.evaluate(req)
            return create_response(
                command_envelope,
                self.name,
                payload={
                    "session_id": session_id,
                    "decision": record.decision.value,
                    "severity": record.severity.value,
                    "reason_code": record.reason_code.value,
                    "action": record.action.value,
                    "sequence_number": record.sequence_number,
                },
            )
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_get_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle safety.status.get query (read-only snapshot)."""
        payload = query_envelope.payload
        session_id = payload.get("session_id")

        if not session_id:
            return create_error_response(query_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id")

        record = self.get_gate_status(str(session_id))
        if record is None:
            return create_error_response(query_envelope, self.name, "ERR_SESSION_NOT_FOUND", f"No gate status for session {session_id!r}")

        return create_response(
            query_envelope,
            self.name,
            payload={
                "session_id": session_id,
                "decision": record.decision.value,
                "severity": record.severity.value,
                "reason_code": record.reason_code.value,
                "action": record.action.value,
                "sequence_number": record.sequence_number,
                "evaluated_at_utc": record.evaluated_at_utc,
            },
        )
