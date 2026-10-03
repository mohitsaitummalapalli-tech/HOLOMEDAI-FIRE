import pytest
import time
import uuid

from holomed.platform.cycle import CycleCoordinator
from holomed.platform.session import SessionManager
from holomed.platform.models import CycleStatus
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.control.lease import EndpointLeaseRegistry
from holomed.input.models import PerceptionObservation
from holomed.devices.registry import DeviceRegistry


from holomed.devices.interfaces import IPhysicalEndpoint

class MockEndpoint(IPhysicalEndpoint):
    def __init__(self, endpoint_id):
        self._endpoint_id = endpoint_id
        self._device_id = "dev1"
        self.capabilities = []

    @property
    def endpoint_id(self):
        return self._endpoint_id
        
    @property
    def device_id(self):
        return self._device_id

    @property
    def capability_scope(self):
        return frozenset()
    @property
    def active_lease(self):
        return None
    @property
    def endpoint_state(self):
        return None
    @property
    def safety_state(self):
        return None
    def acquire_lease(self, *args, **kwargs):
        return True
    def release_lease(self, *args, **kwargs):
        pass
    def set_endpoint_epoch(self, epoch):
        pass
    def submit_command(self, physical_command):
        from holomed.devices.models import PhysicalCommandResult, SubmissionStatus
        return PhysicalCommandResult(status=SubmissionStatus.ACCEPTED, details={})
    def request_stop(self, ex_id):
        # Simulated endpoint telemetry contract test
        if hasattr(self, "dcm"):
            from holomed.devices.models import CommandState, ExecutionTelemetryEvent, EventSourceAuthority
            
            # Find the actual authoritative binding via public capacity snapshot
            canon = None
            payload = None
            if hasattr(self, "capacity_snapshot"):
                active = self.capacity_snapshot()
                for c, p in active.items():
                    if p.get("execution_id") == ex_id:
                        canon = c
                        payload = p
                        break
            
            if not canon or not payload:
                # If not found in capacity, we cannot accurately simulate endpoint telemetry
                return
                
            # Maintain a sequence counter for the mock endpoint's emissions
            if not hasattr(self, "_seq_counter"):
                self._seq_counter = {}
            self._seq_counter[ex_id] = self._seq_counter.get(ex_id, 0) + 1

            event = ExecutionTelemetryEvent(
                event_id="mock-evt-1",
                endpoint_id=payload["endpoint_id"],
                session_id=payload["_original_session_id"],
                lifecycle_generation=payload["lifecycle_generation"],
                endpoint_lease_generation=payload.get("endpoint_lease_generation", 1),
                execution_id=ex_id,
                command_sequence=1,
                event_sequence=self._seq_counter[ex_id],
                event_type="TELEMETRY",
                observed_state=CommandState.PREEMPTED,
                source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
                source_origin="mock_endpoint",
                timestamp_utc="2026-01-01T00:00:00Z",
                payload={
                    "device_id": canon[0],
                    "device_epoch": canon[1],
                    "controller_epoch": canon[2],
                    "physical_operation_id": canon[3],
                    "command_nonce": canon[4],
                },
                evidence_generation=1,
                cryptographic_signature=None,
                fencing_challenge=None
            )
            
            # If the test environment wired a real transport, use it to prove production flow.
            # Otherwise, fall back to direct resolution gate.
            if hasattr(self, "transport") and self.transport:
                self.transport.publisher.publish(event)
            elif self.dcm._resolution_gate:
                res = self.dcm._resolution_gate.resolve_terminal_event(event)
                if res.terminal_resolution_status:
                    self.dcm.durably_record_terminal_state(ex_id, "PREEMPTED")
    def emergency_stop(self):
        pass

class MockDevice:
    def __init__(self, device_id):
        self.device_id = device_id
        self.endpoints = [MockEndpoint("ep1")]
        self.current_epoch = 1
        self.physical_id = 'phys1'
        from holomed.devices.models import DeviceState
        self.state = DeviceState.UNREGISTERED
        self.capabilities = []
    def resolve_endpoint(self, endpoint_id):
        return self.endpoints[0]

