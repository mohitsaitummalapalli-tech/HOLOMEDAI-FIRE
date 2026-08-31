"""HoloMed AI - DeviceControlManager implementing device command and query control plane."""

from __future__ import annotations

import weakref
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from holomed.core.subscription import validate_concrete_topic
from holomed.devices.control.exceptions import (
    CommandNotFoundError,
    ControlCapacityError,
    DeviceCommandValidationError,
    DeviceControlError,
    DeviceNotFoundError,
    QueryNotFoundError,
)
from holomed.devices.control.idempotency import IdempotencyTracker
from holomed.devices.control.models import (
    CommandHandler,
    DeviceCommandDefinition,
    DeviceQueryDefinition,
    MAX_REGISTERED_COMMANDS,
    MAX_REGISTERED_QUERIES,
    QueryHandler,
)
from holomed.devices.control.verifier import CommandVerifier
from holomed.devices.interfaces import IDevice, IDeviceEventSink, NullDeviceEventSink
from holomed.devices.registry import DeviceRegistry
from holomed.protocol.builders import (
    create_error_response,
    create_event,
    create_response,
)
from holomed.protocol.models import MessageEnvelope, MessageType
from holomed.runtime.context import RuntimeContext
from holomed.runtime.exceptions import ServiceLifecycleError
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import HealthStatus, OwnedResourceSet, ServiceHealth
from holomed.runtime.service import IService, ServiceState


