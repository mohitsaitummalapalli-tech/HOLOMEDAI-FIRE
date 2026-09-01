"""HoloMed AI - DeviceCoordinationService implementing IService."""

from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from holomed.core.subscription import validate_concrete_topic
from holomed.devices.coordination.events import (
    IDeviceCoordinationEventSink,
    NullDeviceCoordinationEventSink,
)
from holomed.devices.coordination.exceptions import (
    DeviceCoordinationCapacityError,
    DeviceCoordinationDeviceNotFoundError,
    DeviceCoordinationEpochMismatchError,
    DeviceCoordinationIdentityError,
    DeviceCoordinationLifecycleError,
    DeviceCoordinationSequenceError,
    DeviceCoordinationSerializationError,
    DeviceCoordinationShutdownError,
    DeviceCoordinationValidationError,
)
from holomed.devices.coordination.health import DeviceHealthAggregator
from holomed.devices.coordination.models import (
    MAX_COORDINATION_ERROR_MESSAGE_LENGTH,
    MAX_DEVICE_SNAPSHOT_SIZE,
    MAX_SAFE_INTEGER,
    DeviceObservation,
    DeviceSnapshot,
    DeviceTelemetryRecord,
    TelemetryStatus,
    serialize_coordination_payload,
)
from holomed.devices.coordination.telemetry import TelemetryAccumulator
from holomed.devices.interfaces import IDevice
from holomed.devices.models import (
    DeviceHealth,
    DeviceShutdownFailureRecord,
    DeviceState,
    DeviceType,
)
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


