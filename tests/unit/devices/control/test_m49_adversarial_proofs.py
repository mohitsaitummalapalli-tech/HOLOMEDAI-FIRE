import os
import json
import time
import pytest
import subprocess
import sys
from pathlib import Path
import shutil

from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.authority import ControllerAuthorityStore
from holomed.devices.control.models import GLOBAL_PHYSICAL_OPERATION_CAPACITY
from holomed.persistence.exceptions import PersistenceCapacityError, PersistenceEpochMismatchError, PersistenceIdentityReuseError, PersistenceLifecycleError, PersistenceValidationError

HELPER_SCRIPT = Path(__file__).parent / "_proof_helper.py"

@pytest.fixture
def shared_store_path(tmp_path_factory):
    # Explicitly create a truly shared directory for all workers
    return tmp_path_factory.mktemp("shared_proof_store")

def setup_test_store(path: Path) -> tuple[ControllerAuthorityStore, int, str]:
    authority = ControllerAuthorityStore(path)
    epoch = authority.allocate_next_epoch()
    store = DurableSessionStore(path, epoch_id=epoch)
    store.start_session("proof_session", epoch)
    return authority, epoch, "proof_session"

# ---------------------------------------------------------
# 1. EPOCH TOCTOU RACE PROOF
# ---------------------------------------------------------
def test_epoch_lock_serialization(shared_store_path):
    """
    Test A & B combined: Process A acquires lock, B waits.
    We spawn worker A, then worker B. Worker B should be blocked for at least 1.5s
    by worker A which holds the physical epoch lock.
    """
    authority, epoch, session = setup_test_store(shared_store_path)

    pa = subprocess.Popen([sys.executable, str(HELPER_SCRIPT), "race_a", str(shared_store_path), str(epoch)], stdout=subprocess.PIPE, text=True)
    pb = subprocess.Popen([sys.executable, str(HELPER_SCRIPT), "race_b", str(shared_store_path)], stdout=subprocess.PIPE, text=True)

    out_a, _ = pa.communicate()
    out_b, _ = pb.communicate()

    assert "SUCCESS" in out_a, "Process A failed to mutate."
    assert "ADVANCED" in out_b, "Process B failed to advance."

def test_stale_epoch_rejection(shared_store_path):
    """
    Test B: Stale writer rejection.
    Process A has epoch E, Process B advances to E+1. Process A's mutation must be rejected.
    """
    authority, epoch, session = setup_test_store(shared_store_path)

    # Process A attempts mutation with stale epoch, but we instantiate the store FIRST
    store_a = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store_a.restore_session_from_disk(session)

    # Process B advances authority AFTER store A is created
    epoch_2 = authority.allocate_next_epoch()
    assert epoch_2 > epoch

    with pytest.raises(PersistenceEpochMismatchError):
        store_a.record_operation_admitted(
            session, "ep_a", "dev_a", 1, 1, "op_a", "nonce_a", "exec_a", "test"
        )

# ---------------------------------------------------------
# 3. GLOBAL CAPACITY PROOF
# ---------------------------------------------------------
def test_global_capacity_multiprocess_proof(shared_store_path):
    authority, epoch, session = setup_test_store(shared_store_path)

    N = GLOBAL_PHYSICAL_OPERATION_CAPACITY
    K = 10
    total_workers = N + K

    procs = []
    for i in range(total_workers):
        p = subprocess.Popen([sys.executable, str(HELPER_SCRIPT), "capacity", str(shared_store_path), str(epoch), f"ep_{i}", f"op_{i}"], stdout=subprocess.PIPE, text=True)
        procs.append(p)

    results = []
    for p in procs:
        out, _ = p.communicate()
        results.append(out.strip())

    admitted = sum(1 for r in results if "ADMITTED" in r)
    rejected = sum(1 for r in results if "CAPACITY_REJECTED" in r)
    unexpected = sum(1 for r in results if "UNEXPECTED" in r)

    assert unexpected == 0, f"Setup errors occurred: {results}"
    assert admitted == N, f"Expected {N} admitted, got {admitted}"
    assert rejected == K, f"Expected {K} rejected, got {rejected}"

    # Inspect final journal
    store = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store.restore_session_from_disk(session)
    assert store.get_active_physical_operations() == N

