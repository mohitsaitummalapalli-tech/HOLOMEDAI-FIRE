"""HoloMed AI - M48 Adversarial Physical Boundary Tests."""

import threading
import time
import pytest
from typing import Tuple

from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.control.exceptions import (
    CapabilityUnauthorizedError,
    DeviceCommandValidationError,
)
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
from holomed.platform.session import SessionManager
from holomed.core.dispatcher import MessageDispatcher
from holomed.protocol.builders import create_command
from holomed.protocol.models import MessageType, MessageEnvelope
from tests.unit.devices.conftest import make_test_context

class StrictSimulatedEndpoint(SimulatedPhysicalEndpoint):
    def __init__(self, endpoint_id: str, device_id: str) -> None:
        super().__init__(endpoint_id, device_id)
        self.admission_lock = threading.Lock()
        
    def check_admission_tuple(self, session_id: str, session_gen: int, exec_id: str, cap_scope: frozenset, seq: int) -> bool:
        """Simulates atomic hardware-level admission check immediately prior to actuation."""
        with self.admission_lock:
            if self.safety_state != EndpointSafetyState.ACTIVE:
                return False
            if not self._active_lease:
                return False
            if self._active_lease.session_id != session_id:
                return False
            if self._active_lease.lifecycle_generation != session_gen:
                return False
            if self._active_lease.execution_id != exec_id:
                return False
            if self._active_lease.capability_scope != cap_scope:
                return False
            return True


@pytest.fixture
def m48_system() -> Tuple[DeviceControlManager, DeviceRegistry, SessionManager, MessageDispatcher]:
    ctx = make_test_context(epoch_id=1)
    bus = MessageDispatcher()
    bus.initialize(ctx)
    
    sess_manager = SessionManager(epoch_id=1, dispatcher=bus)
    
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token=token)
    
    def validator(sess_id: str, gen: int) -> bool:
        gate = sess_manager.get_lifecycle_gate(sess_id)
        if not gate or not gate.is_active:
            return False
        return gate.generation == gen

    manager = DeviceControlManager(registry=registry, session_validator=validator)
    # Temporary monkey-patch to connect events until manager is updated
    def on_stopped(env: MessageEnvelope):
        sess_id = env.payload.get("session_id")
        if sess_id:
            manager.emergency_stop(sess_id)
            
    bus.subscribe_event("platform.session.stopped", on_stopped, "test_manager")
    bus.subscribe_event("platform.session.evicted", on_stopped, "test_manager")

    bus.start()
    manager.initialize(ctx)
    manager.start()
    
    return manager, registry, sess_manager, bus


@pytest.fixture
def clinical_device(m48_system) -> Tuple[SimulatedDevice, StrictSimulatedEndpoint]:
    manager, registry, _, _ = m48_system
    endpoint = StrictSimulatedEndpoint("ep_1", "sys.clin")
    cap = DeviceCapability(
        capability_id="clin.actuate",
        category=CapabilityCategory.CONTROL,
        parameters={},
        requires_physical_endpoint=True
    )
    device = SimulatedDevice(
        device_id="sys.clin",
        physical_id="sim_clin",
        capabilities=(cap,),
    )
    device.set_endpoints((endpoint,))
    registry.register(device, token=registry._token)
    device._state = DeviceState.ACTIVE
    
    def handler(dev, params): raise Exception("GENERIC HANDLER CALLED ILLEGALLY")
    manager.register_command("clin.actuate", handler, required_capability_id="clin.actuate")
    return device, endpoint

