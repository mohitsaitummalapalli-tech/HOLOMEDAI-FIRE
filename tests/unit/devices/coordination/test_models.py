"""Tests for M00.8 coordination models, data structures, and serialization."""

import math
from types import MappingProxyType
import pytest

from holomed.devices.coordination.exceptions import (
    DeviceCoordinationCapacityError,
    DeviceCoordinationError,
    DeviceCoordinationLifecycleError,
    DeviceCoordinationSerializationError,
    DeviceCoordinationValidationError,
)
from holomed.devices.coordination.models import (
    MAX_COORDINATION_ERROR_MESSAGE_LENGTH,
    MAX_COORDINATION_PAYLOAD_BYTES,
    MAX_DEVICE_SNAPSHOT_SIZE,
    DeviceObservation,
    DeviceSnapshot,
    DeviceTelemetryRecord,
    TelemetryStatus,
    convert_for_json_serialization,
    serialize_coordination_payload,
)
from holomed.devices.exceptions import (
    DeviceCapacityError,
    DeviceError,
    DeviceLifecycleError,
    DeviceValidationError,
)
from holomed.devices.models import DeviceState, DeviceType
from holomed.runtime.models import HealthStatus


def test_exception_hierarchy_multiple_inheritance():
    """Verify D163: All coordination exceptions inherit from DeviceCoordinationError and base errors."""
    assert issubclass(DeviceCoordinationError, DeviceError)
    assert issubclass(DeviceCoordinationValidationError, (DeviceCoordinationError, DeviceValidationError))
    assert issubclass(DeviceCoordinationLifecycleError, (DeviceCoordinationError, DeviceLifecycleError))
    assert issubclass(DeviceCoordinationCapacityError, (DeviceCoordinationError, DeviceCapacityError))


def test_device_observation_exact_8_fields():
    """Verify D158: DeviceObservation contains exactly 8 fields and omits capabilities."""
    expected_fields = {
        "device_id",
        "physical_id",
        "device_type",
        "state",
        "health_status",
        "health_message",
        "observed_at_utc",
        "epoch_id",
    }
    assert set(DeviceObservation.__dataclass_fields__.keys()) == expected_fields


def test_device_observation_immutability():
    """Verify ATK-14: DeviceObservation is a frozen dataclass rejecting mutation."""
    obs = DeviceObservation(
        device_id="cam_01",
        physical_id="usb://0",
        device_type=DeviceType.RGB_CAMERA,
        state=DeviceState.ACTIVE,
        health_status=HealthStatus.HEALTHY,
        health_message="OK",
        observed_at_utc="2026-09-01T00:00:00.000000Z",
        epoch_id=1,
    )
    with pytest.raises((AttributeError, TypeError)):
        obs.device_id = "cam_02"  # type: ignore


def test_device_observation_validation_rejections():
    """Verify field validation on DeviceObservation."""
    # Invalid device_id
    with pytest.raises(DeviceCoordinationValidationError):
        DeviceObservation("", "p0", DeviceType.RGB_CAMERA, DeviceState.ACTIVE, HealthStatus.HEALTHY, "OK", "ts", 1)

    # Oversized message (> 1024 chars)
    oversized = "a" * (MAX_COORDINATION_ERROR_MESSAGE_LENGTH + 1)
    with pytest.raises(DeviceCoordinationValidationError):
        DeviceObservation("d1", "p0", DeviceType.RGB_CAMERA, DeviceState.ACTIVE, HealthStatus.HEALTHY, oversized, "ts", 1)

    # Negative epoch_id
    with pytest.raises(DeviceCoordinationValidationError):
        DeviceObservation("d1", "p0", DeviceType.RGB_CAMERA, DeviceState.ACTIVE, HealthStatus.HEALTHY, "OK", "ts", -1)