# ---------------------------------------------------------
# 4. ENDPOINT CAPACITY PROOF
# ---------------------------------------------------------
def test_endpoint_capacity_multiprocess_proof(shared_store_path):
    authority, epoch, session = setup_test_store(shared_store_path)

    M = 10

    procs = []
    for i in range(M):
        p = subprocess.Popen([sys.executable, str(HELPER_SCRIPT), "capacity", str(shared_store_path), str(epoch), "shared_ep", f"op_{i}"], stdout=subprocess.PIPE, text=True)
        procs.append(p)

    results = []
    for p in procs:
        out, _ = p.communicate()
        results.append(out.strip())

    admitted = sum(1 for r in results if "ADMITTED" in r)
    rejected = sum(1 for r in results if "CAPACITY_REJECTED" in r)

    assert admitted == 1, f"Expected exactly 1 admitted to same endpoint, got {admitted}"
    assert rejected == M - 1, f"Expected {M-1} rejected, got {rejected}"

# ---------------------------------------------------------
# 5. SAME-JOURNAL CONCURRENCY PROOF
# ---------------------------------------------------------
def test_same_journal_concurrency_proof(shared_store_path):
    authority, epoch, session = setup_test_store(shared_store_path)

    procs = []
    for i in range(10):
        p = subprocess.Popen([sys.executable, str(HELPER_SCRIPT), "concurrency", str(shared_store_path), str(epoch), f"op_{i}"], stdout=subprocess.PIPE, text=True)
        procs.append(p)

    for p in procs:
        out, _ = p.communicate()
        assert "FAILED" not in out, f"Worker failed: {out}"

    from holomed.persistence.journal import JournalReader
    entries, _ = JournalReader.read_and_recover_journal(shared_store_path / f"{session}.jsonl")

    sequences = [e.sequence_number for e in entries]
    # 1 session start + 30 admissions = 31 entries
    assert len(sequences) == 31, f"Expected 31 exact commits, got {len(sequences)}"
    assert len(sequences) == len(set(sequences)), "Duplicate sequence numbers found!"
    assert sorted(sequences) == list(range(-1, len(sequences) - 1)), "Unbroken hash chain and monotonic sequences failed!"

# ---------------------------------------------------------
# 6. CRASH-TAIL RECOVERY PROOF
# ---------------------------------------------------------
def test_crash_tail_recovery_proof(shared_store_path):
    authority, epoch, session = setup_test_store(shared_store_path)

    store = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store.restore_session_from_disk(session)
    store.record_operation_admitted(
        session, "ep_c", "dev_c", 1, 1, "op_c", "nonce_c", "exec_c", "test"
    )

    # Append corrupted JSON directly
    with open(shared_store_path / f"{session}.jsonl", "a", encoding="utf-8") as f:
        f.write('{"corrupt": true\n')

    # Recovery should strip the corrupt tail and allow the next admission
    store2 = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store2.restore_session_from_disk(session)
    store2.record_operation_admitted(
        session, "ep_c2", "dev_c2", 1, 1, "op_c2", "nonce_c2", "exec_c2", "test"
    )

    from holomed.persistence.journal import JournalReader
    entries, _ = JournalReader.read_and_recover_journal(shared_store_path / f"{session}.jsonl")

    sequences = [e.sequence_number for e in entries]
    assert sequences == [-1, 0, 1] # Session start + op1 + op2

# ---------------------------------------------------------
# 7. ACTIVE DUPLICATE CANONICAL ADMISSION
# ---------------------------------------------------------
def test_active_duplicate_canonical_admission(shared_store_path):
    authority, epoch, session = setup_test_store(shared_store_path)
    store = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store.restore_session_from_disk(session)

    store.record_operation_admitted(
        session, "ep_d", "dev_d", 1, 1, "op_d", "nonce_d", "exec_d1", "test"
    )

    store.record_operation_admitted(
        session, "ep_d", "dev_d", 1, 1, "op_d", "nonce_d", "exec_d2", "test"
    )

    assert store.get_active_physical_operations() == 1

    from holomed.persistence.journal import JournalReader, JournalEntryType
    entries, _ = JournalReader.read_and_recover_journal(shared_store_path / f"{session}.jsonl")

    admissions = [e for e in entries if e.entry_type == JournalEntryType.OPERATION_ADMITTED]
    assert len(admissions) == 1, "Duplicate OPERATION_ADMITTED event logged!"

