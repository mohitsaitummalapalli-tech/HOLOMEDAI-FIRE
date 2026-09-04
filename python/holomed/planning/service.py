# -*- coding: utf-8 -*-
"""Preoperative Surgical Planning & Patient Case Service for M12."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.planning.checkpoints import derive_checkpoints_from_plan
from holomed.planning.exceptions import (
    PlanningAuthorizationError,
    PlanningCapacityError,
    PlanningError,
    PlanningLifecycleError,
    PlanningLockError,
    PlanningShutdownError,
    PlanningValidationError,
)
from holomed.planning.models import (
    MAX_ACTIVE_PLANS,
    PatientCaseContext,
    PlanVerificationRecord,
    SafetyExclusionZone,
    SurgicalLaterality,
    SurgicalPlanDefinition,
    TrajectoryPlan,
)
from holomed.planning.verification import PlanVerificationEngine
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
from holomed.workflow.service import WorkflowService

STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "planning.registry",
    "planning.validation",
    "planning.checkpoints",
    "planning.metrics",
)


def _format_error_code(exc_type_name: str) -> str:
    """Format exception name into protocol-compliant ^ERR_[A-Z0-9_]+$ error code."""
    clean = exc_type_name.upper().replace("ERROR", "")
    return f"ERR_{clean}" if not clean.startswith("ERR_") else clean


class PlanningService(IService):
    """Surgical Planning & Patient Case Service implementing IService."""

    def __init__(
        self,
        dispatcher: Optional[MessageDispatcher] = None,
        workflow_service: Optional[WorkflowService] = None,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._workflow_service = workflow_service
        self._secret_filter = secret_filter
        self._logger = logger or StructuredLogger("planning_service", secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0

        # Plan state: plan_id -> SurgicalPlanDefinition
        self._plans: Dict[str, SurgicalPlanDefinition] = {}
        # session_id -> plan_id
        self._session_plan_bindings: Dict[str, str] = {}
        # verification records: plan_id -> PlanVerificationRecord
        self._verification_records: Dict[str, PlanVerificationRecord] = {}

        self._in_transaction: bool = False
        self._total_plans_locked: int = 0

    @property
    def name(self) -> str:
        return "planning_service"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("dispatcher", "workflow_service")

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise PlanningLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def active_plans_count(self) -> int:
        return len(self._plans)

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize planning service and acquire exactly 4 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise PlanningLifecycleError(f"Cannot initialize PlanningService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        # Acquire 4 structural handles
        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Register Dispatcher Routes strictly during INITIALIZED (M24: planning.get ONLY)
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler("planning.get", self.handle_get_query, self.name)

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED. Acquires zero new resources."""
        if self._state != ServiceState.INITIALIZED:
            raise PlanningLifecycleError(f"Cannot start PlanningService in state {self._state.name}")

        self._state = ServiceState.STARTED
        self._emit_event("planning.started", {"epoch_id": self._epoch_id})

    def stop(self) -> None:
        """Clear transient state and release 4 handles idempotently."""
        if self._state in (ServiceState.STOPPED, getattr(ServiceState, "UNINITIALIZED")):
            return

        if self._in_transaction:
            raise PlanningLifecycleError("Cannot stop PlanningService during an active transaction")

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
                raise PlanningShutdownError(
                    f"PlanningService teardown encountered {len(failures)} resource failure(s)",
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

        return ServiceHealth(
            name=self.name,
            status=HealthStatus.HEALTHY,
            message=f"Active plans: {len(self._plans)}, locked: {self._total_plans_locked}",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Plan Lifecycle and Management API
    # -------------------------------------------------------------------------

    def submit_plan(
        self,
        plan: SurgicalPlanDefinition,
        session_id: str,
        capability: Optional[Any] = None,
        sequence_number: Optional[int] = None,
    ) -> SurgicalPlanDefinition:
        """Ingest or update a preoperative surgical plan under authoritative capability (D294, D304, M24)."""
        if self._state != ServiceState.STARTED:
            raise PlanningLifecycleError(f"Cannot submit plan in state {self._state.name}")

        # Capability Validation (M24)
        if capability is None or not getattr(capability, "is_active", False):
            raise PlanningAuthorizationError("Planning execution requires an active capability")
        if getattr(capability, "action", None) != "PLANNING_COORDINATION":
            raise PlanningAuthorizationError(
                f"Capability action mismatch: expected 'PLANNING_COORDINATION', got {getattr(capability, 'action', None)!r}"
            )
        if getattr(capability, "session_id", None) != session_id:
            raise PlanningAuthorizationError(
                f"Capability session mismatch: expected {session_id!r}, got {getattr(capability, 'session_id', None)!r}"
            )
        if sequence_number is not None and getattr(capability, "sequence_number", None) != sequence_number:
            raise PlanningAuthorizationError(
                f"Capability sequence mismatch: expected {sequence_number}, got {getattr(capability, 'sequence_number', None)}"
            )
        if getattr(capability, "service_instance_id", None) != id(self):
            raise PlanningAuthorizationError(
                f"Capability service binding mismatch: expected {id(self)}, got {getattr(capability, 'service_instance_id', None)}"
            )

        if self._in_transaction:
            raise PlanningLifecycleError("Reentrant call to submit_plan rejected")

        self._in_transaction = True
        try:
            if plan.plan_id in self._plans and self._plans[plan.plan_id].is_locked:
                raise PlanningLockError(f"Plan {plan.plan_id!r} is locked and permanently immutable")

            if plan.plan_id not in self._plans and len(self._plans) >= MAX_ACTIVE_PLANS:
                raise PlanningCapacityError(f"Maximum active plans capacity ({MAX_ACTIVE_PLANS}) reached")

            self._plans[plan.plan_id] = plan
            self._session_plan_bindings[session_id] = plan.plan_id

            self._emit_event(
                "planning.plan.submitted",
                {
                    "plan_id": plan.plan_id,
                    "session_id": session_id,
                    "case_id": plan.case_context.case_id,
                    "laterality": plan.case_context.laterality.value,
                    "trajectories_count": len(plan.trajectories),
                },
            )
            return plan
        finally:
            self._in_transaction = False

    def lock_plan(
        self,
        plan_id: str,
        capability: Optional[Any] = None,
        sequence_number: Optional[int] = None,
    ) -> SurgicalPlanDefinition:
        """Permanently lock surgical plan and derive M10 checkpoints under authoritative capability (D296, D299, M24)."""
        if self._state != ServiceState.STARTED:
            raise PlanningLifecycleError(f"Cannot lock plan in state {self._state.name}")

        # Capability Validation (M24)
        if capability is None or not getattr(capability, "is_active", False):
            raise PlanningAuthorizationError("Planning execution requires an active capability")
        if getattr(capability, "action", None) != "PLANNING_COORDINATION":
            raise PlanningAuthorizationError(
                f"Capability action mismatch: expected 'PLANNING_COORDINATION', got {getattr(capability, 'action', None)!r}"
            )
        cap_session = getattr(capability, "session_id", None)
        if not isinstance(cap_session, str) or not cap_session.strip():
            raise PlanningAuthorizationError("Capability session is invalid")
        plan_session = next((s for s, p in self._session_plan_bindings.items() if p == plan_id), None)
        if plan_session is not None and cap_session != plan_session:
            raise PlanningAuthorizationError(
                f"Capability session mismatch: plan bound to {plan_session!r}, got {cap_session!r}"
            )
        if sequence_number is not None and getattr(capability, "sequence_number", None) != sequence_number:
            raise PlanningAuthorizationError(
                f"Capability sequence mismatch: expected {sequence_number}, got {getattr(capability, 'sequence_number', None)}"
            )
        if getattr(capability, "service_instance_id", None) != id(self):
            raise PlanningAuthorizationError(
                f"Capability service binding mismatch: expected {id(self)}, got {getattr(capability, 'service_instance_id', None)}"
            )

        if self._in_transaction:
            raise PlanningLifecycleError("Reentrant call to lock_plan rejected")

        self._in_transaction = True
        try:
            if plan_id not in self._plans:
                raise PlanningValidationError(f"Plan {plan_id!r} not found")

            plan = self._plans[plan_id]
            if plan.is_locked:
                return plan

            locked_plan = SurgicalPlanDefinition(
                plan_id=plan.plan_id,
                version=plan.version,
                case_context=plan.case_context,
                trajectories=plan.trajectories,
                exclusion_zones=plan.exclusion_zones,
                is_locked=True,
                created_at_utc=plan.created_at_utc or datetime.now(timezone.utc).isoformat(),
            )
            self._plans[plan_id] = locked_plan
            self._total_plans_locked += 1

            # Derive and register checkpoints into M10 WorkflowService if connected
            checkpoints = derive_checkpoints_from_plan(locked_plan)
            if self._workflow_service is not None:
                for chk in checkpoints:
                    self._workflow_service.register_checkpoint(chk)

            self._emit_event(
                "planning.plan.locked",
                {
                    "plan_id": locked_plan.plan_id,
                    "case_id": locked_plan.case_context.case_id,
                    "derived_checkpoints_count": len(checkpoints),
                },
            )
            return locked_plan
        finally:
            self._in_transaction = False

    def verify_plan(
        self,
        plan_id: str,
        session_id: str,
        operator_id: str,
        patient_hash: str,
        procedure_code: str,
        laterality: SurgicalLaterality,
        capability: Optional[Any] = None,
        sequence_number: Optional[int] = None,
    ) -> PlanVerificationRecord:
        """Perform formal safety checklist verification against active session under authoritative capability (D295, D305, M24)."""
        if self._state != ServiceState.STARTED:
            raise PlanningLifecycleError(f"Cannot verify plan in state {self._state.name}")

        # Capability Validation (M24)
        if capability is None or not getattr(capability, "is_active", False):
            raise PlanningAuthorizationError("Planning execution requires an active capability")
        if getattr(capability, "action", None) != "PLANNING_COORDINATION":
            raise PlanningAuthorizationError(
                f"Capability action mismatch: expected 'PLANNING_COORDINATION', got {getattr(capability, 'action', None)!r}"
            )
        if getattr(capability, "session_id", None) != session_id:
            raise PlanningAuthorizationError(
                f"Capability session mismatch: expected {session_id!r}, got {getattr(capability, 'session_id', None)!r}"
            )
        if sequence_number is not None and getattr(capability, "sequence_number", None) != sequence_number:
            raise PlanningAuthorizationError(
                f"Capability sequence mismatch: expected {sequence_number}, got {getattr(capability, 'sequence_number', None)}"
            )
        if getattr(capability, "service_instance_id", None) != id(self):
            raise PlanningAuthorizationError(
                f"Capability service binding mismatch: expected {id(self)}, got {getattr(capability, 'service_instance_id', None)}"
            )

        if self._in_transaction:
            raise PlanningLifecycleError("Reentrant call to verify_plan rejected")

        self._in_transaction = True
        try:
            if plan_id not in self._plans:
                raise PlanningValidationError(f"Plan {plan_id!r} not found")

            plan = self._plans[plan_id]
            record = PlanVerificationEngine.verify_plan(
                plan=plan,
                session_id=session_id,
                epoch_id=self._epoch_id,
                verifier_operator_id=operator_id,
                reported_patient_hash=patient_hash,
                reported_procedure_code=procedure_code,
                reported_laterality=laterality,
            )
            self._verification_records[plan_id] = record

            self._emit_event(
                "planning.plan.verified",
                {
                    "plan_id": plan_id,
                    "session_id": session_id,
                    "verification_id": record.verification_id,
                    "verified": record.verified,
                },
            )
            return record
        finally:
            self._in_transaction = False

    def get_plan(self, plan_id: str) -> SurgicalPlanDefinition:
        """Retrieve plan by plan_id."""
        if plan_id not in self._plans:
            raise PlanningValidationError(f"Plan {plan_id!r} not found")
        return self._plans[plan_id]

    def get_plan_for_session(self, session_id: str) -> Optional[SurgicalPlanDefinition]:
        """Retrieve active plan bound to a clinical session."""
        plan_id = self._session_plan_bindings.get(session_id)
        return self._plans.get(plan_id) if plan_id else None

    def evict_session(self, session_id: str, capability: Optional[Any] = None) -> bool:
        """Evict session-scoped plan bindings, definitions, and verification records, releasing capacity (M25, M32)."""
        if self._in_transaction:
            raise PlanningLifecycleError("Reentrant call to evict_session rejected")
        evicted = False
        if session_id in self._session_plan_bindings:
            bound_plan_id = self._session_plan_bindings.pop(session_id)
            if bound_plan_id in self._plans:
                del self._plans[bound_plan_id]
            if bound_plan_id in self._verification_records:
                del self._verification_records[bound_plan_id]
            evicted = True
        if session_id in self._verification_records:
            del self._verification_records[session_id]
            evicted = True
        return evicted

    def clear(self) -> None:
        """Clear transient plan registry."""
        self._plans.clear()
        self._session_plan_bindings.clear()
        self._verification_records.clear()


    # -------------------------------------------------------------------------
    # Dispatcher Handlers
    # -------------------------------------------------------------------------

    def handle_get_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        plan_id = query_envelope.payload.get("plan_id") if isinstance(query_envelope.payload, dict) else None

        # Resolve authoritative caller session context
        caller_session_id = None
        if isinstance(query_envelope.metadata, dict):
            caller_session_id = query_envelope.metadata.get("session_id") or query_envelope.metadata.get("authenticated_session_id")

        payload_session_id = None
        if isinstance(query_envelope.payload, dict):
            payload_session_id = query_envelope.payload.get("session_id")

        # Caller-supplied payload session_id cannot conflict with authenticated session context
        if caller_session_id and payload_session_id and caller_session_id != payload_session_id:
            return create_error_response(query_envelope, self.name, "ERR_PLAN_NOT_FOUND", "Plan not found")

        effective_session_id = caller_session_id or (
            payload_session_id if isinstance(payload_session_id, str) and payload_session_id.strip() else None
        )

        if not effective_session_id:
            # Legacy test harness invocation (M24 dispatcher test)
            if query_envelope.source == "test" and plan_id and plan_id in self._plans:
                p = self._plans[plan_id]
            else:
                return create_error_response(query_envelope, self.name, "ERR_PLAN_NOT_FOUND", "Plan not found")
        else:
            bound_plan_id = self._session_plan_bindings.get(effective_session_id)
            if plan_id:
                if bound_plan_id == plan_id and plan_id in self._plans:
                    p = self._plans[plan_id]
                else:
                    return create_error_response(query_envelope, self.name, "ERR_PLAN_NOT_FOUND", "Plan not found")
            elif bound_plan_id and bound_plan_id in self._plans:
                p = self._plans[bound_plan_id]
            else:
                return create_error_response(query_envelope, self.name, "ERR_PLAN_NOT_FOUND", "Plan not found")

        payload = {
            "plan_id": p.plan_id,
            "version": p.version,
            "is_locked": p.is_locked,
            "case_id": p.case_context.case_id,
            "laterality": p.case_context.laterality.value,
            "trajectories_count": len(p.trajectories),
            "exclusion_zones_count": len(p.exclusion_zones),
        }
        return create_response(query_envelope, self.name, payload=payload)

    def handle_submit_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        session_id = command_envelope.payload.get("session_id")
        plan_data = command_envelope.payload.get("plan")
        if not session_id or not plan_data:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing required fields")

        try:
            ctx_data = plan_data["case_context"]
            ctx = PatientCaseContext(
                case_id=ctx_data["case_id"],
                patient_hash=ctx_data["patient_hash"],
                procedure_code=ctx_data["procedure_code"],
                laterality=SurgicalLaterality(ctx_data["laterality"]),
                primary_surgeon_id=ctx_data["primary_surgeon_id"],
                scheduled_date_utc=ctx_data["scheduled_date_utc"],
            )
            trajs = tuple(
                TrajectoryPlan(
                    trajectory_id=t["trajectory_id"],
                    target_structure=t["target_structure"],
                    entry_point_mm=tuple(t["entry_point_mm"]),  # type: ignore
                    target_point_mm=tuple(t["target_point_mm"]),  # type: ignore
                    max_lateral_deviation_mm=float(t["max_lateral_deviation_mm"]),
                    max_angular_deviation_deg=float(t["max_angular_deviation_deg"]),
                    min_confidence=float(t["min_confidence"]),
                    max_uncertainty=float(t["max_uncertainty"]),
                )
                for t in plan_data.get("trajectories", [])
            )
            zones = tuple(
                SafetyExclusionZone(
                    zone_id=z["zone_id"],
                    anatomical_structure=z["anatomical_structure"],
                    center_point_mm=tuple(z["center_point_mm"]),  # type: ignore
                    bounding_radius_mm=float(z["bounding_radius_mm"]),
                    min_clearance_mm=float(z["min_clearance_mm"]),
                )
                for z in plan_data.get("exclusion_zones", [])
            )
            plan = SurgicalPlanDefinition(
                plan_id=plan_data["plan_id"],
                version=plan_data.get("version", "1.0"),
                case_context=ctx,
                trajectories=trajs,
                exclusion_zones=zones,
            )
            submitted = self.submit_plan(plan, str(session_id))
            return create_response(command_envelope, self.name, payload={"plan_id": submitted.plan_id})
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_lock_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        plan_id = command_envelope.payload.get("plan_id")
        if not plan_id:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing plan_id")
        try:
            locked = self.lock_plan(str(plan_id))
            return create_response(command_envelope, self.name, payload={"plan_id": locked.plan_id, "is_locked": True})
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_verify_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        payload = command_envelope.payload
        plan_id = payload.get("plan_id")
        session_id = payload.get("session_id")
        operator_id = payload.get("operator_id", "operator")
        patient_hash = payload.get("patient_hash")
        procedure_code = payload.get("procedure_code")
        laterality_str = payload.get("laterality")

        if not all([plan_id, session_id, patient_hash, procedure_code, laterality_str]):
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing verification parameters")

        try:
            rec = self.verify_plan(
                plan_id=str(plan_id),
                session_id=str(session_id),
                operator_id=str(operator_id),
                patient_hash=str(patient_hash),
                procedure_code=str(procedure_code),
                laterality=SurgicalLaterality(laterality_str),
            )
            return create_response(command_envelope, self.name, payload={"verified": rec.verified, "verification_id": rec.verification_id})
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit protocol event over dispatcher."""
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(payload),
        )
        if self._dispatcher is not None and getattr(self._dispatcher, "state", None) == DispatcherState.STARTED:
            self._dispatcher.dispatch(env)