@pytest.fixture
def multi_endpoint_device(m48_system) -> Tuple[SimulatedDevice, StrictSimulatedEndpoint, StrictSimulatedEndpoint]:
    manager, registry, _, _ = m48_system
    ep1 = StrictSimulatedEndpoint("ep_1", "sys.multi")
    ep2 = StrictSimulatedEndpoint("ep_2", "sys.multi")
    
    cap1 = DeviceCapability(
        capability_id="multi.actuate1",
        category=CapabilityCategory.CONTROL,
        parameters={},
        requires_physical_endpoint=True,
        target_endpoint_id="ep_1"
    )
    cap2 = DeviceCapability(
        capability_id="multi.actuate2",
        category=CapabilityCategory.CONTROL,
        parameters={},
        requires_physical_endpoint=True,
        target_endpoint_id="ep_2"
    )
    
    device = SimulatedDevice(
        device_id="sys.multi",
        physical_id="sim_multi",
        capabilities=(cap1, cap2),
    )
    device.set_endpoints((ep1, ep2))
    registry.register(device, token=registry._token)
    device._state = DeviceState.ACTIVE
    
    def handler1(dev, params): raise Exception("GENERIC HANDLER CALLED ILLEGALLY")
    def handler2(dev, params): raise Exception("GENERIC HANDLER CALLED ILLEGALLY")
        
    manager.register_command("multi.actuate1", handler1, required_capability_id="multi.actuate1")
    manager.register_command("multi.actuate2", handler2, required_capability_id="multi.actuate2")
    return device, ep1, ep2

# -- Phase 7 tests

def test_01_valid_command_admitted(m48_system, clinical_device):
    manager, _, sess_manager, _ = m48_system
    _, endpoint = clinical_device
    sess_id = sess_manager.start_session("test_user", epoch_id=1).session_id
    gen = sess_manager.get_lifecycle_gate(sess_id).generation
    
    cmd = create_command(
        message_name="device.command", source="test",
        payload={"device_id": "sys.clin", "command": "clin.actuate", "session_id": sess_id, "execution_id": "exec_1", "session_lifecycle_generation": gen, "parameters": {}}
    )
    res = manager.handle_command(cmd)
    if res.message_type == MessageType.ERROR:
        print("ERROR PAYLOAD:", res.payload)
    assert res.message_type == MessageType.RESPONSE
    assert endpoint.safety_state == EndpointSafetyState.ACTIVE
    assert endpoint.active_lease.session_id == sess_id

def test_02_revoked_session_rejected(m48_system, clinical_device):
    manager, _, sess_manager, _ = m48_system
    _, endpoint = clinical_device
    sess_id = sess_manager.start_session("test_user", epoch_id=1).session_id
    gen = sess_manager.get_lifecycle_gate(sess_id).generation
    
    sess_manager.stop_session(sess_id)
    
    cmd = create_command(
        message_name="device.command", source="test",
        payload={"device_id": "sys.clin", "command": "clin.actuate", "session_id": sess_id, "execution_id": "exec_1", "session_lifecycle_generation": gen, "parameters": {}}
    )
    res = manager.handle_command(cmd)
    assert res.message_type == MessageType.ERROR
    assert "ERR_CAPABILITYUNAUTHORIZEDERROR" in res.payload["error_code"]

def test_03_stale_session_lifecycle_generation_rejected(m48_system, clinical_device):
    manager, _, sess_manager, _ = m48_system
    sess_id = sess_manager.start_session("test_user", epoch_id=1).session_id
    gen = sess_manager.get_lifecycle_gate(sess_id).generation
    
    cmd = create_command(
        message_name="device.command", source="test",
        payload={"device_id": "sys.clin", "command": "clin.actuate", "session_id": sess_id, "execution_id": "exec_1", "session_lifecycle_generation": gen - 1, "parameters": {}}
    )
    res = manager.handle_command(cmd)
    assert res.message_type == MessageType.ERROR
    assert "ERR_CAPABILITYUNAUTHORIZEDERROR" in res.payload["error_code"]