# ---------------------------------------------------------
# 8. TERMINATED IDENTITY REUSE REJECTION
# ---------------------------------------------------------
def test_terminated_identity_reuse_rejection(shared_store_path):
    authority, epoch, session = setup_test_store(shared_store_path)
    store = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store.restore_session_from_disk(session)

    store.record_operation_admitted(
        session, "ep_t", "dev_t", 1, 1, "op_t", "nonce_t", "exec_t1", "test"
    )
    store.record_operation_terminated(
        session, "dev_t", 1, 1, "op_t", "nonce_t", "OPERATION_COMPLETED"
    )

    assert store.get_active_physical_operations() == 0

    with pytest.raises(PersistenceIdentityReuseError):
        store.record_operation_admitted(
            session, "ep_t", "dev_t", 1, 1, "op_t", "nonce_t", "exec_t2", "test"
        )

# ---------------------------------------------------------
# 9. NON-TERMINAL RESOLUTION REJECTION
# ---------------------------------------------------------
def test_non_terminal_resolution_rejection(shared_store_path):
    authority, epoch, session = setup_test_store(shared_store_path)
    store = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store.restore_session_from_disk(session)

    store.record_operation_admitted(
        session, "ep_n", "dev_n", 1, 1, "op_n", "nonce_n", "exec_n", "test"
    )

    non_terminals = ["QUARANTINED", "RECOVERY_REQUIRED", "UNKNOWN", "ISOLATION_PREPARE", "DISPATCHING", "RUNNING"]

    for state in non_terminals:
        with pytest.raises(PersistenceValidationError, match="non-terminal resolution"):
            store.record_operation_terminated(session, "dev_n", 1, 1, "op_n", "nonce_n", state)
        assert store.get_active_physical_operations() == 1, f"State {state} incorrectly released capacity!"

# ---------------------------------------------------------
# 10. VALID TERMINAL RELEASE
# ---------------------------------------------------------
def test_valid_terminal_release(shared_store_path):
    terminals = ["OPERATION_COMPLETED", "OPERATION_ABORTED_AND_QUIESCENT", "OPERATION_CONFIRMED_ABSENT", "PHYSICALLY_ISOLATED"]

    for state in terminals:
        authority, epoch, session = setup_test_store(shared_store_path / state)
        store = DurableSessionStore(shared_store_path / state, epoch_id=epoch)
        store.restore_session_from_disk(session)
        store.record_operation_admitted(session, "ep_v", "dev_v", 1, 1, "op_v", "nonce_v", "exec_v", "test")

        store.record_operation_terminated(session, "dev_v", 1, 1, "op_v", "nonce_v", state)
        assert store.get_active_physical_operations() == 0

# ---------------------------------------------------------
# 11. CONFLICTING TERMINAL RESOLUTION REJECTION
# ---------------------------------------------------------
def test_conflicting_terminal_resolution_rejection(shared_store_path):
    authority, epoch, session = setup_test_store(shared_store_path)
    store = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store.restore_session_from_disk(session)

    store.record_operation_admitted(session, "ep_c", "dev_c", 1, 1, "op_c", "nonce_c", "exec_c", "test")
    store.record_operation_terminated(session, "dev_c", 1, 1, "op_c", "nonce_c", "OPERATION_COMPLETED")

    with pytest.raises(PersistenceLifecycleError, match="Conflict"):
        store.record_operation_terminated(session, "dev_c", 1, 1, "op_c", "nonce_c", "PHYSICALLY_ISOLATED")

