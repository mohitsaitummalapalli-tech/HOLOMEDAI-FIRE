"""HoloMed AI - M49.1 Canonical Physical Command Context Tests."""

import copy
import dataclasses
import pickle
import pytest

from holomed.devices.models import PhysicalCommand
from holomed.devices.exceptions import DeviceValidationError

def test_physical_command_successful_construction():
    """Test valid construction of PhysicalCommand."""
    cmd = PhysicalCommand(
        endpoint_id="ep_1",
        session_id="sess_123",
        lifecycle_generation=5,
        endpoint_lease_generation=10,
        execution_id="exec_abc",
        capability_scope=frozenset(["cap_actuate", "cap_monitor"]),
        command_sequence=42,
        operation="actuate_motor",
        parameters={"speed": 50, "direction": "cw"}
    )

    assert cmd.endpoint_id == "ep_1"
    assert cmd.session_id == "sess_123"
    assert cmd.lifecycle_generation == 5
    assert cmd.endpoint_lease_generation == 10
    assert cmd.execution_id == "exec_abc"
    assert cmd.capability_scope == frozenset(["cap_actuate", "cap_monitor"])
    assert cmd.command_sequence == 42
    assert cmd.operation == "actuate_motor"
    assert cmd.parameters["speed"] == 50
    assert cmd.parameters["direction"] == "cw"

def test_physical_command_immutability():
    """Test that PhysicalCommand is strictly immutable."""
    cmd = PhysicalCommand(
        endpoint_id="ep_1",
        session_id="sess_123",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id="exec_abc",
        capability_scope=frozenset(["cap_1"]),
        command_sequence=1,
        operation="actuate",
        parameters={"a": 1}
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        cmd.endpoint_id = "ep_2"

    with pytest.raises(dataclasses.FrozenInstanceError):
        cmd.session_id = "sess_2"

    # Parameters must be deep-frozen
    with pytest.raises(TypeError):
        cmd.parameters["a"] = 2

def test_physical_command_alias_mutation():
    """Test that mutating an alias passed to construction does not mutate the command."""
    original_params = {"speed": 50, "nested": {"list": [1, 2, 3]}}
    cmd = PhysicalCommand(
        endpoint_id="ep_1",
        session_id="sess_123",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id="exec_abc",
        capability_scope=frozenset(["cap_1"]),
        command_sequence=1,
        operation="actuate",
        parameters=original_params
    )

    # Mutate the alias
    original_params["speed"] = 100
    original_params["nested"]["list"].append(4)
    original_params["new_key"] = "hacked"

    # The command should remain unchanged and deep frozen
    assert cmd.parameters["speed"] == 50
    assert len(cmd.parameters["nested"]["list"]) == 3
    assert "new_key" not in cmd.parameters

def test_physical_command_required_fields_enforcement():
    """Test that missing or empty required fields raise validation errors."""
    valid_kwargs = {
        "endpoint_id": "ep_1",
        "session_id": "sess_123",
        "lifecycle_generation": 1,
        "endpoint_lease_generation": 1,
        "execution_id": "exec_abc",
        "capability_scope": frozenset(["cap_1"]),
        "command_sequence": 1,
        "operation": "actuate",
        "parameters": {}
    }

    # Missing empty strings
    for field in ["endpoint_id", "session_id", "execution_id", "operation"]:
        kwargs = valid_kwargs.copy()
        kwargs[field] = ""
        with pytest.raises(DeviceValidationError, match="must be a non-empty string"):
            PhysicalCommand(**kwargs)

    # Invalid ints
    for field in ["lifecycle_generation", "endpoint_lease_generation", "command_sequence"]:
        kwargs = valid_kwargs.copy()
        kwargs[field] = 0
        with pytest.raises(DeviceValidationError, match="must be an int >= 1"):
            PhysicalCommand(**kwargs)

    # Invalid capability scope
    kwargs = valid_kwargs.copy()
    kwargs["capability_scope"] = set(["cap_1"]) # not frozenset
    with pytest.raises(DeviceValidationError, match="must be a frozenset"):
        PhysicalCommand(**kwargs)

def test_physical_command_type_shape_validation():
    """Test that correct types and shapes are strictly enforced."""
    valid_kwargs = {
        "endpoint_id": "ep_1",
        "session_id": "sess_123",
        "lifecycle_generation": 1,
        "endpoint_lease_generation": 1,
        "execution_id": "exec_abc",
        "capability_scope": frozenset(["cap_1"]),
        "command_sequence": 1,
        "operation": "actuate",
        "parameters": {}
    }

    kwargs = valid_kwargs.copy()
    kwargs["capability_scope"] = frozenset([123])
    with pytest.raises(DeviceValidationError, match="must contain only strings"):
        PhysicalCommand(**kwargs)

    kwargs = valid_kwargs.copy()
    kwargs["parameters"] = [] # Not a mapping
    with pytest.raises(DeviceValidationError, match="Parameters must be a mapping"):
        PhysicalCommand(**kwargs)

def test_physical_command_serialization_preservation():
    """Test that copying/serialization preserves all security-critical fields perfectly."""
    cmd = PhysicalCommand(
        endpoint_id="ep_1",
        session_id="sess_123",
        lifecycle_generation=5,
        endpoint_lease_generation=10,
        execution_id="exec_abc",
        capability_scope=frozenset(["cap_actuate"]),
        command_sequence=42,
        operation="actuate_motor",
        parameters={"speed": 50}
    )

    # Test copy
    cmd_copied = copy.deepcopy(cmd)
    assert cmd_copied.endpoint_id == cmd.endpoint_id
    assert cmd_copied.session_id == cmd.session_id
    assert cmd_copied.lifecycle_generation == cmd.lifecycle_generation
    assert cmd_copied.endpoint_lease_generation == cmd.endpoint_lease_generation
    assert cmd_copied.execution_id == cmd.execution_id
    assert cmd_copied.capability_scope == cmd.capability_scope
    assert cmd_copied.command_sequence == cmd.command_sequence
    assert cmd_copied.operation == cmd.operation
    assert cmd_copied.parameters == cmd.parameters

    # Test pickle serialization
    cmd_pickled = pickle.loads(pickle.dumps(cmd))
    assert cmd_pickled.endpoint_id == cmd.endpoint_id
    assert cmd_pickled.session_id == cmd.session_id
    assert cmd_pickled.lifecycle_generation == cmd.lifecycle_generation
    assert cmd_pickled.endpoint_lease_generation == cmd.endpoint_lease_generation
    assert cmd_pickled.execution_id == cmd.execution_id
    assert cmd_pickled.capability_scope == cmd.capability_scope
    assert cmd_pickled.command_sequence == cmd.command_sequence
    assert cmd_pickled.operation == cmd.operation
    assert cmd_pickled.parameters == cmd.parameters