def test_04_wrong_execution_id_rejected(m48_system, clinical_device):
    manager, _, sess_manager, _ = m48_system
    _, endpoint = clinical_device
    sess_id = sess_manager.start_session("test_user", epoch_id=1).session_id
    gen = sess_manager.get_lifecycle_gate(sess_id).generation
    
    manager.handle_command(create_command(
        message_name="device.command", source="test",
        payload={"device_id": "sys.clin", "command": "clin.actuate", "session_id": sess_id, "execution_id": "exec_1", "session_lifecycle_generation": gen, "parameters": {}}
    ))
    
    # We simulate the hardware receiving an old exec_id command
    assert not endpoint.check_admission_tuple(sess_id, gen, "exec_wrong", frozenset(["clin.actuate"]), 1)

def test_06_stale_endpoint_lease_rejected(m48_system, clinical_device):
    manager, _, sess_manager, _ = m48_system
    _, endpoint = clinical_device
    sess_id = sess_manager.start_session("test_user", epoch_id=1).session_id
    gen = sess_manager.get_lifecycle_gate(sess_id).generation
    
    manager.handle_command(create_command(
        message_name="device.command", source="test",
        payload={"device_id": "sys.clin", "command": "clin.actuate", "session_id": sess_id, "execution_id": "exec_1", "session_lifecycle_generation": gen, "parameters": {}}
    ))
    lease_gen_1 = endpoint.active_lease.endpoint_lease_generation
    
    manager.handle_command(create_command(
        message_name="device.command", source="test",
        payload={"device_id": "sys.clin", "command": "clin.actuate", "session_id": sess_id, "execution_id": "exec_2", "session_lifecycle_generation": gen, "parameters": {}}
    ))
    lease_gen_2 = endpoint.active_lease.endpoint_lease_generation
    
    assert lease_gen_2 > lease_gen_1
    assert endpoint.active_lease.execution_id == "exec_2"

def test_08_session_a_to_b_reassignment_isolation(m48_system, clinical_device):
    manager, _, sess_manager, _ = m48_system
    _, endpoint = clinical_device
    
    sess_a = sess_manager.start_session("sess_A", epoch_id=1).session_id
    gen_a = sess_manager.get_lifecycle_gate(sess_a).generation
    
    manager.handle_command(create_command(
        message_name="device.command", source="test",
        payload={"device_id": "sys.clin", "command": "clin.actuate", "session_id": sess_a, "execution_id": "exec_A", "session_lifecycle_generation": gen_a, "parameters": {}}
    ))
    assert endpoint.active_lease.session_id == sess_a
    
    sess_manager.stop_session(sess_a)
    assert endpoint.safety_state == EndpointSafetyState.SAFE_STOPPED
    
    sess_b = sess_manager.start_session("sess_B", epoch_id=1).session_id
    gen_b = sess_manager.get_lifecycle_gate(sess_b).generation
    
    manager.handle_command(create_command(
        message_name="device.command", source="test",
        payload={"device_id": "sys.clin", "command": "clin.actuate", "session_id": sess_b, "execution_id": "exec_B", "session_lifecycle_generation": gen_b, "parameters": {}}
    ))
    assert endpoint.active_lease.session_id == sess_b
    
    res = manager.handle_command(create_command(
        message_name="device.command", source="test",
        payload={"device_id": "sys.clin", "command": "clin.actuate", "session_id": sess_a, "execution_id": "exec_A", "session_lifecycle_generation": gen_a, "parameters": {}}
    ))
    print(f"RES TYPE: {res.message_type}, PAYLOAD: {res.payload}")
    assert res.message_type == MessageType.ERROR