class DeviceCoordinationService(IService):
    """Central device state, observation, and telemetry coordination service.

    Guarantees:
    - Implements IService lifecycle (UNINITIALIZED -> INITIALIZED -> STARTED -> STOPPED).
    - Declares dependency on 'device_manager'.
    - Owns exactly 3 structural resources ('coordination.snapshot', 'coordination.telemetry', 'coordination.events').
    - Reads device state strictly through DeviceRegistry without modifying devices.
    - Captures canonically ordered, bounded DeviceSnapshot observations.
    - Synchronous, deterministic health aggregation with strict precedence.
    - Bounded per-device telemetry accumulation with monotonic sequence enforcement.
    - Bounded event sink emission with non-fatal isolation.
    - Static dispatcher route registration ('device.coordination.snapshot', 'device.coordination.health').
    - Reentrancy protection via `_in_transaction` guard across all mutating paths.
    - Atomic secret redaction across all public diagnostics.
    """

    def __init__(
        self,
        registry: DeviceRegistry,
        event_sink: Optional[IDeviceCoordinationEventSink] = None,
        logger: Optional[StructuredLogger] = None,
        secret_filter: Optional[SecretFilter] = None,
        dispatcher: Optional[Any] = None,
    ) -> None:
        self._registry = registry
        self._event_sink: IDeviceCoordinationEventSink = (
            NullDeviceCoordinationEventSink() if event_sink is None else event_sink
        )
        self._logger = logger or StructuredLogger("holomed.devices.coordination")
        self._secret_filter = secret_filter or SecretFilter()
        self._dispatcher = dispatcher
        self._state: ServiceState = ServiceState.UNINITIALIZED

        # Subsystems & Resources
        self._resources: Optional[OwnedResourceSet] = None
        self._context: Optional[RuntimeContext] = None
        self._epoch_id: int = 0
        self._telemetry: Optional[TelemetryAccumulator] = None
        self._health_aggregator = DeviceHealthAggregator(self._secret_filter)

        # Reentrancy & Counters
        self._in_transaction: bool = False
        self._snapshot_count: int = 0
        self._telemetry_update_count: int = 0
        self._sink_errors_count: int = 0

    # --------------------------------------------------------------------------
    # IService Properties & Lifecycle Implementation
    # --------------------------------------------------------------------------
    @property
    def name(self) -> str:
        return "device_coordination"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("device_manager",)

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise ServiceLifecycleError("DeviceCoordinationService uninitialized; OwnedResourceSet not created")
        return self._resources

    def initialize(self, context: RuntimeContext) -> None:
        """Acquire coordination structural resources and initialize telemetry accumulator."""
        if self._state != ServiceState.UNINITIALIZED:
            raise ServiceLifecycleError(f"Cannot initialize DeviceCoordinationService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id

        # Acquire the exactly 3 structural handles
        self._resources = OwnedResourceSet(self.name, context.epoch_id)
        self._resources.acquire("coordination.snapshot")
        self._resources.acquire("coordination.telemetry")
        self._resources.acquire("coordination.events")

        self._telemetry = TelemetryAccumulator(
            registry=self._registry,
            current_epoch_id=self._epoch_id,
        )

        # Register concrete dispatcher routes during INITIALIZED if dispatcher provided
        if self._dispatcher is not None:
            self._dispatcher.register_command_handler(
                "device.coordination.snapshot",
                self.handle_snapshot_command,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "device.coordination.health",
                self.handle_health_query,
                self.name,
            )

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition to STARTED. Acquires zero new resources."""
        if self._state == ServiceState.STARTED:
            raise DeviceCoordinationLifecycleError("DeviceCoordinationService is already STARTED")
        if self._state != ServiceState.INITIALIZED:
            raise ServiceLifecycleError(
                f"Cannot start DeviceCoordinationService in state {self._state.name}, expected INITIALIZED"
            )

        self._state = ServiceState.STARTED

    def stop(self) -> None:
        """Release all structural resources and reset state. Safe and idempotent in STOPPED/UNINITIALIZED."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._in_transaction:
            raise DeviceCoordinationLifecycleError("Cannot stop DeviceCoordinationService during active transaction")

        self._in_transaction = True
        failures: List[DeviceShutdownFailureRecord] = []
        try:
            # 1. Clear telemetry and internal buffers
            if self._telemetry is not None:
                self._telemetry.clear()

            # 2. Release structural resources
            if self._resources is not None:
                for idx, handle_id in enumerate((
                    "coordination.snapshot",
                    "coordination.telemetry",
                    "coordination.events",
                )):
                    try:
                        self._resources.release(handle_id)
                    except Exception as e:
                        if self._resources is not None:
                            self._resources.mark_release_failed(handle_id, str(e))
                        failures.append(
                            DeviceShutdownFailureRecord(
                                device_id="coordination_structural",
                                error_type=type(e).__name__,
                                error_message=str(e),
                                execution_index=idx,
                                unreleased_resources=(handle_id,),
                            )
                        )

            if failures:
                self._state = ServiceState.FAILED
                raise DeviceCoordinationShutdownError(
                    "DeviceCoordinationService resource release failures encountered during stop",
                    tuple(failures),
                )

            self._state = ServiceState.STOPPED
        finally:
            self._in_transaction = False

    def health(self) -> ServiceHealth:
        """Evaluate synchronous health snapshot using aggregate device health."""
        if self._state in (ServiceState.UNINITIALIZED, ServiceState.STOPPED, ServiceState.FAILED):
            return self._health_aggregator.aggregate((), self._state, self.name)

        # Capture lightweight observation health
        observations: List[DeviceObservation] = []
        now_utc = datetime.now(timezone.utc).isoformat()

        for dev in self._registry.all_devices:
            h_status = HealthStatus.HEALTHY
            h_msg = "Device healthy"
            try:
                dev_health = dev.health()
                if not isinstance(dev_health, DeviceHealth):
                    h_status = HealthStatus.FAILED
                    h_msg = "Invalid non-DeviceHealth response from device.health()"
                else:
                    h_status = dev_health.status
                    h_msg = dev_health.message
            except Exception as e:
                h_status = HealthStatus.FAILED
                h_msg = f"Device health evaluation raised {type(e).__name__}"

            redacted_msg = self._secret_filter.redact(h_msg)
            observations.append(
                DeviceObservation(
                    device_id=dev.device_id,
                    physical_id=dev.physical_id,
                    device_type=dev.device_type,
                    state=dev.state,
                    health_status=h_status,
                    health_message=redacted_msg,
                    observed_at_utc=now_utc,
                    epoch_id=self._epoch_id,
                )
            )

        return self._health_aggregator.aggregate(observations, self._state, self.name)

    # --------------------------------------------------------------------------
    # Observation & Snapshot Engine
    # --------------------------------------------------------------------------
    def capture_snapshot(self) -> DeviceSnapshot:
        """Capture a canonically ordered, deeply immutable snapshot of all registered devices."""
        self._require_started("capture_snapshot")
        if self._in_transaction:
            raise DeviceCoordinationLifecycleError("Reentrant capture_snapshot() is strictly forbidden")

        self._in_transaction = True
        now_utc = datetime.now(timezone.utc).isoformat()
        try:
            all_devices = self._registry.all_devices
            if len(all_devices) > MAX_DEVICE_SNAPSHOT_SIZE:
                raise DeviceCoordinationCapacityError(
                    f"Registered device count ({len(all_devices)}) exceeds MAX_DEVICE_SNAPSHOT_SIZE ({MAX_DEVICE_SNAPSHOT_SIZE})"
                )

            observations: List[DeviceObservation] = []
            for dev in all_devices:
                h_status = HealthStatus.HEALTHY
                h_msg = "Device operational"
                try:
                    dev_health = dev.health()
                    if not isinstance(dev_health, DeviceHealth):
                        h_status = HealthStatus.FAILED
                        h_msg = f"Device '{dev.device_id}' returned invalid non-DeviceHealth object"
                    else:
                        h_status = dev_health.status
                        h_msg = dev_health.message
                except Exception as e:
                    h_status = HealthStatus.FAILED
                    h_msg = f"Device '{dev.device_id}' health raised {type(e).__name__}"

                redacted_msg = self._secret_filter.redact(h_msg)
                observations.append(
                    DeviceObservation(
                        device_id=dev.device_id,
                        physical_id=dev.physical_id,
                        device_type=dev.device_type,
                        state=dev.state,
                        health_status=h_status,
                        health_message=redacted_msg,
                        observed_at_utc=now_utc,
                        epoch_id=self._epoch_id,
                    )
                )

            snapshot = DeviceSnapshot(
                epoch_id=self._epoch_id,
                captured_at_utc=now_utc,
                observations=tuple(observations),
            )
            self._snapshot_count += 1

            # Emit coordination event (best-effort)
            self._emit_coordination_event(
                "device.coordination.snapshot_captured",
                {
                    "epoch_id": self._epoch_id,
                    "captured_at_utc": now_utc,
                    "observation_count": len(observations),
                },
            )

            return snapshot
        finally:
            self._in_transaction = False

    # --------------------------------------------------------------------------
    # Telemetry Ingestion & Query Interface
    # --------------------------------------------------------------------------
    def update_telemetry(
        self,
        device_id: str,
        sequence_number: int,
        timestamp_utc: str,
        status: Union[TelemetryStatus, str, Any],
        physical_id: Optional[str] = None,
        epoch_id: Optional[int] = None,
    ) -> DeviceTelemetryRecord:
        """Process and record a validated telemetry update for a registered device."""
        self._require_started("update_telemetry")
        if self._in_transaction:
            raise DeviceCoordinationLifecycleError("Reentrant update_telemetry() is strictly forbidden")

        if self._telemetry is None:
            raise DeviceCoordinationLifecycleError("TelemetryAccumulator is uninitialized")

        self._in_transaction = True
        try:
            target_epoch = self._epoch_id if epoch_id is None else epoch_id

            record = self._telemetry.update(
                device_id=device_id,
                epoch_id=target_epoch,
                sequence_number=sequence_number,
                timestamp_utc=timestamp_utc,
                status=status,
                physical_id=physical_id,
            )
            self._telemetry_update_count += 1

            # Emit coordination event (best-effort)
            self._emit_coordination_event(
                "device.coordination.telemetry_updated",
                {
                    "device_id": device_id,
                    "epoch_id": target_epoch,
                    "sequence_number": sequence_number,
                    "status": str(status),
                },
            )

            return record
        finally:
            self._in_transaction = False

    def get_telemetry(self, device_id: Optional[str] = None) -> Any:
        """Query immutable telemetry statistics for a specific device or all tracked devices."""
        self._require_started("get_telemetry")
        if self._telemetry is None:
            raise DeviceCoordinationLifecycleError("TelemetryAccumulator is uninitialized")

        if device_id is not None:
            rec = self._telemetry.get(device_id)
            if rec is None:
                raise DeviceCoordinationDeviceNotFoundError(f"Device '{device_id}' has no tracked telemetry")
            return rec

        return self._telemetry.all_records()

    def reset_epoch(self, new_epoch_id: int) -> None:
        """Reset coordination state for a new runtime execution epoch."""
        self._require_started("reset_epoch")
        if self._in_transaction:
            raise DeviceCoordinationLifecycleError("Cannot reset epoch during active transaction")

        self._in_transaction = True
        try:
            self._epoch_id = new_epoch_id
            if self._telemetry is not None:
                self._telemetry.reset_epoch(new_epoch_id)
        finally:
            self._in_transaction = False

    def clear(self) -> None:
        """Clear telemetry accumulation buffers."""
        self._require_started("clear")
        if self._in_transaction:
            raise DeviceCoordinationLifecycleError("Cannot clear coordinator during active transaction")

        self._in_transaction = True
        try:
            if self._telemetry is not None:
                self._telemetry.clear()
        finally:
            self._in_transaction = False

    # --------------------------------------------------------------------------
    # Dispatcher Command & Query Handlers
    # --------------------------------------------------------------------------
    def handle_snapshot_command(self, envelope: MessageEnvelope) -> MessageEnvelope:
        """MessageDispatcher handler for topic 'device.coordination.snapshot'."""
        self._require_started("handle_snapshot_command")
        if envelope.message_type != MessageType.COMMAND:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_VALIDATION_ERROR",
                error_message=f"Expected COMMAND envelope, got {envelope.message_type.value}",
            )

        payload = envelope.payload
        req_epoch = payload.get("epoch_id")

        if type(req_epoch) is not int:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_VALIDATION_ERROR",
                error_message="Payload must contain integer 'epoch_id'",
            )

        if not (0 <= req_epoch <= MAX_SAFE_INTEGER):
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_VALIDATION_ERROR",
                error_message=f"epoch_id out of safe integer range [0, {MAX_SAFE_INTEGER}]",
            )

        if req_epoch != self._epoch_id:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_EPOCH_MISMATCH",
                error_message=f"Requested epoch {req_epoch} does not match current coordinator epoch {self._epoch_id}",
            )

        try:
            snapshot = self.capture_snapshot()
            return create_response(
                request=envelope,
                responder_source=self.name,
                payload=snapshot.to_dict(),
            )
        except DeviceCoordinationCapacityError as e:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_CAPACITY_EXCEEDED",
                error_message=str(e),
            )
        except Exception as e:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_COORDINATION_ERROR",
                error_message=self._secret_filter.redact(str(e)),
            )

    def handle_health_query(self, envelope: MessageEnvelope) -> MessageEnvelope:
        """MessageDispatcher handler for topic 'device.coordination.health'."""
        self._require_started("handle_health_query")
        if envelope.message_type != MessageType.QUERY:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_VALIDATION_ERROR",
                error_message=f"Expected QUERY envelope, got {envelope.message_type.value}",
            )

        payload = envelope.payload
        action = payload.get("action")

        if type(action) is not str:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_INVALID_QUERY",
                error_message="Query payload must contain string 'action'",
            )

        if action == "get_aggregate_health":
            health_report = self.health()
            res_payload = {
                "service_name": health_report.name,
                "status": health_report.status.name,
                "message": health_report.message,
                "timestamp_utc": health_report.timestamp_utc,
            }
            return create_response(request=envelope, responder_source=self.name, payload=res_payload)

        elif action == "get_device_health":
            device_id = payload.get("device_id")
            if type(device_id) is not str or not device_id:
                return create_error_response(
                    request=envelope,
                    responder_source=self.name,
                    error_code="ERR_VALIDATION_ERROR",
                    error_message="Action 'get_device_health' requires string 'device_id'",
                )
            if not self._registry.contains(device_id):
                return create_error_response(
                    request=envelope,
                    responder_source=self.name,
                    error_code="ERR_DEVICE_NOT_FOUND",
                    error_message=f"Device '{device_id}' is not registered in DeviceRegistry",
                )
            dev = self._registry.get(device_id)
            try:
                dh = dev.health()
                res_payload = {
                    "device_id": dev.device_id,
                    "physical_id": dev.physical_id,
                    "status": dh.status.name if isinstance(dh, DeviceHealth) else HealthStatus.FAILED.name,
                    "message": self._secret_filter.redact(dh.message) if isinstance(dh, DeviceHealth) else "Invalid health response",
                    "timestamp_utc": dh.timestamp_utc if isinstance(dh, DeviceHealth) else datetime.now(timezone.utc).isoformat(),
                }
            except Exception as e:
                res_payload = {
                    "device_id": dev.device_id,
                    "physical_id": dev.physical_id,
                    "status": HealthStatus.FAILED.name,
                    "message": self._secret_filter.redact(f"Health evaluation raised {type(e).__name__}"),
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                }
            return create_response(request=envelope, responder_source=self.name, payload=res_payload)

        elif action == "get_telemetry":
            device_id = payload.get("device_id")
            try:
                if device_id is not None:
                    if type(device_id) is not str:
                        return create_error_response(
                            request=envelope,
                            responder_source=self.name,
                            error_code="ERR_VALIDATION_ERROR",
                            error_message="device_id must be a string if provided",
                        )
                    rec = self.get_telemetry(device_id)
                    res_payload = {"device_id": device_id, "telemetry": rec.to_dict()}
                else:
                    records = self.get_telemetry()
                    res_payload = {
                        "device_count": len(records),
                        "telemetry": {k: v.to_dict() for k, v in records.items()},
                    }
                return create_response(request=envelope, responder_source=self.name, payload=res_payload)
            except DeviceCoordinationDeviceNotFoundError as e:
                return create_error_response(
                    request=envelope,
                    responder_source=self.name,
                    error_code="ERR_DEVICE_NOT_FOUND",
                    error_message=str(e),
                )

        elif action == "get_snapshot":
            try:
                snapshot = self.capture_snapshot()
                return create_response(request=envelope, responder_source=self.name, payload=snapshot.to_dict())
            except Exception as e:
                return create_error_response(
                    request=envelope,
                    responder_source=self.name,
                    error_code="ERR_COORDINATION_ERROR",
                    error_message=self._secret_filter.redact(str(e)),
                )

        else:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_INVALID_QUERY",
                error_message=f"Unknown query action: {action!r}",
            )

    # --------------------------------------------------------------------------
    # Private Helpers
    # --------------------------------------------------------------------------
    def _require_started(self, op: str) -> None:
        """Enforce that public operational methods execute strictly in STARTED."""
        if self._state != ServiceState.STARTED:
            raise DeviceCoordinationLifecycleError(
                f"Cannot execute '{op}' in state {self._state.name}; must be STARTED"
            )

    def _emit_coordination_event(self, message_name: str, payload: Dict[str, Any]) -> None:
        """Emit a coordination event envelope with non-fatal failure isolation."""
        try:
            envelope = create_event(
                message_name=message_name,
                source=self.name,
                payload=payload,
            )
            self._event_sink.emit(envelope)
        except Exception as e:
            self._sink_errors_count += 1
            self._logger.warning(
                f"Coordination event emission failed for '{message_name}': {e}",
                event="event_emission_error",
            )
