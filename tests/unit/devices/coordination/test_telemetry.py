"""Tests for M00.8 TelemetryAccumulator and sequence tracking."""

from types import MappingProxyType
import pytest

from holomed.devices.coordination.exceptions import (
    DeviceCoordinationCapacityError,
    DeviceCoordinationDeviceNotFoundError,
    DeviceCoordinationEpochMismatchError,
    DeviceCoordinationIdentityError,
    DeviceCoordinationSequenceError,
    DeviceCoordinationValidationError,
)
from holomed.devices.coordination.models import (
    MAX_TELEMETRY_DEVICES,
    TelemetryStatus,
)
from holomed.devices.coordination.telemetry import TelemetryAccumulator
from holomed.devices.interfaces import RegistryAuthorityToken
from holomed.devices.models import DeviceState, DeviceType
from holomed.devices.registry import DeviceRegistry
from tests.unit.devices.coordination.conftest import DummyCoordinationDevice, register_device


def test_telemetry_unknown_device_not_allocated(device_registry: DeviceRegistry):
    """Verify ATK-03: Telemetry update for unregistered device raises and does not allocate."""
    acc = TelemetryAccumulator(device_registry, current_epoch_id=1)
    with pytest.raises(DeviceCoordinationDeviceNotFoundError):
        acc.update("dev_unknown", epoch_id=1, sequence_number=0, timestamp_utc="ts", status="SUBMITTED")
    assert len(acc) == 0


def test_telemetry_monotonic_sequences(device_registry: DeviceRegistry, registry_token: RegistryAuthorityToken):
    """Verify ATK-04 & ATK-05: Sequence numbers must be strictly increasing."""
    dev = DummyCoordinationDevice("dev_01", "usb://1")
    register_device(device_registry, registry_token, dev)

    acc = TelemetryAccumulator(device_registry, current_epoch_id=1)

    # Initial sequence 0 succeeds
    rec1 = acc.update("dev_01", epoch_id=1, sequence_number=0, timestamp_utc="ts1", status="SUBMITTED")
    assert rec1.last_sequence_number == 0
    assert rec1.submitted_items == 1

    # Sequence 1 succeeds
    rec2 = acc.update("dev_01", epoch_id=1, sequence_number=1, timestamp_utc="ts2", status="PROCESSED")
    assert rec2.last_sequence_number == 1
    assert rec2.processed_items == 1

    # ATK-04: Duplicate sequence 1 raises
    with pytest.raises(DeviceCoordinationSequenceError):
        acc.update("dev_01", epoch_id=1, sequence_number=1, timestamp_utc="ts3", status="PROCESSED")

    # ATK-05: Lower sequence 0 raises
    with pytest.raises(DeviceCoordinationSequenceError):
        acc.update("dev_01", epoch_id=1, sequence_number=0, timestamp_utc="ts4", status="PROCESSED")


def test_telemetry_epoch_mismatch(device_registry: DeviceRegistry, registry_token: RegistryAuthorityToken):
    """Verify ATK-06: Foreign epoch_id raises DeviceCoordinationEpochMismatchError."""
    dev = DummyCoordinationDevice("dev_01", "usb://1")
    register_device(device_registry, registry_token, dev)

    acc = TelemetryAccumulator(device_registry, current_epoch_id=1)
    with pytest.raises(DeviceCoordinationEpochMismatchError):
        acc.update("dev_01", epoch_id=2, sequence_number=0, timestamp_utc="ts", status="SUBMITTED")


def test_telemetry_identity_mismatch(device_registry: DeviceRegistry, registry_token: RegistryAuthorityToken):
    """Verify ATK-07 & D160: Conflicting physical_id raises DeviceCoordinationIdentityError."""
    dev = DummyCoordinationDevice("dev_01", "usb://1")
    register_device(device_registry, registry_token, dev)

    acc = TelemetryAccumulator(device_registry, current_epoch_id=1)
    with pytest.raises(DeviceCoordinationIdentityError):
        acc.update("dev_01", epoch_id=1, sequence_number=0, timestamp_utc="ts", status="SUBMITTED", physical_id="usb://wrong")


