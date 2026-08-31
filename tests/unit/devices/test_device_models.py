"""Unit tests for device data models, descriptors, capabilities, and freezing."""

import pytest
from types import MappingProxyType

from holomed.devices.exceptions import DeviceValidationError
from holomed.devices.models import (
    CapabilityCategory,
    DeviceCapability,
    DeviceDescriptor,
    DeviceType,
    deep_freeze_parameter,
    MAX_CAPABILITY_CONTAINER_WIDTH,
    MAX_CAPABILITY_PARAMETER_DEPTH,
    MAX_CAPABILITY_STRING_LENGTH,
    MAX_CAPABILITY_TOTAL_NODES,
    MAX_CAPABILITY_TOTAL_UNITS,
    MAX_DEVICE_ID_LENGTH,
    MAX_METADATA_ENTRIES,
    MAX_METADATA_TOTAL_BYTES,
)


def test_valid_device_descriptor_creation() -> None:
    cap = DeviceCapability(
        capability_id="camera.stream",
        category=CapabilityCategory.STREAMING,
        parameters={"fps": 60, "format": "rgb24"},
    )
    desc = DeviceDescriptor(
        device_id="cam_front",
        physical_id="USB:1/2/3",
        device_type=DeviceType.RGB_CAMERA,
        capabilities=(cap,),
        metadata={"vendor": "Acme", "revision": "1.0"},
    )

    assert desc.device_id == "cam_front"
    assert desc.physical_id == "USB:1/2/3"
    assert desc.device_type == DeviceType.RGB_CAMERA
    assert len(desc.capabilities) == 1
    assert desc.metadata["vendor"] == "Acme"
    assert isinstance(desc.metadata, MappingProxyType)


def test_device_id_validation_rules() -> None:
    # Invalid characters
    with pytest.raises(DeviceValidationError, match="Invalid device_id"):
        DeviceDescriptor(
            device_id="cam front",
            physical_id="USB:1",
            device_type=DeviceType.RGB_CAMERA,
            capabilities=(),
            metadata={},
        )

    # Empty
    with pytest.raises(DeviceValidationError, match="Invalid device_id"):
        DeviceDescriptor(
            device_id="",
            physical_id="USB:1",
            device_type=DeviceType.RGB_CAMERA,
            capabilities=(),
            metadata={},
        )

    # Exceeding MAX_DEVICE_ID_LENGTH (64)
    with pytest.raises(DeviceValidationError, match="Invalid device_id"):
        DeviceDescriptor(
            device_id="a" * (MAX_DEVICE_ID_LENGTH + 1),
            physical_id="USB:1",
            device_type=DeviceType.RGB_CAMERA,
            capabilities=(),
            metadata={},
        )

    # Exact max length is valid
    desc = DeviceDescriptor(
        device_id="a" * MAX_DEVICE_ID_LENGTH,
        physical_id="USB:1",
        device_type=DeviceType.RGB_CAMERA,
        capabilities=(),
        metadata={},
    )
    assert len(desc.device_id) == MAX_DEVICE_ID_LENGTH


def test_metadata_entries_and_bytes_bounds() -> None:
    # Entries limit
    too_many_entries = {f"k_{i}": f"v_{i}" for i in range(MAX_METADATA_ENTRIES + 1)}
    with pytest.raises(DeviceValidationError, match="Metadata entries count"):
        DeviceDescriptor(
            device_id="sensor_1",
            physical_id="I2C:0x42",
            device_type=DeviceType.IMU_SENSOR,
            capabilities=(),
            metadata=too_many_entries,
        )

    # Total bytes limit
    huge_metadata = {"key": "x" * (MAX_METADATA_TOTAL_BYTES + 1)}
    with pytest.raises(DeviceValidationError, match="Metadata total bytes"):
        DeviceDescriptor(
            device_id="sensor_1",
            physical_id="I2C:0x42",
            device_type=DeviceType.IMU_SENSOR,
            capabilities=(),
            metadata=huge_metadata,
        )