# ---------------------------------------------------------
# 12. DUPLICATE IDENTICAL TERMINAL IDEMPOTENCY
# ---------------------------------------------------------
def test_duplicate_identical_terminal_idempotency(shared_store_path):
    authority, epoch, session = setup_test_store(shared_store_path)
    store = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store.restore_session_from_disk(session)

    store.record_operation_admitted(session, "ep_i", "dev_i", 1, 1, "op_i", "nonce_i", "exec_i", "test")
    store.record_operation_terminated(session, "dev_i", 1, 1, "op_i", "nonce_i", "OPERATION_COMPLETED")
    store.record_operation_terminated(session, "dev_i", 1, 1, "op_i", "nonce_i", "OPERATION_COMPLETED")

    from holomed.persistence.journal import JournalReader, JournalEntryType
    entries, _ = JournalReader.read_and_recover_journal(shared_store_path / f"{session}.jsonl")
    terms = [e for e in entries if e.entry_type == JournalEntryType.OPERATION_TERMINATED]
    assert len(terms) == 1

# ---------------------------------------------------------
# 13. ADMISSION/TERMINATION RACE
# ---------------------------------------------------------
def test_admission_termination_race(shared_store_path):
    authority, epoch, session = setup_test_store(shared_store_path)
    store = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store.restore_session_from_disk(session)
    store.record_operation_admitted(session, "ep_t", "dev_t", 1, 1, "op_t", "nonce_t", "exec_t", "test")

    pa = subprocess.Popen([sys.executable, str(HELPER_SCRIPT), "adm_term_race_a", str(shared_store_path), str(epoch), "op_a"], stdout=subprocess.PIPE)
    pb = subprocess.Popen([sys.executable, str(HELPER_SCRIPT), "adm_term_race_b", str(shared_store_path), str(epoch), "op_a"], stdout=subprocess.PIPE)

    pa.communicate()
    pb.communicate()

    store2 = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store2.restore_session_from_disk(session)
    active = store2.get_active_physical_operations()
    assert active <= 1

# ---------------------------------------------------------
# 14. RESTART-ONLY DURABLE ROUTING
# ---------------------------------------------------------
def test_restart_only_durable_routing(shared_store_path):
    authority, epoch, session = setup_test_store(shared_store_path)
    store = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store.restore_session_from_disk(session)
    store.record_operation_admitted(session, "ep_r", "dev_r", 1, 1, "op_r", "nonce_r", "exec_r", "test")
    assert store.get_active_physical_operations() == 1

    del store

    store2 = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store2.restore_session_from_disk(session)
    assert store2.get_active_physical_operations() == 1

    store2.record_operation_terminated(session, "dev_r", 1, 1, "op_r", "nonce_r", "OPERATION_COMPLETED")
    assert store2.get_active_physical_operations() == 0

