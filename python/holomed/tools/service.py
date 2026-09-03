# -*- coding: utf-8 -*-
"""Master Tool Orchestration & Clinical Agent Service for M07."""

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
from holomed.tools.auditor import ToolConsistencyAuditor
from holomed.tools.engine import ToolExecutionEngine
from holomed.tools.events import RecordingToolEventSink
from holomed.tools.exceptions import (
    ToolCapacityError,
    ToolEpochMismatchError,
    ToolLifecycleError,
    ToolResourceIntegrityError,
    ToolSecurityError,
    ToolAuthorizationError,
    ToolSequenceError,
    ToolShutdownError,
    ToolValidationError,
)
from holomed.tools.models import (
    ToolDescriptor,
    ToolInvocationContext,
    ToolResult,
)
from holomed.tools.registry import ToolRegistry
from holomed.tools.serialization import serialize_tool_payload

STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "tools.registry",
    "tools.executor",
    "tools.context",
    "tools.auditor",
    "tools.metrics",
)


class ToolService(IService):
    """Tool Orchestration & Clinical Agent Service implementing IService."""

    def __init__(
        self,
        device_manager: Optional[DeviceManager] = None,
        dispatcher: Optional[MessageDispatcher] = None,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._device_manager = device_manager
        self._dispatcher = dispatcher
        self._secret_filter = secret_filter
        self._logger = logger or StructuredLogger("tool_service", secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0

        # Subsystems
        self._registry: Optional[ToolRegistry] = None
        self._engine: Optional[ToolExecutionEngine] = None
        self._auditor: Optional[ToolConsistencyAuditor] = None
        self._event_sink: Optional[RecordingToolEventSink] = None

        # Metrics
        self._total_invocations: int = 0
        self._in_transaction: bool = False

    @property
    def name(self) -> str:
        return "tool_service"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (
            "device_manager",
            "ultron_service",
            "anatomy_service",
            "xr_service",
        )

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise ToolLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def registry(self) -> Optional[ToolRegistry]:
        return self._registry

    @property
    def engine(self) -> Optional[ToolExecutionEngine]:
        return self._engine

    @property
    def event_sink(self) -> Optional[RecordingToolEventSink]:
        return self._event_sink

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize service state and acquire 5 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise ToolLifecycleError(f"Cannot initialize ToolService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        # Acquire 5 structural handles
        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Instantiate components
        self._registry = ToolRegistry()
        self._engine = ToolExecutionEngine(epoch_id=self._epoch_id)
        self._auditor = ToolConsistencyAuditor(secret_filter=self._secret_filter)
        self._event_sink = RecordingToolEventSink()

        # Register Dispatcher Routes strictly during INITIALIZED
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler(
                "tools.status",
                self.handle_status_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "tools.registry",
                self.handle_registry_query,
                self.name,
            )
            # M21: tools.invoke dispatcher route removed. All clinical tool execution routes through execution.tool.invoke.
            self._dispatcher.register_query_handler(
                "tools.result",
                self.handle_result_query,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "tools.reset",
                self.handle_reset_command,
                self.name,
            )

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED and lock the tool registry."""
        if self._state != ServiceState.INITIALIZED:
            raise ToolLifecycleError(f"Cannot start ToolService in state {self._state.name}")

        if self._registry is not None:
            self._registry.lock()

        self._state = ServiceState.STARTED

    def stop(self) -> None:
        """Release all acquired resources and teardown state cleanly."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._in_transaction:
            raise ToolLifecycleError("Cannot stop ToolService during an active transaction")

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
                raise ToolShutdownError(
                    f"ToolService teardown encountered {len(failures)} resource failure(s)",
                    failures,
                )

            self._state = ServiceState.STOPPED
        finally:
            self._in_transaction = False

    def health(self) -> ServiceHealth:
        """Produce a synchronous in-process health snapshot."""
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
                message="Structural resource corruption detected",
                timestamp_utc=now_utc,
            )

        if any(rec.status == ResourceStatus.UNRELEASED_FAILURE for rec in self._resources.records.values()):
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                message="Resource release failure detected",
                timestamp_utc=now_utc,
            )

        tools_count = self._registry.tool_count if self._registry else 0
        return ServiceHealth(
            name=self.name,
            status=HealthStatus.HEALTHY,
            message=f"Active with {tools_count} tools, {self._total_invocations} invocations",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Public Core API
    # -------------------------------------------------------------------------

    def register_tool(self, descriptor: ToolDescriptor) -> None:
        """Register a clinical tool descriptor."""
        if self._state == ServiceState.STARTED:
            raise ToolLifecycleError("Cannot register tool after service has STARTED")
        if self._registry is None:
            raise ToolResourceIntegrityError("Registry is uninitialized")

        self._registry.register_tool(descriptor)
        self._emit_event(
            "tools.registered",
            {
                "tool_id": descriptor.tool_id,
                "name": descriptor.name,
                "category": descriptor.category.value,
                "safety_classification": descriptor.safety_classification.value,
            },
        )

    def invoke_tool(self, context: ToolInvocationContext, capability: Any = None) -> ToolResult:
        """Execute a tool invocation context synchronously under an authoritative capability."""
        if self._state != ServiceState.STARTED:
            raise ToolLifecycleError(f"Cannot invoke tool in state {self._state.name}")

        # Strict execution capability verification
        if capability is None:
            raise ToolAuthorizationError(
                "Direct uncoordinated call to invoke_tool rejected: missing execution capability"
            )
        if not getattr(capability, "is_active", False):
            raise ToolAuthorizationError("Execution capability is inactive, expired, or replayed")
        if getattr(capability, "session_id", None) != context.session_id:
            raise ToolAuthorizationError(
                f"Capability session mismatch: expected {context.session_id!r}, got {getattr(capability, 'session_id', None)!r}"
            )
        if getattr(capability, "action", None) != "TOOL_INVOCATION":
            raise ToolAuthorizationError(
                f"Capability action mismatch: expected 'TOOL_INVOCATION', got {getattr(capability, 'action', None)!r}"
            )
        if getattr(capability, "sequence_number", None) != context.sequence_number:
            raise ToolAuthorizationError(
                f"Capability sequence mismatch: expected {context.sequence_number}, got {getattr(capability, 'sequence_number', None)}"
            )

        if self._in_transaction:
            raise ToolLifecycleError("Reentrant call to invoke_tool rejected by transaction guard")

        self._in_transaction = True
        try:
            if self._registry is None or self._engine is None:
                raise ToolResourceIntegrityError("Subsystems uninitialized")

            self._total_invocations += 1
            result = self._engine.execute_invocation(context, self._registry)

            self._emit_event(
                "tools.invocation.completed",
                {
                    "invocation_id": result.invocation_id,
                    "tool_id": result.tool_id,
                    "status": result.status.value,
                    "execution_time_ms": result.execution_time_ms,
                },
            )
            return result
        finally:
            self._in_transaction = False

    def audit_tools(self) -> Mapping[str, Any]:
        """Perform deep consistency audit."""
        if self._registry is None or self._engine is None or self._auditor is None:
            raise ToolResourceIntegrityError("Subsystems uninitialized")
        return self._auditor.audit_subsystem(self._registry, self._engine, self._epoch_id)

    def reset(self, epoch_id: int) -> None:
        """Reset service state for a new epoch."""
        if epoch_id != self._epoch_id:
            raise ToolEpochMismatchError(
                f"Reset epoch {epoch_id} does not match service epoch {self._epoch_id}"
            )
        self.clear()

    def evict_session(self, session_id: str, capability: Optional[Any] = None) -> bool:
        """Evict session-scoped tool sequence state under coordinated teardown (M29).

        Validates session_id, enforces transactional reentrancy protection,
        optionally verifies SESSION_TEARDOWN capability binding, and delegates
        eviction to ToolExecutionEngine.
        """
        if not isinstance(session_id, str) or not session_id.strip():
            return False

        if self._in_transaction:
            raise ToolLifecycleError("Reentrant call to evict_session rejected")

        if capability is not None:
            if not getattr(capability, "is_active", False):
                raise ToolAuthorizationError("Teardown capability is inactive or expired")
            if getattr(capability, "action", None) != "SESSION_TEARDOWN":
                raise ToolAuthorizationError(
                    f"Capability action mismatch: expected 'SESSION_TEARDOWN', got {getattr(capability, 'action', None)!r}"
                )
            if getattr(capability, "session_id", None) != session_id:
                raise ToolAuthorizationError(
                    f"Capability session mismatch: expected {session_id!r}, got {getattr(capability, 'session_id', None)!r}"
                )

        self._in_transaction = True
        try:
            if self._engine is not None:
                return self._engine.evict_session(session_id)
            return False
        finally:
            self._in_transaction = False

    def clear(self) -> None:
        """Clear all active sessions, result history, and event sinks."""
        if self._engine is not None:
            self._engine.clear()
        if self._event_sink is not None:
            self._event_sink.clear()
        self._total_invocations = 0

    # -------------------------------------------------------------------------
    # Dispatcher Handlers
    # -------------------------------------------------------------------------

    def handle_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle tools.status query."""
        tools_count = self._registry.tool_count if self._registry else 0
        payload = serialize_tool_payload(
            {
                "service_name": self.name,
                "state": self._state.name,
                "epoch_id": self._epoch_id,
                "tool_count": tools_count,
                "is_registry_locked": self._registry.is_locked if self._registry else False,
                "total_invocations": self._total_invocations,
            }
        )
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_registry_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle tools.registry query."""
        if self._registry is None:
            return create_error_response(
                query_envelope,
                self.name,
                error_code="REGISTRY_UNINITIALIZED",
                error_message="Tool registry uninitialized",
            )

        tools_list = [
            {
                "tool_id": t.tool_id,
                "name": t.name,
                "version": t.version,
                "category": t.category.value,
                "safety_classification": t.safety_classification.value,
                "description": t.description,
                "required_parameters": list(t.required_parameters),
                "optional_parameters": list(t.optional_parameters),
            }
            for t in self._registry.tools
        ]
        payload = serialize_tool_payload({"tools": tools_list})
        return create_response(query_envelope, self.name, payload=dict(payload))

    def handle_invoke_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle tools.invoke command."""
        p = command_envelope.payload
        tool_id = p.get("tool_id")
        params = p.get("parameters", {})
        session_id = p.get("session_id", "default_session")
        sequence_number = p.get("sequence_number", 0)

        if not tool_id:
            return create_error_response(
                command_envelope,
                self.name,
                error_code="INVALID_INVOCATION",
                error_message="Missing tool_id",
            )

        ctx = ToolInvocationContext(
            invocation_id=command_envelope.message_id,
            tool_id=str(tool_id),
            epoch_id=self._epoch_id,
            session_id=str(session_id),
            sequence_number=int(sequence_number),
            depth=1,
            parameters=params,
            correlation_id=command_envelope.correlation_id,
            causation_id=command_envelope.causation_id,
            timestamp_utc=command_envelope.timestamp_utc,
        )

        try:
            res = self.invoke_tool(ctx)
            payload = serialize_tool_payload(
                {
                    "invocation_id": res.invocation_id,
                    "tool_id": res.tool_id,
                    "status": res.status.value,
                    "result_payload": dict(res.result_payload),
                    "execution_time_ms": res.execution_time_ms,
                    "confidence": res.confidence,
                    "uncertainty_metric": res.uncertainty_metric,
                    "is_simulated": res.is_simulated,
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

    def handle_result_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle tools.result query."""
        inv_id = query_envelope.payload.get("invocation_id")
        if not inv_id or self._engine is None:
            return create_error_response(
                query_envelope,
                self.name,
                error_code="INVALID_RESULT_QUERY",
                error_message="Missing invocation_id",
            )

        for res in self._engine.result_history:
            if res.invocation_id == inv_id:
                payload = serialize_tool_payload(
                    {
                        "invocation_id": res.invocation_id,
                        "tool_id": res.tool_id,
                        "status": res.status.value,
                        "result_payload": dict(res.result_payload),
                        "execution_time_ms": res.execution_time_ms,
                        "confidence": res.confidence,
                        "uncertainty_metric": res.uncertainty_metric,
                    }
                )
                return create_response(query_envelope, self.name, payload=dict(payload))

        return create_error_response(
            query_envelope,
            self.name,
            error_code="RESULT_NOT_FOUND",
            error_message=f"No result found for invocation {inv_id!r}",
        )

    def handle_reset_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        """Handle tools.reset command."""
        req_epoch = command_envelope.payload.get("epoch_id")
        if req_epoch != self._epoch_id:
            return create_error_response(
                command_envelope,
                self.name,
                error_code="EPOCH_MISMATCH",
                error_message=f"Command epoch {req_epoch} does not match service epoch {self._epoch_id}",
            )

        self.clear()
        payload = serialize_tool_payload({"reset_completed": True, "epoch_id": self._epoch_id})
        return create_response(command_envelope, self.name, payload=dict(payload))

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit protocol event over dispatcher and record in event sink."""
        frozen_payload = serialize_tool_payload(payload)
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(frozen_payload),
        )

        if self._event_sink is not None:
            try:
                self._event_sink.record_event(env)
            except ToolCapacityError:
                pass

        if self._dispatcher is not None and getattr(self._dispatcher, "state", None) == DispatcherState.STARTED:
            self._dispatcher.dispatch(env)
