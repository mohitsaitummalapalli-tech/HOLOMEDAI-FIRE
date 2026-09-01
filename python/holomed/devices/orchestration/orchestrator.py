"""HoloMed AI - DevicePlaneOrchestrator implementing IService."""

from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.coordination.coordinator import DeviceCoordinationService
from holomed.devices.data.models import DeviceData, ProcessingReceipt
from holomed.devices.data.processor import DeviceDataProcessor
from holomed.devices.manager import DeviceManager
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.devices.orchestration.auditor import DeviceSubsystemConsistencyAuditor
from holomed.devices.orchestration.bridge import (
    WHITELISTED_BRIDGE_TOPICS,
    DeviceCrossPlaneBridge,
)
from holomed.devices.orchestration.events import (
    IDeviceOrchestrationEventSink,
    NullDeviceOrchestrationEventSink,
)
from holomed.devices.orchestration.exceptions import (
    DeviceOrchestrationCapacityError,
    DeviceOrchestrationIntegrityError,
    DeviceOrchestrationLifecycleError,
    DeviceOrchestrationShutdownError,
    DeviceOrchestrationValidationError,
)
from holomed.devices.orchestration.models import (
    MAX_SAFE_INTEGER,
    AuditReport,
    BridgeStatistics,
    OrchestratorStatus,
)
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