# ---------------------------------------------------------
# 15. EVIDENCE IDENTITY/GENERATION MISMATCH REJECTION
# ---------------------------------------------------------
def test_evidence_identity_generation_mismatch_rejection(shared_store_path):
    from holomed.persistence.sessions import DurableSessionStore
    from holomed.persistence.exceptions import PersistenceTerminationConflictError
    from unittest.mock import MagicMock
    from holomed.devices.control.manager import DeviceControlManager
    from holomed.devices.transport import TelemetryTransport
    from holomed.devices.resolution import ExecutionResolutionGate
    from holomed.devices.reconciler import TelemetryReconciler
    from holomed.devices.models import ExecutionTelemetryEvent, EventSourceAuthority, CommandState, PhysicalCommand, DeviceType, DeviceState, EndpointLease, EndpointSafetyState, EndpointState
    from holomed.devices.registry import DeviceRegistry, RegistryAuthorityToken
    from holomed.devices.interfaces import IDevice, IPhysicalEndpoint
    from typing import Tuple, Optional

    authority, epoch, session = setup_test_store(shared_store_path)
    store = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store.restore_session_from_disk(session)
    store.record_operation_admitted(session, "ep_e", "dev_e", 1, epoch, "op_e", "nonce_e", "exec_e", "test")

    # 1. Setup production path components
    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)

    class MockEndpoint(IPhysicalEndpoint):
        @property
        def endpoint_id(self): return "ep_e"
        @property
        def device_id(self): return "dev_e"
        @property
        def safety_state(self): return EndpointSafetyState.SAFE
        @property
        def endpoint_state(self): return EndpointState.READY
        def recover(self): pass
        def emergency_stop(self): return EndpointSafetyState.SAFE
        def request_stop(self, execution_id): pass
        @property
        def active_lease(self): return EndpointLease("ep_e", "dev_e", session, 1, 1, "exec_e", frozenset(), 1, epoch)
        def acquire_lease(self, lease): pass
        def release_lease(self, session_id): pass
        def submit_command(self, cmd): pass

    class MockDevice(IDevice):
        def __init__(self):
            self._state = DeviceState.UNREGISTERED
        @property
        def device_id(self): return "dev_e"
        @property
        def physical_id(self): return "phys_e"
        @property
        def device_type(self): return DeviceType.ACTUATOR
        @property
        def state(self): return self._state
        @state.setter
        def state(self, val): self._state = val
        @property
        def current_epoch(self): return 1
        @property
        def capabilities(self): return tuple()
        @property
        def endpoints(self): return (MockEndpoint(),)
        def initialize(self, acc): pass
        def start(self): pass
        def stop(self, acc): pass
        def health(self): pass

    dev = MockDevice()
    registry.register(dev, token)

    manager = DeviceControlManager(
        registry=registry,
        capacity_releaser=store.record_operation_terminated
    )
    transport = TelemetryTransport()
    gate = ExecutionResolutionGate()
    reconciler = TelemetryReconciler(transport, gate)

    gate.claim_execution_ownership("exec_e", 1)

    def _run_pipeline(event: ExecutionTelemetryEvent, manager_command_override: PhysicalCommand = None):
        if manager_command_override:
            manager._active_commands[event.execution_id] = manager_command_override

        transport.publisher.publish(event)
        records = reconciler.process_pending_events()

        for record in records:
            if record.terminal_resolution_status and gate.is_capacity_release_terminal(record.current_state):
                manager.release_capacity_for_execution(record.execution_id, record.current_state)

    def _make_event(exec_id="exec_e", gen=1, state=CommandState.OPERATION_COMPLETED):
        return ExecutionTelemetryEvent(
            event_id="evt_1", endpoint_id="ep_e", session_id=session,
            lifecycle_generation=gen, endpoint_lease_generation=1, execution_id=exec_id,
            command_sequence=1, event_sequence=1, event_type="STATE", observed_state=state,
            source_authority=EventSourceAuthority.ENDPOINT_ADAPTER, source_origin="driver",
            timestamp_utc="2026-09-19T00:00:00Z", payload={}, evidence_generation=1,
            cryptographic_signature=None, fencing_challenge=None
        )

    # 2. Lifecycle generation mismatch -> Rejected at the Gate
    _run_pipeline(_make_event(gen=999))
    record_at_gate = gate._records["exec_e"]
    assert record_at_gate.terminal_resolution_status is False
    assert record_at_gate.current_state == CommandState.ACCEPTED

    def _make_cmd(d_epoch=1, c_epoch=epoch, op_id="op_e", exec_id="exec_e"):
        return PhysicalCommand(
            device_epoch=d_epoch, controller_epoch=c_epoch, physical_operation_id=op_id,
            command_nonce="nonce_e", endpoint_id="ep_e", session_id=session,
            lifecycle_generation=1, endpoint_lease_generation=1, execution_id=exec_id,
            capability_scope=frozenset(), command_sequence=1, operation="test", parameters={}
        )

    from holomed.persistence.exceptions import PersistenceEpochMismatchError

    # 3. Controller epoch mismatch -> Rejected by Store (canonical identity check)
    with pytest.raises(PersistenceEpochMismatchError):
        _run_pipeline(_make_event(), manager_command_override=_make_cmd(c_epoch=epoch+99))

    gate._records.pop("exec_e", None)
    gate.claim_execution_ownership("exec_e", 1)

    # 4. Device epoch mismatch -> Rejected by Store
    with pytest.raises(PersistenceTerminationConflictError):
        _run_pipeline(_make_event(), manager_command_override=_make_cmd(d_epoch=2))

    gate._records.pop("exec_e", None)
    gate.claim_execution_ownership("exec_e", 1)

    # 5. Physical operation ID mismatch -> Rejected by Store
    with pytest.raises(PersistenceTerminationConflictError):
        _run_pipeline(_make_event(), manager_command_override=_make_cmd(op_id="op_WRONG"))

    # Verify operation is STILL active physically
    assert store.get_active_physical_operations() == 1

