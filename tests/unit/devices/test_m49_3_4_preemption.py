import pytest
import time
from holomed.devices.models import CommandState, EndpointSafetyState, EndpointState, DeviceType, EndpointLease, StopRouteState
from holomed.devices.simulated import SimulatedDevice, SimulatedPhysicalEndpoint
from holomed.devices.registry import DeviceRegistry
from unittest.mock import MagicMock
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.transport import TelemetryTransport, TelemetryPublisher
from holomed.devices.reconciler import TelemetryReconciler
from holomed.runtime.context import RuntimeContext
from holomed.devices.interfaces import RegistryAuthorityToken

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
    return DeviceRegistry(RegistryAuthorityToken())  # type: ignore

@pytest.fixture
def manager(registry, gate, publisher):
    from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel
    dcm = DeviceControlManager(registry=registry, resolution_gate=gate, rehydration_engine=MagicMock(), authoritative_epoch_provider=lambda: 1)
    ctx = RuntimeContext(app_config=AppConfig(app_name="test", environment=EnvironmentProfile.TESTING, host="localhost", port=8000, log_level=LogLevel.INFO), epoch_id=1)
    dcm.initialize(ctx)
    dcm.start()
    return dcm

@pytest.fixture
def endpoint_and_device(gate, publisher, registry):
    ep = SimulatedPhysicalEndpoint("ep_1", "dev_1", gate=gate, publisher=publisher)
    dev = SimulatedDevice("dev_1", "phys_1", DeviceType.SIMULATED_GENERIC)
    dev.set_endpoints((ep,))
    registry.register(dev, registry._token)
    
    lease = EndpointLease(
        device_epoch=1,
        controller_epoch=1,
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

def create_cmd(exec_id: str, seq: int = 1):
    from holomed.devices.models import PhysicalCommand
    return PhysicalCommand(
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op_123",
        command_nonce="nonce_abc",
        endpoint_id="ep_1",
        session_id="session_1",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id=exec_id,
        capability_scope=frozenset(["cap_1"]),
        command_sequence=seq,
        operation="move",
        parameters={},
    )

def test_pre_claim_cancellation(endpoint_and_device, manager, gate, transport):
    ep, dev = endpoint_and_device
    exec_id = "exec_pre_claim"
    cmd = create_cmd(exec_id)
    
    # We want to cancel before worker claims it.
    manager.preempt_execution("dev_1", "ep_1", exec_id, 1)
    
    # The Gate should record PRE_CLAIM_CANCELLED
    rec = gate._records[exec_id]
    assert rec.stop_route_state == StopRouteState.PRE_CLAIM_CANCELLED
    assert rec.current_state == CommandState.PREEMPTED
    
    # Worker tries to dequeue it
    ep._submit_lock.acquire()
    try:
        ep._command_queue.put(cmd)
    finally:
        ep._submit_lock.release()
        
    time.sleep(0.1)
    
    # Worker should fail to claim and NOT publish RUNNING
    events = transport.drain_normal()
    states = [e.observed_state for e in events if e.execution_id == exec_id]
    assert CommandState.RUNNING not in states
    # Note: Pre-claim cancellation generates no physical telemetry!

def test_post_claim_stop_routing(endpoint_and_device, manager, gate, transport):
    ep, dev = endpoint_and_device
    exec_id = "exec_post_claim"
    cmd = create_cmd(exec_id)
    
    ep.submit_command(cmd)
    
    # wait for RUNNING
    time.sleep(0.05)
    
    # Worker claimed it
    assert gate._records[exec_id].execution_claimed is True
    
    # Now route stop
    manager.preempt_execution("dev_1", "ep_1", exec_id, 1)
    
    assert gate._records[exec_id].stop_route_state == StopRouteState.PHYSICAL_ROUTING_ACCEPTED
    assert exec_id in ep._stop_requests
    
    # Let it finish physically
    time.sleep(0.15)
    
    reconciler = TelemetryReconciler(transport, gate)
    reconciler.process_pending_events()
    
    rec = gate._records[exec_id]
    assert rec.current_state == CommandState.PREEMPTED
    
def test_idempotent_stop_routing(endpoint_and_device, manager, gate):
    ep, dev = endpoint_and_device
    exec_id = "exec_idem"
    cmd = create_cmd(exec_id)
    
    ep.submit_command(cmd)
    time.sleep(0.05)
    
    manager.preempt_execution("dev_1", "ep_1", exec_id, 1)
    manager.preempt_execution("dev_1", "ep_1", exec_id, 1)
    
    assert gate._records[exec_id].stop_route_state == StopRouteState.PHYSICAL_ROUTING_ACCEPTED
    
def test_stop_routing_fails_to_deliver_terminal(endpoint_and_device, manager, gate, transport):
    ep, dev = endpoint_and_device
    exec_id = "exec_fail_deliver"
    cmd = create_cmd(exec_id)
    ep.submit_command(cmd)
    time.sleep(0.02)
    
    # Interlock worker so it doesn't emit PREEMPTED
    ep.inject_hardware_interlock()
    
    manager.preempt_execution("dev_1", "ep_1", exec_id, 1)
    
    time.sleep(0.15)
    
    reconciler = TelemetryReconciler(transport, gate)
    reconciler.process_pending_events()
    
    rec = gate.resolve_timeout(exec_id, 1)
    # The physical failure due to interlock results in INTERLOCKED -> QUARANTINED
    assert rec.current_state == CommandState.INTERLOCKED


# ---------------------------------------------------------------------------
# Issue #2: Pre-claim Gate authority (deterministic tests)
# ---------------------------------------------------------------------------

def test_preclaim_gate_authority_deterministic(gate, transport, publisher, registry, manager):
    """Prove: route_stop_request() pre-claim -> PRE_CLAIM_CANCELLED -> worker claim fails -> no RUNNING."""
    ep = SimulatedPhysicalEndpoint("ep_det_1", "dev_det_1", gate=gate, publisher=publisher)
    # Stop the default worker so we can control execution deterministically
    ep.stop_worker()

    dev = SimulatedDevice("dev_det_1", "phys_det_1", DeviceType.SIMULATED_GENERIC)
    dev.set_endpoints((ep,))
    registry.register(dev, registry._token)

    lease = EndpointLease(
        device_epoch=1,
        controller_epoch=1,
        endpoint_id="ep_det_1", device_id="dev_det_1", session_id="s1",
        lifecycle_generation=1, endpoint_lease_generation=1,
        execution_id="exec_det_1", capability_scope=frozenset(["cap"]),
    )
    ep.acquire_lease(lease)

    exec_id = "exec_det_preclaim"

    # Step 1: Pre-claim cancellation via Gate (before any worker interaction)
    route_result = gate.route_stop_request(exec_id, 1)
    assert route_result == StopRouteState.PRE_CLAIM_CANCELLED

    # Step 2: Verify Gate record
    rec = gate._records[exec_id]
    assert rec.current_state == CommandState.PREEMPTED
    assert rec.terminal_resolution_status is True
    assert rec.execution_claimed is False
    assert rec.stop_route_state == StopRouteState.PRE_CLAIM_CANCELLED

    # Step 3: Worker tries to claim -> must fail
    claimed = gate.claim_execution_ownership(exec_id, 1)
    assert claimed is False

    # Step 4: Verify no RUNNING telemetry was ever emitted
    events = transport.drain_normal()
    running_events = [e for e in events if e.execution_id == exec_id and e.observed_state == CommandState.RUNNING]
    assert len(running_events) == 0

    # Step 5: State remains PREEMPTED (not overwritten)
    assert gate._records[exec_id].current_state == CommandState.PREEMPTED


def test_preclaim_no_worker_local_preempted_bypass(gate, transport, publisher, registry, manager):
    """Prove: there is NO path from _stop_requests -> PREEMPTED before Gate claim."""
    ep = SimulatedPhysicalEndpoint("ep_det_2", "dev_det_2", gate=gate, publisher=publisher)
    # Stop default worker
    ep.stop_worker()

    dev = SimulatedDevice("dev_det_2", "phys_det_2", DeviceType.SIMULATED_GENERIC)
    dev.set_endpoints((ep,))
    registry.register(dev, registry._token)

    lease = EndpointLease(
        device_epoch=1,
        controller_epoch=1,
        endpoint_id="ep_det_2", device_id="dev_det_2", session_id="s2",
        lifecycle_generation=1, endpoint_lease_generation=1,
        execution_id="exec_det_2", capability_scope=frozenset(["cap"]),
    )
    ep.acquire_lease(lease)

    exec_id = "exec_det_bypass"

    # Add to worker-local stop set BEFORE any claim
    ep.request_stop(exec_id)
    assert exec_id in ep._stop_requests

    # Pre-claim cancel via Gate
    gate.route_stop_request(exec_id, 1)

    # Worker claim must fail because Gate already resolved
    claimed = gate.claim_execution_ownership(exec_id, 1)
    assert claimed is False

    # No telemetry at all (worker never ran)
    events = transport.drain_normal()
    bypass_events = [e for e in events if e.execution_id == exec_id]
    assert len(bypass_events) == 0

    # Gate state is authoritative PREEMPTED
    assert gate._records[exec_id].current_state == CommandState.PREEMPTED
    assert gate._records[exec_id].stop_route_state == StopRouteState.PRE_CLAIM_CANCELLED


def test_preclaim_gate_then_worker_dequeue_integration(gate, transport, publisher, registry, manager):
    """Full integration: pre-claim cancel -> worker dequeues -> claim rejected -> no physical execution."""
    import threading as thr

    # Use a barrier to synchronize: worker pauses before claiming
    claim_barrier = thr.Event()
    original_claim = gate.claim_execution_ownership

    claim_results = []

    def instrumented_claim(eid, gen):
        claim_barrier.wait(timeout=2.0)
        result = original_claim(eid, gen)
        claim_results.append((eid, result))
        return result

    gate.claim_execution_ownership = instrumented_claim

    ep = SimulatedPhysicalEndpoint("ep_det_3", "dev_det_3", gate=gate, publisher=publisher)
    dev = SimulatedDevice("dev_det_3", "phys_det_3", DeviceType.SIMULATED_GENERIC)
    dev.set_endpoints((ep,))
    registry.register(dev, registry._token)

    lease = EndpointLease(
        device_epoch=1,
        controller_epoch=1,
        endpoint_id="ep_det_3", device_id="dev_det_3", session_id="s3",
        lifecycle_generation=1, endpoint_lease_generation=1,
        execution_id="exec_det_3", capability_scope=frozenset(["cap"]),
    )
    ep.acquire_lease(lease)

    exec_id = "exec_det_integ"
    from holomed.devices.models import PhysicalCommand
    cmd = PhysicalCommand(
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op_123",
        command_nonce="nonce_abc",
        endpoint_id="ep_det_3", session_id="s3",
        lifecycle_generation=1, endpoint_lease_generation=1,
        execution_id=exec_id, capability_scope=frozenset(["cap"]),
        command_sequence=1, operation="move", parameters={},
    )

    # Submit command (worker will dequeue and block at barrier)
    ep.submit_command(cmd)

    # Give worker time to dequeue and reach the barrier
    time.sleep(0.15)

    # While worker is blocked at barrier, pre-claim cancel via Gate
    gate.route_stop_request(exec_id, 1)
    assert gate._records[exec_id].current_state == CommandState.PREEMPTED
    assert gate._records[exec_id].terminal_resolution_status is True

    # Release barrier -- worker calls claim, which returns False
    claim_barrier.set()
    time.sleep(0.1)

    # Verify claim was rejected
    matching = [r for r in claim_results if r[0] == exec_id]
    assert len(matching) == 1
    assert matching[0][1] is False

    # Verify no RUNNING telemetry
    events = transport.drain_normal()
    running = [e for e in events if e.execution_id == exec_id and e.observed_state == CommandState.RUNNING]
    assert len(running) == 0

    # Restore and cleanup
    gate.claim_execution_ownership = original_claim
    ep.stop_worker()


# ---------------------------------------------------------------------------
# Issue #3: Exactly-once physical routing authorization (deterministic tests)
# ---------------------------------------------------------------------------

def test_exactly_once_routing_gate_level(gate):
    """Prove: first route returns PHYSICAL_ROUTING_ACCEPTED, all duplicates return ALREADY_ROUTED."""
    exec_id = "exec_once"

    # Establish claim
    gate.claim_execution_ownership(exec_id, 1)
    assert gate._records[exec_id].execution_claimed is True

    # First stop -> PHYSICAL_ROUTING_ACCEPTED
    first = gate.route_stop_request(exec_id, 1)
    assert first == StopRouteState.PHYSICAL_ROUTING_ACCEPTED

    # Record stores PHYSICAL_ROUTING_ACCEPTED
    assert gate._records[exec_id].stop_route_state == StopRouteState.PHYSICAL_ROUTING_ACCEPTED

    # Second -> ALREADY_ROUTED
    second = gate.route_stop_request(exec_id, 1)
    assert second == StopRouteState.ALREADY_ROUTED

    # Third -> still ALREADY_ROUTED
    third = gate.route_stop_request(exec_id, 1)
    assert third == StopRouteState.ALREADY_ROUTED


def test_exactly_once_physical_delivery_via_manager(gate, transport, publisher, registry, manager):
    """Prove: manager calls request_stop exactly once even with duplicate preempt_execution calls."""
    ep = SimulatedPhysicalEndpoint("ep_once_1", "dev_once_1", gate=gate, publisher=publisher)
    dev = SimulatedDevice("dev_once_1", "phys_once_1", DeviceType.SIMULATED_GENERIC)
    dev.set_endpoints((ep,))
    registry.register(dev, registry._token)

    lease = EndpointLease(
        device_epoch=1,
        controller_epoch=1,
        endpoint_id="ep_once_1", device_id="dev_once_1", session_id="s_once",
        lifecycle_generation=1, endpoint_lease_generation=1,
        execution_id="exec_once_1", capability_scope=frozenset(["cap"]),
    )
    ep.acquire_lease(lease)

    exec_id = "exec_once_phys"
    from holomed.devices.models import PhysicalCommand
    cmd = PhysicalCommand(
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="op_123",
        command_nonce="nonce_abc",
        endpoint_id="ep_once_1", session_id="s_once",
        lifecycle_generation=1, endpoint_lease_generation=1,
        execution_id=exec_id, capability_scope=frozenset(["cap"]),
        command_sequence=1, operation="move", parameters={},
    )
    ep.submit_command(cmd)
    time.sleep(0.05)  # Let worker claim

    # Instrument request_stop to count calls
    stop_call_count = 0
    original_request_stop = ep.request_stop

    def counting_request_stop(execution_id: str):
        nonlocal stop_call_count
        stop_call_count += 1
        return original_request_stop(execution_id)

    ep.request_stop = counting_request_stop

    # First preempt -> should call request_stop
    manager.preempt_execution("dev_once_1", "ep_once_1", exec_id, 1)
    assert stop_call_count == 1

    # Second preempt -> Gate returns ALREADY_ROUTED -> manager does NOT call request_stop
    manager.preempt_execution("dev_once_1", "ep_once_1", exec_id, 1)
    assert stop_call_count == 1  # Still 1, not 2

    # Third preempt -> same
    manager.preempt_execution("dev_once_1", "ep_once_1", exec_id, 1)
    assert stop_call_count == 1  # Still 1

    # Restore and cleanup
    ep.request_stop = original_request_stop
    ep.stop_worker()