def test_device_snapshot_canonical_ordering():
    """Verify ATK-29: Snapshot observations are ordered by (device_type.name ASC, device_id ASC)."""
    obs_b = DeviceObservation("b_cam", "p1", DeviceType.RGB_CAMERA, DeviceState.ACTIVE, HealthStatus.HEALTHY, "OK", "ts", 1)
    obs_a = DeviceObservation("a_cam", "p2", DeviceType.RGB_CAMERA, DeviceState.ACTIVE, HealthStatus.HEALTHY, "OK", "ts", 1)
    obs_z_imu = DeviceObservation("z_imu", "p3", DeviceType.IMU_SENSOR, DeviceState.ACTIVE, HealthStatus.HEALTHY, "OK", "ts", 1)
    obs_a_imu = DeviceObservation("a_imu", "p4", DeviceType.IMU_SENSOR, DeviceState.ACTIVE, HealthStatus.HEALTHY, "OK", "ts", 1)

    # Input unordered
    snapshot = DeviceSnapshot(epoch_id=1, captured_at_utc="ts", observations=(obs_b, obs_z_imu, obs_a, obs_a_imu))

    # IMU_SENSOR comes before RGB_CAMERA alphabetically
    # Within IMU_SENSOR: a_imu before z_imu
    # Within RGB_CAMERA: a_cam before b_cam
    ordered_ids = [o.device_id for o in snapshot.observations]
    assert ordered_ids == ["a_imu", "z_imu", "a_cam", "b_cam"]


def test_device_snapshot_capacity_bounds():
    """Verify ATK-01 & ATK-02: Snapshot succeeds at 256 devices, fails at 257."""
    obs_list = [
        DeviceObservation(f"dev_{i:03d}", f"p_{i}", DeviceType.RGB_CAMERA, DeviceState.ACTIVE, HealthStatus.HEALTHY, "OK", "ts", 1)
        for i in range(MAX_DEVICE_SNAPSHOT_SIZE)
    ]
    # Exactly 256 succeeds
    snapshot = DeviceSnapshot(epoch_id=1, captured_at_utc="ts", observations=tuple(obs_list))
    assert len(snapshot.observations) == 256

    # 257 fails
    obs_list.append(
        DeviceObservation("dev_256", "p_256", DeviceType.RGB_CAMERA, DeviceState.ACTIVE, HealthStatus.HEALTHY, "OK", "ts", 1)
    )
    with pytest.raises(DeviceCoordinationCapacityError):
        DeviceSnapshot(epoch_id=1, captured_at_utc="ts", observations=tuple(obs_list))


def test_telemetry_record_bounds():
    """Verify D164: TelemetryRecord checks bounds on integer counters."""
    rec = DeviceTelemetryRecord(
        device_id="dev_01",
        physical_id="usb://1",
        epoch_id=1,
        submitted_items=10,
        processed_items=8,
        completed_items=8,
        failed_items=1,
        rejected_items=1,
        last_sequence_number=5,
        last_observation_timestamp="ts",
    )
    assert rec.submitted_items == 10

    # Negative counter rejected
    with pytest.raises(DeviceCoordinationValidationError):
        DeviceTelemetryRecord("dev_01", "p1", 1, -1, 0, 0, 0, 0, 0, "ts")


def test_serialization_mapping_proxy_and_bounds():
    """Verify ATK-11, ATK-12, ATK-13, ATK-17, ATK-18, D162: Serialization constraints."""
    data = {
        "nested": MappingProxyType({"alpha": "test", "num": 1.0}),
        "negative_zero": -0.0,
        "tuple_items": (1, 2, 3),
    }

    # Converts cleanly to JSON without TypeError on MappingProxyType
    serialized = serialize_coordination_payload(data)
    assert '"alpha":"test"' in serialized
    assert '"negative_zero":0.0' in serialized  # ATK-13: -0.0 -> 0.0

    # ATK-11: NaN rejection
    with pytest.raises(DeviceCoordinationSerializationError):
        serialize_coordination_payload({"val": float("nan")})

    # ATK-12: Infinity rejection
    with pytest.raises(DeviceCoordinationSerializationError):
        serialize_coordination_payload({"val": float("inf")})

    # ATK-18: Oversized payload (> 65536 bytes)
    big_data = {"payload": "x" * (MAX_COORDINATION_PAYLOAD_BYTES + 10)}
    with pytest.raises(DeviceCoordinationSerializationError):
        serialize_coordination_payload(big_data)