class DeviceControlManager(IService):
    """Central controller and mediator for device commands and queries.

    Guarantees:
    - Implements IService.
    - Registers concrete topics 'device.command' and 'device.query' during INITIALIZED.
    - Does NOT attempt dynamic MessageDispatcher registration after STARTED.
    - Resolves live IDevice instances dynamically from DeviceRegistry at execution time.
    - Prevents handlers from permanently retaining device instances.
    - Strictly enforces command state and capability requirements.
    - Provides bounded FIFO idempotency tracking (4096 entries).
    - Ensures SecretFilter redaction on errors and event emission isolation.
    """

    def __init__(
        self,
        registry: DeviceRegistry,
        event_sink: Optional[IDeviceEventSink] = None,
        logger: Optional[StructuredLogger] = None,
        secret_filter: Optional[SecretFilter] = None,
    ) -> None:
        self._registry = registry
        self._event_sink: IDeviceEventSink = event_sink or NullDeviceEventSink()
        self._logger = logger or StructuredLogger("holomed.devices.control")
        self._secret_filter = secret_filter or SecretFilter()
        self._state: ServiceState = ServiceState.UNINITIALIZED

        # Resources & Subsystems
        self._resources: Optional[OwnedResourceSet] = None
        self._idempotency = IdempotencyTracker()
        self._verifier = CommandVerifier()

        # Command & Query Registries
        self._commands: Dict[str, DeviceCommandDefinition] = {}
        self._queries: Dict[str, DeviceQueryDefinition] = {}

        # Reentrancy & accounting
        self._in_transaction: bool = False
        self._executed_commands_count: int = 0
        self._executed_queries_count: int = 0
        self._sink_errors_count: int = 0

    # --------------------------------------------------------------------------
    # IService Properties & Lifecycle Implementation
    # --------------------------------------------------------------------------
    @property
    def name(self) -> str:
        return "device_control_manager"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("device_manager",)

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise ServiceLifecycleError("DeviceControlManager uninitialized; OwnedResourceSet not created")
        return self._resources

    def initialize(self, context: RuntimeContext) -> None:
        """Acquire control-plane structural resources and prepare for dispatch."""
        if self._state != ServiceState.UNINITIALIZED:
            raise ServiceLifecycleError(f"Cannot initialize DeviceControlManager in state {self._state.name}")

        self._resources = OwnedResourceSet(self.name, context.epoch_id)
        self._resources.acquire("control.registry")
        self._resources.acquire("control.idempotency")

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition to STARTED. Seals static definitions and enables execution."""
        if self._state == ServiceState.STARTED:
            return
        if self._state != ServiceState.INITIALIZED:
            raise ServiceLifecycleError(f"Cannot start DeviceControlManager in state {self._state.name}, expected INITIALIZED")

        self._state = ServiceState.STARTED

    def stop(self) -> None:
        """Tear down all resources and clear in-memory caches."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        self._idempotency.clear()
        if self._resources is not None:
            for h in list(self._resources.outstanding_handles):
                self._resources.release(h.resource_id)

        self._state = ServiceState.STOPPED

    def health(self) -> ServiceHealth:
        """Evaluate and return synchronous health snapshot."""
        now_utc = datetime.now(timezone.utc).isoformat()
        if self._state == ServiceState.UNINITIALIZED:
            return ServiceHealth(name=self.name, status=HealthStatus.FAILED, message="DeviceControlManager UNINITIALIZED", timestamp_utc=now_utc)
        if self._state == ServiceState.FAILED:
            return ServiceHealth(name=self.name, status=HealthStatus.FAILED, message="DeviceControlManager FAILED", timestamp_utc=now_utc)

        msg = (
            f"DeviceControlManager operational (commands={len(self._commands)}, "
            f"queries={len(self._queries)}, executed_cmds={self._executed_commands_count}, "
            f"executed_queries={self._executed_queries_count}, cached_keys={len(self._idempotency)})"
        )
        return ServiceHealth(
            name=self.name,
            status=HealthStatus.HEALTHY,
            message=self._secret_filter.redact(msg),
            timestamp_utc=now_utc,
        )

    # --------------------------------------------------------------------------
    # Static Registration Interface (Permitted in INITIALIZED or STARTED)
    # --------------------------------------------------------------------------
    def register_command(
        self,
        command_name: str,
        handler: CommandHandler,
        required_capability_id: Optional[str] = None,
        allow_ready: bool = False,
        description: str = "",
    ) -> None:
        """Register a command definition into the control plane."""
        if self._in_transaction:
            raise ServiceLifecycleError("Cannot register command while dispatch transaction is active")
        if len(self._commands) >= MAX_REGISTERED_COMMANDS:
            raise ControlCapacityError(f"Max registered commands exceeded ({MAX_REGISTERED_COMMANDS})")

        cmd_def = DeviceCommandDefinition(
            command_name=command_name,
            handler=handler,
            required_capability_id=required_capability_id,
            allow_ready=allow_ready,
            description=description,
        )
        if cmd_def.command_name in self._commands:
            raise ControlCapacityError(f"Command '{cmd_def.command_name}' is already registered; replacement is forbidden")

        self._commands[cmd_def.command_name] = cmd_def

    def register_query(
        self,
        query_name: str,
        handler: QueryHandler,
        allowed_states: Tuple[Any, ...] = (),
        description: str = "",
    ) -> None:
        """Register a query definition into the control plane."""
        if self._in_transaction:
            raise ServiceLifecycleError("Cannot register query while dispatch transaction is active")
        if len(self._queries) >= MAX_REGISTERED_QUERIES:
            raise ControlCapacityError(f"Max registered queries exceeded ({MAX_REGISTERED_QUERIES})")

        q_kwargs: dict[str, Any] = {"query_name": query_name, "handler": handler, "description": description}
        if allowed_states:
            q_kwargs["allowed_states"] = tuple(allowed_states)

        q_def = DeviceQueryDefinition(**q_kwargs)
        if q_def.query_name in self._queries:
            raise ControlCapacityError(f"Query '{q_def.query_name}' is already registered; replacement is forbidden")

        self._queries[q_def.query_name] = q_def

    # --------------------------------------------------------------------------
    # Dispatcher Handlers (Registered during INITIALIZED)
    # --------------------------------------------------------------------------
    def handle_command(self, envelope: MessageEnvelope) -> MessageEnvelope:
        """MessageDispatcher handler for 'device.command'."""
        self._require_started("handle_command")
        if envelope.message_type != MessageType.COMMAND:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="INVALID_MESSAGE_TYPE",
                error_message=f"Expected COMMAND envelope, got {envelope.message_type.value}",
            )

        # 1. Parse required routing parameters
        payload = envelope.payload
        device_id = payload.get("device_id")
        command_name = payload.get("command")
        raw_params = payload.get("parameters", {})

        if type(device_id) is not str or not device_id:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_VALIDATION_ERROR",
                error_message="Payload must contain non-empty string 'device_id'",
            )
        if type(command_name) is not str or not command_name:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_VALIDATION_ERROR",
                error_message="Payload must contain non-empty string 'command'",
            )

        # 2. Check Idempotency Cache
        try:
            cached_rec = self._idempotency.get(envelope.message_id, payload)
            if cached_rec is not None:
                return create_response(
                    request=envelope,
                    responder_source=self.name,
                    payload=dict(cached_rec.response_payload),
                    metadata=dict(cached_rec.response_metadata),
                )
        except Exception as e:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_IDEMPOTENCY_CONFLICT",
                error_message=self._secret_filter.redact(str(e)),
            )

        # 3. Lookup Command Definition
        cmd_def = self._commands.get(command_name)
        if cmd_def is None:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_COMMAND_NOT_FOUND",
                error_message=f"Command '{command_name}' is not registered in control plane",
            )

        # 4. Resolve Device Dynamically
        if not self._registry.contains(device_id):
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_DEVICE_NOT_FOUND",
                error_message=f"Device '{device_id}' not found in registry",
            )
        device = self._registry.get(device_id)

        # 5. Authorize State and Capability
        try:
            self._verifier.verify_command_authorization(device, cmd_def)
            canonical_params = self._verifier.validate_and_canonicalize_parameters(raw_params)
        except Exception as e:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code=f"ERR_{type(e).__name__.upper()}",
                error_message=self._secret_filter.redact(str(e)),
            )

        # 6. Execute Command Handler
        self._in_transaction = True
        try:
            raw_result = cmd_def.handler(device, canonical_params)
            canonical_result = self._verifier.validate_and_canonicalize_command_result(raw_result)
        except Exception as e:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_EXECUTION_FAILURE",
                error_message=self._secret_filter.redact(str(e)),
            )
        finally:
            self._in_transaction = False

        # 7. Record in Idempotency Cache
        self._idempotency.record(envelope.message_id, payload, canonical_result)
        self._executed_commands_count += 1

        # 8. Emit Audit Event (isolated failure)
        self._emit_audit_event("device.command.executed", {
            "device_id": device_id,
            "command": command_name,
            "correlation_id": envelope.correlation_id,
        })

        return create_response(
            request=envelope,
            responder_source=self.name,
            payload=dict(canonical_result),
        )

    def handle_query(self, envelope: MessageEnvelope) -> MessageEnvelope:
        """MessageDispatcher handler for 'device.query'."""
        self._require_started("handle_query")
        if envelope.message_type != MessageType.QUERY:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_INVALID_MESSAGE_TYPE",
                error_message=f"Expected QUERY envelope, got {envelope.message_type.value}",
            )

        payload = envelope.payload
        device_id = payload.get("device_id")
        query_name = payload.get("query")
        raw_params = payload.get("parameters", {})

        if type(device_id) is not str or not device_id:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_VALIDATION_ERROR",
                error_message="Payload must contain non-empty string 'device_id'",
            )
        if type(query_name) is not str or not query_name:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_VALIDATION_ERROR",
                error_message="Payload must contain non-empty string 'query'",
            )

        q_def = self._queries.get(query_name)
        if q_def is None:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_QUERY_NOT_FOUND",
                error_message=f"Query '{query_name}' is not registered in control plane",
            )

        if not self._registry.contains(device_id):
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_DEVICE_NOT_FOUND",
                error_message=f"Device '{device_id}' not found in registry",
            )
        device = self._registry.get(device_id)

        try:
            self._verifier.verify_query_authorization(device, q_def)
            canonical_params = self._verifier.validate_and_canonicalize_parameters(raw_params)
        except Exception as e:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code=f"ERR_{type(e).__name__.upper()}",
                error_message=self._secret_filter.redact(str(e)),
            )

        self._in_transaction = True
        try:
            raw_result = q_def.handler(device, canonical_params)
            canonical_result = self._verifier.validate_and_canonicalize_query_result(raw_result)
        except Exception as e:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_EXECUTION_FAILURE",
                error_message=self._secret_filter.redact(str(e)),
            )
        finally:
            self._in_transaction = False

        self._executed_queries_count += 1
        return create_response(
            request=envelope,
            responder_source=self.name,
            payload=dict(canonical_result),
        )

    # --------------------------------------------------------------------------
    # Private Helpers
    # --------------------------------------------------------------------------
    def _require_started(self, operation_name: str) -> None:
        if self._state != ServiceState.STARTED:
            raise ServiceLifecycleError(f"Cannot perform {operation_name}() while DeviceControlManager is in {self._state.name}, expected STARTED")

    def _emit_audit_event(self, topic: str, payload: Dict[str, Any]) -> None:
        validate_concrete_topic(topic)
        envelope = create_event(
            message_name=topic,
            source=self.name,
            payload=payload,
        )
        try:
            self._event_sink.emit(envelope)
        except Exception as e:
            self._sink_errors_count += 1
            self._logger.warning(
                "Failed to emit control audit event to sink",
                event="control_event_sink_error",
                extra={"topic": topic, "error": self._secret_filter.redact(str(e))},
            )