class DevicePlaneOrchestrator(IService):
    """Authoritative master service orchestrating the four underlying device planes.

    Guarantees:
    - Implements IService declaring dependencies on manager, control, data, and coordination.
    - Owns exactly 3 structural handles ('orchestration.bridge', 'orchestration.supervisor', 'orchestration.audit').
    - Establishes a deterministic cross-plane event bridge (D166, D168, D169).
    - Single-writer rule: telemetry updates from data processing route solely via bridge (D168).
    - Holistic cross-plane consistency auditor (D171).
    - Idempotent stop in UNINITIALIZED and STOPPED (D175).
    - Reentrancy guarded across all mutating paths via `_in_transaction`.
    - Zero external hardware, network, or concurrency dependencies.
    """

    def __init__(
        self,
        device_manager: DeviceManager,
        control_manager: DeviceControlManager,
        data_processor: DeviceDataProcessor,
        coordination: DeviceCoordinationService,
        event_sink: Optional[IDeviceOrchestrationEventSink] = None,
        logger: Optional[StructuredLogger] = None,
        secret_filter: Optional[SecretFilter] = None,
        dispatcher: Optional[Any] = None,
    ) -> None:
        self._device_manager = device_manager
        self._control_manager = control_manager
        self._data_processor = data_processor
        self._coordination = coordination

        self._event_sink: IDeviceOrchestrationEventSink = (
            NullDeviceOrchestrationEventSink() if event_sink is None else event_sink
        )
        self._logger = logger or StructuredLogger("holomed.devices.orchestration")
        self._secret_filter = secret_filter or SecretFilter()
        self._dispatcher = dispatcher
        self._state: ServiceState = ServiceState.UNINITIALIZED

        # Resources & Context
        self._resources: Optional[OwnedResourceSet] = None
        self._context: Optional[RuntimeContext] = None
        self._epoch_id: int = 0

        # Sub-engines
        self._bridge: Optional[DeviceCrossPlaneBridge] = None
        self._auditor: Optional[DeviceSubsystemConsistencyAuditor] = None

        # Reentrancy & Counters
        self._in_transaction: bool = False
        self._sink_errors_count: int = 0

    # --------------------------------------------------------------------------
    # IService Properties & Lifecycle Implementation
    # --------------------------------------------------------------------------
    @property
    def name(self) -> str:
        return "device_orchestration"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (
            "device_manager",
            "device_control_manager",
            "device_data_processor",
            "device_coordination",
        )

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise ServiceLifecycleError("DevicePlaneOrchestrator uninitialized; OwnedResourceSet not created")
        return self._resources

    @property
    def bridge(self) -> DeviceCrossPlaneBridge:
        if self._bridge is None:
            raise DeviceOrchestrationLifecycleError("DeviceCrossPlaneBridge uninitialized")
        return self._bridge

    def initialize(self, context: RuntimeContext) -> None:
        """Acquire orchestration structural resources and wire cross-plane bridge."""
        if self._state != ServiceState.UNINITIALIZED:
            raise ServiceLifecycleError(f"Cannot initialize DevicePlaneOrchestrator in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id

        # Acquire the exactly 3 structural handles
        self._resources = OwnedResourceSet(self.name, context.epoch_id)
        self._resources.acquire("orchestration.bridge")
        self._resources.acquire("orchestration.supervisor")
        self._resources.acquire("orchestration.audit")

        # Instantiate bridge and auditor
        reg = getattr(self._device_manager, "registry", None) or getattr(self._device_manager, "_registry", None)
        self._bridge = DeviceCrossPlaneBridge(
            registry=reg,
            coordination=self._coordination,
            current_epoch_id=self._epoch_id,
            secret_filter=self._secret_filter,
            logger=self._logger,
        )
        self._auditor = DeviceSubsystemConsistencyAuditor(
            device_manager=self._device_manager,
            control_manager=self._control_manager,
            data_processor=self._data_processor,
            coordination=self._coordination,
            secret_filter=self._secret_filter,
        )

        # Wire bridge to dispatcher routes strictly in INITIALIZED (D170)
        if self._dispatcher is not None:
            # Register query/command handlers
            self._dispatcher.register_query_handler(
                "device.orchestration.status",
                self.handle_status_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "device.orchestration.audit",
                self.handle_audit_query,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "device.orchestration.sync",
                self.handle_sync_command,
                self.name,
            )

            # Subscribe bridge to whitelisted topics (D166)
            for topic in WHITELISTED_BRIDGE_TOPICS:
                self._dispatcher.subscribe_event(topic, self._bridge.handle_event, self.name)

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition to STARTED. Acquires zero new resources."""
        if self._state == ServiceState.STARTED:
            raise DeviceOrchestrationLifecycleError("DevicePlaneOrchestrator is already STARTED")
        if self._state != ServiceState.INITIALIZED:
            raise ServiceLifecycleError(
                f"Cannot start DevicePlaneOrchestrator in state {self._state.name}, expected INITIALIZED"
            )

        self._state = ServiceState.STARTED

    def stop(self) -> None:
        """Release structural resources and reset state. Safe and idempotent in STOPPED/UNINITIALIZED (D175)."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._in_transaction:
            raise DeviceOrchestrationLifecycleError("Cannot stop DevicePlaneOrchestrator during active transaction")

        self._in_transaction = True
        failures: List[DeviceShutdownFailureRecord] = []
        try:
            # 1. Clear bridge buffers
            if self._bridge is not None:
                self._bridge.clear()

            # 2. Release structural resources
            if self._resources is not None:
                for idx, handle_id in enumerate((
                    "orchestration.bridge",
                    "orchestration.supervisor",
                    "orchestration.audit",
                )):
                    try:
                        self._resources.release(handle_id)
                    except Exception as e:
                        if self._resources is not None:
                            self._resources.mark_release_failed(handle_id, str(e))
                        failures.append(
                            DeviceShutdownFailureRecord(
                                device_id="orchestration_structural",
                                error_type=type(e).__name__,
                                error_message=str(e),
                                execution_index=idx,
                                unreleased_resources=(handle_id,),
                            )
                        )

            if failures:
                self._state = ServiceState.FAILED
                raise DeviceOrchestrationShutdownError(
                    "DevicePlaneOrchestrator structural resource release failures encountered during stop",
                    tuple(failures),
                )

            self._state = ServiceState.STOPPED
        finally:
            self._in_transaction = False

    def health(self) -> ServiceHealth:
        """Compute aggregate health combining child services and consistency auditor."""
        now_utc = datetime.now(timezone.utc).isoformat()
        if self._state in (ServiceState.UNINITIALIZED, ServiceState.STOPPED, ServiceState.FAILED):
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.FAILED if self._state == ServiceState.FAILED else HealthStatus.UNHEALTHY,
                message=f"DevicePlaneOrchestrator is {self._state.name}",
                timestamp_utc=now_utc,
            )

        # Query child services health
        worst_status = HealthStatus.HEALTHY
        severity = {
            HealthStatus.HEALTHY: 0,
            HealthStatus.DEGRADED: 1,
            HealthStatus.UNHEALTHY: 2,
            HealthStatus.FAILED: 3,
        }

        for svc in (self._device_manager, self._control_manager, self._data_processor, self._coordination):
            try:
                h = svc.health()
                if severity.get(h.status, 3) > severity.get(worst_status, 0):
                    worst_status = h.status
            except Exception:
                worst_status = HealthStatus.FAILED

        return ServiceHealth(
            name=self.name,
            status=worst_status,
            message=f"DevicePlaneOrchestrator health evaluated: {worst_status.name}",
            timestamp_utc=now_utc,
        )

    # --------------------------------------------------------------------------
    # Public Subsystem Operations
    # --------------------------------------------------------------------------
    def audit(self) -> AuditReport:
        """Execute comprehensive cross-plane consistency check."""
        self._require_started("audit")
        if self._in_transaction:
            raise DeviceOrchestrationLifecycleError("Reentrant audit() is forbidden")

        self._in_transaction = True
        try:
            if self._auditor is None:
                raise DeviceOrchestrationLifecycleError("DeviceSubsystemConsistencyAuditor is uninitialized")
            return self._auditor.audit(self._epoch_id)
        finally:
            self._in_transaction = False

    def sync(self, epoch_id: Optional[int] = None) -> Dict[str, Any]:
        """Trigger deterministic cross-plane snapshot synchronization."""
        self._require_started("sync")
        if self._in_transaction:
            raise DeviceOrchestrationLifecycleError("Reentrant sync() is forbidden")

        self._in_transaction = True
        try:
            target_epoch = self._epoch_id if epoch_id is None else epoch_id
            if target_epoch != self._epoch_id:
                raise DeviceOrchestrationValidationError(
                    f"Requested sync epoch {target_epoch} does not match active epoch {self._epoch_id}"
                )

            # Trigger coordination snapshot
            snapshot = self._coordination.capture_snapshot()

            # Emit sync event (best-effort)
            self._emit_orchestration_event(
                "device.orchestration.synchronized",
                {
                    "epoch_id": self._epoch_id,
                    "device_count": len(snapshot.observations),
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                },
            )

            return {
                "synchronized": True,
                "epoch_id": self._epoch_id,
                "snapshot_captured": True,
                "devices_synced": len(snapshot.observations),
            }
        finally:
            self._in_transaction = False

    def submit_data(self, data: DeviceData) -> ProcessingReceipt:
        """Submit data item through data plane. Telemetry updates follow via bridge (D168)."""
        self._require_started("submit_data")
        # Forward to data processor; the resulting audit event will route to coordination via bridge
        return self._data_processor.submit(data)

    def get_status(self) -> OrchestratorStatus:
        """Return consolidated status across all 4 device planes."""
        self._require_started("get_status")
        child_states = {
            "device_manager": self._get_service_state_str(self._device_manager),
            "device_control_manager": self._get_service_state_str(self._control_manager),
            "device_data_processor": self._get_service_state_str(self._data_processor),
            "device_coordination": self._get_service_state_str(self._coordination),
        }
        bridge_stats = self._bridge.get_statistics() if self._bridge else BridgeStatistics(0, 0, 0, 0, 0)
        return OrchestratorStatus(
            service_name=self.name,
            state=self._state.name,
            epoch_id=self._epoch_id,
            child_services=MappingProxyType(child_states),
            bridge_statistics=bridge_stats,
        )

    # --------------------------------------------------------------------------
    # Dispatcher Handlers (Registered in INITIALIZED)
    # --------------------------------------------------------------------------
    def handle_status_query(self, envelope: MessageEnvelope) -> MessageEnvelope:
        """Dispatcher query handler for 'device.orchestration.status'."""
        self._require_started("handle_status_query")
        if envelope.message_type != MessageType.QUERY:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_VALIDATION_ERROR",
                error_message=f"Expected QUERY envelope, got {envelope.message_type.value}",
            )

        status = self.get_status()
        return create_response(request=envelope, responder_source=self.name, payload=status.to_dict())

    def handle_audit_query(self, envelope: MessageEnvelope) -> MessageEnvelope:
        """Dispatcher query handler for 'device.orchestration.audit'."""
        self._require_started("handle_audit_query")
        if envelope.message_type != MessageType.QUERY:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_VALIDATION_ERROR",
                error_message=f"Expected QUERY envelope, got {envelope.message_type.value}",
            )

        report = self.audit()
        return create_response(request=envelope, responder_source=self.name, payload=report.to_dict())

    def handle_sync_command(self, envelope: MessageEnvelope) -> MessageEnvelope:
        """Dispatcher command handler for 'device.orchestration.sync'."""
        self._require_started("handle_sync_command")
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

        if req_epoch != self._epoch_id:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_EPOCH_MISMATCH",
                error_message=f"Requested epoch {req_epoch} does not match active epoch {self._epoch_id}",
            )

        try:
            sync_res = self.sync(epoch_id=req_epoch)
            return create_response(request=envelope, responder_source=self.name, payload=sync_res)
        except Exception as e:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_ORCHESTRATION_ERROR",
                error_message=self._secret_filter.redact(str(e)),
            )

    # --------------------------------------------------------------------------
    # Private Helpers
    # --------------------------------------------------------------------------
    def _require_started(self, op: str) -> None:
        """Enforce that public methods run strictly in STARTED state."""
        if self._state != ServiceState.STARTED:
            raise DeviceOrchestrationLifecycleError(
                f"Cannot execute '{op}' in state {self._state.name}; must be STARTED"
            )

    def _get_service_state_str(self, svc: Any) -> str:
        """Safely extract service state name."""
        state = getattr(svc, "state", None) or getattr(svc, "_state", None)
        if isinstance(state, ServiceState):
            return state.name
        return str(state or "UNKNOWN")

    def _emit_orchestration_event(self, message_name: str, payload: Dict[str, Any]) -> None:
        """Emit an orchestration event envelope with non-fatal isolation."""
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
                f"Orchestration event emission failed for '{message_name}': {e}",
                event="event_emission_error",
            )
