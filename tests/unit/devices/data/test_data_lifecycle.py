"""Unit tests for M00.7 DeviceDataProcessor lifecycle, shutdown drain, and health."""

import pytest

from holomed.devices.data.exceptions import DeviceDataLifecycleError
from holomed.devices.data.models import (
    DeviceData,
    DeviceDataKind,
    ProcessingStatus,
)
from holomed.devices.data.processor import DeviceDataProcessor
from holomed.devices.interfaces import RegistryAuthorityToken
from holomed.devices.models import DeviceState, DeviceType
from holomed.devices.registry import DeviceRegistry
from holomed.devices.simulated import SimulatedDevice
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState
from tests.unit.devices.conftest import make_test_context


def make_item(device_id: str, seq: int) -> DeviceData:
    return DeviceData(
        device_id=device_id,
        physical_id=f"USB:{device_id}",
        device_type=DeviceType.RGB_CAMERA,
        kind=DeviceDataKind.OBSERVATION,
        sequence_number=seq,
        timestamp_utc="2026-08-31T12:00:00Z",
        payload={"val": seq},
    )


def test_lifecycle_transitions_and_double_start() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    processor = DeviceDataProcessor(registry=registry)

    ctx = make_test_context(epoch_id=1)
    processor.initialize(ctx)
    assert processor._state == ServiceState.INITIALIZED

    processor.start()
    assert processor._state == ServiceState.STARTED

    # Double start raises DeviceDataLifecycleError
    with pytest.raises(DeviceDataLifecycleError, match="already STARTED"):
        processor.start()

    processor.stop()
    assert processor._state == ServiceState.STOPPED


def test_shutdown_drain_and_stopped_devices_handled(make_context=None) -> None:
    """D145: Verify queued items whose devices remain ACTIVE are processed, and stopped devices are rejected."""
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)

    dev_active = SimulatedDevice("cam_active", "USB:cam_active", device_type=DeviceType.RGB_CAMERA)
    dev_stopped = SimulatedDevice("cam_stopped", "USB:cam_stopped", device_type=DeviceType.RGB_CAMERA)
    registry.register(dev_active, token)
    registry.register(dev_stopped, token)
    dev_active._state = DeviceState.ACTIVE
    dev_stopped._state = DeviceState.ACTIVE

    processor = DeviceDataProcessor(registry=registry)
    ctx = make_test_context(epoch_id=1)
    processor.initialize(ctx)
    processor.start()

    processed_items = []
    processor.register_processor(
        DeviceDataKind.OBSERVATION,
        lambda d: processed_items.append(d.device_id) or {"ok": True},
    )

    # Submit item for active device and stopped device
    processor.submit(make_item("cam_active", 1))
    processor.submit(make_item("cam_stopped", 1))

    assert processor.get_queue_depth() == 2

    # Stop cam_stopped device prior to processor shutdown
    dev_stopped._state = DeviceState.STOPPED

    # Execute processor stop() -> executes DRAIN_AND_COMPLETE
    processor.stop()

    assert processor._state == ServiceState.STOPPED
    assert processor.get_queue_depth() == 0
    # Active item was processed
    assert "cam_active" in processed_items
    # Stopped item was rejected during drain without failing stop()
    assert "cam_stopped" not in processed_items
    assert processor.get_processing_stats()["rejected"] == 1


def test_health_evaluation() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    processor = DeviceDataProcessor(registry=registry)

    # UNINITIALIZED
    h1 = processor.health()
    assert h1.status == HealthStatus.FAILED

    ctx = make_test_context(epoch_id=1)
    processor.initialize(ctx)
    # INITIALIZED
    h2 = processor.health()
    assert h2.status == HealthStatus.HEALTHY

    processor.start()
    # STARTED
    h3 = processor.health()
    assert h3.status == HealthStatus.HEALTHY