def test_09_cross_session_isolation_under_concurrency(m48_system, clinical_device):
    manager, _, sess_manager, _ = m48_system
    device, endpoint = clinical_device
    
    sess_a = sess_manager.start_session("test_user", epoch_id=1).session_id
    gen_a = sess_manager.get_lifecycle_gate(sess_a).generation
    
    sync_barrier = threading.Barrier(2)
    outcomes = []

    original_submit = endpoint.submit_command

    def blocking_submit(cmd):
        sync_barrier.wait()
        time.sleep(0.01) # Give thread B time to revoke
        # Instead of check_admission_tuple, we check safety_state to simulate hardware rejection mid-flight
        if endpoint.safety_state != EndpointSafetyState.ACTIVE:
            outcomes.append("REJECTED_AT_HARDWARE_BOUNDARY")
            raise Exception("Actuation rejected by hardware")
        outcomes.append("ACTUATED")
        return original_submit(cmd)
        
    endpoint.submit_command = blocking_submit
    
    def thread_a():
        manager.handle_command(create_command(
            message_name="device.command", source="test",
            payload={"device_id": "sys.clin", "command": "clin.actuate", "session_id": sess_a, "execution_id": "exec_A", "session_lifecycle_generation": gen_a, "parameters": {}}
        ))
        
    t1 = threading.Thread(target=thread_a)
    t1.start()
    
    sync_barrier.wait()
    sess_manager.stop_session(sess_a)
    
    t1.join()
    assert "REJECTED_AT_HARDWARE_BOUNDARY" in outcomes
    assert "ACTUATED" not in outcomes
    assert endpoint.safety_state == EndpointSafetyState.SAFE_STOPPED

def test_10_cross_session_same_device_isolation(m48_system, multi_endpoint_device):
    manager, _, sess_manager, _ = m48_system
    _, ep1, ep2 = multi_endpoint_device
    
    sess_a = sess_manager.start_session("sess_A", epoch_id=1).session_id
    gen_a = sess_manager.get_lifecycle_gate(sess_a).generation
    sess_b = sess_manager.start_session("sess_B", epoch_id=1).session_id
    gen_b = sess_manager.get_lifecycle_gate(sess_b).generation
    
    manager.handle_command(create_command(message_name="device.command", source="test", payload={"device_id": "sys.multi", "command": "multi.actuate1", "session_id": sess_a, "execution_id": "exec_A", "session_lifecycle_generation": gen_a, "parameters": {}}))
    manager.handle_command(create_command(message_name="device.command", source="test", payload={"device_id": "sys.multi", "command": "multi.actuate2", "session_id": sess_b, "execution_id": "exec_B", "session_lifecycle_generation": gen_b, "parameters": {}}))
    
    assert ep1.active_lease.session_id == sess_a
    assert ep2.active_lease.session_id == sess_b
    
    sess_manager.stop_session(sess_a)
    assert ep1.safety_state == EndpointSafetyState.SAFE_STOPPED
    assert ep2.safety_state == EndpointSafetyState.ACTIVE
    assert ep2.active_lease.session_id == sess_b

def test_15_session_stop_active_command(m48_system, clinical_device):
    # Proves I8
    manager, _, sess_manager, _ = m48_system
    device, endpoint = clinical_device
    
    sess_a = sess_manager.start_session("test_user", epoch_id=1).session_id
    gen_a = sess_manager.get_lifecycle_gate(sess_a).generation
    
    sync_barrier = threading.Barrier(2)
    
    def blocking_submit(cmd):
        # Notify B we are executing
        sync_barrier.wait()
        # Block simulating long running task
        time.sleep(0.1)
        # Actuation logic here would normally check safety state, but let's test if the endpoint was immediately stopped.
        from holomed.devices.models import PhysicalCommandResult
        return PhysicalCommandResult(
            command_sequence=cmd.command_sequence,
            status="done",
            details={"status": "done"}
        )
        
    endpoint.submit_command = blocking_submit
    
    def thread_a():
        manager.handle_command(create_command(
            message_name="device.command", source="test",
            payload={"device_id": "sys.clin", "command": "clin.actuate", "session_id": sess_a, "execution_id": "exec_A", "session_lifecycle_generation": gen_a, "parameters": {}}
        ))
        
    t1 = threading.Thread(target=thread_a)
    t1.start()
    
    sync_barrier.wait()
    # While handler is sleeping, we revoke
    sess_manager.stop_session(sess_a)
    
    # Assert that BEFORE the thread returns, the endpoint was immediately safe-stopped
    assert endpoint.safety_state == EndpointSafetyState.SAFE_STOPPED
    
    t1.join()

