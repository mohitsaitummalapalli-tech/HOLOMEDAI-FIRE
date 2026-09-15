import pytest
import time
from holomed.devices.models import CommandState, EndpointSafetyState, EndpointState, DeviceType, EndpointLease, StopRouteState
from holomed.devices.simulated import SimulatedDevice, SimulatedPhysicalEndpoint
from holomed.devices.registry import DeviceRegistry
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.transport import TelemetryTransport, TelemetryPublisher
from holomed.devices.reconciler import TelemetryReconciler
from holomed.runtime.context import RuntimeContext

@pytest.fixture
def gate():
    return ExecutionResolutionGate()

@pytest.fixture
def transport():
    return TelemetryTransport()

@pytest.fixture
def publisher(transport):
    return TelemetryPublisher(transport)

@pytest.fixture
def registry():
    return DeviceRegistry("test_token")

@pytest.fixture
def manager(registry, gate, publisher):
    from holomed.configuration.models import AppConfig
    dcm = DeviceControlManager(registry=registry, resolution_gate=gate)
    ctx = RuntimeContext(app_config=AppConfig(app_name="test", environment="test", host="localhost", port=8000, log_level="INFO"), epoch_id=1)
    dcm.initialize(ctx)
    dcm.start()
    return dcm

@pytest.fixture
def endpoint_and_device(gate, publisher, registry):
    ep = SimulatedPhysicalEndpoint("ep_1", "dev_1", gate=gate, publisher=publisher)
    dev = SimulatedDevice("dev_1", "phys_1", DeviceType.SIMULATED_GENERIC)
    dev.set_endpoints((ep,))
    registry.register(dev, "test_token")
    
    lease = EndpointLease(
        endpoint_id="ep_1",
        device_id="dev_1",
        session_id="session_1",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id="exec_1",
        capability_scope=frozenset(["cap_1"]),
    )
    ep.acquire_lease(lease)
    
    yield ep, dev
    ep.stop_worker()

def create_cmd(exec_id: str, seq: int = 1, generation: int = 1):
    from holomed.devices.models import PhysicalCommand
    return PhysicalCommand(
        endpoint_id="ep_1",
        session_id="session_1",
        lifecycle_generation=generation,
        endpoint_lease_generation=1,
        execution_id=exec_id,
        capability_scope=frozenset(["cap_1"]),
        command_sequence=seq,
        operation="move",
        parameters={},
    )

def test_race_4_8_stop_vs_completed(endpoint_and_device, manager, gate, transport):
    ep, dev = endpoint_and_device
    exec_id = "exec_race_4"
    cmd = create_cmd(exec_id)
    ep.submit_command(cmd)
    
    # Wait for completion
    time.sleep(0.15)
    
    reconciler = TelemetryReconciler(transport, gate)
    reconciler.process_pending_events()
    
    assert gate._records[exec_id].current_state == CommandState.COMPLETED
    
    # Now issue stop request (Race 8: Stop after COMPLETED)
    manager.preempt_execution("dev_1", "ep_1", exec_id, 1)
    
    # Gate should ignore it, state remains COMPLETED, not PREEMPTED
    assert gate._records[exec_id].current_state == CommandState.COMPLETED
    assert gate._records[exec_id].stop_route_state == StopRouteState.NOT_REQUESTED

def test_race_5_stop_vs_timeout(endpoint_and_device, manager, gate, transport):
    ep, dev = endpoint_and_device
    exec_id = "exec_race_5"
    cmd = create_cmd(exec_id)
    
    # Pre-claim stop commits first
    manager.preempt_execution("dev_1", "ep_1", exec_id, 1)
    
    # Timeout tries to resolve
    rec = gate.resolve_timeout(exec_id, 1)
    
    # Should be PREEMPTED, not FAULTED_UNKNOWN
    assert rec.current_state == CommandState.PREEMPTED
    
