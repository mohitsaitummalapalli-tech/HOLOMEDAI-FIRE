"""HoloMed AI - Unit tests for physical endpoint lease mechanics (M48)."""

import pytest

from holomed.devices.control.exceptions import (
    CapabilityUnauthorizedError,
    DeviceCommandValidationError,
)
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.control.verifier import CommandVerifier
from holomed.devices.models import (
    CapabilityCategory,
    DeviceCapability,
    DeviceState,
    DeviceType,
    EndpointSafetyState,
)
from holomed.devices.registry import DeviceRegistry
from holomed.devices.interfaces import RegistryAuthorityToken
from holomed.devices.simulated import SimulatedDevice, SimulatedPhysicalEndpoint
from holomed.protocol.builders import create_command; from holomed.protocol.models import MessageType
from tests.unit.devices.conftest import make_test_context


@pytest.fixture
def manager_and_device():
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token=token)
    manager = DeviceControlManager(registry=registry)
    ctx = make_test_context(epoch_id=1)
    
    # Create device with a physical capability and endpoint
    endpoint = SimulatedPhysicalEndpoint("ep_1", "sys.test.phys")
    cap = DeviceCapability(
        capability_id="test.actuate",
        category=CapabilityCategory.CONTROL,
        parameters={},
        requires_physical_endpoint=True
    )
    device = SimulatedDevice(
        device_id="sys.test.phys",
        physical_id="sim_1",
        capabilities=(cap,),
    )
    device.set_endpoints((endpoint,))
    registry.register(device, token=token)
    device._state = DeviceState.ACTIVE
    
    manager.initialize(ctx)
    manager.start()
    
    def handler(dev, params):
        # Fake physical actuation handler
        return {"status": "ok", "seq": params.get("_command_sequence_ep_1")}
        
    manager.register_command("test.actuate", handler, required_capability_id="test.actuate")
    
    return manager, device, endpoint


def test_reject_physical_command_without_session_id(manager_and_device):
    manager, _, _ = manager_and_device
    cmd = create_command(
        message_name="device.command",
        source="test",
        payload={
            "device_id": "sys.test.phys",
            "command": "test.actuate",
            "execution_id": "exec_1",
            "session_lifecycle_generation": 1,
            "parameters": {},
        }
    )
    res = manager.handle_command(cmd); print(res.payload)
    assert res.message_type == MessageType.ERROR
    assert "ERR_VALIDATION_ERROR" in res.payload["error_code"]
    assert "session_id" in res.payload["error_message"]


def test_reject_physical_command_without_execution_id(manager_and_device):
    manager, _, _ = manager_and_device
    cmd = create_command(
        message_name="device.command",
        source="test",
        payload={
            "device_id": "sys.test.phys",
            "command": "test.actuate",
            "session_id": "sess_1",
            "session_lifecycle_generation": 1,
            "parameters": {},
        }
    )
    res = manager.handle_command(cmd); print(res.payload)
    assert res.message_type == MessageType.ERROR
    assert "ERR_VALIDATION_ERROR" in res.payload["error_code"]
    assert "execution_id" in res.payload["error_message"]


def test_reject_physical_command_without_lifecycle_generation(manager_and_device):
    manager, _, _ = manager_and_device
    cmd = create_command(
        message_name="device.command",
        source="test",
        payload={
            "device_id": "sys.test.phys",
            "command": "test.actuate",
            "session_id": "sess_1",
            "execution_id": "exec_1",
            "parameters": {},
        }
    )
    res = manager.handle_command(cmd); print(res.payload)
    assert res.message_type == MessageType.ERROR
    assert "ERR_VALIDATION_ERROR" in res.payload["error_code"]
    assert "session_lifecycle_generation" in res.payload["error_message"]


def test_successful_lease_issues_sequence_number(manager_and_device):
    manager, _, endpoint = manager_and_device
    cmd = create_command(
        message_name="device.command",
        source="test",
        payload={
            "device_id": "sys.test.phys",
            "command": "test.actuate",
            "session_id": "sess_1",
            "execution_id": "exec_1",
            "session_lifecycle_generation": 1,
            "parameters": {},
        }
    )
    res = manager.handle_command(cmd)
    assert res.message_type == MessageType.RESPONSE
    assert res.payload["actuated_sequence"] == 1
    
    assert endpoint.safety_state == EndpointSafetyState.ACTIVE
    assert endpoint.active_lease is not None
    assert endpoint.active_lease.session_id == "sess_1"


