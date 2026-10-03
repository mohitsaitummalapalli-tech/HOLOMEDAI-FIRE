import pytest
from holomed.platform.cycle import CycleCoordinator
from holomed.platform.session import SessionManager
from holomed.devices.control.manager import DeviceControlManager, AdmissionState
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.control.daemon import ReconciliationDaemon
from holomed.devices.reconciler import TelemetryReconciler
from holomed.devices.transport import TelemetryTransport
from holomed.devices.registry import DeviceRegistry
from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.authority import ControllerAuthorityStore, DeviceEpochAuthority
from holomed.runtime.service import ServiceState
from tests.unit.platform.test_live_ingress import MockDevice, MockIntentDetector, MockProposer, mock_perception
import uuid

def test_live_path_end_to_end(tmp_path, mock_perception):
    store = DurableSessionStore(storage_root=tmp_path, epoch_id=1)
    store._authority.allocate_next_epoch()
    dev_auth = DeviceEpochAuthority(tmp_path / "devices")
    dev_auth.allocate_next_device_epoch("dev1")
    store.start_session("session_e2e", 1)

    resolution_gate = ExecutionResolutionGate()
    transport = TelemetryTransport()
    reconciler = TelemetryReconciler(transport, resolution_gate)
    daemon = ReconciliationDaemon(reconciler, store)

    session_manager = SessionManager(epoch_id=1)
    session_manager.start_session("session_e2e", 1)

    def mock_validator(sess_id, gen):
        return sess_id == "session_e2e" and gen == 1

    registry = DeviceRegistry(token="mock_token")
    device = MockDevice("dev1")
    class MockCap:
        def __init__(self):
            self.capability_id = "cap_grasp"
            self.requires_physical_endpoint = True
            self.target_endpoint_id = "ep1"
    device.capabilities = [MockCap()]
    registry.register(device, "mock_token")
    from holomed.devices.models import DeviceState
    device.state = DeviceState.READY

    dcm = DeviceControlManager(
        registry=registry,
        resolution_gate=resolution_gate,
        session_validator=mock_validator,
        capacity_snapshot_provider=store.get_active_operations_snapshot,
        capacity_admitter=store.record_operation_admitted,
        capacity_releaser=store.record_operation_terminated,
        authoritative_epoch_provider=lambda: 1
    )
    dcm._state = ServiceState.STARTED
    dcm._admission_state = AdmissionState.READY
    dcm._timeout_shutdown = type("Event", (), {"is_set": lambda: True, "wait": lambda x: None, "clear": lambda: None})()
    dcm._deadlines = {}

    device.endpoints[0].dcm = dcm
    device.endpoints[0].capacity_snapshot = store.get_active_operations_snapshot
    device.endpoints[0].transport = transport

    class MockCommandDef:
        def __init__(self, name):
            self.command_name = name
            self.required_scopes = frozenset(["global"])
            self.required_capability_id = "cap_grasp"
            self.allow_ready = True
            self.active_lease_required = False
            self.validate_parameters = lambda x: {}
            self.handler = lambda *args, **kwargs: {"status": "success"}

    dcm._commands = {"grasp": MockCommandDef("grasp"), "cancel": MockCommandDef("cancel"), "release": MockCommandDef("release")}

    coordinator = CycleCoordinator(epoch_id=1)
    coordinator._intent_detector = MockIntentDetector()
    coordinator._proposer = MockProposer()

    services = {"device_control_manager": dcm}

    corr_id = "11111111-1111-1111-1111-111111111111"
    obs_grasp = mock_perception(corr_id, 0.01)
    
    coordinator.execute_cycle(
        session_manager=session_manager,
        session_id="session_e2e",
        sequence_number=1,
        services=services,
        cycle_params={"observation": obs_grasp, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
    )
    
    active = store.get_active_operations_snapshot()
    ex_id = None
    canon = None
    for c, p in active.items():
        if p.get("correlation_id") == corr_id:
            ex_id = p["execution_id"]
            canon = c
            break
            
    assert ex_id is not None
    
    obs_release = mock_perception(corr_id, 0.20)
    coordinator.execute_cycle(
        session_manager=session_manager,
        session_id="session_e2e",
        sequence_number=2,
        services=services,
        cycle_params={"observation": obs_release, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
    )
    
    # Event should be published to transport. Process it.
    daemon.run_reconciliation_cycle()
    
    active_after = store.get_active_operations_snapshot()
    assert canon not in active_after
    
    # Verify in journal that it is PREEMPTED
    j_path = tmp_path / "session_e2e.jsonl"
    import json
    found_term = False
    with open(j_path, "r") as f:
        for line in f:
            if not line.strip(): continue
            rec = json.loads(line)
            if rec.get("entry_type") == "OPERATION_TERMINATED":
                if rec.get("payload", {}).get("resolution") == "PREEMPTED":
                    found_term = True
    assert found_term

def test_live_path_timeout(tmp_path, mock_perception):
    store = DurableSessionStore(storage_root=tmp_path, epoch_id=1)
    store._authority.allocate_next_epoch()
    dev_auth = DeviceEpochAuthority(tmp_path / "devices")
    dev_auth.allocate_next_device_epoch("dev1")
    store.start_session("session_timeout", 1)

    resolution_gate = ExecutionResolutionGate()
    transport = TelemetryTransport()
    reconciler = TelemetryReconciler(transport, resolution_gate)
    daemon = ReconciliationDaemon(reconciler, store)

    session_manager = SessionManager(epoch_id=1)
    session_manager.start_session("session_timeout", 1)

    def mock_validator(sess_id, gen):
        return sess_id == "session_timeout" and gen == 1

    registry = DeviceRegistry(token="mock_token")
    device = MockDevice("dev1")
    class MockCap:
        def __init__(self):
            self.capability_id = "cap_grasp"
            self.requires_physical_endpoint = True
            self.target_endpoint_id = "ep1"
    device.capabilities = [MockCap()]
    registry.register(device, "mock_token")
    from holomed.devices.models import DeviceState
    device.state = DeviceState.READY

    dcm = DeviceControlManager(
        registry=registry,
        resolution_gate=resolution_gate,
        session_validator=mock_validator,
        capacity_snapshot_provider=store.get_active_operations_snapshot,
        capacity_admitter=store.record_operation_admitted,
        capacity_releaser=store.record_operation_terminated,
        authoritative_epoch_provider=lambda: 1
    )
    dcm._state = ServiceState.STARTED
    dcm._admission_state = AdmissionState.READY
    dcm._timeout_shutdown = type("Event", (), {"is_set": lambda: True, "wait": lambda x: None, "clear": lambda: None})()
    dcm._deadlines = {}

    device.endpoints[0].dcm = dcm
    device.endpoints[0].capacity_snapshot = store.get_active_operations_snapshot
    device.endpoints[0].transport = transport

    class MockCommandDef:
        def __init__(self, name):
            self.command_name = name
            self.required_scopes = frozenset(["global"])
            self.required_capability_id = "cap_grasp"
            self.allow_ready = True
            self.active_lease_required = False
            self.validate_parameters = lambda x: {}
            self.handler = lambda *args, **kwargs: {"status": "success"}

    dcm._commands = {"grasp": MockCommandDef("grasp")}

    coordinator = CycleCoordinator(epoch_id=1)
    coordinator._intent_detector = MockIntentDetector()
    coordinator._proposer = MockProposer()

    services = {"device_control_manager": dcm}

    corr_id = "22222222-2222-2222-2222-222222222222"
    obs_grasp = mock_perception(corr_id, 0.01)
    
    coordinator.execute_cycle(
        session_manager=session_manager,
        session_id="session_timeout",
        sequence_number=1,
        services=services,
        cycle_params={"observation": obs_grasp, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
    )
    
    active = store.get_active_operations_snapshot()
    ex_id = None
    canon = None
    for c, p in active.items():
        if p.get("correlation_id") == corr_id:
            ex_id = p["execution_id"]
            canon = c
            break
            
    assert ex_id is not None
    
    dcm._deadlines[ex_id] = (0, 1) # Set deadline to past
    dcm.trigger_test_timeout(ex_id)
    
    j_path = tmp_path / "session_timeout.jsonl"
    import json
    found_term = False
    with open(j_path, "r") as f:
        for line in f:
            if not line.strip(): continue
            rec = json.loads(line)
            if rec.get("entry_type") == "OPERATION_TERMINATED":
                if rec.get("payload", {}).get("resolution") == "FAULTED_UNKNOWN":
                    found_term = True
    assert found_term
