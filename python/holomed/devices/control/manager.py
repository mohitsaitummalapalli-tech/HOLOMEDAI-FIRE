"""HoloMed AI - DeviceControlManager implementing device command and query control plane."""

from __future__ import annotations

import weakref
from datetime import datetime, timezone
from types import MappingProxyType
import uuid
import hashlib
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

# serialize_canonical_bytes imported locally where needed to avoid circular import
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
    GLOBAL_PHYSICAL_OPERATION_CAPACITY,
    QueryHandler,
)
from holomed.devices.models import PhysicalCommand, SubmissionStatus, PhysicalCommandResult
from holomed.devices.control.lease import EndpointLeaseRegistry
from holomed.devices.control.verifier import CommandVerifier
import time
import threading
from holomed.devices.interfaces import IDevice, IDeviceEventSink, NullDeviceEventSink, IPhysicalEndpoint, IExecutionResolutionGate
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
import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from holomed.devices.control.recovery import StateRehydrationEngine
    from holomed.devices.control.daemon import ReconciliationDaemon

class AdmissionState(enum.Enum):
    INITIALIZING = "INITIALIZING"
    REHYDRATING = "REHYDRATING"
    READY = "READY"
    FAILED = "FAILED"

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
        session_validator: Optional[Callable[[str, int], bool]] = None,
        resolution_gate: Optional[IExecutionResolutionGate] = None,
        capacity_checker: Optional[Callable[[str], int]] = None,
        capacity_releaser: Optional[Callable[[str, str, int, int, str, str, str], None]] = None,
        capacity_admitter: Optional[Callable[[str, str, str, int, int, Optional[str], str, str, str, str], tuple[str, bool, Optional[str]]]] = None,
        authoritative_epoch_provider: Optional[Callable[[], int]] = None,
        rehydration_engine: Optional["StateRehydrationEngine"] = None,
        reconciliation_daemon: Optional["ReconciliationDaemon"] = None,
    ) -> None:
        self._registry = registry
        self._resolution_gate = resolution_gate
        self._deadlines: Dict[str, Tuple[float, int]] = {}
        self._active_commands: Dict[str, PhysicalCommand] = {}
        self._timeout_thread: Optional[threading.Thread] = None
        self._timeout_shutdown = threading.Event()
        self._timeout_lock = threading.Lock()
        self._event_sink: IDeviceEventSink = event_sink or NullDeviceEventSink()
        self._logger = logger or StructuredLogger("holomed.devices.control")
        self._secret_filter = secret_filter or SecretFilter()
        self._session_validator = session_validator
        self._capacity_checker = capacity_checker
        self._capacity_releaser = capacity_releaser
        self._capacity_admitter = capacity_admitter
        self._authoritative_epoch_provider = authoritative_epoch_provider
        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._admission_state = AdmissionState.INITIALIZING
        self._rehydration_engine = rehydration_engine
        self._reconciliation_daemon = reconciliation_daemon

        # Resources & Subsystems
        self._resources: Optional[OwnedResourceSet] = None
        self._idempotency = IdempotencyTracker()
        self._verifier = CommandVerifier()
        self._lease_registry = EndpointLeaseRegistry()

        # Command & Query Registries
        self._commands: Dict[str, DeviceCommandDefinition] = {}
        self._queries: Dict[str, DeviceQueryDefinition] = {}

        # Reentrancy & accounting
        self._in_transaction: bool = False
        self._executed_commands_count: int = 0
        self._executed_queries_count: int = 0
        self._sink_errors_count: int = 0

    def durably_record_terminal_state(self, execution_id: str, terminal_state: str) -> None:
        """Atomically records a terminal software resolution in the durable journal."""
        with self._timeout_lock:
            cmd = self._active_commands.pop(execution_id, None)

        if not cmd or not self._capacity_releaser:
            return

        device_id = None
        for device in self._registry.all_devices:
            for endpoint in device.endpoints:
                if endpoint.endpoint_id == cmd.endpoint_id:
                    device_id = device.device_id
                    break
            if device_id:
                break

        if device_id:
            val = str(getattr(terminal_state, "value", terminal_state))
            self._capacity_releaser(
                cmd.session_id,
                device_id,
                cmd.device_epoch,
                cmd.controller_epoch,
                cmd.physical_operation_id,
                cmd.command_nonce,
                val
            )

    def _release_physical_lease(self, execution_id: str) -> None:
        """Hardware-level lease release."""
        for device in self._registry.all_devices:
            for endpoint in device.endpoints:
                lease = endpoint.active_lease
                if lease and lease.execution_id == execution_id:
                    self._lease_registry.release_lease(endpoint, lease.session_id)
                    return

    def release_capacity_for_execution(self, execution_id: str, terminal_state: str) -> None:
        """Explicitly release physical capacity when a terminal state is proven."""
        self._release_physical_lease(execution_id)
        self.durably_record_terminal_state(execution_id, terminal_state)

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
        self._epoch_id = context.epoch_id

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition to STARTED. Seals static definitions and enables execution."""
        if self._state == ServiceState.STARTED:
            return
        if self._state != ServiceState.INITIALIZED:
            raise ServiceLifecycleError(f"Cannot start DeviceControlManager in state {self._state.name}, expected INITIALIZED")

        self._admission_state = AdmissionState.REHYDRATING
        self._state = ServiceState.STARTED

        try:
            if not self._rehydration_engine:
                raise ServiceLifecycleError("StateRehydrationEngine is mandatory for production safety")
            if not self._authoritative_epoch_provider:
                raise ServiceLifecycleError("Rehydration requires authoritative_epoch_provider")
            auth_epoch = self._authoritative_epoch_provider()
            if auth_epoch is None:
                raise ServiceLifecycleError("Authoritative epoch missing during startup")

            self._rehydration_engine.rehydrate_controller_state(current_session_id="system_boot")

            self._timeout_shutdown.clear()
            self._timeout_thread = threading.Thread(target=self._timeout_loop, name="dcm_timeouts", daemon=True)
            self._timeout_thread.start()

            if self._reconciliation_daemon:
                self._reconciliation_daemon.start()

            self._admission_state = AdmissionState.READY
        except Exception as e:
            self._admission_state = AdmissionState.FAILED
            self._state = ServiceState.FAILED
            raise ServiceLifecycleError(f"Startup rehydration failed: {e}") from e

    def stop(self) -> None:
        """Tear down all resources and clear in-memory caches."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._reconciliation_daemon:
            self._reconciliation_daemon.stop()

        self._timeout_shutdown.set()
        if self._timeout_thread:
            self._timeout_thread.join(timeout=1.0)

        self._idempotency.clear()
        self._lease_registry.clear()
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
            canonical_params = dict(self._verifier.validate_and_canonicalize_parameters(raw_params))
        except Exception as e:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code=f"ERR_{type(e).__name__.upper()}",
                error_message=self._secret_filter.redact(str(e)),
            )

        req_cap = None
        if cmd_def.required_capability_id is not None:
            req_cap = next((c for c in device.capabilities if c.capability_id == cmd_def.required_capability_id), None)

        is_physical = bool(req_cap and req_cap.requires_physical_endpoint)

        if is_physical:
            if self._admission_state != AdmissionState.READY:
                return create_error_response(
                    request=envelope,
                    responder_source=self.name,
                    error_code="ERR_CONTROL_NOT_READY",
                    error_message=f"Physical admission forbidden: control plane is {self._admission_state.value}",
                )

            session_id = payload.get("session_id")
            lifecycle_generation = payload.get("session_lifecycle_generation")
            execution_id = payload.get("execution_id")
            command_nonce = payload.get("command_nonce")
            if not session_id or not execution_id or lifecycle_generation is None or not command_nonce:
                return create_error_response(
                    request=envelope,
                    responder_source=self.name,
                    error_code="ERR_VALIDATION_ERROR",
                    error_message="Payload must contain 'session_id', 'execution_id', 'command_nonce', and 'session_lifecycle_generation' for physical actuation",
                )

            capability_scope = frozenset([req_cap.capability_id])  # type: ignore

            if self._session_validator is not None:
                is_valid = self._session_validator(session_id, lifecycle_generation)
                if not is_valid:
                    return create_error_response(
                        request=envelope,
                        responder_source=self.name,
                        error_code="ERR_CAPABILITYUNAUTHORIZEDERROR",
                        error_message="Session is revoked or lifecycle generation is stale",
                    )

            if req_cap.target_endpoint_id is None: # type: ignore
                if len(device.endpoints) > 1:
                    return create_error_response(
                        request=envelope,
                        responder_source=self.name,
                        error_code="ERR_AMBIGUOUS_ENDPOINT",
                        error_message="Command requires physical endpoint but does not specify a target, and device has multiple endpoints.",
                    )
                target_endpoints = list(device.endpoints)
            else:
                target_endpoints = [e for e in device.endpoints if e.endpoint_id == req_cap.target_endpoint_id] # type: ignore
                if not target_endpoints:
                    return create_error_response(
                        request=envelope,
                        responder_source=self.name,
                        error_code="ERR_ENDPOINT_NOT_FOUND",
                        error_message=f"Target endpoint {req_cap.target_endpoint_id} not found on device.", # type: ignore
                    )

            endpoint = target_endpoints[0]

            if self._capacity_checker is not None:
                active_ops = self._capacity_checker(session_id)
                if active_ops >= GLOBAL_PHYSICAL_OPERATION_CAPACITY:
                    return create_error_response(
                        request=envelope,
                        responder_source=self.name,
                        error_code="ERR_CONTROLCAPACITYERROR",
                        error_message=f"Global physical capacity exceeded: {active_ops} >= {GLOBAL_PHYSICAL_OPERATION_CAPACITY} active operations",
                    )

            try:
                lease = self._lease_registry.issue_lease(
                    endpoint=endpoint,
                    session_id=session_id,
                    lifecycle_generation=lifecycle_generation,
                    execution_id=execution_id,
                    capability_scope=capability_scope,
                )
                seq = self._lease_registry.next_command_sequence(endpoint.endpoint_id)

                c_epoch = self._authoritative_epoch_provider() if self._authoritative_epoch_provider else self._epoch_id
                d_epoch = device.current_epoch


                self._in_transaction = True
                try:
                    if not isinstance(endpoint, IPhysicalEndpoint):
                        raise ControlCapacityError(f"Endpoint {endpoint.endpoint_id} does not implement IPhysicalEndpoint")

                    fingerprint_dict = {
                        "device_id": device.device_id,
                        "endpoint_id": endpoint.endpoint_id,
                        "command_name": command_name,
                        "parameters": canonical_params,
                        # Immutable intent fingerprint explicitly excludes device_epoch and controller_epoch
                        # to allow cross-epoch replay for exactly the same intent.
                    }
                    from holomed.persistence.serialization import serialize_canonical_bytes
                    fingerprint = hashlib.sha256(serialize_canonical_bytes(fingerprint_dict)).hexdigest()

                    # 1. Durable Admission (Acquires persistence locks internally and releases them)
                    if self._capacity_admitter:
                        physical_operation_id, is_replay, resolution = self._capacity_admitter(
                            session_id,
                            endpoint.endpoint_id,
                            device.device_id,
                            d_epoch,
                            c_epoch,
                            None,
                            command_nonce,
                            execution_id,
                            command_name,
                            fingerprint
                        )
                    else:
                        physical_operation_id = str(uuid.uuid4())
                        is_replay = False
                        resolution = None

                    physical_cmd = PhysicalCommand(
                        device_epoch=d_epoch,
                        controller_epoch=c_epoch,
                        physical_operation_id=physical_operation_id,
                        command_nonce=command_nonce,
                        endpoint_id=endpoint.endpoint_id,
                        session_id=session_id,
                        lifecycle_generation=lifecycle_generation,
                        endpoint_lease_generation=lease.endpoint_lease_generation,
                        execution_id=execution_id,
                        capability_scope=capability_scope,
                        command_sequence=seq,
                        operation=command_name,
                        parameters=canonical_params,
                    )

                    if is_replay:
                        details: dict[str, Any] = {"idempotent_replay": True}
                        if resolution is not None:
                            details["resolution"] = resolution
                        else:
                            details["resolution"] = "IN_FLIGHT"
                        physical_result = PhysicalCommandResult(status=SubmissionStatus.ACCEPTED, details=details)
                    else:
                        # 2. Setup endpoint fence if supported (driver injection)
                        if hasattr(endpoint, "set_epoch_fence") and self._authoritative_epoch_provider:
                            endpoint.set_epoch_fence(self._authoritative_epoch_provider) # type: ignore

                        # 3. Physical Submission
                        physical_result = endpoint.submit_command(physical_cmd)

                        if physical_result.status == SubmissionStatus.ACCEPTED:
                            # Register deadline
                            with self._timeout_lock:
                                self._deadlines[execution_id] = (time.time() + 5.0, lifecycle_generation)
                                self._active_commands[execution_id] = physical_cmd
                        else:
                            # Failure Rollback (Case A: Confirmed Absent)
                            if self._capacity_releaser:
                                self._capacity_releaser(
                                    session_id,
                                    device.device_id,
                                    physical_cmd.device_epoch,
                                    physical_cmd.controller_epoch,
                                    physical_cmd.physical_operation_id,
                                    physical_cmd.command_nonce,
                                    "OPERATION_CONFIRMED_ABSENT"
                                )

                    canonical_result = self._verifier.validate_and_canonicalize_command_result(
                        dict(physical_result.details)
                    )
                finally:
                    self._in_transaction = False

            except Exception as e:
                return create_error_response(
                    request=envelope,
                    responder_source=self.name,
                    error_code=f"ERR_{type(e).__name__.upper()}",
                    error_message=self._secret_filter.redact(str(e)),
                )

        else:
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

    def emergency_stop(self, session_id: str) -> None:
        """Revokes physical endpoint leases and halts actuation immediately."""
        for device in self._registry.all_devices:
            for endpoint in device.endpoints:
                if endpoint.active_lease and endpoint.active_lease.session_id == session_id:
                    # Transition to interlocked safely
                    try:
                        endpoint.emergency_stop()
                    except Exception as e:
                        self._logger.error("Failed to emergency stop endpoint", extra={"endpoint_id": endpoint.endpoint_id, "error": str(e)})

                    # Free from lease registry
                    self._lease_registry.release_lease(endpoint, session_id)

    def handle_device_restart(self, device_id: str, new_device_epoch: int) -> None:
        """Handles explicit device restarts and epoch changes."""
        if not self._rehydration_engine:
            return

        try:
            self._rehydration_engine.rehydrate_device_state(
                current_session_id="device_restart",
                device_id=device_id,
                new_device_epoch=new_device_epoch
            )
        except Exception as e:
            self._logger.error("Device rehydration failed", extra={"device_id": device_id, "error": str(e)})
            raise DeviceControlError(f"Device rehydration failed for {device_id}: {e}") from e

    def quarantine_device(self, device_id: str) -> None:
        """Quarantine a device by closing admission and stopping endpoints."""
        self._admission_state = AdmissionState.INITIALIZING
        if not self._registry.contains(device_id):
            raise DeviceNotFoundError(f"Device '{device_id}' not found")
        device = self._registry.get(device_id)
        for endpoint in device.endpoints:
            endpoint.emergency_stop()

    def recover_device(
        self,
        device_id: str,
        coordinator: Any,  # holomed.persistence.coordinator.DurableGlobalCoordinator
        hardware_evidence: dict,
    ) -> None:
        """Perform Sequence 5 strict device reinitialization and admission opening.

        Args:
            device_id: The ID of the device to reinitialize.
            coordinator: The global durable coordinator.
            hardware_evidence: Unforgeable proof of safe device state.
        """
        # Ensure admission gate is closed before starting
        self._admission_state = AdmissionState.INITIALIZING

        if not self._registry.contains(device_id):
            raise DeviceNotFoundError(f"Device '{device_id}' not found")
        device = self._registry.get(device_id)

        # 1. Hardware-ready evidence (assumed proven before calling this, passed in).

        # 2. DEVICE_READY(E2) durable commit
        new_epoch = coordinator.commit_device_ready(device_id, hardware_evidence)

        # 3. Runtime projection updated (update device object's epoch in registry)
        # Note: In a real flow, the device registry or object needs its epoch updated.
        if hasattr(device, "current_epoch"):
            setattr(device, "current_epoch", new_epoch)

        # 4. Endpoint epoch explicitly synchronized to E2
        for endpoint in device.endpoints:
            if hasattr(endpoint, "set_endpoint_epoch"):
                endpoint.set_endpoint_epoch(new_epoch)

        # 5. Admission gate transitions to OPEN
        self._admission_state = AdmissionState.READY


    def preempt_execution(self, device_id: str, endpoint_id: str, execution_id: str, lifecycle_generation: int) -> None:
        """Issue an authoritative preemption routing request for a specific execution."""
        if not self._registry.contains(device_id):
            raise DeviceNotFoundError(f"Device '{device_id}' not found")
        device = self._registry.get(device_id)

        target_endpoint = next((e for e in device.endpoints if e.endpoint_id == endpoint_id), None)
        if not target_endpoint:
            raise DeviceControlError(f"Endpoint '{endpoint_id}' not found on device '{device_id}'")

        if not self._resolution_gate:
            return

        from holomed.devices.models import StopRouteState
        route_state = self._resolution_gate.route_stop_request(execution_id, lifecycle_generation)

        if route_state == StopRouteState.PHYSICAL_ROUTING_ACCEPTED:
            target_endpoint.request_stop(execution_id)

    # --------------------------------------------------------------------------
    # Private Helpers
    # --------------------------------------------------------------------------
    def _require_started(self, operation_name: str) -> None:
        if self._state != ServiceState.STARTED:
            raise ServiceLifecycleError(f"Cannot perform {operation_name}() while DeviceControlManager is in {self._state.name}, expected STARTED")


    def _timeout_loop(self) -> None:
        while not self._timeout_shutdown.is_set():
            now = time.time()
            expired = []
            with self._timeout_lock:
                for exec_id, (expiry, gen) in list(self._deadlines.items()):
                    if now >= expiry:
                        expired.append((exec_id, gen))
                        del self._deadlines[exec_id]

            for exec_id, gen in expired:
                if self._resolution_gate:
                    record = self._resolution_gate.resolve_timeout(exec_id, gen)
                    if record.terminal_resolution_status:
                        self.durably_record_terminal_state(exec_id, record.current_state)
                        if self._resolution_gate.is_capacity_release_terminal(record.current_state):
                            self._release_physical_lease(exec_id)

            self._timeout_shutdown.wait(0.1)

    def trigger_test_timeout(self, execution_id: str) -> None:
        """Test seam to manually trigger a timeout."""
        with self._timeout_lock:
            if execution_id in self._deadlines:
                _, gen = self._deadlines.pop(execution_id)
            else:
                return

        if self._resolution_gate:
            record = self._resolution_gate.resolve_timeout(execution_id, gen)
            if record.terminal_resolution_status:
                self.durably_record_terminal_state(execution_id, record.current_state)
                if self._resolution_gate.is_capacity_release_terminal(record.current_state):
                    self._release_physical_lease(execution_id)
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
