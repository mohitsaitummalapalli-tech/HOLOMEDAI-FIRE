"""HoloMed AI - Bounded telemetry accumulation and sequence tracking engine."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Optional, Union

from holomed.devices.coordination.exceptions import (
    DeviceCoordinationCapacityError,
    DeviceCoordinationDeviceNotFoundError,
    DeviceCoordinationEpochMismatchError,
    DeviceCoordinationIdentityError,
    DeviceCoordinationSequenceError,
    DeviceCoordinationValidationError,
)
from holomed.devices.coordination.models import (
    MAX_SAFE_INTEGER,
    MAX_TELEMETRY_DEVICES,
    DeviceTelemetryRecord,
    TelemetryStatus,
)
from holomed.devices.interfaces import IDevice
from holomed.devices.registry import DeviceRegistry


class TelemetryAccumulator:
    """Bounded, deterministic telemetry accumulator with sequence monotonicity enforcement."""

    def __init__(
        self,
        registry: DeviceRegistry,
        current_epoch_id: int,
        max_devices: int = MAX_TELEMETRY_DEVICES,
    ) -> None:
        self._registry = registry
        self._epoch_id = current_epoch_id
        self._max_devices = max_devices
        # device_id -> DeviceTelemetryRecord
        self._records: Dict[str, DeviceTelemetryRecord] = {}

    @property
    def epoch_id(self) -> int:
        return self._epoch_id

    def reset_epoch(self, new_epoch_id: int) -> None:
        """Reset telemetry accumulator for a new execution epoch."""
        if type(new_epoch_id) is not int or new_epoch_id < 0 or new_epoch_id > MAX_SAFE_INTEGER:
            raise DeviceCoordinationValidationError(f"Invalid epoch_id: {new_epoch_id!r}")
        self._epoch_id = new_epoch_id
        self._records.clear()

    def update(
        self,
        device_id: str,
        epoch_id: int,
        sequence_number: int,
        timestamp_utc: str,
        status: Union[TelemetryStatus, str, Any],
        physical_id: Optional[str] = None,
    ) -> DeviceTelemetryRecord:
        """Process and accumulate a validated telemetry update for a registered device."""
        # 1. Device Existence Check in Registry (Section 11: unknown devices MUST NOT allocate)
        if not self._registry.contains(device_id):
            raise DeviceCoordinationDeviceNotFoundError(f"Device '{device_id}' is not registered in DeviceRegistry")

        device: IDevice = self._registry.get(device_id)

        # 2. Epoch Validation
        if epoch_id != self._epoch_id:
            raise DeviceCoordinationEpochMismatchError(
                f"Epoch mismatch: update provides {epoch_id}, coordinator is in epoch {self._epoch_id}"
            )

        # 3. Device Identity Validation (Section 11, D160)
        if physical_id is not None and physical_id != device.physical_id:
            raise DeviceCoordinationIdentityError(
                f"Device physical_id mismatch for '{device_id}': expected '{device.physical_id}', got '{physical_id}'"
            )

        # 4. Sequence Number Validation (Section 11, D164)
        if type(sequence_number) is not int or sequence_number < 0 or sequence_number > MAX_SAFE_INTEGER:
            raise DeviceCoordinationValidationError(
                f"sequence_number must be int in range [0, {MAX_SAFE_INTEGER}], got {sequence_number!r}"
            )

        # 5. Check Device Re-registration (Section 23: physical_id changed -> fresh session)
        existing = self._records.get(device_id)
        if existing is not None and existing.physical_id != device.physical_id:
            # Device re-registered with new physical_id; retire prior session
            existing = None

        # 6. Monotonic Sequence Enforcement
        if existing is not None:
            if sequence_number == existing.last_sequence_number:
                raise DeviceCoordinationSequenceError(
                    f"Duplicate sequence number {sequence_number} rejected for device '{device_id}'"
                )
            if sequence_number < existing.last_sequence_number:
                raise DeviceCoordinationSequenceError(
                    f"Out-of-order sequence number {sequence_number} < {existing.last_sequence_number} rejected for device '{device_id}'"
                )

        # 7. Capacity Validation before allocation
        if existing is None and len(self._records) >= self._max_devices:
            raise DeviceCoordinationCapacityError(
                f"TelemetryAccumulator device capacity exceeded ({self._max_devices})"
            )

        # 8. Status Resolution (D159: map status to counter increment)
        norm_status = self._normalize_status(status)

        # 9. Compute Updated Counters
        sub_inc = 1 if norm_status == TelemetryStatus.SUBMITTED else 0
        proc_inc = 1 if norm_status == TelemetryStatus.PROCESSED else 0
        comp_inc = 1 if norm_status == TelemetryStatus.COMPLETED else 0
        fail_inc = 1 if norm_status == TelemetryStatus.FAILED else 0
        rej_inc = 1 if norm_status == TelemetryStatus.REJECTED else 0

        prev_sub = existing.submitted_items if existing else 0
        prev_proc = existing.processed_items if existing else 0
        prev_comp = existing.completed_items if existing else 0
        prev_fail = existing.failed_items if existing else 0
        prev_rej = existing.rejected_items if existing else 0

        # Enforce signed 64-bit integer overflow protection
        for prev, inc, name in (
            (prev_sub, sub_inc, "submitted_items"),
            (prev_proc, proc_inc, "processed_items"),
            (prev_comp, comp_inc, "completed_items"),
            (prev_fail, fail_inc, "failed_items"),
            (prev_rej, rej_inc, "rejected_items"),
        ):
            if prev + inc > MAX_SAFE_INTEGER:
                raise DeviceCoordinationCapacityError(f"Counter '{name}' overflowed maximum safe integer")

        record = DeviceTelemetryRecord(
            device_id=device_id,
            physical_id=device.physical_id,
            epoch_id=self._epoch_id,
            submitted_items=prev_sub + sub_inc,
            processed_items=prev_proc + proc_inc,
            completed_items=prev_comp + comp_inc,
            failed_items=prev_fail + fail_inc,
            rejected_items=prev_rej + rej_inc,
            last_sequence_number=sequence_number,
            last_observation_timestamp=timestamp_utc,
        )

        self._records[device_id] = record
        return record

    def _normalize_status(self, status: Any) -> TelemetryStatus:
        """Canonicalize string or enum status to TelemetryStatus."""
        if isinstance(status, TelemetryStatus):
            return status

        # Handle M00.7 ProcessingStatus or string conversion
        str_val = getattr(status, "value", str(status)).upper()

        if str_val in ("SUBMITTED", "ACCEPTED"):
            return TelemetryStatus.SUBMITTED
        if str_val == "PROCESSED":
            return TelemetryStatus.PROCESSED
        if str_val == "COMPLETED":
            return TelemetryStatus.COMPLETED
        if str_val == "FAILED":
            return TelemetryStatus.FAILED
        if str_val == "REJECTED":
            return TelemetryStatus.REJECTED

        raise DeviceCoordinationValidationError(f"Invalid telemetry status: {status!r}")

    def get(self, device_id: str) -> Optional[DeviceTelemetryRecord]:
        """Look up immutable telemetry record for a specific device."""
        return self._records.get(device_id)

    def all_records(self) -> Mapping[str, DeviceTelemetryRecord]:
        """Return deterministic, sorted immutable mapping of all device telemetry records."""
        sorted_items = {dev_id: self._records[dev_id] for dev_id in sorted(self._records.keys())}
        return MappingProxyType(sorted_items)

    def retire_session(self, device_id: str) -> None:
        """Explicitly retire telemetry tracking for a deregistered device."""
        self._records.pop(device_id, None)

    def clear(self) -> None:
        """Clear all active telemetry records."""
        self._records.clear()

    def __len__(self) -> int:
        return len(self._records)