# ---------------------------------------------------------
# 16. EPOCH AUTHORITY FAIL-CLOSED VALIDATION
# ---------------------------------------------------------
def test_epoch_authority_fail_closed(shared_store_path):
    from holomed.persistence.exceptions import PersistenceResourceIntegrityError

    authority = ControllerAuthorityStore(shared_store_path)

    # 1. Missing epoch file
    with pytest.raises(PersistenceResourceIntegrityError, match="Missing epoch authority file"):
        authority.read_current_epoch()

    # 2. Malformed JSON
    epoch_file = shared_store_path / "controller_epoch.json"
    epoch_file.write_text("{malformed", encoding="utf-8")
    with pytest.raises(PersistenceResourceIntegrityError, match="Malformed epoch authority JSON"):
        authority.read_current_epoch()

    # 3. Missing epoch_id
    epoch_file.write_text('{"other": 1}', encoding="utf-8")
    with pytest.raises(PersistenceResourceIntegrityError, match="missing 'epoch_id'"):
        authority.read_current_epoch()

    # 4. Invalid epoch type
    epoch_file.write_text('{"epoch_id": "1"}', encoding="utf-8")
    with pytest.raises(PersistenceResourceIntegrityError, match="must be an integer"):
        authority.read_current_epoch()

# ---------------------------------------------------------
# 17. PHYSICAL SUBMISSION FENCE (TOCTOU CLOSURE)
# ---------------------------------------------------------
def test_submission_fence_closes_toctou(shared_store_path):
    from holomed.persistence.sessions import DurableSessionStore
    from unittest.mock import MagicMock
    from holomed.devices.control.manager import DeviceControlManager
    from holomed.devices.models import CommandState, PhysicalCommand, DeviceType, DeviceState, EndpointLease, EndpointSafetyState, EndpointState
    from holomed.devices.registry import DeviceRegistry, RegistryAuthorityToken
    from holomed.devices.simulated import SimulatedPhysicalEndpoint
    from holomed.devices.interfaces import IDevice
    from holomed.protocol.models import MessageEnvelope, MessageType

    authority, epoch, session = setup_test_store(shared_store_path)
    store = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store.restore_session_from_disk(session)

    token = RegistryAuthorityToken()
    registry = DeviceRegistry(token)

    endpoint = SimulatedPhysicalEndpoint("ep_fence", "dev_fence")
    endpoint.acquire_lease(EndpointLease("ep_fence", "dev_fence", session, 1, 1, "exec_1", frozenset(), 1, epoch))

    class FencedDevice(IDevice):
        def __init__(self):
            self._state = DeviceState.UNREGISTERED
        @property
        def device_id(self): return "dev_fence"
        @property
        def physical_id(self): return "phys_fence"
        @property
        def device_type(self): return DeviceType.ACTUATOR
        @property
        def state(self): return self._state
        @state.setter
        def state(self, val): self._state = val
        @property
        def current_epoch(self): return 1
        @property
        def capabilities(self):
            from holomed.devices.models import DeviceCapability, CapabilityCategory
            from types import MappingProxyType
            return (DeviceCapability("cap1", CapabilityCategory.CONTROL, MappingProxyType({}), requires_physical_endpoint=True, target_endpoint_id="ep_fence"),)
        @property
        def endpoints(self): return (endpoint,)
        def initialize(self, acc): pass
        def start(self): pass
        def stop(self, acc): pass
        def health(self): pass

    dev = FencedDevice()
    registry.register(dev, token)
    dev.state = DeviceState.ACTIVE

    manager = DeviceControlManager(
        registry=registry,
        capacity_releaser=store.record_operation_terminated,
        capacity_admitter=store.record_operation_admitted,
        authoritative_epoch_provider=authority.read_current_epoch,
        rehydration_engine=MagicMock()
    )

    from holomed.runtime.context import RuntimeContext
    ctx = RuntimeContext("tnt1", "srv1", epoch)
    manager.initialize(ctx)
    manager.start()

    manager.register_command("test_actuate", lambda d, p: {"success": True}, required_capability_id="cap1")

    # Simulate the rollover BETWEEN admission and endpoint execution.
    # We monkeypatch endpoint.submit_command to advance the epoch FIRST, simulating the exact race!
    original_submit = endpoint.submit_command
    def adversarial_submit(cmd):
        # A new controller has just taken over authority BEFORE we physically transmit!
        authority.allocate_next_epoch()
        # Now we proceed with physical transmission
        return original_submit(cmd)

    endpoint.submit_command = adversarial_submit

    import datetime
    import uuid
    env = MessageEnvelope(
        protocol_version="1.0",
        message_id=str(uuid.uuid4()),
        correlation_id=str(uuid.uuid4()),
        causation_id=str(uuid.uuid4()),
        message_type=MessageType.COMMAND,
        message_name="test_actuate",
        source="client",
        target="dev_fence",
        timestamp_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        metadata={},
        payload={
            "device_id": "dev_fence",
            "command": "test_actuate",
            "session_id": session,
            "session_lifecycle_generation": 1,
            "execution_id": "exec_1",
            "parameters": {}
        }
    )

    # Manager will do: admit -> adversarial_submit -> physical fence reads new epoch -> REJECT!
    res = manager.handle_command(env)
    assert res is not None
    assert res.message_type == MessageType.ERROR
    assert res.payload["error_code"] == "ERR_PERSISTENCEEPOCHMISMATCHERROR"

    # Prove that the command was NEVER enqueued or executed.
    # The queue must be empty because the atomic fence rejected it.
    assert endpoint._command_queue.empty()

    # Prove that the durable capacity remains admitted because the stale controller lost authority
    # and therefore cannot write the termination record!
    assert store.get_active_physical_operations() == 1

    from holomed.persistence.journal import JournalReader, JournalEntryType
    entries, _ = JournalReader.read_and_recover_journal(shared_store_path / f"{session}.jsonl")

    admissions = [e for e in entries if e.entry_type == JournalEntryType.OPERATION_ADMITTED]
    terminations = [e for e in entries if e.entry_type == JournalEntryType.OPERATION_TERMINATED]

    assert len(admissions) == 1
    assert len(terminations) == 0