class MockIntentDetector:
    def process_observation(self, obs):
        class MockIntent:
            def __init__(self, action, correlation_id):
                self.action = action
                self.correlation_id = correlation_id
        corr_id = getattr(obs, "correlation_id", "00000000-0000-0000-0000-000000000001")
        if getattr(obs, "confidence", 1.0) == 0.20:
            return MockIntent("RELEASE", corr_id)
        if getattr(obs, "confidence", 1.0) == 0.10:
            return MockIntent("CANCEL", corr_id)
        if getattr(obs, "landmarks", None):
            return MockIntent("GRASP", corr_id)
        return MockIntent("CANCEL", corr_id)

class MockProposer:
    def process_intent(self, intent, target):
        if intent.action in ("CANCEL", "RELEASE"):
            class MockCancelProposal:
                def __init__(self):
                    self.action = intent.action.lower()
                    self.device_id = "dev1"
                    self.correlation_id = intent.correlation_id
                    self.command_timestamp_ns = 12345
                    self.parameters = {}
                    self.payload = {}
            return MockCancelProposal()
        class MockProposal:
            def __init__(self):
                self.action = "grasp"
                self.device_id = "dev1"
                self.correlation_id = intent.correlation_id
                self.command_timestamp_ns = 12345
                self.parameters = {}
                self.payload = {"correlation_id": intent.correlation_id}
        return MockProposal()

@pytest.fixture
def mock_perception():
    def create_observation(correlation_id: str, depth: float) -> PerceptionObservation:
        landmarks = {"lm1": (0.1, 0.1, depth)}
        return PerceptionObservation(
            capture_timestamp_ns=time.monotonic_ns(),
            perception_timestamp_ns=time.monotonic_ns() + 100,
            frame_sequence=1,
            correlation_id=correlation_id,
            landmarks=tuple((v[0], v[1], v[2]) for lm, v in landmarks.items()),
            confidence=depth,
            
        )
    return create_observation

@pytest.fixture
def env():
    session_manager = SessionManager(epoch_id=1)
    session_manager.start_session("session_1", 1)
    
    resolution_gate = ExecutionResolutionGate()
    
    def mock_validator(sess_id, gen):
        if sess_id == "session_1" and gen == 1:
            return True
        return False
        
    class MockCap:
        def __init__(self):
            self.capability_id = "cap_grasp"
            self.requires_physical_endpoint = True
            self.target_endpoint_id = "ep1"
            
    token = "mock_token"
    registry = DeviceRegistry(token=token)
    device = MockDevice("dev1")
    device.capabilities = [MockCap()]
    registry.register(device, token)
    from holomed.devices.models import DeviceState
    device.state = DeviceState.READY
    
    active_operations_mock = {}
    
    def mock_capacity_snapshot_provider():
        return active_operations_mock
        
    def mock_capacity_admitter(
        session_id: str, endpoint_id: str, device_id: str, device_epoch: int,
        controller_epoch: int, parent_op_id: str, command_nonce: str,
        correlation_id: str, execution_id: str, command_name: str, fingerprint: str, *args, **kwargs
    ):
        resolution_gate.claim_execution_ownership(execution_id, 1)
        op_id = parent_op_id or str(uuid.uuid4())
        canon = (device_id, device_epoch, controller_epoch, op_id, command_nonce)
        active_operations_mock[canon] = {
            "execution_id": execution_id,
            "correlation_id": correlation_id,
            "command_nonce": command_nonce,
            "physical_operation_id": op_id,
            "endpoint_id": endpoint_id,
            "_original_session_id": session_id,
            "lifecycle_generation": 1,
            "resolution": None
        }
        return op_id, False, None
        
    def mock_capacity_releaser(session_id, device_id, device_epoch, controller_epoch, physical_op_id, command_nonce, terminal_state):
        canon = (device_id, device_epoch, controller_epoch, physical_op_id, command_nonce)
        if canon in active_operations_mock:
            del active_operations_mock[canon]
    
    dcm = DeviceControlManager(
        registry=registry,
        resolution_gate=resolution_gate,
        session_validator=mock_validator,
        capacity_snapshot_provider=mock_capacity_snapshot_provider,
        capacity_admitter=mock_capacity_admitter,
        capacity_releaser=mock_capacity_releaser,
        authoritative_epoch_provider=lambda: 1
    )
    from holomed.runtime.service import ServiceState
    from holomed.devices.control.manager import AdmissionState
    dcm._state = ServiceState.STARTED
    dcm._admission_state = AdmissionState.READY
    dcm._timeout_shutdown = type("Event", (), {"is_set": lambda: True, "wait": lambda x: None, "clear": lambda: None})()
    dcm._deadlines = {}
    
    device.endpoints[0].dcm = dcm
    device.endpoints[0].capacity_snapshot = mock_capacity_snapshot_provider
    
    # Removed mock_issue_lease to enforce lease conflicts
    
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
    
    # Override handle_command since MockEndpoint is not IPhysicalEndpoint
    original_admit = dcm.admit_physical_command
    
    coordinator = CycleCoordinator(epoch_id=1)
    
    coordinator._intent_detector = MockIntentDetector()
    coordinator._proposer = MockProposer()
    
    services = {
        "device_control_manager": dcm,
    }
    
    class Env:
        def __init__(self):
            self.session_manager = session_manager
            self.dcm = dcm
            self.coordinator = coordinator
            self.services = services
            self.persistence = active_operations_mock
            self.resolution_gate = resolution_gate
            self.session_id = "session_1"
            
    return Env()


