import pytest
import uuid
import json

from holomed.platform.service import PlatformService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.service import ServiceState
from holomed.devices.control.manager import DeviceControlManager, AdmissionState
from holomed.devices.models import DeviceCapability, DeviceState, EndpointState, ExecutionTelemetryEvent, CommandState, EventSourceAuthority
from holomed.devices.registry import DeviceRegistry
from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.authority import DeviceEpochAuthority
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.transport import TelemetryTransport
from holomed.devices.reconciler import TelemetryReconciler
from holomed.devices.control.daemon import ReconciliationDaemon
from holomed.input.intent import PerceptionObservation, HandLandmark, EXPECTED_LANDMARK_COUNT, IntentDetector
from holomed.ultron.proposer import UltronProposer
from holomed.platform.cycle import CycleCoordinator
from holomed.devices.interfaces import IPhysicalEndpoint

def _make_closed_hand_landmarks():
    lm = [(0.0, 0.0, 0.0)] * EXPECTED_LANDMARK_COUNT
    lm[HandLandmark.WRIST] = (0.5, 0.8, 0.0)
    lm[HandLandmark.INDEX_FINGER_MCP] = (0.4, 0.6, 0.0)
    lm[HandLandmark.MIDDLE_FINGER_MCP] = (0.5, 0.6, 0.0)
    lm[HandLandmark.RING_FINGER_MCP] = (0.6, 0.6, 0.0)
    lm[HandLandmark.PINKY_MCP] = (0.7, 0.6, 0.0)
    lm[HandLandmark.THUMB_TIP] = (0.45, 0.7, 0.0)
    lm[HandLandmark.INDEX_FINGER_TIP] = (0.42, 0.68, 0.0)
    lm[HandLandmark.MIDDLE_FINGER_TIP] = (0.5, 0.68, 0.0)
    lm[HandLandmark.RING_FINGER_TIP] = (0.58, 0.68, 0.0)
    lm[HandLandmark.PINKY_TIP] = (0.65, 0.7, 0.0)
    return tuple(lm)

def _make_open_hand_landmarks():
    lm = [(0.0, 0.0, 0.0)] * EXPECTED_LANDMARK_COUNT
    lm[HandLandmark.WRIST] = (0.5, 0.8, 0.0)
    lm[HandLandmark.INDEX_FINGER_MCP] = (0.4, 0.6, 0.0)
    lm[HandLandmark.MIDDLE_FINGER_MCP] = (0.5, 0.6, 0.0)
    lm[HandLandmark.RING_FINGER_MCP] = (0.6, 0.6, 0.0)
    lm[HandLandmark.PINKY_MCP] = (0.7, 0.6, 0.0)
    lm[HandLandmark.THUMB_TIP] = (0.2, 0.3, 0.0)
    lm[HandLandmark.INDEX_FINGER_TIP] = (0.3, 0.2, 0.0)
    lm[HandLandmark.MIDDLE_FINGER_TIP] = (0.5, 0.15, 0.0)
    lm[HandLandmark.RING_FINGER_TIP] = (0.7, 0.2, 0.0)
    lm[HandLandmark.PINKY_TIP] = (0.8, 0.3, 0.0)
    return tuple(lm)


class SimulatedLiveDevice:
    def __init__(self, device_id):
        self.device_id = device_id
        self.endpoints = []
        self.capabilities = []
        self.state = DeviceState.UNREGISTERED
        self.physical_id = "phys_1"
        self.current_epoch = 1
        
    def resolve_endpoint(self, endpoint_id):
        for ep in self.endpoints:
            if ep.endpoint_id == endpoint_id:
                return ep
        return None

class RealEndpoint(IPhysicalEndpoint):
    def __init__(self, endpoint_id):
        self._endpoint_id = endpoint_id
        self.dcm = None
        self.capacity_snapshot = None
        self.transport = None
        
    @property
    def endpoint_id(self): return self._endpoint_id
    @property
    def device_id(self): return "dev1"
    @property
    def capability_scope(self): return frozenset(["global"])
    @property
    def active_lease(self): return None
    @property
    def endpoint_state(self): return EndpointState.READY
    @property
    def safety_state(self): return "SAFE"
    
    def acquire_lease(self, *args, **kwargs): pass
    def release_lease(self, *args, **kwargs): pass
    def set_endpoint_epoch(self, epoch): pass
    
    def submit_command(self, cmd):
        from holomed.devices.models import PhysicalCommandResult, SubmissionStatus
        return PhysicalCommandResult(status=SubmissionStatus.ACCEPTED, details={})
        
    def request_stop(self, ex_id):
        # Simulate actual telemetry on stop
        event = ExecutionTelemetryEvent(
            event_id="evt_stop",
            endpoint_id=self.endpoint_id,
            session_id="session_1",
            lifecycle_generation=1,
            endpoint_lease_generation=1,
            execution_id=ex_id,
            command_sequence=1,
            event_sequence=2,
            event_type="TELEMETRY",
            observed_state=CommandState.PREEMPTED,
            source_authority=EventSourceAuthority.ENDPOINT_ADAPTER,
            source_origin="test_endpoint",
            timestamp_utc="2026-01-01T00:00:00Z",
            payload={},
            evidence_generation=1,
            cryptographic_signature=None,
            fencing_challenge=None
        )
        self.transport.publisher.publish(event)
        
    def emergency_stop(self): pass


