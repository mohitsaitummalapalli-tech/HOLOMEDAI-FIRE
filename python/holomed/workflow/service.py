# -*- coding: utf-8 -*-
"""Clinical Workflow & Safety Interlock Service for M10."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.persistence.service import PersistenceService
from holomed.platform.service import PlatformService
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
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceState
from holomed.tools.models import ToolSafetyClassification
from holomed.workflow.authorization import ToolAuthorizationGate
from holomed.workflow.checkpoints import AnatomicalCheckpointValidator
from holomed.workflow.confirmation import ConfirmationManager
from holomed.workflow.events import RecordingWorkflowEventSink
from holomed.workflow.exceptions import (
    WorkflowCapacityError,
    WorkflowConfirmationRequiredError,
    WorkflowEpochMismatchError,
    WorkflowLifecycleError,
    WorkflowResourceIntegrityError,
    WorkflowSafetyInterlockError,
    WorkflowSessionError,
    WorkflowShutdownError,
    WorkflowTransitionError,
    WorkflowValidationError,
)
from holomed.workflow.interlocks import SafetyInterlockEngine
from holomed.workflow.models import (
    MAX_ACTIVE_WORKFLOWS,
    PROCEDURE_ID_REGEX,
    AnatomicalCheckpoint,
    ConfirmationRequest,
    ConfirmationResponse,
    ProcedureDefinition,
    SafetyInterlock,
    WorkflowPhase,
    WorkflowStateSnapshot,
    WorkflowToolAuthorizationDecision,
    WorkflowToolAuthorizationStatus,
)
from holomed.workflow.procedure import (
    STANDARD_SURGICAL_GUIDANCE_V1,
    validate_procedure_definition,
)
from holomed.workflow.state_machine import WorkflowStateMachine

STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "workflow.state",
    "workflow.interlocks",
    "workflow.confirmations",
    "workflow.metrics",
)


def _format_error_code(exc_type_name: str) -> str:
    """Format exception name into protocol-compliant ^ERR_[A-Z0-9_]+$ error code."""
    clean = exc_type_name.upper().replace("ERROR", "")
    return f"ERR_{clean}" if not clean.startswith("ERR_") else clean


class WorkflowService(IService):
    """Clinical Workflow & Safety Interlock Service implementing IService."""

    def __init__(
        self,
        platform_service: Optional[PlatformService] = None,
        persistence_service: Optional[PersistenceService] = None,
        dispatcher: Optional[MessageDispatcher] = None,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._platform_service = platform_service
        self._persistence_service = persistence_service
        self._dispatcher = dispatcher
        self._secret_filter = secret_filter
        self._logger = logger or StructuredLogger("workflow_service", secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0

        # Workflow State
        self._workflows: dict[str, WorkflowStateMachine] = {}
        self._procedures: dict[str, ProcedureDefinition] = {}
        self._confirmations: dict[str, ConfirmationManager] = {}
        self._interlock_engine: SafetyInterlockEngine = SafetyInterlockEngine()
        self._checkpoint_validator: AnatomicalCheckpointValidator = AnatomicalCheckpointValidator()
        self._event_sink: RecordingWorkflowEventSink = RecordingWorkflowEventSink()

        # Reentrancy Guard (synchronous, not a threading lock)
        self._in_transaction: bool = False
        self._total_transitions: int = 0

    @property
    def name(self) -> str:
        return "workflow_service"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("platform_service", "persistence_service")

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise WorkflowLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def event_sink(self) -> RecordingWorkflowEventSink:
        return self._event_sink

    @property
    def interlock_engine(self) -> SafetyInterlockEngine:
        return self._interlock_engine

    @property
    def checkpoint_validator(self) -> AnatomicalCheckpointValidator:
        return self._checkpoint_validator

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize workflow service and acquire exactly 4 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise WorkflowLifecycleError(f"Cannot initialize WorkflowService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        # Acquire 4 structural handles
        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Register default procedure
        self._procedures[STANDARD_SURGICAL_GUIDANCE_V1.procedure_id] = STANDARD_SURGICAL_GUIDANCE_V1

        # Register Dispatcher Routes strictly during INITIALIZED
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler(
                "workflow.status",
                self.handle_status_query,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "workflow.start",
                self.handle_start_command,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "workflow.transition",
                self.handle_transition_command,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "workflow.confirm",
                self.handle_confirm_command,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "workflow.abort",
                self.handle_abort_command,
                self.name,
            )

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED. Acquires zero new resources."""
        if self._state != ServiceState.INITIALIZED:
            raise WorkflowLifecycleError(f"Cannot start WorkflowService in state {self._state.name}")

        self._state = ServiceState.STARTED
        self._emit_event("workflow.started", {"epoch_id": self._epoch_id})

    def stop(self) -> None:
        """Stop workflows, clear transient state, and release 4 handles idempotently."""
        if self._state in (ServiceState.STOPPED, ServiceServiceState := getattr(ServiceState, "UNINITIALIZED")):
            return

        if self._in_transaction:
            raise WorkflowLifecycleError("Cannot stop WorkflowService during an active transaction")

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
                raise WorkflowShutdownError(
                    f"WorkflowService teardown encountered {len(failures)} resource failure(s)",
                    failures,
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

        if self._interlock_engine.has_critical_interlock():
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.DEGRADED,
                message="Critical safety interlock currently tripped",
                timestamp_utc=now_utc,
            )

        return ServiceHealth(
            name=self.name,
            status=HealthStatus.HEALTHY,
            message=f"Active workflows: {len(self._workflows)}, transitions: {self._total_transitions}",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Core Clinical Workflow API
    # -------------------------------------------------------------------------

    def start_workflow(
        self,
        session_id: str,
        procedure: Optional[ProcedureDefinition] = None,
    ) -> WorkflowStateSnapshot:
        """Initialize a new workflow instance for a clinical session."""
        if self._state != ServiceState.STARTED:
            raise WorkflowLifecycleError(f"Cannot start workflow in state {self._state.name}")
        if self._in_transaction:
            raise WorkflowLifecycleError("Reentrant call to start_workflow rejected")

        if not isinstance(session_id, str) or not PROCEDURE_ID_REGEX.match(session_id):
            raise WorkflowValidationError(f"Invalid session_id syntax: {session_id!r}")

        self._in_transaction = True
        try:
            if session_id in self._workflows:
                sm = self._workflows[session_id]
                if not sm.is_terminal:
                    return sm.snapshot

            if len(self._workflows) >= MAX_ACTIVE_WORKFLOWS:
                raise WorkflowCapacityError(
                    f"Active workflow capacity limit ({MAX_ACTIVE_WORKFLOWS}) exceeded"
                )

            proc = procedure or STANDARD_SURGICAL_GUIDANCE_V1
            validate_procedure_definition(proc)

            workflow_id = f"wf_{session_id}"
            sm = WorkflowStateMachine(
                workflow_id=workflow_id,
                session_id=session_id,
                epoch_id=self._epoch_id,
                procedure=proc,
                initial_phase=WorkflowPhase.PATIENT_CONTEXT,
            )
            self._workflows[session_id] = sm
            self._confirmations[session_id] = ConfirmationManager(session_id, self._epoch_id)

            self._emit_event(
                "workflow.phase.entered",
                {
                    "session_id": session_id,
                    "workflow_id": workflow_id,
                    "phase": WorkflowPhase.PATIENT_CONTEXT.value,
                },
            )
            return sm.snapshot
        finally:
            self._in_transaction = False

    def transition_phase(
        self,
        session_id: str,
        target_phase: WorkflowPhase,
        sequence_number: int,
    ) -> WorkflowStateSnapshot:
        """Evaluate prerequisites, confirmations, interlocks, and transition phase."""
        if self._state != ServiceState.STARTED:
            raise WorkflowLifecycleError(f"Cannot transition phase in state {self._state.name}")
        if self._in_transaction:
            raise WorkflowLifecycleError("Reentrant call to transition_phase rejected")

        self._in_transaction = True
        try:
            if session_id not in self._workflows:
                raise WorkflowSessionError(f"Session {session_id!r} has no active workflow")

            sm = self._workflows[session_id]
            cm = self._confirmations[session_id]

            # 1. Critical Interlock Check -> Abort
            if self._interlock_engine.has_critical_interlock():
                self._emit_event("workflow.phase.blocked", {"session_id": session_id, "reason": "CRITICAL_INTERLOCK"})
                sm.abort(sequence_number, reason="Critical safety interlock tripped")
                raise WorkflowSafetyInterlockError("Critical safety interlock tripped; workflow aborted")

            # 2. Blocking Interlock Check
            if self._interlock_engine.has_blocking_interlock() and target_phase not in (
                WorkflowPhase.ABORTED,
                WorkflowPhase.RECOVERY_REQUIRED,
            ):
                self._emit_event("workflow.phase.blocked", {"session_id": session_id, "reason": "BLOCKING_INTERLOCK"})
                raise WorkflowSafetyInterlockError("Active safety interlock blocks phase transition")

            # 3. Human Confirmation Gate Check
            if target_phase in sm._procedure.required_confirmations:
                if not cm.is_phase_confirmed(target_phase):
                    self._emit_event(
                        "workflow.confirmation.requested",
                        {"session_id": session_id, "target_phase": target_phase.value},
                    )
                    raise WorkflowConfirmationRequiredError(
                        f"Phase {target_phase.name} requires explicit human confirmation"
                    )

            # 4. State Machine Transition
            snapshot = sm.transition_to(target_phase, sequence_number)
            self._total_transitions += 1

            self._emit_event(
                "workflow.phase.entered",
                {
                    "session_id": session_id,
                    "workflow_id": sm.workflow_id,
                    "phase": target_phase.value,
                    "sequence_number": sequence_number,
                },
            )
            return snapshot
        finally:
            self._in_transaction = False

    def request_confirmation(
        self,
        session_id: str,
        target_phase: WorkflowPhase,
        rationale: str,
        sequence_number: int,
    ) -> ConfirmationRequest:
        """Create a pending confirmation request for a clinical phase."""
        if session_id not in self._workflows:
            raise WorkflowSessionError(f"Session {session_id!r} has no active workflow")
        cm = self._confirmations[session_id]
        sm = self._workflows[session_id]

        req = cm.request_confirmation(
            workflow_id=sm.workflow_id,
            target_phase=target_phase,
            rationale=rationale,
            sequence_number=sequence_number,
        )
        self._emit_event(
            "workflow.confirmation.requested",
            {
                "session_id": session_id,
                "confirmation_id": req.confirmation_id,
                "target_phase": target_phase.value,
            },
        )
        return req

    def confirm(self, response: ConfirmationResponse) -> ConfirmationRequest:
        """Resolve a pending confirmation with an operator response."""
        session_id = response.session_id
        if session_id not in self._confirmations:
            raise WorkflowSessionError(f"Session {session_id!r} has no active confirmation manager")

        cm = self._confirmations[session_id]
        req = cm.resolve_confirmation(response)

        self._emit_event(
            "workflow.confirmation.resolved",
            {
                "session_id": session_id,
                "confirmation_id": response.confirmation_id,
                "approved": response.approved,
                "operator_id": response.operator_id,
            },
        )
        return req

    def abort_workflow(
        self,
        session_id: str,
        sequence_number: int,
        reason: str = "",
    ) -> WorkflowStateSnapshot:
        """Safely transition active workflow to ABORTED."""
        if session_id not in self._workflows:
            raise WorkflowSessionError(f"Session {session_id!r} not found")
        sm = self._workflows[session_id]
        snapshot = sm.abort(sequence_number, reason)
        self._emit_event("workflow.aborted", {"session_id": session_id, "reason": reason})
        return snapshot

    def authorize_tool(
        self,
        session_id: str,
        tool_id: str,
        safety_classification: ToolSafetyClassification,
        is_surgical_actuation: bool = False,
    ) -> WorkflowToolAuthorizationDecision:
        """Evaluate tool invocation authorization under current phase and interlocks."""
        if session_id not in self._workflows:
            raise WorkflowSessionError(f"Session {session_id!r} not found")

        sm = self._workflows[session_id]
        decision = ToolAuthorizationGate.evaluate_authorization(
            tool_id=tool_id,
            safety_classification=safety_classification,
            current_phase=sm.current_phase,
            has_blocking_interlocks=self._interlock_engine.has_blocking_interlock(),
            epoch_id=self._epoch_id,
            session_id=session_id,
            is_surgical_actuation_intent=is_surgical_actuation,
        )

        if decision.status != WorkflowToolAuthorizationStatus.PERMITTED:
            self._emit_event(
                "workflow.tool.blocked",
                {"session_id": session_id, "tool_id": tool_id, "status": decision.status.value},
            )
        return decision

    def register_checkpoint(self, checkpoint: AnatomicalCheckpoint) -> None:
        """Register an anatomical checkpoint for spatial verification."""
        self._checkpoint_validator.register_checkpoint(checkpoint)

    def evaluate_checkpoint(
        self,
        checkpoint_id: str,
        session_id: str,
        measured_confidence: float,
        measured_uncertainty: float,
        measured_deviation_mm: float,
    ) -> SafetyInterlock:
        """Evaluate anatomical checkpoint and register resulting SafetyInterlock."""
        interlock = self._checkpoint_validator.evaluate_checkpoint(
            checkpoint_id=checkpoint_id,
            measured_confidence=measured_confidence,
            measured_uncertainty=measured_uncertainty,
            measured_deviation_mm=measured_deviation_mm,
            epoch_id=self._epoch_id,
            session_id=session_id,
        )
        self._interlock_engine.register_interlock(interlock)
        if not interlock.status:
            self._emit_event(
                "workflow.interlock.tripped",
                {
                    "session_id": session_id,
                    "interlock_id": interlock.interlock_id,
                    "severity": interlock.severity.value,
                    "reason": interlock.reason,
                },
            )
        return interlock

    def get_workflow_state(self, session_id: str) -> WorkflowStateSnapshot:
        """Retrieve active workflow state snapshot."""
        if session_id not in self._workflows:
            raise WorkflowSessionError(f"Session {session_id!r} not found")
        return self._workflows[session_id].snapshot

    def clear(self) -> None:
        """Clear transient in-memory state."""
        self._workflows.clear()
        self._confirmations.clear()
        self._interlock_engine.clear()
        self._checkpoint_validator.clear()
        self._event_sink.clear()

    # -------------------------------------------------------------------------
    # Dispatcher Handlers
    # -------------------------------------------------------------------------

    def handle_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        session_id = query_envelope.payload.get("session_id")
        h = self.health()
        if session_id and session_id in self._workflows:
            sm = self._workflows[session_id]
            snap = sm.snapshot
            payload = {
                "service_name": self.name,
                "state": self._state.name,
                "workflow_id": snap.workflow_id,
                "session_id": snap.session_id,
                "current_phase": snap.current_phase.value,
                "transition_count": snap.transition_count,
                "is_terminal": snap.is_terminal,
                "health_status": h.status.name,
                "active_interlocks_count": self._interlock_engine.interlock_count,
            }
        else:
            payload = {
                "service_name": self.name,
                "state": self._state.name,
                "health_status": h.status.name,
                "active_workflows_count": len(self._workflows),
            }
        return create_response(query_envelope, self.name, payload=payload)

    def handle_start_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        session_id = command_envelope.payload.get("session_id")
        if not session_id:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id")
        try:
            snap = self.start_workflow(str(session_id))
            payload = {
                "workflow_id": snap.workflow_id,
                "session_id": snap.session_id,
                "current_phase": snap.current_phase.value,
            }
            return create_response(command_envelope, self.name, payload=payload)
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_transition_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        session_id = command_envelope.payload.get("session_id")
        target_phase_str = command_envelope.payload.get("target_phase")
        seq = command_envelope.payload.get("sequence_number")
        if not session_id or not target_phase_str or seq is None:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing required parameters")
        try:
            target_phase = WorkflowPhase(target_phase_str)
            snap = self.transition_phase(str(session_id), target_phase, int(seq))
            return create_response(command_envelope, self.name, payload={"current_phase": snap.current_phase.value})
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_confirm_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        session_id = command_envelope.payload.get("session_id")
        cid = command_envelope.payload.get("confirmation_id")
        approved = command_envelope.payload.get("approved")
        operator_id = command_envelope.payload.get("operator_id", "operator")
        seq = command_envelope.payload.get("sequence_number", 0)
        if not session_id or not cid or approved is None:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing parameters")
        try:
            now_utc = datetime.now(timezone.utc).isoformat()
            resp = ConfirmationResponse(
                confirmation_id=str(cid),
                session_id=str(session_id),
                epoch_id=self._epoch_id,
                approved=bool(approved),
                operator_id=str(operator_id),
                timestamp_utc=now_utc,
                sequence_number=int(seq),
            )
            req = self.confirm(resp)
            return create_response(command_envelope, self.name, payload={"resolved_confirmation_id": req.confirmation_id})
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_abort_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        session_id = command_envelope.payload.get("session_id")
        seq = command_envelope.payload.get("sequence_number", 999999)
        reason = command_envelope.payload.get("reason", "Operator abort command")
        if not session_id:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id")
        try:
            snap = self.abort_workflow(str(session_id), int(seq), str(reason))
            return create_response(command_envelope, self.name, payload={"current_phase": snap.current_phase.value})
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit protocol event over dispatcher and record in event sink."""
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(payload),
        )
        try:
            self._event_sink.record_event(env)
        except WorkflowCapacityError:
            pass

        if self._dispatcher is not None and getattr(self._dispatcher, "state", None) == DispatcherState.STARTED:
            self._dispatcher.dispatch(env)