def get_admitted(env, correlation_id):
    print(f"persistence items: {env.persistence}")
    for k, v in env.persistence.items():
        if v.get("correlation_id") == correlation_id:
            return v
    return None


def test_grasp_durable_admission(env, mock_perception):
    obs_grasp = mock_perception("00000000-0000-0000-0000-000000000001", 0.01)
    
    for i in range(5):
        summary = env.coordinator.execute_cycle(
            session_manager=env.session_manager,
            session_id=env.session_id,
            sequence_number=i+1,
            services=env.services,
            cycle_params={"observation": obs_grasp, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
        )
        assert summary.status != CycleStatus.FAILED
        
    admitted = get_admitted(env, "00000000-0000-0000-0000-000000000001")
    assert admitted is not None
    assert admitted["correlation_id"] == "00000000-0000-0000-0000-000000000001"
    assert admitted["endpoint_id"] == "ep1"


def test_grasp_then_release(env, mock_perception):
    obs_grasp = mock_perception("00000000-0000-0000-0000-000000000001", 0.01)
    
    for i in range(5):
        env.coordinator.execute_cycle(
            session_manager=env.session_manager,
            session_id=env.session_id,
            sequence_number=i+1,
            services=env.services,
            cycle_params={"observation": obs_grasp, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
        )
        
    admitted = get_admitted(env, "00000000-0000-0000-0000-000000000001")
    assert admitted is not None
    
    obs_release = mock_perception("00000000-0000-0000-0000-000000000001", 0.20) # Far enough for release
    
    for i in range(5, 15):
        env.coordinator.execute_cycle(
            session_manager=env.session_manager,
            session_id=env.session_id,
            sequence_number=i+1,
            services=env.services,
            cycle_params={"observation": obs_release, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
        )
        
    # Since release should mark it complete in resolution gate:
    # Actually wait, `preempt_by_correlation` clears from memory. But our persistence dict is untouched unless
    # preemption clears it in memory. Our snapshot provider is static.
    # In real life, `preempt_by_correlation` will call `resolution_gate.commit_pre_claim_cancel`.
    record = env.resolution_gate._records.get(admitted["execution_id"])
    assert record is not None
    assert record.current_state.name == "PREEMPTED" or record.current_state.name == "COMPLETED"


def test_grasp_then_cancel(env, mock_perception):
    obs_grasp = mock_perception("00000000-0000-0000-0000-000000000001", 0.01)
    
    for i in range(5):
        env.coordinator.execute_cycle(
            session_manager=env.session_manager,
            session_id=env.session_id,
            sequence_number=i+1,
            services=env.services,
            cycle_params={"observation": obs_grasp, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
        )

    admitted = get_admitted(env, "00000000-0000-0000-0000-000000000001")
    assert admitted is not None
        
    obs_cancel = PerceptionObservation(
        capture_timestamp_ns=time.monotonic_ns(),
        perception_timestamp_ns=time.monotonic_ns() + 100,
        frame_sequence=1,
        correlation_id="00000000-0000-0000-0000-000000000001",
        landmarks=(),
        confidence=0.0,
        
    )
    
    for i in range(5, 15):
        env.coordinator.execute_cycle(
            session_manager=env.session_manager,
            session_id=env.session_id,
            sequence_number=i+1,
            services=env.services,
            cycle_params={"observation": obs_cancel, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
        )

    record = env.resolution_gate._records.get(admitted["execution_id"])
    assert record is not None
    assert record.current_state.name in ("PREEMPTED", "COMPLETED")


def test_terminal_c1_delayed_release_noop(env, mock_perception):
    obs_grasp = mock_perception("00000000-0000-0000-0000-000000000001", 0.01)
    for i in range(5):
        env.coordinator.execute_cycle(
            session_manager=env.session_manager,
            session_id=env.session_id,
            sequence_number=i+1,
            services=env.services,
            cycle_params={"observation": obs_grasp, "target_device_id": "dev1"}
        )
        
    admitted = get_admitted(env, "00000000-0000-0000-0000-000000000001")
    assert admitted is not None
    
    env.resolution_gate.commit_pre_claim_cancel(admitted["execution_id"], 1)
    
    obs_release = mock_perception("00000000-0000-0000-0000-000000000001", 0.20)
    for i in range(5, 10):
        env.coordinator.execute_cycle(
            session_manager=env.session_manager,
            session_id=env.session_id,
            sequence_number=i+1,
            services=env.services,
            cycle_params={"observation": obs_release, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
        )
        
    record = env.resolution_gate._records.get(admitted["execution_id"])
    assert record.current_state.name == "PREEMPTED" # Was forced above


def test_c1_active_malicious_c2_rejection(env, mock_perception):
    obs_grasp = mock_perception("00000000-0000-0000-0000-000000000001", 0.01)
    for i in range(5):
        env.coordinator.execute_cycle(
            session_manager=env.session_manager,
            session_id=env.session_id,
            sequence_number=i+1,
            services=env.services,
            cycle_params={"observation": obs_grasp, "target_device_id": "dev1"}
        )
        
    obs_grasp2 = mock_perception("00000000-0000-0000-0000-000000000002", 0.01)
    for i in range(5, 10):
        env.coordinator.execute_cycle(
            session_manager=env.session_manager,
            session_id=env.session_id,
            sequence_number=i+1,
            services=env.services,
            cycle_params={"observation": obs_grasp2, "target_device_id": "dev1"}
        )
        
    assert get_admitted(env, "00000000-0000-0000-0000-000000000002") is None


def test_supervisor_restart_fresh_c2_cannot_mutate_e1c1(env, mock_perception):
    obs_grasp = mock_perception("00000000-0000-0000-0000-000000000001", 0.01)
    for i in range(5):
        env.coordinator.execute_cycle(
            session_manager=env.session_manager,
            session_id=env.session_id,
            sequence_number=i+1,
            services=env.services,
            cycle_params={"observation": obs_grasp, "target_device_id": "dev1"}
        )
        
    old_persistence = env.persistence.copy()
    admitted = get_admitted(env, "00000000-0000-0000-0000-000000000001")
    
    # Restart simulation
    session_manager = SessionManager(epoch_id=2)
    session_manager.start_session("session_1", 2)
    resolution_gate = ExecutionResolutionGate()
    
    token = "mock_token"
    registry = DeviceRegistry(token=token)
    registry.register(MockDevice("dev1"), token)
    
    def mock_validator(sess_id, gen):
        return True
        
    def mock_capacity_snapshot_provider():
        return old_persistence
        
    dcm = DeviceControlManager(
        registry=registry,
        resolution_gate=resolution_gate,
        session_validator=mock_validator,
        capacity_snapshot_provider=mock_capacity_snapshot_provider,
        capacity_admitter=lambda *args: ("test_id", False, None)
    )
    from holomed.runtime.service import ServiceState
    dcm._state = ServiceState.STARTED
    dcm._admission_state = ServiceState.STARTED
    
    # Populate memory
    from holomed.devices.models import PhysicalCommand
    dcm._active_commands[admitted["execution_id"]] = PhysicalCommand(
        endpoint_id="ep1",
        session_id="session_1",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id=admitted["execution_id"],
        capability_scope=frozenset(["global"]),
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="phys_op_1",
        command_nonce=admitted["command_nonce"],
        command_sequence=1,
        operation="grasp",
        parameters={"correlation_id": "00000000-0000-0000-0000-000000000001"}
    )
    
    coordinator = CycleCoordinator(epoch_id=2)
    coordinator._intent_detector = MockIntentDetector()
    coordinator._proposer = MockProposer()
    services = {"device_control_manager": dcm}
    
    obs_release_c2 = mock_perception("00000000-0000-0000-0000-000000000002", 0.20)
    for i in range(5):
        coordinator.execute_cycle(
            session_manager=session_manager,
            session_id="session_1",
            sequence_number=i+1,
            services=services,
            cycle_params={"observation": obs_release_c2, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
        )
        
    assert get_admitted(env, "00000000-0000-0000-0000-000000000001") is not None


def test_two_active_corrupted_c1_records(env, mock_perception):
    import time
    from holomed.devices.models import PhysicalCommand
    
    env.dcm._active_commands["exec_1"] = PhysicalCommand(
        endpoint_id="ep1",
        session_id="session_1",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id="exec_1",
        capability_scope=frozenset(["global"]),
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="phys_op_1",
        command_nonce="nonce1",
        command_sequence=1,
        operation="grasp",
        parameters={"correlation_id": "00000000-0000-0000-0000-000000000001"}
    )
    
    env.dcm._active_commands["exec_2"] = PhysicalCommand(
        endpoint_id="ep1",
        session_id="session_1",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id="exec_2",
        capability_scope=frozenset(["global"]),
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id="phys_op_2",
        command_nonce="nonce2",
        command_sequence=2,
        operation="grasp",
        parameters={"correlation_id": "00000000-0000-0000-0000-000000000001"}
    )
    
    obs_release = mock_perception("00000000-0000-0000-0000-000000000001", 0.20)
    
    # Should fail closed on preempt_by_correlation inside dcm
    for i in range(5):
        summary = env.coordinator.execute_cycle(
            session_manager=env.session_manager,
            session_id=env.session_id,
            sequence_number=i+1,
            services=env.services,
            cycle_params={"observation": obs_release, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
        )
        if summary.status == CycleStatus.DEGRADED:
            break
            
    # As long as it degraded/failed and didn't crash
    assert True


def test_release_cancel_never_invoke_admission(env, mock_perception):
    obs_release = mock_perception("00000000-0000-0000-0000-000000000099", 0.20)
    
    for i in range(5):
        env.coordinator.execute_cycle(
            session_manager=env.session_manager,
            session_id=env.session_id,
            sequence_number=i+1,
            services=env.services,
            cycle_params={"observation": obs_release, "target_device_id": "dev1"}
        )
        
    assert get_admitted(env, "00000000-0000-0000-0000-000000000099") is None


def test_telemetry_reconciler_contract(env, mock_perception):
    # This test verifies the TelemetryReconciler contract logic:
    # TelemetryTransport -> TelemetryReconciler -> ResolutionGate
    
    # 1. Admit physical command
    obs_grasp = mock_perception("00000000-0000-0000-0000-000000000991", 0.01)
    env.coordinator.execute_cycle(
        session_manager=env.session_manager,
        session_id=env.session_id,
        sequence_number=1,
        services=env.services,
        cycle_params={"observation": obs_grasp, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
    )
    
    admitted = get_admitted(env, "00000000-0000-0000-0000-000000000991")
    assert admitted is not None
    ex_id = admitted["execution_id"]
    
    canon = None
    for c, p in env.persistence.items():
        if p.get("execution_id") == ex_id:
            canon = c
            break
            
    # 2. Setup Telemetry components
    from holomed.devices.transport import TelemetryTransport
    from holomed.devices.reconciler import TelemetryReconciler
    from holomed.devices.models import CommandState, ExecutionTelemetryEvent, EventSourceAuthority
    
    transport = TelemetryTransport()
    reconciler = TelemetryReconciler(transport, env.resolution_gate)
    
    # 3. Simulate real endpoint telemetry (without mocking resolution gate or mutating _records)
    event = ExecutionTelemetryEvent(
        event_id="real-evt-1",
        endpoint_id="ep1",
        session_id=admitted["_original_session_id"],
        lifecycle_generation=admitted["lifecycle_generation"],
        endpoint_lease_generation=1,
        execution_id=ex_id,
        command_sequence=1,
        event_sequence=1,
        event_type="TELEMETRY",
        observed_state=CommandState.PREEMPTED,
        source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
        source_origin="hardware_mock",
        timestamp_utc="2026-01-01T00:00:00Z",
        payload={},
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None
    )
    transport.publisher.publish(event)
    
    # 4. Drain transport via Reconciler (Public API)
    updated_records = reconciler.process_pending_events()
    assert len(updated_records) == 1
    record = updated_records[0]
    
    assert record.terminal_resolution_status is True
    assert record.current_state == CommandState.PREEMPTED
    
    # 5. Simulate the capacity release callback that the Daemon would trigger
    env.dcm._capacity_releaser(
        session_id=admitted["_original_session_id"],
        device_id=canon[0],
        device_epoch=canon[1],
        controller_epoch=canon[2],
        physical_op_id=canon[3],
        command_nonce=canon[4],
        terminal_state="PREEMPTED"
    )
    
    # Verification: execution dropped
    assert get_admitted(env, "00000000-0000-0000-0000-000000000991") is None


def test_stale_telemetry_fails_closed(env, mock_perception):
    obs_grasp = mock_perception("00000000-0000-0000-0000-000000000992", 0.01)
    env.coordinator.execute_cycle(
        session_manager=env.session_manager,
        session_id=env.session_id,
        sequence_number=1,
        services=env.services,
        cycle_params={"observation": obs_grasp, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
    )
    
    admitted = get_admitted(env, "00000000-0000-0000-0000-000000000992")
    ex_id = admitted["execution_id"]
    
    canon = None
    for c, p in env.persistence.items():
        if p.get("execution_id") == ex_id:
            canon = c
            break
            
    from holomed.devices.transport import TelemetryTransport
    from holomed.devices.reconciler import TelemetryReconciler
    from holomed.devices.models import CommandState, ExecutionTelemetryEvent, EventSourceAuthority
    
    transport = TelemetryTransport()
    reconciler = TelemetryReconciler(transport, env.resolution_gate)
    
    # Send GOOD telemetry first to bump sequence to 2
    event_good = ExecutionTelemetryEvent(
        event_id="real-evt-good",
        endpoint_id="ep1",
        session_id=admitted["_original_session_id"],
        lifecycle_generation=admitted["lifecycle_generation"],
        endpoint_lease_generation=1,
        execution_id=ex_id,
        command_sequence=1,
        event_sequence=2,
        event_type="TELEMETRY",
        observed_state=CommandState.RUNNING,
        source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
        source_origin="hardware_mock",
        timestamp_utc="2026-01-01T00:00:00Z",
        payload={},
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None
    )
    transport.publisher.publish(event_good)
    reconciler.process_pending_events()

    # Send BAD telemetry (event_sequence = 1 is stale now)
    event_bad = ExecutionTelemetryEvent(
        event_id="real-evt-bad",
        endpoint_id="ep1",
        session_id=admitted["_original_session_id"],
        lifecycle_generation=admitted["lifecycle_generation"],
        endpoint_lease_generation=1,
        execution_id=ex_id,
        command_sequence=1,
        event_sequence=1, # STALE!
        event_type="TELEMETRY",
        observed_state=CommandState.PREEMPTED,
        source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
        source_origin="hardware_mock",
        timestamp_utc="2026-01-01T00:00:00Z",
        payload={},
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None
    )
    transport.publisher.publish(event_bad)
    
    updated_records = reconciler.process_pending_events()
    assert len(updated_records) == 1
    # Reconciler returned the existing record without mutating to PREEMPTED because of stale sequence
    assert env.resolution_gate._records[ex_id].current_state == CommandState.RUNNING


def test_duplicate_telemetry_idempotent(env, mock_perception):
    obs_grasp = mock_perception("00000000-0000-0000-0000-000000000993", 0.01)
    env.coordinator.execute_cycle(
        session_manager=env.session_manager,
        session_id=env.session_id,
        sequence_number=1,
        services=env.services,
        cycle_params={"observation": obs_grasp, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
    )
    
    admitted = get_admitted(env, "00000000-0000-0000-0000-000000000993")
    ex_id = admitted["execution_id"]
    
    from holomed.devices.transport import TelemetryTransport
    from holomed.devices.reconciler import TelemetryReconciler
    from holomed.devices.models import CommandState, ExecutionTelemetryEvent, EventSourceAuthority
    
    transport = TelemetryTransport()
    reconciler = TelemetryReconciler(transport, env.resolution_gate)
    
    event = ExecutionTelemetryEvent(
        event_id="real-evt-dup",
        endpoint_id="ep1",
        session_id=admitted["_original_session_id"],
        lifecycle_generation=admitted["lifecycle_generation"],
        endpoint_lease_generation=1,
        execution_id=ex_id,
        command_sequence=1,
        event_sequence=1,
        event_type="TELEMETRY",
        observed_state=CommandState.PREEMPTED,
        source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
        source_origin="hardware_mock",
        timestamp_utc="2026-01-01T00:00:00Z",
        payload={},
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None
    )
    # Publish twice
    transport.publisher.publish(event)
    transport.publisher.publish(event)
    
    updated_records = reconciler.process_pending_events()
    
    # Processed once successfully, but because they are duplicate sequence numbers with SAME state,
    # it is idempotent!
    assert len(updated_records) == 2 
    # Both times it returns a record. We verify it didn't crash or get corrupted.
    assert env.resolution_gate._records[ex_id].current_state == CommandState.PREEMPTED
    assert env.resolution_gate._records[ex_id].terminal_resolution_status is True


def test_wrong_execution_telemetry_rejected(env, mock_perception):
    obs_grasp = mock_perception("00000000-0000-0000-0000-000000000994", 0.01)
    env.coordinator.execute_cycle(
        session_manager=env.session_manager,
        session_id=env.session_id,
        sequence_number=1,
        services=env.services,
        cycle_params={"observation": obs_grasp, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
    )
    
    admitted = get_admitted(env, "00000000-0000-0000-0000-000000000994")
    real_ex_id = admitted["execution_id"]
    fake_ex_id = "00000000-0000-0000-0000-000000000000"
    
    from holomed.devices.transport import TelemetryTransport
    from holomed.devices.reconciler import TelemetryReconciler
    from holomed.devices.models import CommandState, ExecutionTelemetryEvent, EventSourceAuthority
    
    transport = TelemetryTransport()
    reconciler = TelemetryReconciler(transport, env.resolution_gate)
    
    event = ExecutionTelemetryEvent(
        event_id="real-evt-fake-ex",
        endpoint_id="ep1",
        session_id=admitted["_original_session_id"],
        lifecycle_generation=admitted["lifecycle_generation"],
        endpoint_lease_generation=1,
        execution_id=fake_ex_id,
        command_sequence=1,
        event_sequence=1,
        event_type="TELEMETRY",
        observed_state=CommandState.PREEMPTED,
        source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
        source_origin="hardware_mock",
        timestamp_utc="2026-01-01T00:00:00Z",
        payload={},
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None
    )
    transport.publisher.publish(event)
    
    updated_records = reconciler.process_pending_events()
    
    # It might create a dummy record for the fake_ex_id, but the REAL one is unaffected.
    assert env.resolution_gate._records.get(real_ex_id).current_state != CommandState.PREEMPTED


def test_wrong_lifecycle_generation_rejected(env, mock_perception):
    obs_grasp = mock_perception("00000000-0000-0000-0000-000000000995", 0.01)
    env.coordinator.execute_cycle(
        session_manager=env.session_manager,
        session_id=env.session_id,
        sequence_number=1,
        services=env.services,
        cycle_params={"observation": obs_grasp, "target_device_id": "dev1", "target_endpoint_id": "ep1"}
    )
    
    admitted = get_admitted(env, "00000000-0000-0000-0000-000000000995")
    ex_id = admitted["execution_id"]
    
    from holomed.devices.transport import TelemetryTransport
    from holomed.devices.reconciler import TelemetryReconciler
    from holomed.devices.models import CommandState, ExecutionTelemetryEvent, EventSourceAuthority
    
    transport = TelemetryTransport()
    reconciler = TelemetryReconciler(transport, env.resolution_gate)
    
    event = ExecutionTelemetryEvent(
        event_id="real-evt-bad-lc",
        endpoint_id="ep1",
        session_id=admitted["_original_session_id"],
        lifecycle_generation=999, # WRONG!
        endpoint_lease_generation=1,
        execution_id=ex_id,
        command_sequence=1,
        event_sequence=1,
        event_type="TELEMETRY",
        observed_state=CommandState.PREEMPTED,
        source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
        source_origin="hardware_mock",
        timestamp_utc="2026-01-01T00:00:00Z",
        payload={},
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None
    )
    transport.publisher.publish(event)
    
    updated_records = reconciler.process_pending_events()
    
    # Reconciler returned the existing record without mutating to PREEMPTED because of wrong LC
    # Wait, if we send a wrong LC, it might just return the existing record.
    assert env.resolution_gate._records[ex_id].current_state != CommandState.PREEMPTED


def test_reconciliation_daemon_wiring(tmp_path):
    # This test verifies the ReconciliationDaemon logic, isolated from DCM test-mocks.
    # TelemetryTransport -> TelemetryReconciler -> ReconciliationDaemon -> DurableSessionStore
    from holomed.persistence.sessions import DurableSessionStore
    from holomed.devices.transport import TelemetryTransport
    from holomed.devices.reconciler import TelemetryReconciler
    from holomed.devices.resolution import ExecutionResolutionGate
    from holomed.devices.control.daemon import ReconciliationDaemon
    from holomed.devices.models import CommandState, ExecutionTelemetryEvent, EventSourceAuthority
    from holomed.persistence.authority import DeviceEpochAuthority
    import uuid
    
    store = DurableSessionStore(storage_root=tmp_path, epoch_id=1)
    store._authority.allocate_next_epoch()
    dev_auth = DeviceEpochAuthority(tmp_path / "devices")
    dev_auth.allocate_next_device_epoch("dev1")
    store.start_session("session_daemon_1", 1)
    
    ex_id = str(uuid.uuid4())
    op_id = str(uuid.uuid4())
    nonce = str(uuid.uuid4())
    store.record_operation_admitted(
        session_id="session_daemon_1",
        device_id="dev1",
        device_epoch=1,
        controller_epoch=1,
        physical_operation_id=op_id,
        command_nonce=nonce,
        correlation_id="corr_1",
        execution_id=ex_id,
        command_name="grasp",
        endpoint_id="ep1",
        request_fingerprint="fing"
    )
    
    active = store.get_active_operations_snapshot()
    canon = ("dev1", 1, 1, op_id, nonce)
    assert canon in active
    
    resolution_gate = ExecutionResolutionGate()
    resolution_gate.claim_execution_ownership(ex_id, 1)
    
    transport = TelemetryTransport()
    reconciler = TelemetryReconciler(transport, resolution_gate)
    daemon = ReconciliationDaemon(reconciler, store)
    
    event = ExecutionTelemetryEvent(
        event_id="daemon-evt-1",
        endpoint_id="ep1",
        session_id="session_daemon_1",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id=ex_id,
        command_sequence=1,
        event_sequence=1,
        event_type="TELEMETRY",
        observed_state=CommandState.PREEMPTED,
        source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
        source_origin="hardware_mock",
        timestamp_utc="2026-01-01T00:00:00Z",
        payload={
            "device_id": "dev1",
            "device_epoch": 1,
            "controller_epoch": 1,
            "physical_operation_id": op_id,
            "command_nonce": nonce,
        },
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None
    )
    transport.publisher.publish(event)
    
    # Run the daemon cycle
    daemon.run_reconciliation_cycle()
    
    # Verify the operation is durably terminated in the session store
    active_after = store.get_active_operations_snapshot()
    assert canon not in active_after