def test_capability_canonical_sorting_and_uniqueness() -> None:
    cap_b = DeviceCapability("b.stream", CapabilityCategory.STREAMING, {})
    cap_a = DeviceCapability("a.control", CapabilityCategory.CONTROL, {})

    desc = DeviceDescriptor(
        device_id="tool_1",
        physical_id="PCI:1",
        device_type=DeviceType.SURGICAL_TOOL,
        capabilities=(cap_b, cap_a),
        metadata={},
    )
    # Sorted canonically by capability_id ASC
    assert desc.capabilities[0].capability_id == "a.control"
    assert desc.capabilities[1].capability_id == "b.stream"

    # Duplicate capability_id rejected
    with pytest.raises(DeviceValidationError, match="Duplicate capability_id"):
        DeviceDescriptor(
            device_id="tool_1",
            physical_id="PCI:1",
            device_type=DeviceType.SURGICAL_TOOL,
            capabilities=(cap_a, cap_a),
            metadata={},
        )


def test_parameter_freezing_bool_precedence_over_int() -> None:
    # In Python isinstance(True, int) is True; verify bool is treated as bool with 4 units
    stats = {"nodes": 0, "units": 0}
    frozen = deep_freeze_parameter({"flag": True}, stats=stats)
    assert frozen["flag"] is True
    assert type(frozen["flag"]) is bool
    # dict: key 'flag' (4 bytes) + 8 + cost(True) (4 bytes) = 16 units
    assert stats["units"] == 16


def test_parameter_freezing_rejects_heterogeneous_keys_without_type_error() -> None:
    with pytest.raises(DeviceValidationError, match="Parameter key must be exact str"):
        deep_freeze_parameter({"key": 1, 100: "value"})


def test_parameter_freezing_rejects_nan_and_infinity() -> None:
    with pytest.raises(DeviceValidationError, match="NaN and Infinite"):
        deep_freeze_parameter({"bad_float": float("nan")})

    with pytest.raises(DeviceValidationError, match="NaN and Infinite"):
        deep_freeze_parameter({"bad_float": float("inf")})

    with pytest.raises(DeviceValidationError, match="NaN and Infinite"):
        deep_freeze_parameter({"bad_float": float("-inf")})


def test_parameter_freezing_negative_zero_normalized() -> None:
    frozen = deep_freeze_parameter({"zero": -0.0})
    assert frozen["zero"] == 0.0
    import math
    assert math.copysign(1.0, frozen["zero"]) == 1.0


def test_parameter_freezing_nfc_normalization() -> None:
    import unicodedata
    nfd_str = unicodedata.normalize("NFD", "héllo")
    frozen = deep_freeze_parameter({"text": nfd_str})
    assert unicodedata.is_normalized("NFC", frozen["text"])


def test_parameter_freezing_cycle_detection() -> None:
    cyclic_dict: dict = {}
    cyclic_dict["self"] = cyclic_dict
    with pytest.raises(DeviceValidationError, match="Cyclic reference detected"):
        deep_freeze_parameter(cyclic_dict)


def test_parameter_freezing_depth_limit() -> None:
    nested: dict = {"val": 1}
    for _ in range(MAX_CAPABILITY_PARAMETER_DEPTH + 1):
        nested = {"sub": nested}

    with pytest.raises(DeviceValidationError, match="nesting depth exceeds"):
        deep_freeze_parameter(nested)


def test_parameter_freezing_container_width_limit() -> None:
    wide_dict = {f"k_{i}": i for i in range(MAX_CAPABILITY_CONTAINER_WIDTH + 1)}
    with pytest.raises(DeviceValidationError, match="container width"):
        deep_freeze_parameter(wide_dict)


def test_parameter_freezing_sets_restricted_to_primitives() -> None:
    # Set with nested frozenset is rejected
    with pytest.raises(DeviceValidationError, match="Nested container or complex type"):
        deep_freeze_parameter({"items": {frozenset({1, 2})}})

    # Set with invalid complex object
    class CustomComplex:
        pass

    with pytest.raises(DeviceValidationError, match="Nested container or complex type"):
        deep_freeze_parameter({"items": {CustomComplex()}})

    # Set with valid primitives is accepted
    frozen = deep_freeze_parameter({"items": {"a", 1, True, None}})
    assert isinstance(frozen["items"], frozenset)
