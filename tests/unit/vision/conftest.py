"""Shared fixtures for M01 Spatial Vision test suite."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid
import zlib
import pytest

from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel
from holomed.devices.interfaces import IDevice
from holomed.devices.manager import DeviceManager
from holomed.devices.models import DeviceHealth, DeviceState, DeviceType
from holomed.devices.registry import DeviceRegistry
from holomed.runtime.context import RuntimeContext
from holomed.runtime.models import HealthStatus
from holomed.vision.models import FrameDescriptor, PixelFormat


class DummyCameraDevice(IDevice):
    """Test camera device implementing IDevice."""

    def __init__(self, device_id: str, physical_id: str) -> None:
        self._device_id = device_id
        self._physical_id = physical_id
        self._state = DeviceState.UNREGISTERED

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def physical_id(self) -> str:
        return self._physical_id

    @property
    def device_type(self) -> DeviceType:
        return DeviceType.RGB_CAMERA

    @property
    def capabilities(self) -> frozenset[DeviceCapability]:
        return frozenset()

    @property
    def state(self) -> DeviceState:
        return self._state

    def initialize(self) -> None:
        self._state = DeviceState.INITIALIZED

    def start(self) -> None:
        self._state = DeviceState.ACTIVE

    def stop(self) -> None:
        self._state = DeviceState.STOPPED

    def health(self) -> DeviceHealth:
        return DeviceHealth(
            device_id=self._device_id,
            status=HealthStatus.HEALTHY,
            message="Camera healthy",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )


@pytest.fixture
def test_runtime_context() -> RuntimeContext:
    app_config = AppConfig(
        app_name="HoloMed AI",
        environment=EnvironmentProfile.TESTING,
        host="127.0.0.1",
        port=8080,
        log_level=LogLevel.DEBUG,
        gemini_api_key="test_key",
        protocol_version="1.0",
    )
    return RuntimeContext(app_config=app_config, epoch_id=1)


@pytest.fixture
def device_registry_with_camera(test_runtime_context: RuntimeContext) -> tuple[DeviceRegistry, DeviceManager]:
    dm = DeviceManager()
    dm.initialize(test_runtime_context)
    dm.start()

    reg = getattr(dm, "registry", None) or getattr(dm, "_registry", None)
    token = getattr(dm, "registry_token", None) or getattr(dm, "_registry_token", None)

    cam = DummyCameraDevice("cam_01", "usb://port1")
    reg.register(cam, token)
    cam._state = DeviceState.ACTIVE

    return reg, dm


def make_test_frame(
    width: int = 160,
    height: int = 120,
    pixel_format: PixelFormat = PixelFormat.GRAY8,
    sequence_number: int = 1,
    epoch_id: int = 1,
    device_id: str = "cam_01",
    physical_id: str = "usb://port1",
) -> tuple[FrameDescriptor, bytes]:
    """Helper to synthesize valid test frame bytes and matching FrameDescriptor."""
    bpp = pixel_format.bytes_per_pixel
    stride = width * bpp
    total_bytes = stride * height

    # Synthesize test pattern with central brightness peak
    raw_data = bytearray(total_bytes)
    center_idx = (height // 2) * stride + (width // 2) * bpp
    if center_idx < total_bytes:
        raw_data[center_idx] = 255
        if bpp > 1:
            raw_data[center_idx + 1] = 255
            raw_data[center_idx + 2] = 255

    crc = zlib.crc32(raw_data)
    desc = FrameDescriptor(
        frame_id=str(uuid.uuid4()),
        device_id=device_id,
        physical_id=physical_id,
        sequence_number=sequence_number,
        timestamp_utc="2026-09-01T08:00:00.000000Z",
        width=width,
        height=height,
        pixel_format=pixel_format,
        stride_bytes=stride,
        total_bytes=total_bytes,
        checksum_crc32=crc,
        epoch_id=epoch_id,
        buffer_handle_id="vision.slot.0",
    )
    return desc, bytes(raw_data)