def test_race_9_stale_generation_stop(endpoint_and_device, manager, gate):
    ep, dev = endpoint_and_device
    exec_id = "exec_race_9"
    cmd = create_cmd(exec_id, generation=1)
    ep.submit_command(cmd)
    time.sleep(0.05)
    
    # Stop with stale generation 0
    manager.preempt_execution("dev_1", "ep_1", exec_id, 0)
    
    # Gate should reject it
    assert gate._records[exec_id].stop_route_state == StopRouteState.NOT_REQUESTED

def test_race_11_stop_transport_unavailable(endpoint_and_device, manager, gate, transport):
    ep, dev = endpoint_and_device
    exec_id = "exec_race_11"
    cmd = create_cmd(exec_id)
    ep.submit_command(cmd)
    time.sleep(0.05)
    
    manager.preempt_execution("dev_1", "ep_1", exec_id, 1)
    
    # Clear transport to simulate lost telemetry
    transport.drain_normal()
    transport.drain_critical()
    
    # Timeout expires
    rec = gate.resolve_timeout(exec_id, 1)
    assert rec.current_state == CommandState.FAULTED_UNKNOWN
    
def test_race_13_stop_while_quarantined_unclaimed(endpoint_and_device, manager, gate, transport):
    ep, dev = endpoint_and_device
    
    # Force quarantine
    ep._endpoint_state = EndpointState.QUARANTINED
    
    exec_id = "exec_race_13"
    
    # Pre-claim cancellation succeeds
    manager.preempt_execution("dev_1", "ep_1", exec_id, 1)
    assert gate._records[exec_id].current_state == CommandState.PREEMPTED
    assert gate._records[exec_id].stop_route_state == StopRouteState.PRE_CLAIM_CANCELLED

def test_race_14_stop_while_quarantined_claimed(endpoint_and_device, manager, gate, transport):
    ep, dev = endpoint_and_device
    exec_id = "exec_race_14"
    cmd = create_cmd(exec_id)
    ep.submit_command(cmd)
    time.sleep(0.05)
    
    # Force quarantine (from another execution)
    ep._endpoint_state = EndpointState.QUARANTINED
    
    # Post-claim stop routing
    manager.preempt_execution("dev_1", "ep_1", exec_id, 1)
    
    assert gate._records[exec_id].stop_route_state == StopRouteState.PHYSICAL_ROUTING_ACCEPTED
    
    # Let it finish physically
    time.sleep(0.15)
    reconciler = TelemetryReconciler(transport, gate)
    reconciler.process_pending_events()
    
    # Should resolve to PREEMPTED but endpoint remains QUARANTINED
    assert gate._records[exec_id].current_state == CommandState.PREEMPTED
    assert ep._endpoint_state == EndpointState.QUARANTINED

def test_race_23_late_preempt_after_completed(endpoint_and_device, manager, gate, transport, publisher):
    ep, dev = endpoint_and_device
    exec_id = "exec_race_23"
    cmd = create_cmd(exec_id)
    ep.submit_command(cmd)
    
    time.sleep(0.15)
    reconciler = TelemetryReconciler(transport, gate)
    reconciler.process_pending_events()
    
    assert gate._records[exec_id].current_state == CommandState.COMPLETED
    
    # Fabricate late PREEMPTED observation
    from holomed.devices.models import ExecutionTelemetryEvent, EventSourceAuthority
    obs = ExecutionTelemetryEvent(
        event_id="evt_1",
        endpoint_id="ep_1",
        session_id="session_1",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id=exec_id,
        command_sequence=1,
        event_sequence=99,
        event_type="STATE_CHANGE",
        observed_state=CommandState.PREEMPTED,
        source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
        source_origin="tests",
        timestamp_utc="2026-09-15T00:00:00Z",
        payload={}
    )
    publisher.publish(obs)
    reconciler.process_pending_events()
    
    # Remains COMPLETED
    assert gate._records[exec_id].current_state == CommandState.COMPLETED
