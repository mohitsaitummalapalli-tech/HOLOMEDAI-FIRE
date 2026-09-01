"""HoloMed AI - DeviceCrossPlaneBridge for cross-plane event synchronization."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Set

from holomed.devices.coordination.coordinator import DeviceCoordinationService
from holomed.devices.coordination.models import TelemetryStatus
from holomed.devices.interfaces import IDevice, IDeviceEventSink
from holomed.devices.orchestration.exceptions import (
    DeviceOrchestrationBridgeError,
    DeviceOrchestrationCapacityError,
    DeviceOrchestrationLifecycleError,
    DeviceOrchestrationValidationError,
)
from holomed.devices.orchestration.models import (
    MAX_SAFE_INTEGER,
    BridgeStatistics,
)
from holomed.devices.registry import DeviceRegistry
from holomed.protocol.models import MessageEnvelope, MessageType
from holomed.runtime.logging import SecretFilter, StructuredLogger


WHITELISTED_BRIDGE_TOPICS: frozenset[str] = frozenset({
    "device.data.processed",
    "device.data.failed",
    "device.data.rejected",
    "device.registered",
    "device.deregistered",
})


class DeviceCrossPlaneBridge(IDeviceEventSink):
    """Deterministic, non-reentrant cross-plane event adapter connecting data & manager to coordination."""

    def __init__(
        self,
        registry: DeviceRegistry,
        coordination: DeviceCoordinationService,
        current_epoch_id: int,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._registry = registry
        self._coordination = coordination
        self._epoch_id = current_epoch_id
        self._secret_filter = secret_filter or SecretFilter()
        self._logger = logger or StructuredLogger("holomed.devices.orchestration.bridge")

        # Recursion & Reentrancy Guard (D166)
        self._in_bridge_dispatch: bool = False

        # Counters (D174)
        self._events_bridged: int = 0
        self._telemetry_updates_emitted: int = 0
        self._identity_mismatches_rejected: int = 0
        self._stale_events_rejected: int = 0
        self._bridge_errors: int = 0

    @property
    def epoch_id(self) -> int:
        return self._epoch_id

    def set_epoch_id(self, new_epoch_id: int) -> None:
        """Update active epoch ID."""
        if type(new_epoch_id) is not int or new_epoch_id < 0 or new_epoch_id > MAX_SAFE_INTEGER:
            raise DeviceOrchestrationValidationError(f"Invalid epoch_id: {new_epoch_id!r}")
        self._epoch_id = new_epoch_id

    def emit(self, envelope: MessageEnvelope) -> None:
        """Process an emitted event envelope as an IDeviceEventSink."""
        self.handle_event(envelope)

    def handle_event(self, envelope: MessageEnvelope) -> bool:
        """Synchronously translate a cross-plane event envelope into coordination actions."""
        # 1. Enforce Non-Reentrant Guard (D166)
        if self._in_bridge_dispatch:
            return False

        # 2. Filter Whitelisted Topics
        topic = envelope.message_name
        if topic not in WHITELISTED_BRIDGE_TOPICS:
            return False

        # Strictly reject any coordination event (D166)
        if topic.startswith("device.coordination."):
            return False

        self._in_bridge_dispatch = True
        try:
            success = False
            if topic in ("device.data.processed", "device.data.failed", "device.data.rejected"):
                success = self._handle_data_event(envelope)
            elif topic == "device.registered":
                success = self._handle_device_registered(envelope)
            elif topic == "device.deregistered":
                success = self._handle_device_deregistered(envelope)

            if success:
                self._increment_counter("events_bridged")
            return success
        except Exception as e:
            self._increment_counter("bridge_errors")
            self._logger.warning(
                f"Cross-plane bridge failed to handle '{topic}': {self._secret_filter.redact(str(e))}",
                event="bridge_error",
            )
            return False
        finally:
            self._in_bridge_dispatch = False

    def _handle_data_event(self, envelope: MessageEnvelope) -> bool:
        """Process data plane event and update coordination telemetry (D168, D169, D176)."""
        payload = envelope.payload
        device_id = payload.get("device_id")
        sequence_number = payload.get("sequence_number")

        if type(device_id) is not str or not device_id:
            self._increment_counter("bridge_errors")
            return False

        if type(sequence_number) is not int or sequence_number < 0 or sequence_number > MAX_SAFE_INTEGER:
            self._increment_counter("bridge_errors")
            return False

        # Check device registration existence
        if not self._registry.contains(device_id):
            self._increment_counter("stale_events_rejected")
            return False

        registered_device: IDevice = self._registry.get(device_id)

        # Validate physical_id if provided in payload (D169)
        payload_phys_id = payload.get("physical_id")
        if payload_phys_id is not None:
            if payload_phys_id != registered_device.physical_id:
                self._increment_counter("identity_mismatches_rejected")
                return False
            resolved_phys_id = payload_phys_id
        else:
            # Fallback resolution from registry (D176)
            resolved_phys_id = registered_device.physical_id

        # Resolve status
        topic = envelope.message_name
        if topic == "device.data.processed":
            status = TelemetryStatus.PROCESSED
        elif topic == "device.data.failed":
            status = TelemetryStatus.FAILED
        elif topic == "device.data.rejected":
            status = TelemetryStatus.REJECTED
        else:
            return False

        # Synchronously invoke coordination update_telemetry
        try:
            self._coordination.update_telemetry(
                device_id=device_id,
                sequence_number=sequence_number,
                timestamp_utc=envelope.timestamp_utc,
                status=status,
                physical_id=resolved_phys_id,
                epoch_id=self._epoch_id,
            )
            self._increment_counter("telemetry_updates_emitted")
            return True
        except Exception as e:
            self._increment_counter("bridge_errors")
            self._logger.warning(
                f"Telemetry update invocation failed for '{device_id}': {self._secret_filter.redact(str(e))}",
                event="telemetry_update_error",
            )
            return False

    def _handle_device_registered(self, envelope: MessageEnvelope) -> bool:
        """Trigger coordination snapshot update upon device registration."""
        try:
            self._coordination.capture_snapshot()
            return True
        except Exception:
            return False

    def _handle_device_deregistered(self, envelope: MessageEnvelope) -> bool:
        """Retire telemetry tracking and trigger snapshot update upon device deregistration."""
        device_id = envelope.payload.get("device_id")
        if type(device_id) is str and device_id:
            # Retire session if available
            if hasattr(self._coordination, "retire_session"):
                self._coordination.retire_session(device_id)
            elif hasattr(self._coordination, "_telemetry") and self._coordination._telemetry is not None:
                self._coordination._telemetry.retire_session(device_id)

        try:
            self._coordination.capture_snapshot()
            return True
        except Exception:
            return False

    def _increment_counter(self, name: str) -> None:
        """Increment named counter enforcing signed 64-bit bounds (D174)."""
        current_val = getattr(self, f"_{name}")
        if current_val + 1 > MAX_SAFE_INTEGER:
            raise DeviceOrchestrationCapacityError(f"Counter '{name}' exceeded MAX_SAFE_INTEGER")
        setattr(self, f"_{name}", current_val + 1)

    def get_statistics(self) -> BridgeStatistics:
        """Return immutable snapshot of current bridge statistics."""
        return BridgeStatistics(
            events_bridged=self._events_bridged,
            telemetry_updates_emitted=self._telemetry_updates_emitted,
            identity_mismatches_rejected=self._identity_mismatches_rejected,
            stale_events_rejected=self._stale_events_rejected,
            bridge_errors=self._bridge_errors,
        )

    def clear(self) -> None:
        """Reset bridge counters."""
        self._events_bridged = 0
        self._telemetry_updates_emitted = 0
        self._identity_mismatches_rejected = 0
        self._stale_events_rejected = 0
        self._bridge_errors = 0