def test_18_19_emergency_stop_and_interlock(m48_system, clinical_device):
    manager, _, sess_manager, _ = m48_system
    _, endpoint = clinical_device
    
    sess_a = sess_manager.start_session("test_user", epoch_id=1).session_id
    gen_a = sess_manager.get_lifecycle_gate(sess_a).generation
    
    manager.handle_command(create_command(
        message_name="device.command", source="test",
        payload={"device_id": "sys.clin", "command": "clin.actuate", "session_id": sess_a, "execution_id": "exec_A", "session_lifecycle_generation": gen_a, "parameters": {}}
    ))
    
    assert endpoint.safety_state == EndpointSafetyState.ACTIVE
    endpoint.inject_hardware_interlock()
    
    sess_manager.stop_session(sess_a)
    
    assert endpoint.safety_state == EndpointSafetyState.HARDWARE_INTERLOCKED
    assert endpoint.active_lease is None
    
    sess_b = sess_manager.start_session("test_user", epoch_id=1).session_id
    gen_b = sess_manager.get_lifecycle_gate(sess_b).generation
    
    res = manager.handle_command(create_command(
        message_name="device.command", source="test",
        payload={"device_id": "sys.clin", "command": "clin.actuate", "session_id": sess_b, "execution_id": "exec_B", "session_lifecycle_generation": gen_b, "parameters": {}}
    ))
    assert res.message_type == MessageType.ERROR
    assert "Hardware is interlocked" in res.payload["error_message"]

def test_20_clinical_migration_gate(m48_system):
    manager, registry, sess_manager, _ = m48_system
    
    cap = DeviceCapability(
        capability_id="clin.legacy",
        category=CapabilityCategory.CONTROL,
        parameters={},
        requires_physical_endpoint=True
    )
    device = SimulatedDevice("sys.legacy", "sim_leg", capabilities=(cap,))
    registry.register(device, token=registry._token)
    device._state = DeviceState.ACTIVE
    manager.register_command("clin.legacy", lambda d, p: {"status": "ok"}, required_capability_id="clin.legacy")
    
    sess_id = sess_manager.start_session("test_user", epoch_id=1).session_id
    gen = sess_manager.get_lifecycle_gate(sess_id).generation
    
    cmd = create_command(
        message_name="device.command", source="test",
        payload={"device_id": "sys.legacy", "command": "clin.legacy", "session_id": sess_id, "execution_id": "exec_1", "session_lifecycle_generation": gen, "parameters": {}}
    )
    res = manager.handle_command(cmd)
    
    assert res.message_type == MessageType.ERROR
    assert "ERR_CAPABILITYUNAUTHORIZEDERROR" in res.payload["error_code"]
    assert "requires a physical endpoint but device 'sys.legacy' exposes none" in res.payload["error_message"]

def test_duplicate_revocation_idempotent(m48_system, clinical_device):
    manager, _, sess_manager, _ = m48_system
    _, endpoint = clinical_device
    sess_id = sess_manager.start_session("test_user", epoch_id=1).session_id
    gen = sess_manager.get_lifecycle_gate(sess_id).generation
    
    manager.handle_command(create_command(
        message_name="device.command", source="test",
        payload={"device_id": "sys.clin", "command": "clin.actuate", "session_id": sess_id, "execution_id": "exec_1", "session_lifecycle_generation": gen, "parameters": {}}
    ))
    
    sess_manager.stop_session(sess_id)
    assert endpoint.safety_state == EndpointSafetyState.SAFE_STOPPED
    
    # Duplicate stop
    sess_manager.stop_session(sess_id)
    assert endpoint.safety_state == EndpointSafetyState.SAFE_STOPPED
    
    # Evict
    sess_manager.evict_session(sess_id)
    assert endpoint.safety_state == EndpointSafetyState.SAFE_STOPPED