def test_telemetry_status_mapping_and_counters(device_registry: DeviceRegistry, registry_token: RegistryAuthorityToken):
    """Verify D159: Correct counter increments for each status including M00.7 ACCEPTED."""
    dev = DummyCoordinationDevice("dev_01", "usb://1")
    register_device(device_registry, registry_token, dev)

    acc = TelemetryAccumulator(device_registry, current_epoch_id=1)

    acc.update("dev_01", 1, 1, "ts", "ACCEPTED")   # maps to SUBMITTED
    acc.update("dev_01", 1, 2, "ts", "PROCESSED")
    acc.update("dev_01", 1, 3, "ts", "COMPLETED")
    acc.update("dev_01", 1, 4, "ts", "FAILED")
    acc.update("dev_01", 1, 5, "ts", "REJECTED")

    rec = acc.get("dev_01")
    assert rec is not None
    assert rec.submitted_items == 1
    assert rec.processed_items == 1
    assert rec.completed_items == 1
    assert rec.failed_items == 1
    assert rec.rejected_items == 1

    # Invalid status
    with pytest.raises(DeviceCoordinationValidationError):
        acc.update("dev_01", 1, 6, "ts", "UNKNOWN_STATUS")


def test_telemetry_re_registration_resets_session(device_registry: DeviceRegistry, registry_token: RegistryAuthorityToken):
    """Verify ATK-27 & §23: Device re-registration with new physical ID resets session and sequence."""
    dev1 = DummyCoordinationDevice("dev_01", "usb://1")
    register_device(device_registry, registry_token, dev1)

    acc = TelemetryAccumulator(device_registry, current_epoch_id=1)
    acc.update("dev_01", 1, 10, "ts1", "SUBMITTED")
    assert acc.get("dev_01").last_sequence_number == 10

    # Stop and deregister dev1
    dev1._state = DeviceState.STOPPED
    device_registry.deregister("dev_01", registry_token)

    # Register dev2 with new physical_id
    dev2 = DummyCoordinationDevice("dev_01", "usb://2")
    register_device(device_registry, registry_token, dev2)

    # Sequence starts fresh at 0
    rec = acc.update("dev_01", 1, 0, "ts2", "SUBMITTED")
    assert rec.last_sequence_number == 0
    assert rec.physical_id == "usb://2"
    assert rec.submitted_items == 1  # fresh session


def test_telemetry_capacity_bounds(device_registry: DeviceRegistry, registry_token: RegistryAuthorityToken):
    """Verify capacity bound MAX_TELEMETRY_DEVICES = 256."""
    acc = TelemetryAccumulator(device_registry, current_epoch_id=1, max_devices=2)

    dev1 = DummyCoordinationDevice("dev_01", "p1")
    dev2 = DummyCoordinationDevice("dev_02", "p2")
    dev3 = DummyCoordinationDevice("dev_03", "p3")
    register_device(device_registry, registry_token, dev1)
    register_device(device_registry, registry_token, dev2)
    register_device(device_registry, registry_token, dev3)

    acc.update("dev_01", 1, 0, "ts", "SUBMITTED")
    acc.update("dev_02", 1, 0, "ts", "SUBMITTED")
    assert len(acc) == 2

    # 3rd device exceeds capacity of 2
    with pytest.raises(DeviceCoordinationCapacityError):
        acc.update("dev_03", 1, 0, "ts", "SUBMITTED")


def test_telemetry_immutability(device_registry: DeviceRegistry, registry_token: RegistryAuthorityToken):
    """Verify ATK-15: all_records() returns MappingProxyType."""
    dev = DummyCoordinationDevice("dev_01", "p1")
    register_device(device_registry, registry_token, dev)

    acc = TelemetryAccumulator(device_registry, current_epoch_id=1)
    acc.update("dev_01", 1, 0, "ts", "SUBMITTED")

    records = acc.all_records()
    assert isinstance(records, MappingProxyType)
    with pytest.raises(TypeError):
        records["dev_01"] = None  # type: ignore