def test_sequential_commands_increment_sequence(manager_and_device):
    manager, _, endpoint = manager_and_device
    
    def send_cmd():
        return manager.handle_command(create_command(
            message_name="device.command",
            source="test",
            payload={
                "device_id": "sys.test.phys",
                "command": "test.actuate",
                "session_id": "sess_1",
                "execution_id": "exec_1",
                "session_lifecycle_generation": 1,
                "parameters": {},
            }
        ))
        
    res1 = send_cmd()
    res2 = send_cmd()
    
    assert res1.payload["actuated_sequence"] == 1
    assert res2.payload["actuated_sequence"] == 2
    assert endpoint.active_lease.endpoint_lease_generation == 1


def test_new_session_execution_rejected_if_lease_active(manager_and_device):
    manager, _, endpoint = manager_and_device

    manager.handle_command(create_command(
        message_name="device.command",
        source="test",
        payload={
            "device_id": "sys.test.phys",
            "command": "test.actuate",
            "session_id": "sess_1",
            "execution_id": "exec_1",
            "session_lifecycle_generation": 1,
            "parameters": {},
        }
    ))

    # Session 2 tries to take over
    response = manager.handle_command(create_command(
        message_name="device.command",
        source="test",
        payload={
            "device_id": "sys.test.phys",
            "command": "test.actuate",
            "session_id": "sess_2",
            "execution_id": "exec_2",
            "session_lifecycle_generation": 1,
            "parameters": {},
        }
    ))

    assert response.message_name == "device.command.error"
    assert response.payload.get("error_code") == "ERR_CONTROLCAPACITYERROR"
    assert endpoint.active_lease.session_id == "sess_1"


def test_emergency_stop_revokes_lease_and_interlocks(manager_and_device):
    manager, _, endpoint = manager_and_device
    
    manager.handle_command(create_command(
        message_name="device.command",
        source="test",
        payload={
            "device_id": "sys.test.phys",
            "command": "test.actuate",
            "session_id": "sess_1",
            "execution_id": "exec_1",
            "session_lifecycle_generation": 1,
            "parameters": {},
        }
    ))
    
    assert endpoint.active_lease is not None
    manager.emergency_stop("sess_1")
    
    assert endpoint.active_lease is None
    assert endpoint.safety_state == EndpointSafetyState.SAFE_STOPPED


def test_hardware_interlock_prevents_lease_acquisition(manager_and_device):
    manager, _, endpoint = manager_and_device
    endpoint.inject_hardware_interlock()
    
    cmd = create_command(
        message_name="device.command",
        source="test",
        payload={
            "device_id": "sys.test.phys",
            "command": "test.actuate",
            "session_id": "sess_1",
            "execution_id": "exec_1",
            "session_lifecycle_generation": 1,
            "parameters": {},
        }
    )
    res = manager.handle_command(cmd); print(res.payload)
    
    assert res.message_type == MessageType.ERROR
    assert "ERR_RUNTIMEERROR" in res.payload["error_code"]
    assert "Hardware is interlocked" in res.payload["error_message"]


def test_verifier_rejects_missing_endpoint_for_physical_capability(manager_and_device):
    manager, device, _ = manager_and_device
    
    # Remove endpoints from device
    device.set_endpoints(())
    
    cmd = create_command(
        message_name="device.command",
        source="test",
        payload={
            "device_id": "sys.test.phys",
            "command": "test.actuate",
            "session_id": "sess_1",
            "execution_id": "exec_1",
            "session_lifecycle_generation": 1,
            "parameters": {},
        }
    )
    res = manager.handle_command(cmd); print(res.payload)
    
    assert res.message_type == MessageType.ERROR
    assert "ERR_CAPABILITYUNAUTHORIZEDERROR" in res.payload["error_code"]
    assert "requires a physical endpoint but device 'sys.test.phys' exposes none" in res.payload["error_message"]

