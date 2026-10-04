import pytest
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.registry import DeviceRegistry
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.models import CommandState, StopRouteState, DeviceState, EndpointState, DeviceType, PhysicalCommandResult, SubmissionStatus, EndpointSafetyState, DeviceHealth, HealthStatus, DeviceCapability, CapabilityCategory
from holomed.devices.interfaces import IDevice, IPhysicalEndpoint, RegistryAuthorityToken
from holomed.persistence.sessions import DurableSessionStore, PersistenceIdentityReuseError
from types import MappingProxyType
import uuid
from typing import Any

class MockEndpoint(IPhysicalEndpoint):
    def __init__(self, endpoint_id):
        self._endpoint_id = endpoint_id
        self._active_lease = None
        self._endpoint_state = EndpointState.READY
    @property
    def endpoint_id(self): return self._endpoint_id
    @property
    def capability_scope(self): return frozenset(["STREAMING"])
    @property
    def active_lease(self): return self._active_lease
    @property
    def endpoint_state(self): return self._endpoint_state
    def submit_command(self, physical_command):
        return PhysicalCommandResult(status=SubmissionStatus.ACCEPTED, details={})
    def request_stop(self, execution_id): pass
    def __hash__(self): return hash(self.endpoint_id)
    def acquire_lease(self, lease):
        self._active_lease = lease
    def release_lease(self, session_id):
        if self._active_lease and self._active_lease.session_id == session_id:
            self._active_lease = None
    def emergency_stop(self): return EndpointSafetyState.SAFE_STOPPED
    @property
    def device_id(self): return "dev-1"
    @property
    def safety_state(self): return EndpointSafetyState.ACTIVE
    def set_endpoint_epoch(self, epoch_id: int) -> None: pass
    def recover(self): pass


class MockDevice(IDevice):
    def __init__(self, device_id):
        self._device_id = device_id
        self._endpoints = [MockEndpoint("ep_1")]
        self._physical_id = "phys_" + device_id
        self._state = DeviceState.UNREGISTERED
    @property
    def physical_id(self): return self._physical_id
    @property
    def state(self): return self._state
    @state.setter
    def state(self, value): self._state = value
    @property
    def device_id(self): return self._device_id
    @property
    def device_type(self): return DeviceType.SIMULATED_GENERIC
    @property
    def device_state(self): return self.state
    @property
    def endpoints(self): return tuple(self._endpoints)
    @property
    def device_class(self): return "class"
    @property
    def capabilities(self):
        return (DeviceCapability("cap1", CapabilityCategory.CONTROL, MappingProxyType({}), requires_physical_endpoint=True, target_endpoint_id="ep_1"),)
    @property
    def current_epoch(self): return 1
    def initialize(self, accessor: Any = None): pass
    def start(self): pass
    def stop(self, accessor: Any = None): pass
    def health(self):
        return DeviceHealth(self._device_id, HealthStatus.HEALTHY, "OK", "2026-09-01T12:00:00Z")


@pytest.fixture
def store(tmp_path):
    epoch_file = tmp_path / "controller_epoch.json"
    epoch_file.write_text('{"epoch_id": 0}')
    dev_dir = tmp_path / "devices" / "dev_1"
    dev_dir.mkdir(parents=True)
    (dev_dir / "device_epoch.json").write_text('{"device_epoch": 0}')
    store = DurableSessionStore(tmp_path, epoch_id=0)
    store.start_session("session_1", epoch_id=0)
    store.start_session("legit_session", epoch_id=0)
    store.start_session("attacker_session", epoch_id=0)
    store.start_session("foreign_session", epoch_id=0)
    store.start_session("session_2", epoch_id=0)
    return store

@pytest.fixture
def manager_and_gate(store):
    token = RegistryAuthorityToken()
    reg = DeviceRegistry(token)
    mock_dev = MockDevice("dev_1")
    reg.register(mock_dev, token)

    gate = ExecutionResolutionGate()

    mgr = DeviceControlManager(
        registry=reg,
        resolution_gate=gate,
        capacity_admitter=store.record_operation_admitted,
        capacity_snapshot_provider=store.get_active_operations_snapshot,
        capacity_releaser=store.record_operation_terminated,
        authoritative_epoch_provider=lambda: 0
    )
    return mgr, gate, store

def test_unknown_preempt_does_not_create_resolution_record(manager_and_gate):
    mgr, gate, store = manager_and_gate
    exec_id = "unknown_exec_123"

    mgr.preempt_execution("session_1", "dev_1", "ep_1", exec_id, 1)

    assert exec_id not in gate._records

def test_known_long_running_execution_preempt_after_cache_expiry(manager_and_gate):
    mgr, gate, store = manager_and_gate

    exec_id = "known_exec_123"
    phys_op_id, is_replay, _ = store.record_operation_admitted(
        session_id="session_1",
        endpoint_id="ep_1",
        device_id="dev_1",
        device_epoch=0,
        controller_epoch=0,
        physical_operation_id="phys_1",
        command_nonce="nonce_1", correlation_id="nonce_1",
        execution_id=exec_id,
        command_name="TEST_CMD"
    )

    assert exec_id not in mgr._active_commands

    mgr.preempt_execution("session_1", "dev_1", "ep_1", exec_id, 1)

    assert exec_id in gate._records
    assert gate._records[exec_id].stop_route_state == StopRouteState.PRE_CLAIM_CANCELLED

def test_known_execution_unauthorized_preempt_rejected(manager_and_gate):
    mgr, gate, store = manager_and_gate

    exec_id = "known_exec_234"
    phys_op_id, is_replay, _ = store.record_operation_admitted(
        session_id="session_1",
        endpoint_id="ep_1",
        device_id="dev_1",
        device_epoch=0,
        controller_epoch=0,
        physical_operation_id="phys_2",
        command_nonce="nonce_2", correlation_id="nonce_2",
        execution_id=exec_id,
        command_name="TEST_CMD"
    )

    mgr.preempt_execution("foreign_session", "dev_1", "ep_1", exec_id, 1)

    assert exec_id not in gate._records

def test_unknown_id_cannot_poison_future_admission(manager_and_gate):
    mgr, gate, store = manager_and_gate
    exec_id = "target_exec_id"

    mgr.preempt_execution("attacker_session", "dev_1", "ep_1", exec_id, 1)
    assert exec_id not in gate._records

    phys_op_id, is_replay, _ = store.record_operation_admitted(
        session_id="legit_session",
        endpoint_id="ep_1",
        device_id="dev_1",
        device_epoch=0,
        controller_epoch=0,
        physical_operation_id="phys_3",
        command_nonce="nonce_3", correlation_id="nonce_3",
        execution_id=exec_id,
        command_name="TEST_CMD"
    )
    assert not is_replay

def test_known_execution_id_cannot_be_rebound(manager_and_gate):
    mgr, gate, store = manager_and_gate
    exec_id = "bound_exec_id"

    store.record_operation_admitted(
        session_id="session_1",
        endpoint_id="ep_1",
        device_id="dev_1",
        device_epoch=0,
        controller_epoch=0,
        physical_operation_id="phys_4",
        command_nonce="nonce_4", correlation_id="nonce_4",
        execution_id=exec_id,
        command_name="TEST_CMD"
    )

    with pytest.raises(PersistenceIdentityReuseError):
        store.record_operation_admitted(
            session_id="session_2",
            endpoint_id="ep_1",
            device_id="dev_1",
            device_epoch=0,
            controller_epoch=0,
            physical_operation_id="phys_5",
            command_nonce="nonce_5", correlation_id="nonce_5",
            execution_id=exec_id,
            command_name="TEST_CMD"
        )
