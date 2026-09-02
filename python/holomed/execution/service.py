# -*- coding: utf-8 -*-
"""Navigation Safety Gate Execution Enforcement & Coordination Gateway Implementing IService for M19."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple
import uuid

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.execution.constants import (
    MAX_ACTIVE_EXECUTION_SESSIONS,
    SERVICE_NAME,
    STRUCTURAL_RESOURCE_IDS,
)
from holomed.execution.exceptions import (
    ExecutionCapacityError,
    ExecutionLifecycleError,
    ExecutionShutdownError,
    ExecutionValidationError,
)
from holomed.execution.models import (
    ExecutionStatus,
    NavigationExecutionRequest,
    NavigationExecutionResult,
    NavigationExecutionStatusRecord,
    RecoveryReorientationExecutionRequest,
    RecoveryReorientationExecutionResult,
    ToolExecutionRequest,
    ToolExecutionResult,
    TrajectoryBindingExecutionRequest,
    TrajectoryBindingExecutionResult,
    WorkflowResumptionExecutionRequest,
    WorkflowResumptionExecutionResult,
)
from holomed.execution._capability import _create_execution_capability
from holomed.navigation.models import (
    TrackedInstrumentPose,
    TrajectoryDeviationRecord,
)
from holomed.navigation.service import NavigationService
from holomed.persistence.service import PersistenceService
from holomed.protocol.builders import (
    create_error_response,
    create_event,
    create_response,
)
from holomed.protocol.models import MessageEnvelope
from holomed.recovery.models import RecoveryStatusRecord
from holomed.recovery.service import RecoveryService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceState
from holomed.safety_gate.models import (
    GateDecision,
    GateReasonCode,
    GateRequest,
    SafetyGateAction,
)
from holomed.safety_gate.service import SafetyGateService
from holomed.tools.models import (
    ToolInvocationContext,
    ToolSafetyClassification,
)
from holomed.tools.service import ToolService
from holomed.workflow.models import (
    WorkflowResumptionRequest,
    WorkflowToolAuthorizationStatus,
)
from holomed.workflow.service import WorkflowService


def _format_error_code(exc_type_name: str) -> str:
    """Format exception class name into protocol-compliant ^ERR_[A-Z0-9_]+$ error code."""
    clean = exc_type_name.upper().replace("ERROR", "")
    return f"ERR_{clean}" if not clean.startswith("ERR_") else clean


class ClinicalExecutionGatewayService(IService):
    """Canonical Universal Clinical Execution Gateway & Coordination Service for M21."""

    def __init__(
        self,
        dispatcher: Optional[MessageDispatcher] = None,
        safety_gate_service: Optional[SafetyGateService] = None,
        workflow_service: Optional[WorkflowService] = None,
        navigation_service: Optional[NavigationService] = None,
        persistence_service: Optional[PersistenceService] = None,
        recovery_service: Optional[RecoveryService] = None,
        tool_service: Optional[ToolService] = None,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._safety_gate_service = safety_gate_service
        self._workflow_service = workflow_service
        self._navigation_service = navigation_service
        self._persistence_service = persistence_service
        self._recovery_service = recovery_service
        self._tool_service = tool_service
        self._secret_filter = secret_filter
        self._logger = logger or StructuredLogger(SERVICE_NAME, secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0

        # Session tracking cache
        self._latest_results: Dict[str, NavigationExecutionResult] = {}
        # session_id -> (last_execution_status, last_reason_code) for persistence deduplication
        self._persisted_states: Dict[str, Tuple[ExecutionStatus, GateReasonCode]] = {}

        # Single-threaded synchronous reentrancy guard
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
            raise ExecutionLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def active_sessions_count(self) -> int:
        return len(self._latest_results)

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize execution gateway and acquire exactly 4 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise ExecutionLifecycleError(f"Cannot initialize NavigationExecutionService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Register Dispatcher Routes (Strictly 6 execution routes in M21)
        if self._dispatcher is not None:
            self._dispatcher.register_command_handler(
                "execution.navigation.execute", self.handle_execute_command, self.name
            )
            self._dispatcher.register_command_handler(
                "execution.recovery.execute", self.handle_recovery_execute_command, self.name
            )
            self._dispatcher.register_command_handler(
                "execution.trajectory.bind", self.handle_trajectory_bind_command, self.name
            )
            self._dispatcher.register_command_handler(
                "execution.tool.invoke", self.handle_tool_invoke_command, self.name
            )
            self._dispatcher.register_command_handler(
                "execution.workflow.resume", self.handle_workflow_resume_command, self.name
            )
            self._dispatcher.register_query_handler(
                "execution.status.get", self.handle_get_status_query, self.name
            )

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED."""
        if self._state != ServiceState.INITIALIZED:
            raise ExecutionLifecycleError(f"Cannot start NavigationExecutionService in state {self._state.name}")

        self._state = ServiceState.STARTED
        self._emit_event("execution.started", {"epoch_id": self._epoch_id, "state": ServiceState.STARTED.name})

    def stop(self) -> None:
        """Stop execution gateway, clear transient states, and release 4 handles idempotently."""
        if self._state in (ServiceState.STOPPED, getattr(ServiceState, "UNINITIALIZED")):
            return

        if self._in_transaction:
            raise ExecutionLifecycleError("Cannot stop NavigationExecutionService during an active transaction")

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
                raise ExecutionShutdownError(
                    f"NavigationExecutionService teardown encountered {len(failures)} resource failure(s)",
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
            message=f"Active execution sessions: {len(self._latest_results)}",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Core Coordinated Navigation Execution (Dual-Gated)
    # -------------------------------------------------------------------------

    def execute_navigation(self, request: NavigationExecutionRequest) -> NavigationExecutionResult:
        """Execute a coordinated real-time navigation step under synchronous dual-gate ordering."""
        if self._state != ServiceState.STARTED:
            raise ExecutionLifecycleError(f"Cannot execute navigation in state {self._state.name}")
        if self._in_transaction:
            raise ExecutionLifecycleError("Reentrant call to execute_navigation rejected")

        self._in_transaction = True
        try:
            session_id = request.session_id
            if session_id not in self._latest_results and len(self._latest_results) >= MAX_ACTIVE_EXECUTION_SESSIONS:
                raise ExecutionCapacityError(f"Max active execution sessions ({MAX_ACTIVE_EXECUTION_SESSIONS}) exceeded")

            # 1. Identity & Binding Validations
            if request.action != SafetyGateAction.TOOL_NAVIGATION:
                raise ExecutionValidationError(
                    f"Action mismatch: execute_navigation requires TOOL_NAVIGATION, got {request.action.value}"
                )
            if request.pose.session_id != session_id:
                raise ExecutionValidationError(
                    f"Pose session_id {request.pose.session_id!r} does not match request {session_id!r}"
                )
            if request.pose.instrument_id != request.instrument_id:
                raise ExecutionValidationError(
                    f"Pose instrument_id {request.pose.instrument_id!r} does not match request {request.instrument_id!r}"
                )
            if request.pose.sequence_number != request.sequence_number:
                raise ExecutionValidationError(
                    f"Pose sequence_number {request.pose.sequence_number} does not match request {request.sequence_number}"
                )

            # 2. Step 1: Inline M18 Safety Gate Evaluation (Zero Cached Decisions)
            gate_decision = GateDecision.DENIED_INTERLOCKED
            gate_reason = GateReasonCode.NONE
            if self._safety_gate_service is not None:
                gate_req = GateRequest(
                    session_id=session_id,
                    action=SafetyGateAction.TOOL_NAVIGATION,
                    sequence_number=request.sequence_number,
                    now_utc=request.now_utc,
                    instrument_id=request.instrument_id,
                    target_trajectory_id=request.target_trajectory_id,
                )
                gate_rec = self._safety_gate_service.evaluate(gate_req)
                gate_decision = gate_rec.decision
                gate_reason = gate_rec.reason_code
            else:
                # Missing safety gate service fails closed
                gate_decision = GateDecision.DENIED_INTERLOCKED
                gate_reason = GateReasonCode.NONE

            # Short-circuit on M18 Denial: M10 and M14 MUST NOT be called!
            if gate_decision in (GateDecision.DENIED_INTERLOCKED, GateDecision.DENIED_CRITICAL):
                res = NavigationExecutionResult(
                    session_id=session_id,
                    execution_status=ExecutionStatus.BLOCKED_SAFETY_GATE,
                    gate_decision=gate_decision,
                    gate_reason_code=gate_reason,
                    action=request.action,
                    sequence_number=request.sequence_number,
                    instrument_id=request.instrument_id,
                    target_trajectory_id=request.target_trajectory_id,
                    executed_at_utc=request.now_utc,
                    error_message=f"Blocked by M18 safety gate: {gate_reason.value}",
                )
                self._record_execution_result(res)
                return res

            # 3. Step 2: M10 Workflow & Tool Authorization Gate
            wf_status: Optional[WorkflowToolAuthorizationStatus] = None
            if self._workflow_service is not None:
                if request.tool_safety_classification is not None:
                    wf_dec = self._workflow_service.authorize_tool(
                        session_id=session_id,
                        tool_id=request.instrument_id,
                        safety_classification=request.tool_safety_classification,
                    )
                    wf_status = wf_dec.status
                    if wf_status != WorkflowToolAuthorizationStatus.PERMITTED:
                        res = NavigationExecutionResult(
                            session_id=session_id,
                            execution_status=ExecutionStatus.BLOCKED_WORKFLOW,
                            gate_decision=gate_decision,
                            gate_reason_code=gate_reason,
                            action=request.action,
                            sequence_number=request.sequence_number,
                            instrument_id=request.instrument_id,
                            target_trajectory_id=request.target_trajectory_id,
                            executed_at_utc=request.now_utc,
                            workflow_status=wf_status,
                            error_message=f"Blocked by M10 workflow: {wf_status.value}",
                        )
                        self._record_execution_result(res)
                        return res
                else:
                    wf_state = self._workflow_service.get_workflow_state(session_id)
                    if wf_state is not None and wf_state.current_phase.value in ("ABORTED", "RECOVERY_REQUIRED"):
                        res = NavigationExecutionResult(
                            session_id=session_id,
                            execution_status=ExecutionStatus.BLOCKED_WORKFLOW,
                            gate_decision=gate_decision,
                            gate_reason_code=gate_reason,
                            action=request.action,
                            sequence_number=request.sequence_number,
                            instrument_id=request.instrument_id,
                            target_trajectory_id=request.target_trajectory_id,
                            executed_at_utc=request.now_utc,
                            error_message=f"Blocked by M10 workflow phase: {wf_state.current_phase.value}",
                        )
                        self._record_execution_result(res)
                        return res

            # 4. Step 3: M14 Navigation Pose Validation & Geometric Deviation Calculation
            if self._navigation_service is None:
                res = NavigationExecutionResult(
                    session_id=session_id,
                    execution_status=ExecutionStatus.FAILED_NAVIGATION_GEOMETRY,
                    gate_decision=gate_decision,
                    gate_reason_code=gate_reason,
                    action=request.action,
                    sequence_number=request.sequence_number,
                    instrument_id=request.instrument_id,
                    target_trajectory_id=request.target_trajectory_id,
                    executed_at_utc=request.now_utc,
                    error_message="NavigationService handle is unavailable",
                )
                self._record_execution_result(res)
                return res

            deviation_rec: Optional[TrajectoryDeviationRecord] = None
            cap_pose = _create_execution_capability(
                service_instance_id=id(self._navigation_service),
                session_id=session_id,
                action="TOOL_NAVIGATION",
                sequence_number=request.sequence_number,
            )
            cap_eval = _create_execution_capability(
                service_instance_id=id(self._navigation_service),
                session_id=session_id,
                action="TOOL_NAVIGATION",
                sequence_number=request.sequence_number,
            )
            try:
                try:
                    self._navigation_service.submit_pose(request.pose, capability=cap_pose)
                    deviation_rec = self._navigation_service.evaluate(
                        session_id=session_id,
                        instrument_id=request.instrument_id,
                        now_utc=request.now_utc,
                        capability=cap_eval,
                    )
                finally:
                    cap_pose.invalidate()
                    cap_eval.invalidate()
            except Exception as e:
                # Catch and preserve actual M14 failure; never mask with earlier gate permit
                raw_err = str(e)
                redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
                res = NavigationExecutionResult(
                    session_id=session_id,
                    execution_status=ExecutionStatus.FAILED_NAVIGATION_GEOMETRY,
                    gate_decision=gate_decision,
                    gate_reason_code=gate_reason,
                    action=request.action,
                    sequence_number=request.sequence_number,
                    instrument_id=request.instrument_id,
                    target_trajectory_id=request.target_trajectory_id,
                    executed_at_utc=request.now_utc,
                    workflow_status=wf_status,
                    error_message=redacted,
                )
                self._record_execution_result(res)
                return res

            # 5. Success Resolution (Clear vs. Caution)
            exec_status = (
                ExecutionStatus.EXECUTED_WITH_CAUTION
                if gate_decision == GateDecision.PERMITTED_WITH_CAUTION
                else ExecutionStatus.EXECUTED_CLEAR
            )

            res = NavigationExecutionResult(
                session_id=session_id,
                execution_status=exec_status,
                gate_decision=gate_decision,
                gate_reason_code=gate_reason,
                action=request.action,
                sequence_number=request.sequence_number,
                instrument_id=request.instrument_id,
                target_trajectory_id=request.target_trajectory_id,
                executed_at_utc=request.now_utc,
                workflow_status=wf_status,
                deviation_record=deviation_rec,
            )
            self._record_execution_result(res)
            return res
        finally:
            self._in_transaction = False

    # -------------------------------------------------------------------------
    # Trajectory Binding Execution (Strict Action Isolation & Dual-Gate)
    # -------------------------------------------------------------------------

    def execute_trajectory_binding(
        self, request: TrajectoryBindingExecutionRequest
    ) -> TrajectoryBindingExecutionResult:
        """Execute trajectory binding under TRAJECTORY_ALIGNMENT dual-gate authorization."""
        if self._state != ServiceState.STARTED:
            raise ExecutionLifecycleError(f"Cannot execute trajectory binding in state {self._state.name}")
        if self._in_transaction:
            raise ExecutionLifecycleError("Reentrant call to execute_trajectory_binding rejected")

        self._in_transaction = True
        try:
            session_id = request.session_id

            if request.action != SafetyGateAction.TRAJECTORY_ALIGNMENT:
                raise ExecutionValidationError(
                    f"Action mismatch: execute_trajectory_binding requires TRAJECTORY_ALIGNMENT, got {request.action.value}"
                )

            # 1. Step 1: Inline M18 Safety Gate Evaluation
            gate_decision = GateDecision.DENIED_INTERLOCKED
            gate_reason = GateReasonCode.NONE
            if self._safety_gate_service is not None:
                gate_req = GateRequest(
                    session_id=session_id,
                    action=SafetyGateAction.TRAJECTORY_ALIGNMENT,
                    sequence_number=request.sequence_number,
                    now_utc=request.now_utc,
                    target_trajectory_id=request.trajectory_id,
                )
                gate_rec = self._safety_gate_service.evaluate(gate_req)
                gate_decision = gate_rec.decision
                gate_reason = gate_rec.reason_code
            else:
                gate_decision = GateDecision.DENIED_INTERLOCKED
                gate_reason = GateReasonCode.NONE

            # Short-circuit on M18 Denial: M10 and M14 MUST NOT be called!
            if gate_decision in (GateDecision.DENIED_INTERLOCKED, GateDecision.DENIED_CRITICAL):
                res = TrajectoryBindingExecutionResult(
                    session_id=session_id,
                    trajectory_id=request.trajectory_id,
                    execution_status=ExecutionStatus.BLOCKED_SAFETY_GATE,
                    gate_decision=gate_decision,
                    gate_reason_code=gate_reason,
                    action=request.action,
                    sequence_number=request.sequence_number,
                    executed_at_utc=request.now_utc,
                    error_message=f"Blocked by M18 safety gate: {gate_reason.value}",
                )
                if self._persistence_service is not None:
                    self._persistence_service.record_audit(
                        {
                            "session_id": session_id,
                            "trajectory_id": request.trajectory_id,
                            "event": "trajectory_binding_blocked_safety_gate",
                            "decision": gate_decision.value,
                            "reason": gate_reason.value,
                            "sequence_number": request.sequence_number,
                        },
                        session_id=session_id,
                    )
                return res

            # 2. Step 2: M10 Workflow Authorization Gate
            wf_status: Optional[WorkflowToolAuthorizationStatus] = None
            if self._workflow_service is not None:
                wf_dec = self._workflow_service.authorize_tool(
                    session_id=session_id,
                    tool_id=request.trajectory_id,
                    safety_classification=request.tool_safety_classification,
                )
                wf_status = wf_dec.status
                if wf_status != WorkflowToolAuthorizationStatus.PERMITTED:
                    res = TrajectoryBindingExecutionResult(
                        session_id=session_id,
                        trajectory_id=request.trajectory_id,
                        execution_status=ExecutionStatus.BLOCKED_WORKFLOW,
                        gate_decision=gate_decision,
                        gate_reason_code=gate_reason,
                        action=request.action,
                        sequence_number=request.sequence_number,
                        executed_at_utc=request.now_utc,
                        workflow_status=wf_status,
                        error_message=f"Blocked by M10 workflow: {wf_status.value}",
                    )
                    if self._persistence_service is not None:
                        self._persistence_service.record_audit(
                            {
                                "session_id": session_id,
                                "trajectory_id": request.trajectory_id,
                                "event": "trajectory_binding_blocked_workflow",
                                "decision": gate_decision.value,
                                "reason": gate_reason.value,
                                "workflow_status": wf_status.value,
                                "sequence_number": request.sequence_number,
                            },
                            session_id=session_id,
                        )
                    return res

            # 3. Step 3: Invoke M14 Trajectory Binding
            if self._navigation_service is None:
                res = TrajectoryBindingExecutionResult(
                    session_id=session_id,
                    trajectory_id=request.trajectory_id,
                    execution_status=ExecutionStatus.FAILED_NAVIGATION_GEOMETRY,
                    gate_decision=gate_decision,
                    gate_reason_code=gate_reason,
                    action=request.action,
                    sequence_number=request.sequence_number,
                    executed_at_utc=request.now_utc,
                    workflow_status=wf_status,
                    error_message="NavigationService handle is unavailable",
                )
                return res

            try:
                self._navigation_service.bind_trajectory(
                    session_id=session_id,
                    trajectory_id=request.trajectory_id,
                    plan_trajectory=request.plan_trajectory,
                )
            except Exception as e:
                raw_err = str(e)
                redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
                res = TrajectoryBindingExecutionResult(
                    session_id=session_id,
                    trajectory_id=request.trajectory_id,
                    execution_status=ExecutionStatus.FAILED_NAVIGATION_GEOMETRY,
                    gate_decision=gate_decision,
                    gate_reason_code=gate_reason,
                    action=request.action,
                    sequence_number=request.sequence_number,
                    executed_at_utc=request.now_utc,
                    workflow_status=wf_status,
                    error_message=redacted,
                )
                if self._persistence_service is not None:
                    self._persistence_service.record_audit(
                        {
                            "session_id": session_id,
                            "trajectory_id": request.trajectory_id,
                            "event": "trajectory_binding_failed_geometry",
                            "error": redacted,
                            "sequence_number": request.sequence_number,
                        },
                        session_id=session_id,
                    )
                return res

            # 4. Step 4: Success Resolution
            exec_status = (
                ExecutionStatus.EXECUTED_WITH_CAUTION
                if gate_decision == GateDecision.PERMITTED_WITH_CAUTION
                else ExecutionStatus.EXECUTED_CLEAR
            )

            res = TrajectoryBindingExecutionResult(
                session_id=session_id,
                trajectory_id=request.trajectory_id,
                execution_status=exec_status,
                gate_decision=gate_decision,
                gate_reason_code=gate_reason,
                action=request.action,
                sequence_number=request.sequence_number,
                executed_at_utc=request.now_utc,
                workflow_status=wf_status,
            )

            if self._persistence_service is not None:
                self._persistence_service.record_audit(
                    {
                        "session_id": session_id,
                        "trajectory_id": request.trajectory_id,
                        "event": "trajectory_binding_executed",
                        "sequence_number": request.sequence_number,
                        "epoch_id": self._epoch_id,
                        "executed_at_utc": request.now_utc,
                    },
                    session_id=session_id,
                )

            self._emit_event(
                "execution.trajectory.bound",
                {
                    "session_id": session_id,
                    "trajectory_id": request.trajectory_id,
                    "sequence_number": request.sequence_number,
                    "epoch_id": self._epoch_id,
                },
            )
            return res
        finally:
            self._in_transaction = False

    # -------------------------------------------------------------------------
    # Spatial Recovery Reorientation Execution (Strict Action Isolation & Dual-Gate)
    # -------------------------------------------------------------------------

    def execute_recovery_reorientation(
        self, request: RecoveryReorientationExecutionRequest
    ) -> RecoveryReorientationExecutionResult:
        """Execute spatial recovery reorientation under RECOVERY_REORIENTATION dual-gate authorization."""
        if self._state != ServiceState.STARTED:
            raise ExecutionLifecycleError(f"Cannot execute recovery in state {self._state.name}")
        if self._in_transaction:
            raise ExecutionLifecycleError("Reentrant call to execute_recovery_reorientation rejected")

        self._in_transaction = True
        try:
            session_id = request.session_id

            if request.action != SafetyGateAction.RECOVERY_REORIENTATION:
                raise ExecutionValidationError(
                    f"Action mismatch: execute_recovery_reorientation requires RECOVERY_REORIENTATION, got {request.action.value}"
                )

            # 1. Step 1: Inline M18 Safety Gate Evaluation
            gate_decision = GateDecision.DENIED_INTERLOCKED
            gate_reason = GateReasonCode.NONE
            if self._safety_gate_service is not None:
                gate_req = GateRequest(
                    session_id=session_id,
                    action=SafetyGateAction.RECOVERY_REORIENTATION,
                    sequence_number=request.sequence_number,
                    now_utc=request.now_utc,
                )
                gate_rec = self._safety_gate_service.evaluate(gate_req)
                gate_decision = gate_rec.decision
                gate_reason = gate_rec.reason_code
            else:
                gate_decision = GateDecision.DENIED_INTERLOCKED
                gate_reason = GateReasonCode.NONE

            # Short-circuit on M18 Denial: M10 and M17 MUST NOT be called!
            if gate_decision in (GateDecision.DENIED_INTERLOCKED, GateDecision.DENIED_CRITICAL):
                res = RecoveryReorientationExecutionResult(
                    session_id=session_id,
                    execution_status=ExecutionStatus.BLOCKED_SAFETY_GATE,
                    gate_decision=gate_decision,
                    gate_reason_code=gate_reason,
                    action=request.action,
                    sequence_number=request.sequence_number,
                    executed_at_utc=request.now_utc,
                    error_message=f"Blocked by M18 safety gate: {gate_reason.value}",
                )
                if self._persistence_service is not None:
                    self._persistence_service.record_audit(
                        {
                            "session_id": session_id,
                            "event": "recovery_reorientation_blocked_safety_gate",
                            "decision": gate_decision.value,
                            "reason": gate_reason.value,
                            "sequence_number": request.sequence_number,
                        },
                        session_id=session_id,
                    )
                return res

            # 2. Step 2: M10 Workflow Authorization Gate
            wf_status: Optional[WorkflowToolAuthorizationStatus] = None
            if self._workflow_service is not None:
                wf_dec = self._workflow_service.authorize_tool(
                    session_id=session_id,
                    tool_id=f"recovery_{session_id}",
                    safety_classification=request.tool_safety_classification,
                )
                wf_status = wf_dec.status
                if wf_status != WorkflowToolAuthorizationStatus.PERMITTED:
                    res = RecoveryReorientationExecutionResult(
                        session_id=session_id,
                        execution_status=ExecutionStatus.BLOCKED_WORKFLOW,
                        gate_decision=gate_decision,
                        gate_reason_code=gate_reason,
                        action=request.action,
                        sequence_number=request.sequence_number,
                        executed_at_utc=request.now_utc,
                        workflow_status=wf_status,
                        error_message=f"Blocked by M10 workflow: {wf_status.value}",
                    )
                    if self._persistence_service is not None:
                        self._persistence_service.record_audit(
                            {
                                "session_id": session_id,
                                "event": "recovery_reorientation_blocked_workflow",
                                "decision": gate_decision.value,
                                "reason": gate_reason.value,
                                "workflow_status": wf_status.value,
                                "sequence_number": request.sequence_number,
                            },
                            session_id=session_id,
                        )
                    return res

            # 3. Step 3: Invoke M17 Public Recovery Operation
            if self._recovery_service is None:
                res = RecoveryReorientationExecutionResult(
                    session_id=session_id,
                    execution_status=ExecutionStatus.FAILED_NAVIGATION_GEOMETRY,
                    gate_decision=gate_decision,
                    gate_reason_code=gate_reason,
                    action=request.action,
                    sequence_number=request.sequence_number,
                    executed_at_utc=request.now_utc,
                    workflow_status=wf_status,
                    error_message="RecoveryService handle is unavailable",
                )
                return res

            rec_status: Optional[RecoveryStatusRecord] = None
            try:
                op = request.recovery_operation.upper()
                if op == "STAGE":
                    if request.plan_id is None or request.cloud is None:
                        raise ExecutionValidationError("STAGE operation requires plan_id and cloud")
                    self._recovery_service.stage_candidate(
                        session_id=session_id,
                        plan_id=request.plan_id,
                        cloud=request.cloud,
                        now_utc=request.now_utc,
                    )
                elif op == "VERIFY":
                    if request.authorization is None or request.checkpoint_plan_mm is None or request.checkpoint_measured_mm is None:
                        raise ExecutionValidationError("VERIFY operation requires authorization and checkpoint pairs")
                    self._recovery_service.verify_candidate(
                        session_id=session_id,
                        authorization=request.authorization,
                        checkpoint_plan_mm=request.checkpoint_plan_mm,
                        checkpoint_measured_mm=request.checkpoint_measured_mm,
                        now_utc=request.now_utc,
                    )
                elif op == "ACTIVATE":
                    rec_status = self._recovery_service.activate_recovery(
                        session_id=session_id,
                        plan_trajectory=request.plan_trajectory,
                        zones=request.zones,
                        landmarks=request.landmarks,
                        registration_error_mm=request.registration_error_mm,
                        static_margin_mm=request.static_margin_mm,
                        now_utc=request.now_utc,
                    )
                elif op == "RESET":
                    self._recovery_service.reset_recovery(session_id)
                elif op == "STATUS":
                    rec_status = self._recovery_service.get_recovery_status(session_id)
                else:
                    raise ExecutionValidationError(f"Unknown recovery_operation: {request.recovery_operation!r}")

                if rec_status is None:
                    rec_status = self._recovery_service.get_recovery_status(session_id)
            except Exception as e:
                raw_err = str(e)
                redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
                res = RecoveryReorientationExecutionResult(
                    session_id=session_id,
                    execution_status=ExecutionStatus.FAILED_NAVIGATION_GEOMETRY,
                    gate_decision=gate_decision,
                    gate_reason_code=gate_reason,
                    action=request.action,
                    sequence_number=request.sequence_number,
                    executed_at_utc=request.now_utc,
                    workflow_status=wf_status,
                    error_message=redacted,
                )
                if self._persistence_service is not None:
                    self._persistence_service.record_audit(
                        {
                            "session_id": session_id,
                            "event": "recovery_reorientation_failed",
                            "error": redacted,
                            "sequence_number": request.sequence_number,
                        },
                        session_id=session_id,
                    )
                return res

            # 4. Step 4: Success Resolution
            exec_status = (
                ExecutionStatus.EXECUTED_WITH_CAUTION
                if gate_decision == GateDecision.PERMITTED_WITH_CAUTION
                else ExecutionStatus.EXECUTED_CLEAR
            )

            res = RecoveryReorientationExecutionResult(
                session_id=session_id,
                execution_status=exec_status,
                gate_decision=gate_decision,
                gate_reason_code=gate_reason,
                action=request.action,
                sequence_number=request.sequence_number,
                executed_at_utc=request.now_utc,
                workflow_status=wf_status,
                recovery_status=rec_status,
            )

            if self._persistence_service is not None:
                self._persistence_service.record_audit(
                    {
                        "session_id": session_id,
                        "event": "recovery_reorientation_executed",
                        "operation": request.recovery_operation,
                        "sequence_number": request.sequence_number,
                        "epoch_id": self._epoch_id,
                        "executed_at_utc": request.now_utc,
                    },
                    session_id=session_id,
                )

            self._emit_event(
                "execution.recovery.reoriented",
                {
                    "session_id": session_id,
                    "operation": request.recovery_operation,
                    "sequence_number": request.sequence_number,
                    "epoch_id": self._epoch_id,
                },
            )
            return res
        finally:
            self._in_transaction = False

    # -------------------------------------------------------------------------
    # Clinical Tool Execution (Strict Action Isolation & Dual-Gate Coordination)
    # -------------------------------------------------------------------------

    def execute_tool(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        """Execute a clinical tool invocation under dual-gate evaluation and coordinator capability."""
        if self._state != ServiceState.STARTED:
            raise ExecutionLifecycleError(f"Cannot execute tool in state {self._state.name}")
        if self._in_transaction:
            raise ExecutionLifecycleError("Reentrant call to execute_tool rejected")

        self._in_transaction = True
        try:
            session_id = request.session_id

            # 1. Step 1: Inline M18 Safety Gate Evaluation
            gate_decision = GateDecision.DENIED_INTERLOCKED
            gate_reason = GateReasonCode.NONE
            if self._safety_gate_service is not None:
                gate_req = GateRequest(
                    session_id=session_id,
                    action=SafetyGateAction.TOOL_INVOCATION,
                    sequence_number=request.sequence_number,
                    now_utc=request.now_utc,
                    tool_id=request.tool_id,
                    safety_classification=request.safety_classification,
                )
                gate_rec = self._safety_gate_service.evaluate(gate_req)
                gate_decision = gate_rec.decision
                gate_reason = gate_rec.reason_code
            else:
                gate_decision = GateDecision.DENIED_INTERLOCKED
                gate_reason = GateReasonCode.NONE

            # Short-circuit on M18 Denial
            if gate_decision in (GateDecision.DENIED_INTERLOCKED, GateDecision.DENIED_CRITICAL):
                res = ToolExecutionResult(
                    session_id=session_id,
                    tool_id=request.tool_id,
                    execution_status=ExecutionStatus.BLOCKED_SAFETY_GATE,
                    gate_decision=gate_decision,
                    gate_reason_code=gate_reason,
                    action=request.action,
                    sequence_number=request.sequence_number,
                    executed_at_utc=request.now_utc,
                    error_message=f"Blocked by M18 safety gate: {gate_reason.value}",
                )
                self._record_tool_result(res)
                return res

            # 2. Step 2: M10 Workflow & Tool Authorization Gate
            wf_status: Optional[WorkflowToolAuthorizationStatus] = None
            if self._workflow_service is not None:
                wf_dec = self._workflow_service.authorize_tool(
                    session_id=session_id,
                    tool_id=request.tool_id,
                    safety_classification=request.safety_classification,
                )
                wf_status = wf_dec.status
                if wf_status != WorkflowToolAuthorizationStatus.PERMITTED:
                    res = ToolExecutionResult(
                        session_id=session_id,
                        tool_id=request.tool_id,
                        execution_status=ExecutionStatus.BLOCKED_WORKFLOW,
                        gate_decision=gate_decision,
                        gate_reason_code=gate_reason,
                        action=request.action,
                        sequence_number=request.sequence_number,
                        executed_at_utc=request.now_utc,
                        workflow_status=wf_status,
                        error_message=f"Blocked by M10 workflow: {wf_status.value}",
                    )
                    self._record_tool_result(res)
                    return res

            # 3. Step 3: Tool Execution via ToolService
            if self._tool_service is None:
                res = ToolExecutionResult(
                    session_id=session_id,
                    tool_id=request.tool_id,
                    execution_status=ExecutionStatus.BLOCKED_WORKFLOW,
                    gate_decision=gate_decision,
                    gate_reason_code=gate_reason,
                    action=request.action,
                    sequence_number=request.sequence_number,
                    executed_at_utc=request.now_utc,
                    error_message="ToolService handle is unavailable",
                )
                self._record_tool_result(res)
                return res

            ctx = ToolInvocationContext(
                invocation_id=str(uuid.uuid4()),
                tool_id=request.tool_id,
                epoch_id=self._epoch_id,
                session_id=session_id,
                sequence_number=request.sequence_number,
                depth=1,
                parameters=dict(request.parameters),
                correlation_id=request.correlation_id,
                causation_id=request.causation_id,
                timestamp_utc=request.now_utc,
            )
            cap = _create_execution_capability(
                service_instance_id=id(self._tool_service),
                session_id=session_id,
                action="TOOL_INVOCATION",
                sequence_number=request.sequence_number,
            )
            try:
                tool_res = self._tool_service.invoke_tool(ctx, capability=cap)
            except Exception as e:
                raw_err = str(e)
                redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
                res = ToolExecutionResult(
                    session_id=session_id,
                    tool_id=request.tool_id,
                    execution_status=ExecutionStatus.BLOCKED_WORKFLOW,
                    gate_decision=gate_decision,
                    gate_reason_code=gate_reason,
                    action=request.action,
                    sequence_number=request.sequence_number,
                    executed_at_utc=request.now_utc,
                    workflow_status=wf_status,
                    error_message=redacted,
                )
                self._record_tool_result(res)
                return res
            finally:
                cap.invalidate()

            status = (
                ExecutionStatus.EXECUTED_CLEAR
                if gate_decision == GateDecision.PERMITTED_CLEAR
                else ExecutionStatus.EXECUTED_WITH_CAUTION
            )
            res = ToolExecutionResult(
                session_id=session_id,
                tool_id=request.tool_id,
                execution_status=status,
                gate_decision=gate_decision,
                gate_reason_code=gate_reason,
                action=request.action,
                sequence_number=request.sequence_number,
                executed_at_utc=request.now_utc,
                workflow_status=wf_status,
                tool_result=tool_res,
            )
            self._record_tool_result(res)
            return res
        finally:
            self._in_transaction = False

    def _record_tool_result(self, result: ToolExecutionResult) -> None:
        """Record and audit tool execution result."""
        session_id = result.session_id
        if self._persistence_service is not None:
            audit_payload = {
                "session_id": session_id,
                "tool_id": result.tool_id,
                "execution_status": result.execution_status.value,
                "gate_decision": result.gate_decision.value,
                "gate_reason_code": result.gate_reason_code.value,
                "action": result.action.value,
                "sequence_number": result.sequence_number,
                "epoch_id": self._epoch_id,
                "executed_at_utc": result.executed_at_utc,
            }
            if result.error_message:
                audit_payload["error_message"] = result.error_message
            self._persistence_service.record_audit(audit_payload, session_id=session_id)

        self._emit_event(
            "execution.tool.completed",
            {
                "session_id": session_id,
                "tool_id": result.tool_id,
                "execution_status": result.execution_status.value,
                "gate_decision": result.gate_decision.value,
                "sequence_number": result.sequence_number,
                "epoch_id": self._epoch_id,
            },
        )

    # -------------------------------------------------------------------------
    # Internal Persistence & Query Methods
    # -------------------------------------------------------------------------

    def _record_execution_result(self, result: NavigationExecutionResult) -> None:
        """Record latest result, deduplicate persistence, and emit event."""
        session_id = result.session_id
        self._latest_results[session_id] = result

        current_sig = (result.execution_status, result.gate_reason_code)
        last_sig = self._persisted_states.get(session_id)

        # Deduplicate persistence: persist on status change, reason change under failure, or first occurrence
        should_persist = (
            last_sig is None
            or last_sig != current_sig
            or result.execution_status in (
                ExecutionStatus.BLOCKED_SAFETY_GATE,
                ExecutionStatus.BLOCKED_WORKFLOW,
                ExecutionStatus.FAILED_NAVIGATION_GEOMETRY,
            )
        )

        if should_persist:
            self._persisted_states[session_id] = current_sig
            if self._persistence_service is not None:
                audit_payload = {
                    "session_id": session_id,
                    "execution_status": result.execution_status.value,
                    "gate_decision": result.gate_decision.value,
                    "gate_reason_code": result.gate_reason_code.value,
                    "action": result.action.value,
                    "sequence_number": result.sequence_number,
                    "instrument_id": result.instrument_id,
                    "epoch_id": self._epoch_id,
                    "executed_at_utc": result.executed_at_utc,
                }
                if result.error_message:
                    audit_payload["error_message"] = result.error_message
                self._persistence_service.record_audit(audit_payload, session_id=session_id)

            self._emit_event(
                "execution.navigation.completed",
                {
                    "session_id": session_id,
                    "execution_status": result.execution_status.value,
                    "gate_decision": result.gate_decision.value,
                    "sequence_number": result.sequence_number,
                    "epoch_id": self._epoch_id,
                },
            )

    def get_execution_status(self, session_id: str) -> Optional[NavigationExecutionStatusRecord]:
        """Query latest execution result for a clinical session."""
        latest = self._latest_results.get(session_id)
        if latest is None:
            return None
        return NavigationExecutionStatusRecord(
            session_id=session_id,
            latest_result=latest,
            epoch_id=self._epoch_id,
            updated_at_utc=latest.executed_at_utc,
        )

    def clear(self) -> None:
        """Clear transient session tracking cache."""
        self._latest_results.clear()
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

    def handle_execute_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle execution.navigation.execute command."""
        payload = command_envelope.payload
        session_id = payload.get("session_id")
        instrument_id = payload.get("instrument_id")
        traj_id = payload.get("target_trajectory_id")
        seq = payload.get("sequence_number", 1)
        now_utc = payload.get("now_utc") or datetime.now(timezone.utc).isoformat()
        pose_dict = payload.get("pose")

        if not session_id or not instrument_id or not traj_id or not pose_dict:
            return create_error_response(
                command_envelope, self.name, "ERR_INVALID_ARGS", "Missing session, instrument, trajectory or pose"
            )

        try:
            pose = TrackedInstrumentPose(
                instrument_id=str(pose_dict["instrument_id"]),
                session_id=str(pose_dict["session_id"]),
                epoch_id=int(pose_dict.get("epoch_id", self._epoch_id)),
                sequence_number=int(pose_dict["sequence_number"]),
                tip_position_mm=tuple(pose_dict["tip_position_mm"]),  # type: ignore
                orientation_quaternion=tuple(pose_dict["orientation_quaternion"]),  # type: ignore
                coordinate_frame=str(pose_dict.get("coordinate_frame", "PATIENT_TRACKER")),
                confidence=float(pose_dict.get("confidence", 1.0)),
                uncertainty=float(pose_dict.get("uncertainty", 0.0)),
                timestamp_utc=str(pose_dict["timestamp_utc"]),
            )
            req = NavigationExecutionRequest(
                session_id=str(session_id),
                sequence_number=int(seq),
                now_utc=str(now_utc),
                instrument_id=str(instrument_id),
                target_trajectory_id=str(traj_id),
                pose=pose,
            )
            res = self.execute_navigation(req)
            return create_response(
                command_envelope,
                self.name,
                payload={
                    "session_id": res.session_id,
                    "execution_status": res.execution_status.value,
                    "gate_decision": res.gate_decision.value,
                    "gate_reason_code": res.gate_reason_code.value,
                    "sequence_number": res.sequence_number,
                },
            )
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_get_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle execution.status.get query."""
        payload = query_envelope.payload
        session_id = payload.get("session_id")

        if not session_id:
            return create_error_response(query_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id")

        record = self.get_execution_status(str(session_id))
        if record is None:
            return create_error_response(
                query_envelope, self.name, "ERR_SESSION_NOT_FOUND", f"No execution status for session {session_id!r}"
            )

        return create_response(
            query_envelope,
            self.name,
            payload={
                "session_id": record.session_id,
                "execution_status": record.latest_result.execution_status.value,
                "gate_decision": record.latest_result.gate_decision.value,
                "gate_reason_code": record.latest_result.gate_reason_code.value,
                "sequence_number": record.latest_result.sequence_number,
                "updated_at_utc": record.updated_at_utc,
            },
        )

    def handle_recovery_execute_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle execution.recovery.execute command."""
        payload = command_envelope.payload
        session_id = payload.get("session_id")
        seq = payload.get("sequence_number", 1)
        now_utc = payload.get("now_utc") or datetime.now(timezone.utc).isoformat()

        if not session_id:
            return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id")

        try:
            req = RecoveryReorientationExecutionRequest(
                session_id=str(session_id),
                sequence_number=int(seq),
                now_utc=str(now_utc),
                recovery_operation=str(payload.get("recovery_operation", "STATUS")),
                registration_error_mm=float(payload.get("registration_error_mm", 0.5)),
                static_margin_mm=float(payload.get("static_margin_mm", 0.0)),
            )
            res = self.execute_recovery_reorientation(req)
            return create_response(
                command_envelope,
                self.name,
                payload={
                    "session_id": res.session_id,
                    "execution_status": res.execution_status.value,
                    "gate_decision": res.gate_decision.value,
                    "gate_reason_code": res.gate_reason_code.value,
                    "sequence_number": res.sequence_number,
                },
            )
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_trajectory_bind_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle execution.trajectory.bind command."""
        payload = command_envelope.payload
        session_id = payload.get("session_id")
        traj_id = payload.get("trajectory_id")
        seq = payload.get("sequence_number", 1)
        now_utc = payload.get("now_utc") or datetime.now(timezone.utc).isoformat()
        plan_dict = payload.get("plan_trajectory")

        if not session_id or not traj_id or not plan_dict:
            return create_error_response(
                command_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id, trajectory_id, or plan_trajectory"
            )

        try:
            from holomed.planning.models import TrajectoryPlan
            plan_traj = TrajectoryPlan(
                trajectory_id=str(plan_dict["trajectory_id"]),
                target_structure=str(plan_dict.get("target_structure", "target")),
                entry_point_mm=tuple(plan_dict["entry_point_mm"]),  # type: ignore
                target_point_mm=tuple(plan_dict["target_point_mm"]),  # type: ignore
                max_lateral_deviation_mm=float(plan_dict.get("max_lateral_deviation_mm", 2.0)),
                max_angular_deviation_deg=float(plan_dict.get("max_angular_deviation_deg", 5.0)),
            )
            req = TrajectoryBindingExecutionRequest(
                session_id=str(session_id),
                trajectory_id=str(traj_id),
                plan_trajectory=plan_traj,
                sequence_number=int(seq),
                now_utc=str(now_utc),
            )
            res = self.execute_trajectory_binding(req)
            return create_response(
                command_envelope,
                self.name,
                payload={
                    "session_id": res.session_id,
                    "trajectory_id": res.trajectory_id,
                    "execution_status": res.execution_status.value,
                    "gate_decision": res.gate_decision.value,
                    "gate_reason_code": res.gate_reason_code.value,
                    "sequence_number": res.sequence_number,
                },
            )
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_tool_invoke_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle execution.tool.invoke command."""
        payload = command_envelope.payload
        session_id = payload.get("session_id")
        tool_id = payload.get("tool_id")
        seq = payload.get("sequence_number", 1)
        now_utc = payload.get("now_utc") or datetime.now(timezone.utc).isoformat()
        params = payload.get("parameters", {})
        classification_val = payload.get("safety_classification")

        if not session_id or not tool_id or not classification_val:
            return create_error_response(
                command_envelope, self.name, "ERR_INVALID_ARGS", "Missing session_id, tool_id, or safety_classification"
            )

        try:
            classification = ToolSafetyClassification(str(classification_val))
            req = ToolExecutionRequest(
                session_id=str(session_id),
                tool_id=str(tool_id),
                sequence_number=int(seq),
                now_utc=str(now_utc),
                parameters=dict(params),
                safety_classification=classification,
                correlation_id=command_envelope.correlation_id,
                causation_id=command_envelope.causation_id,
            )
            res = self.execute_tool(req)
            resp_payload: dict[str, Any] = {
                "session_id": res.session_id,
                "tool_id": res.tool_id,
                "execution_status": res.execution_status.value,
                "gate_decision": res.gate_decision.value,
                "gate_reason_code": res.gate_reason_code.value,
                "sequence_number": res.sequence_number,
            }
            if res.tool_result is not None:
                resp_payload["result_payload"] = dict(res.tool_result.result_payload)
                resp_payload["status"] = res.tool_result.status.value
            return create_response(command_envelope, self.name, payload=resp_payload)
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)

    def handle_workflow_resume_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle execution.workflow.resume command routing to M20 WorkflowService.resume_from_recovery."""
        payload = command_envelope.payload
        session_id = payload.get("session_id")
        seq = payload.get("sequence_number")
        now_utc = payload.get("now_utc") or datetime.now(timezone.utc).isoformat()
        recovery_rev = payload.get("recovery_revision")
        cleared_ids = payload.get("cleared_interlock_ids", ())

        if not session_id or seq is None or recovery_rev is None:
            return create_error_response(
                command_envelope,
                self.name,
                "ERR_INVALID_ARGS",
                "Missing session_id, sequence_number, or recovery_revision in payload",
            )

        if self._workflow_service is None:
            return create_error_response(
                command_envelope,
                self.name,
                "ERR_WORKFLOW_UNAVAILABLE",
                "WorkflowService is unavailable",
            )

        try:
            req = WorkflowResumptionRequest(
                session_id=str(session_id),
                sequence_number=int(seq),
                now_utc=str(now_utc),
                recovery_revision=int(recovery_rev),
                cleared_interlock_ids=tuple(cleared_ids),
            )
            snapshot = self._workflow_service.resume_from_recovery(
                request=req,
                safety_gate_service=self._safety_gate_service,
            )
            if self._persistence_service is not None:
                self._persistence_service.record_audit(
                    {
                        "session_id": str(session_id),
                        "event": "workflow_resumption_executed",
                        "sequence_number": int(seq),
                        "recovery_revision": int(recovery_rev),
                        "epoch_id": self._epoch_id,
                        "executed_at_utc": str(now_utc),
                    },
                    session_id=str(session_id),
                )
            self._emit_event(
                "execution.workflow.resumed",
                {
                    "session_id": str(session_id),
                    "sequence_number": int(seq),
                    "phase": snapshot.current_phase.value,
                    "epoch_id": self._epoch_id,
                },
            )
            return create_response(
                command_envelope,
                self.name,
                payload={
                    "session_id": snapshot.session_id,
                    "workflow_id": snapshot.workflow_id,
                    "phase": snapshot.current_phase.value,
                    "sequence_number": int(seq),
                    "recovery_revision": int(recovery_rev),
                },
            )
        except Exception as e:
            raw_err = str(e)
            redacted = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(command_envelope, self.name, _format_error_code(type(e).__name__), redacted)


# M19 Backward Compatibility Alias
NavigationExecutionService = ClinicalExecutionGatewayService