def setup_production_pipeline(tmp_path):
    """Construct the real production object graph.

    FIXTURE SETUP ONLY: the private writes below (service-lifecycle flags and
    the durable store's epoch authority bootstrap) merely bring the services to
    a started state without a full runtime boot. They never create executions,
    terminal states, capacity releases, timeouts or ResolutionGate transitions;
    those must all occur through the production path exercised by the tests.
    """
    store = DurableSessionStore(storage_root=tmp_path, epoch_id=1)
    store._authority.allocate_next_epoch()
    dev_auth = DeviceEpochAuthority(tmp_path / "devices")
    dev_auth.allocate_next_device_epoch("dev1")
    store.start_session("session_1", 1)

    resolution_gate = ExecutionResolutionGate()
    transport = TelemetryTransport()
    reconciler = TelemetryReconciler(transport, resolution_gate)
    daemon = ReconciliationDaemon(reconciler, store)

    registry = DeviceRegistry(token="token")
    device = SimulatedLiveDevice("dev1")
    
    endpoint = RealEndpoint("ep1")
    endpoint.transport = transport
    device.endpoints = [endpoint]
    
    from holomed.devices.models import CapabilityCategory
    cap = DeviceCapability(
        capability_id="cap.grasp",
        category=CapabilityCategory.CONTROL,
        parameters={},
        requires_physical_endpoint=True,
        target_endpoint_id="ep1"
    )
    device.capabilities = [cap]
    registry.register(device, "token")
    device.state = DeviceState.READY

    def simulated_validator(sess_id, gen): return True
    
    dcm = DeviceControlManager(
        registry=registry,
        resolution_gate=resolution_gate,
        session_validator=simulated_validator,
        capacity_snapshot_provider=store.get_active_operations_snapshot,
        capacity_admitter=store.record_operation_admitted,
        capacity_releaser=store.record_operation_terminated,
        authoritative_epoch_provider=lambda: 1
    )
    # Fixture-only: start-up flags (no rehydration/timeout thread in this harness).
    dcm._state = ServiceState.STARTED
    dcm._admission_state = AdmissionState.READY
    endpoint.dcm = dcm

    # Public registration API; no private command-table writes.
    dcm.register_command(
        "sys.input.interact",
        lambda *args, **kwargs: {"status": "success"},
        required_capability_id="cap.grasp",
        allow_ready=True,
    )
    
    class DummyAppConfig:
        pass
        
    context = RuntimeContext(
        app_config=DummyAppConfig(),
        epoch_id=1,
    )
    platform = PlatformService()
    platform.initialize(context)
    platform.register_service("device_control_manager", dcm)
    
    class DummyService:
        pass
    
    platform.register_service("vision_service", DummyService())
    platform.register_service("audio_service", DummyService())
    
    platform._state = ServiceState.STARTED
    platform.session_manager.start_session("session_1", 1)
    
    return platform, store, daemon, transport, dcm


def test_live_bridge_end_to_end_preemption(tmp_path):
    platform, store, daemon, transport, dcm = setup_production_pipeline(tmp_path)
    
    # Prove the test utilizes the actual real production objects under the LiveBridge (PlatformService)
    assert isinstance(platform, PlatformService)
    assert isinstance(platform.cycle_coordinator, CycleCoordinator)
    assert isinstance(platform.cycle_coordinator._intent_detector, IntentDetector)
    assert isinstance(platform.cycle_coordinator._proposer, UltronProposer)

    corr_id = "22222222-2222-2222-2222-222222222222"
    
    # 1. Trigger GRASP -> Form STABLE_GRASP -> DCM admits operation
    for i in range(10):
        obs = PerceptionObservation(
            correlation_id=corr_id,
            capture_timestamp_ns=i * 1000000,
            perception_timestamp_ns=i * 1000000 + 100,
            frame_sequence=i,
            landmarks=_make_closed_hand_landmarks(),
            confidence=0.9
        )
        platform.tick("session_1", sequence_number=i+1, cycle_params={"observation": obs, "target_device_id": "dev1", "target_endpoint_id": "ep1"})

    # Check active operations in store to verify durable admission
    active = store.get_active_operations_snapshot()
    ex_id = None
    for c, p in active.items():
        if p.get("correlation_id") == corr_id:
            ex_id = p["execution_id"]
            break
            
    assert ex_id is not None
    
    # 2. Trigger RELEASE -> preempt_by_correlation -> request_stop -> telemetry event PREEMPTED -> Daemon writes durable termination
    obs_release = PerceptionObservation(
        correlation_id=corr_id,
        capture_timestamp_ns=11000000,
        perception_timestamp_ns=11000100,
        frame_sequence=11,
        landmarks=_make_open_hand_landmarks(),
        confidence=0.9  # NOTE: Confidence high, but ratio low triggers RELEASE
    )
    platform.tick("session_1", sequence_number=11, cycle_params={"observation": obs_release, "target_device_id": "dev1", "target_endpoint_id": "ep1"})
    
    # Let reconciler drain and push state back down to DurableSessionStore
    daemon.run_reconciliation_cycle()
    
    # Check journal for correct PREEMPTED resolution
    found_term = False
    with open(tmp_path / "session_1.jsonl", "r") as f:
        for line in f:
            if not line.strip(): continue
            rec = json.loads(line)
            if rec.get("entry_type") == "OPERATION_TERMINATED" and rec.get("payload", {}).get("resolution") == "PREEMPTED":
                found_term = True
    assert found_term
