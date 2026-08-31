"""HoloMed AI - DeviceDataProcessor implementing synchronous bounded data plane."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from holomed.core.subscription import validate_concrete_topic
from holomed.devices.data.events import (
    IDeviceDataEventSink,
    NullDeviceDataEventSink,
)
from holomed.devices.data.exceptions import (
    DataPayloadValidationError,
    DataProcessorCapacityError,
    DeviceDataCapacityError,
    DeviceDataEpochMismatchError,
    DeviceDataLifecycleError,
    DeviceDataNotFoundError,
    DeviceDataValidationError,
    DeviceIdentityMismatchError,
    DuplicateDataSequenceError,
    OutOfOrderDataError,
)
from holomed.devices.data.models import (
    DataProcessor,
    DeviceData,
    DeviceDataKind,
    MAX_DATA_PAYLOAD_BYTES,
    MAX_QUEUE_ITEMS,
    MAX_TRACKED_DEVICE_STATS,
    ProcessingReceipt,
    ProcessingResult,
    ProcessingStatus,
)
from holomed.devices.data.queue import BoundedDeviceDataQueue
from holomed.devices.data.registry import DeviceDataProcessorRegistry
from holomed.devices.interfaces import IDevice
from holomed.devices.models import DeviceState, deep_freeze_parameter
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


class DeviceDataProcessor(IService):
    """Central bounded data plane processor implementing IService.

    Guarantees:
    - Strictly bounded in-memory FIFO queue (256 items).
    - Synchronous, deterministic execution.
    - Zero background workers or threads.
    - Deep freezing and LSCU canonicalization on all payloads and results.
    - Sequence tracking per registered device session (D148).
    - Non-mutating rejection on queue capacity overflow (D149).
    - Protocol-compliant error codes ERR_[A-Z0-9_]+ (D150).
    - Immutable query return types (D151).
    - Best-effort event sink emission with error isolation (D152).
    - Reentrancy protection across all mutative operations (D153).
    - Device validation prior to statistics allocation (D154).
    - DRAIN_AND_COMPLETE shutdown policy handling stopped/failed devices cleanly (D145).
    """

    def __init__(
        self,
        registry: DeviceRegistry,
        event_sink: Optional[IDeviceDataEventSink] = None,
        logger: Optional[StructuredLogger] = None,
        secret_filter: Optional[SecretFilter] = None,
        max_queue_items: int = MAX_QUEUE_ITEMS,
    ) -> None:
        self._registry = registry
        self._event_sink: IDeviceDataEventSink = event_sink or NullDeviceDataEventSink()
        self._logger = logger or StructuredLogger("holomed.devices.data")
        self._secret_filter = secret_filter or SecretFilter()
        self._state: ServiceState = ServiceState.UNINITIALIZED

        # Resources & Subsystems
        self._resources: Optional[OwnedResourceSet] = None
        self._context: Optional[RuntimeContext] = None
        self._queue = BoundedDeviceDataQueue(max_items=max_queue_items)
        self._processor_registry = DeviceDataProcessorRegistry()

        # Sequence and Statistics Tracking
        # device_id -> last_sequence_number
        self._device_sequences: Dict[str, int] = {}
        # Aggregate Counters
        self._submitted_count: int = 0
        self._accepted_count: int = 0
        self._rejected_count: int = 0
        self._processed_count: int = 0
        self._failed_count: int = 0
        self._sink_errors_count: int = 0

        # Per-Device Counters (bounded to MAX_TRACKED_DEVICE_STATS)
        # device_id -> {"submitted": int, "processed": int, "failed": int}
        self._device_stats: Dict[str, Dict[str, int]] = {}

        # Reentrancy Guard (D153)
        self._in_transaction: bool = False

    # --------------------------------------------------------------------------
    # IService Properties & Lifecycle Implementation
    # --------------------------------------------------------------------------
    @property
    def name(self) -> str:
        return "device_data_processor"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("device_manager",)

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise ServiceLifecycleError("DeviceDataProcessor uninitialized; OwnedResourceSet not created")
        return self._resources

    def initialize(self, context: RuntimeContext) -> None:
        """Acquire data-plane structural resources and prepare queue and registry."""
        if self._state != ServiceState.UNINITIALIZED:
            raise ServiceLifecycleError(f"Cannot initialize DeviceDataProcessor in state {self._state.name}")

        self._context = context
        self._resources = OwnedResourceSet(self.name, context.epoch_id)
        self._resources.acquire("data.queue")
        self._resources.acquire("data.registry")
        self._resources.acquire("data.statistics")

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition to STARTED. Enables submission and processing."""
        if self._state == ServiceState.STARTED:
            raise DeviceDataLifecycleError("DeviceDataProcessor is already STARTED")
        if self._state != ServiceState.INITIALIZED:
            raise ServiceLifecycleError(f"Cannot start DeviceDataProcessor in state {self._state.name}, expected INITIALIZED")

        self._state = ServiceState.STARTED

    def stop(self) -> None:
        """Execute DRAIN_AND_COMPLETE shutdown policy and release structural resources (D145)."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._in_transaction:
            raise DeviceDataLifecycleError("Cannot stop DeviceDataProcessor during active transaction")

        self._in_transaction = True
        try:
            # 1. Drain remaining queue items synchronously
            while not self._queue.is_empty:
                item = self._queue.get()
                if item is None:
                    break

                # Resolve device state for queued item
                if not self._registry.contains(item.device_id):
                    self._record_shutdown_item_rejection(item, "Device deregistered prior to shutdown drain")
                    continue

                device = self._registry.get(item.device_id)
                if device.state not in (DeviceState.READY, DeviceState.ACTIVE):
                    # D145: Items whose devices became STOPPED/FAILED are cleanly rejected during drain
                    self._record_shutdown_item_rejection(item, f"Device in state {device.state.name} during shutdown drain")
                    continue

                # Device is still READY/ACTIVE: execute processor
                self._execute_single_item(item)

            # 2. Clear state
            self._queue.clear()
            self._processor_registry.clear()
            self._device_sequences.clear()

            # 3. Release structural resources
            if self._resources is not None:
                for h in list(self._resources.outstanding_handles):
                    self._resources.release(h.resource_id)

            self._state = ServiceState.STOPPED
        except Exception:
            self._state = ServiceState.FAILED
            raise
        finally:
            self._in_transaction = False

    def health(self) -> ServiceHealth:
        """Produce synchronous health snapshot."""
        now_utc = datetime.now(timezone.utc).isoformat()
        if self._state == ServiceState.UNINITIALIZED:
            return ServiceHealth(name=self.name, status=HealthStatus.FAILED, message="DeviceDataProcessor UNINITIALIZED", timestamp_utc=now_utc)
        if self._state == ServiceState.FAILED:
            return ServiceHealth(name=self.name, status=HealthStatus.FAILED, message="DeviceDataProcessor FAILED", timestamp_utc=now_utc)

        msg = (
            f"DeviceDataProcessor operational (queue_depth={len(self._queue)}, "
            f"submitted={self._submitted_count}, processed={self._processed_count}, "
            f"failed={self._failed_count}, sink_errors={self._sink_errors_count})"
        )
        status = HealthStatus.HEALTHY if self._failed_count == 0 else HealthStatus.DEGRADED
        return ServiceHealth(
            name=self.name,
            status=status,
            message=self._secret_filter.redact(msg),
            timestamp_utc=now_utc,
        )

    # --------------------------------------------------------------------------
    # Processor Registration
    # --------------------------------------------------------------------------
    def register_processor(self, kind: DeviceDataKind, processor: DataProcessor) -> None:
        """Register a synchronous processor for a specific DeviceDataKind."""
        if self._in_transaction:
            raise DeviceDataLifecycleError("Cannot register processor during active processing transaction")
        self._processor_registry.register(kind, processor)

    # --------------------------------------------------------------------------
    # Data Submission & Synchronous Processing Pipeline
    # --------------------------------------------------------------------------
    def submit(self, data: DeviceData) -> ProcessingReceipt:
        """Submit a validated DeviceData item to the bounded queue."""
        self._require_started("submit")
        if self._in_transaction:
            raise DeviceDataLifecycleError("Reentrant submit() during processing transaction is strictly forbidden")

        if not isinstance(data, DeviceData):
            raise DeviceDataValidationError(f"Expected DeviceData instance, got {type(data).__name__}")

        self._in_transaction = True
        self._submitted_count += 1
        now_utc = datetime.now(timezone.utc).isoformat()

        try:
            # 1. Device Existence Check (D154: validate before statistics allocation)
            if not self._registry.contains(data.device_id):
                self._rejected_count += 1
                self._emit_audit_event("device.data.rejected", {
                    "device_id": data.device_id,
                    "sequence_number": data.sequence_number,
                    "reason": "DEVICE_NOT_FOUND",
                })
                raise DeviceDataNotFoundError(f"Device '{data.device_id}' is not registered in DeviceRegistry")

            # 2. Device Identity & State Validation
            device = self._registry.get(data.device_id)
            if device.physical_id != data.physical_id:
                self._rejected_count += 1
                self._emit_audit_event("device.data.rejected", {
                    "device_id": data.device_id,
                    "sequence_number": data.sequence_number,
                    "reason": "PHYSICAL_ID_MISMATCH",
                })
                raise DeviceIdentityMismatchError(
                    f"Physical ID mismatch for '{data.device_id}': expected '{device.physical_id}', got '{data.physical_id}'"
                )

            if device.device_type != data.device_type:
                self._rejected_count += 1
                self._emit_audit_event("device.data.rejected", {
                    "device_id": data.device_id,
                    "sequence_number": data.sequence_number,
                    "reason": "DEVICE_TYPE_MISMATCH",
                })
                raise DeviceIdentityMismatchError(
                    f"Device type mismatch for '{data.device_id}': expected '{device.device_type.value}', got '{data.device_type.value}'"
                )

            if device.state not in (DeviceState.READY, DeviceState.ACTIVE):
                self._rejected_count += 1
                self._emit_audit_event("device.data.rejected", {
                    "device_id": data.device_id,
                    "sequence_number": data.sequence_number,
                    "reason": f"INVALID_DEVICE_STATE_{device.state.name}",
                })
                raise DeviceDataValidationError(
                    f"Device '{data.device_id}' in state {device.state.name} cannot submit data (requires READY or ACTIVE)"
                )

            # 3. Monotonic Sequence Number Validation (D148)
            last_seq = self._device_sequences.get(data.device_id)
            if last_seq is not None:
                if data.sequence_number == last_seq:
                    self._rejected_count += 1
                    self._emit_audit_event("device.data.rejected", {
                        "device_id": data.device_id,
                        "sequence_number": data.sequence_number,
                        "reason": "DUPLICATE_SEQUENCE_NUMBER",
                    })
                    raise DuplicateDataSequenceError(
                        f"Duplicate sequence number {data.sequence_number} for device '{data.device_id}'"
                    )
                if data.sequence_number < last_seq:
                    self._rejected_count += 1
                    self._emit_audit_event("device.data.rejected", {
                        "device_id": data.device_id,
                        "sequence_number": data.sequence_number,
                        "reason": "OUT_OF_ORDER_SEQUENCE_NUMBER",
                    })
                    raise OutOfOrderDataError(
                        f"Out-of-order sequence number {data.sequence_number} < {last_seq} for device '{data.device_id}'"
                    )

            # 4. Atomic Queue Enqueue (D149)
            try:
                self._queue.put(data)
            except DeviceDataCapacityError:
                self._rejected_count += 1
                self._emit_audit_event("device.data.rejected", {
                    "device_id": data.device_id,
                    "sequence_number": data.sequence_number,
                    "reason": "QUEUE_CAPACITY_EXCEEDED",
                })
                raise

            # 5. Update Device State and Counters on Successful Enqueue
            self._device_sequences[data.device_id] = data.sequence_number
            self._accepted_count += 1
            self._record_device_stat(data.device_id, "submitted")

            self._emit_audit_event("device.data.accepted", {
                "device_id": data.device_id,
                "sequence_number": data.sequence_number,
                "kind": data.kind.value,
                "correlation_id": data.correlation_id,
            })

            return ProcessingReceipt(
                device_id=data.device_id,
                sequence_number=data.sequence_number,
                status=ProcessingStatus.ACCEPTED,
                queue_depth=len(self._queue),
                timestamp_utc=now_utc,
            )
        finally:
            self._in_transaction = False

    def process_next(self) -> Optional[ProcessingResult]:
        """Dequeue and synchronously process the oldest item in the queue."""
        self._require_started("process_next")
        if self._in_transaction:
            raise DeviceDataLifecycleError("Reentrant process_next() during active processing transaction is strictly forbidden")

        item = self._queue.get()
        if item is None:
            return None

        self._in_transaction = True
        try:
            return self._execute_single_item(item)
        finally:
            self._in_transaction = False

    def process_all(self, max_items: int = MAX_QUEUE_ITEMS) -> Tuple[ProcessingResult, ...]:
        """Synchronously process up to max_items from the queue."""
        self._require_started("process_all")
        if self._in_transaction:
            raise DeviceDataLifecycleError("Reentrant process_all() during active processing transaction is strictly forbidden")

        results: List[ProcessingResult] = []
        for _ in range(max_items):
            res = self.process_next()
            if res is None:
                break
            results.append(res)
        return tuple(results)

    # --------------------------------------------------------------------------
    # Dispatcher Handlers (Registered during INITIALIZED)
    # --------------------------------------------------------------------------
    def handle_submit_command(self, envelope: MessageEnvelope) -> MessageEnvelope:
        """Dispatcher handler for 'device.data.submit' COMMAND route."""
        self._require_started("handle_submit_command")
        if envelope.message_type != MessageType.COMMAND:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_INVALID_MESSAGE_TYPE",
                error_message=f"Expected COMMAND envelope, got {envelope.message_type.value}",
            )

        try:
            payload = envelope.payload
            kind_str = payload.get("kind")
            if not kind_str or kind_str not in DeviceDataKind.__members__:
                raise DeviceDataValidationError(f"Invalid or missing data kind: {kind_str!r}")

            data_item = DeviceData(
                device_id=payload.get("device_id", ""),
                physical_id=payload.get("physical_id", ""),
                device_type=payload.get("device_type"),
                kind=DeviceDataKind[kind_str],
                sequence_number=payload.get("sequence_number", -1),
                timestamp_utc=payload.get("timestamp_utc", envelope.timestamp_utc),
                payload=payload.get("payload", {}),
                correlation_id=envelope.correlation_id,
            )
            receipt = self.submit(data_item)
            return create_response(
                request=envelope,
                responder_source=self.name,
                payload={
                    "status": receipt.status.value,
                    "device_id": receipt.device_id,
                    "sequence_number": receipt.sequence_number,
                    "queue_depth": receipt.queue_depth,
                    "timestamp_utc": receipt.timestamp_utc,
                },
            )
        except Exception as e:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_" + type(e).__name__.upper(),
                error_message=self._secret_filter.redact(str(e)),
            )

    def handle_query(self, envelope: MessageEnvelope) -> MessageEnvelope:
        """Dispatcher handler for 'device.data.query' QUERY route."""
        self._require_started("handle_query")
        if envelope.message_type != MessageType.QUERY:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_INVALID_MESSAGE_TYPE",
                error_message=f"Expected QUERY envelope, got {envelope.message_type.value}",
            )

        query_action = envelope.payload.get("action")
        if query_action == "get_queue_depth":
            return create_response(request=envelope, responder_source=self.name, payload={"queue_depth": self.get_queue_depth()})
        elif query_action == "get_processing_stats":
            return create_response(request=envelope, responder_source=self.name, payload=dict(self.get_processing_stats()))
        elif query_action == "get_last_sequence":
            dev_id = envelope.payload.get("device_id", "")
            seq = self.get_last_sequence(dev_id)
            return create_response(request=envelope, responder_source=self.name, payload={"device_id": dev_id, "last_sequence": seq})
        elif query_action == "get_device_stats":
            dev_id = envelope.payload.get("device_id", "")
            stats = self.get_device_stats(dev_id)
            return create_response(request=envelope, responder_source=self.name, payload={"device_id": dev_id, "stats": dict(stats)})
        else:
            return create_error_response(
                request=envelope,
                responder_source=self.name,
                error_code="ERR_QUERY_NOT_FOUND",
                error_message=f"Unknown query action: {query_action!r}",
            )

    # --------------------------------------------------------------------------
    # Deterministic Read-Only Queries (D151: Immutable Return Types)
    # --------------------------------------------------------------------------
    def get_queue_depth(self) -> int:
        return len(self._queue)

    def get_last_sequence(self, device_id: str) -> Optional[int]:
        return self._device_sequences.get(device_id)

    def get_processing_stats(self) -> Mapping[str, int]:
        return MappingProxyType({
            "submitted": self._submitted_count,
            "accepted": self._accepted_count,
            "rejected": self._rejected_count,
            "processed": self._processed_count,
            "failed": self._failed_count,
        })

    def get_device_stats(self, device_id: str) -> Mapping[str, int]:
        stats = self._device_stats.get(device_id, {"submitted": 0, "processed": 0, "failed": 0})
        return MappingProxyType(dict(stats))

    def reset_device_sequence(self, device_id: str) -> None:
        """Reset sequence tracking on device deregistration/re-registration (D148)."""
        self._device_sequences.pop(device_id, None)

    # --------------------------------------------------------------------------
    # Private Helpers & Invariant Enforcement
    # --------------------------------------------------------------------------
    def _require_started(self, operation_name: str) -> None:
        if self._state != ServiceState.STARTED:
            raise DeviceDataLifecycleError(
                f"Cannot perform {operation_name}() while DeviceDataProcessor is in state {self._state.name}, expected STARTED"
            )

    def _record_device_stat(self, device_id: str, stat_type: str) -> None:
        """Record bounded device statistic (D154: max 256 devices)."""
        if device_id not in self._device_stats:
            if len(self._device_stats) >= MAX_TRACKED_DEVICE_STATS:
                return
            self._device_stats[device_id] = {"submitted": 0, "processed": 0, "failed": 0}
        self._device_stats[device_id][stat_type] = self._device_stats[device_id].get(stat_type, 0) + 1

    def _execute_single_item(self, item: DeviceData) -> ProcessingResult:
        """Execute processor on a single dequeued item and record status."""
        now_utc = datetime.now(timezone.utc).isoformat()
        self._emit_audit_event("device.data.processing.started", {
            "device_id": item.device_id,
            "sequence_number": item.sequence_number,
            "kind": item.kind.value,
        })

        processor = self._processor_registry.get(item.kind)
        if processor is None:
            self._failed_count += 1
            self._record_device_stat(item.device_id, "failed")
            self._emit_audit_event("device.data.failed", {
                "device_id": item.device_id,
                "sequence_number": item.sequence_number,
                "error": "NO_REGISTERED_PROCESSOR",
            })
            return ProcessingResult(
                device_id=item.device_id,
                sequence_number=item.sequence_number,
                status=ProcessingStatus.FAILED,
                payload=MappingProxyType({}),
                timestamp_utc=now_utc,
                correlation_id=item.correlation_id,
                error_code="ERR_PROCESSOR_NOT_FOUND",
                error_message=f"No processor registered for data kind '{item.kind.value}'",
            )

        try:
            raw_result = processor(item)
            if not isinstance(raw_result, (dict, MappingProxyType)):
                raise DataPayloadValidationError(f"Processor output must be a mapping, got {type(raw_result).__name__}")

            # Deep freeze and canonicalize result
            stats = {"nodes": 0, "units": 0}
            frozen_result = deep_freeze_parameter(dict(raw_result), depth=0, seen_ids=set(), stats=stats)

            # Validate serialized byte length (D147: serialize MappingProxyType cleanly)
            serialized = json.dumps(
                frozen_result,
                default=lambda o: dict(o) if isinstance(o, MappingProxyType) else o,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            b_len = len(serialized.encode("utf-8"))
            if b_len > MAX_DATA_PAYLOAD_BYTES:
                raise DataPayloadValidationError(f"Processing result size ({b_len} bytes) exceeds maximum ({MAX_DATA_PAYLOAD_BYTES})")

            self._processed_count += 1
            self._record_device_stat(item.device_id, "processed")
            self._emit_audit_event("device.data.processed", {
                "device_id": item.device_id,
                "sequence_number": item.sequence_number,
                "kind": item.kind.value,
            })
            return ProcessingResult(
                device_id=item.device_id,
                sequence_number=item.sequence_number,
                status=ProcessingStatus.PROCESSED,
                payload=frozen_result,
                timestamp_utc=now_utc,
                correlation_id=item.correlation_id,
            )
        except Exception as e:
            self._failed_count += 1
            self._record_device_stat(item.device_id, "failed")
            redacted_err = self._secret_filter.redact(str(e))
            self._emit_audit_event("device.data.failed", {
                "device_id": item.device_id,
                "sequence_number": item.sequence_number,
                "error": redacted_err,
            })
            return ProcessingResult(
                device_id=item.device_id,
                sequence_number=item.sequence_number,
                status=ProcessingStatus.FAILED,
                payload=MappingProxyType({}),
                timestamp_utc=now_utc,
                correlation_id=item.correlation_id,
                error_code="ERR_PROCESSING_FAILURE",
                error_message=redacted_err,
            )

    def _record_shutdown_item_rejection(self, item: DeviceData, reason: str) -> None:
        """D145: Reject item during shutdown drain if device stopped/failed."""
        self._rejected_count += 1
        self._emit_audit_event("device.data.rejected", {
            "device_id": item.device_id,
            "sequence_number": item.sequence_number,
            "reason": reason,
        })

    def _emit_audit_event(self, topic: str, payload: Dict[str, Any]) -> None:
        """Emit audit event to event sink with failure isolation (D152)."""
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
                "Event sink emission failed",
                event="data_event_sink_error",
                extra={"topic": topic, "error": self._secret_filter.redact(str(e))},
            )
