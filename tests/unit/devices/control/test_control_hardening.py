"""Adversarial stress testing, AST import audit, and capacity checks for M00.6."""

import ast
import os
import pytest

from holomed.devices.control.exceptions import (
    ControlCapacityError,
    DeviceCommandResultValidationError,
    DeviceCommandValidationError,
)
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.control.models import (
    MAX_COMMAND_RESULT_BYTES,
    MAX_REGISTERED_COMMANDS,
    MAX_REGISTERED_QUERIES,
)
from holomed.devices.control.verifier import CommandVerifier
from holomed.devices.interfaces import RegistryAuthorityToken
from holomed.devices.models import DeviceState
from holomed.devices.registry import DeviceRegistry
from holomed.devices.simulated import SimulatedDevice
from tests.unit.devices.conftest import make_test_context

PROHIBITED_MODULES = {
    "socket",
    "asyncio",
    "threading",
    "multiprocessing",
    "subprocess",
    "cv2",
    "mediapipe",
    "pyaudio",
    "serial",
    "google.generativeai",
}


def test_ast_prohibited_imports_audit_control() -> None:
    """Verify zero prohibited imports across all python/holomed/devices/control/*.py files."""
    control_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "python", "holomed", "devices", "control")
    control_dir = os.path.abspath(control_dir)

    for root, _, files in os.walk(control_dir):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                with open(file_path, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=file_path)

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            for prohibited in PROHIBITED_MODULES:
                                assert not (alias.name == prohibited or alias.name.startswith(f"{prohibited}.")), (
                                    f"Prohibited import '{alias.name}' in {file_path}"
                                )
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            for prohibited in PROHIBITED_MODULES:
                                assert not (node.module == prohibited or node.module.startswith(f"{prohibited}.")), (
                                    f"Prohibited from-import '{node.module}' in {file_path}"
                                )


def test_command_registry_capacity_limit() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    manager = DeviceControlManager(registry=registry)

    for i in range(MAX_REGISTERED_COMMANDS):
        manager.register_command(f"cmd.{i}", lambda d, p: {})

    with pytest.raises(ControlCapacityError, match="Max registered commands exceeded"):
        manager.register_command("cmd.overflow", lambda d, p: {})


def test_query_registry_capacity_limit() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    manager = DeviceControlManager(registry=registry)

    for i in range(MAX_REGISTERED_QUERIES):
        manager.register_query(f"query.{i}", lambda d, p: {})

    with pytest.raises(ControlCapacityError, match="Max registered queries exceeded"):
        manager.register_query("query.overflow", lambda d, p: {})


def test_result_size_limit_enforced() -> None:
    verifier = CommandVerifier()

    # Exceeding MAX_CAPABILITY_TOTAL_UNITS (16384)
    huge_units_result = {
        f"g_{g}": {f"k_{i:02d}": "x" * 200 for i in range(25)}
        for g in range(4)
    }
    with pytest.raises(DeviceCommandResultValidationError, match="exceed maximum"):
        verifier.validate_and_canonicalize_command_result(huge_units_result)


def test_result_nan_and_infinity_rejected() -> None:
    verifier = CommandVerifier()

    with pytest.raises(DeviceCommandResultValidationError):
        verifier.validate_and_canonicalize_command_result({"val": float("nan")})

    with pytest.raises(DeviceCommandResultValidationError):
        verifier.validate_and_canonicalize_command_result({"val": float("inf")})


def test_dynamic_device_replacement_invokes_new_instance() -> None:
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)
    manager = DeviceControlManager(registry=registry)
    ctx = make_test_context(epoch_id=1)
    manager.initialize(ctx)
    manager.start()

    invoked_instances = []

    def tracking_handler(dev, params):
        invoked_instances.append(dev)
        return {"ok": True}

    manager.register_command("sensor.read", tracking_handler)

    dev_v1 = SimulatedDevice("sensor_1", "I2C:1")
    registry.register(dev_v1, token)
    dev_v1._state = DeviceState.ACTIVE

    from holomed.protocol.builders import create_command
    cmd1 = create_command(message_name="device.command", source="c", payload={"device_id": "sensor_1", "command": "sensor.read"})
    manager.handle_command(cmd1)
    assert invoked_instances[0] is dev_v1

    # Replace device in registry with dev_v2
    dev_v1._state = DeviceState.STOPPED
    registry.deregister("sensor_1", token)
    dev_v2 = SimulatedDevice("sensor_1", "I2C:2")
    registry.register(dev_v2, token)
    dev_v2._state = DeviceState.ACTIVE

    cmd2 = create_command(message_name="device.command", source="c", payload={"device_id": "sensor_1", "command": "sensor.read"})
    manager.handle_command(cmd2)
    assert invoked_instances[1] is dev_v2