def test_durable_admission_closes_toctou(shared_store_path):
    """
    Test Blocker C: Prove the controller-epoch TOCTOU defense at the durable admission boundary.
    Process A reads authoritative controller_epoch = E.
    Process B advances durable authority to E+1.
    Process A attempts admission using E.
    Process A MUST be rejected at the durable admission boundary before ADMITTED append.
    """
    from holomed.persistence.sessions import DurableSessionStore
    from holomed.devices.models import EndpointLease, PhysicalCommand
    import pytest
    from holomed.persistence.exceptions import PersistenceEpochMismatchError

    authority, epoch, session = setup_test_store(shared_store_path)
    store = DurableSessionStore(shared_store_path, epoch_id=epoch)
    store.restore_session_from_disk(session)

    # Process A reads epoch = E (which is `epoch`)
    
    # Process B advances durable authority to E+1
    next_epoch = authority.allocate_next_epoch()
    
    # Process A attempts admission using E
    with pytest.raises(PersistenceEpochMismatchError):
        store.record_operation_admitted(
            session_id=session,
            endpoint_id="ep_admission_fence",
            device_id="dev_fence",
            device_epoch=1,
            controller_epoch=epoch, # STALE EPOCH
            physical_operation_id="op_123",
            command_nonce="nonce_123",
            execution_id="exec_1",
            command_name="test_actuate"
        )
        
    # Prove NO ADMITTED entry and NO capacity corruption
    assert store.get_active_physical_operations() == 0
    
    from holomed.persistence.journal import JournalReader, JournalEntryType
    entries, _ = JournalReader.read_and_recover_journal(shared_store_path / f"{session}.jsonl")
    admissions = [e for e in entries if e.entry_type == JournalEntryType.OPERATION_ADMITTED]
    assert len(admissions) == 0
