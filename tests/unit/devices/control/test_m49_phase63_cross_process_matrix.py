import os
import sys
import time
import uuid
import multiprocessing
from pathlib import Path
from unittest.mock import patch

_PYTHON_SRC = Path(__file__).resolve().parents[4] / "python"
if str(_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(_PYTHON_SRC))

def lock_storage(tmp_path: Path) -> Path:
    storage = tmp_path / "matrix_cross_process_store"
    storage.mkdir(parents=True, exist_ok=True)
    return storage

def _setup_store(storage_path: Path) -> tuple[int, str]:
    from holomed.persistence.authority import ControllerAuthorityStore
    from holomed.persistence.sessions import DurableSessionStore
    from holomed.persistence.devices import DurableDeviceStore

    authority = ControllerAuthorityStore(storage_path)
    epoch = authority.allocate_next_epoch()
    session = "matrix_sess_1"
    store = DurableSessionStore(storage_path, epoch_id=epoch)
    store.start_session(session, epoch)
    device_store_path = storage_path / "devices"
    device_store_path.mkdir(parents=True, exist_ok=True)
    device_store = DurableDeviceStore(device_store_path, epoch_id=epoch)
    device_store.initialize_device('dev1', epoch)
    device_store.record_device_ready_committed('dev1', 'test_tx')
    return epoch, session

# ==============================================================================
# ROW D: Crash after admission validation before journal append
# ==============================================================================
def _worker_d_crash_after_validation(storage_str: str, epoch: int, session: str, q: multiprocessing.Queue):
    try:
        sys.path.insert(0, str(_PYTHON_SRC))
        from holomed.persistence.sessions import DurableSessionStore

        store = DurableSessionStore(Path(storage_str), epoch_id=epoch)
        store.restore_session_from_disk(session)

        def _mock_append(*args, **kwargs):
            q.put(("VALIDATED", os.getpid()))
            time.sleep(0.1)
            os._exit(0)

        with patch('holomed.persistence.journal.JournalWriter.append_entry', _mock_append):
            store.record_operation_admitted(session, "ep1", "dev1", 1, epoch, "op1", "nonce1", "exec1", "cmd1")
    except Exception as e:
        q.put(("ERROR", str(type(e)) + ": " + str(e)))

# ==============================================================================
# ROW E: Crash after journal write/flush before fsync completion
# ==============================================================================
def _worker_e_crash_before_fsync(storage_str: str, epoch: int, session: str, q: multiprocessing.Queue):
    try:
        sys.path.insert(0, str(_PYTHON_SRC))
        from holomed.persistence.sessions import DurableSessionStore
        import os

        store = DurableSessionStore(Path(storage_str), epoch_id=epoch)
        store.restore_session_from_disk(session)

        def _mock_fsync(fd):
            q.put(("FLUSHED_BEFORE_FSYNC", os.getpid()))
            time.sleep(0.1)
            os._exit(0)

        with patch('holomed.persistence.journal.os.fsync', _mock_fsync):
            store.record_operation_admitted(session, "ep1", "dev1", 1, epoch, "op_e", "nonce_e", "exec_e", "cmd_e")
    except Exception as e:
        q.put(("ERROR", str(type(e)) + ": " + str(e)))

# ==============================================================================
# ROW F: Crash after successful fsync
# ==============================================================================
def _worker_f_crash_after_fsync(storage_str: str, epoch: int, session: str, q: multiprocessing.Queue):
    try:
        sys.path.insert(0, str(_PYTHON_SRC))
        from holomed.persistence.sessions import DurableSessionStore
        import os

        store = DurableSessionStore(Path(storage_str), epoch_id=epoch)
        store.restore_session_from_disk(session)

        original_fsync = os.fsync
        def _mock_fsync(fd):
            original_fsync(fd)
            q.put(("FSYNC_COMPLETED", os.getpid()))
            time.sleep(0.1)
            os._exit(0)

        with patch('holomed.persistence.journal.os.fsync', _mock_fsync):
            store.record_operation_admitted(session, "ep1", "dev1", 1, epoch, "op_f", "nonce_f", "exec_f", "cmd_f")
    except Exception as e:
        q.put(("ERROR", str(type(e)) + ": " + str(e)))

# ==============================================================================
# ROW S: Deterministic isolation/admission transaction interleaving
# ==============================================================================
def _worker_s_isolation(storage_str: str, session: str, op_iso_id: str, q_res: multiprocessing.Queue, q_sync_adm_start: multiprocessing.Queue, q_sync_adm_done: multiprocessing.Queue):
    try:
        sys.path.insert(0, str(_PYTHON_SRC))
        from holomed.persistence.coordinator import DurableGlobalCoordinator
        from holomed.persistence.authority import ControllerAuthorityStore
        from holomed.persistence.sessions import DurableSessionStore
        from holomed.persistence.devices import DurableDeviceStore

        storage = Path(storage_str)
        authority = ControllerAuthorityStore(storage)
        session_store = DurableSessionStore(storage, epoch_id=1)
        session_store.restore_session_from_disk(session)
        device_store = DurableDeviceStore(storage / "devices", epoch_id=1)
        device_store.restore_device_from_disk('dev1')

        coordinator = DurableGlobalCoordinator(authority, device_store, session_store)

        original_has_session = session_store.has_session
        def _mock_has_session(session_id):
            # This is called exactly AFTER _get_global_admission_lock is released
            # and BEFORE session file locks are acquired.
            # We signal admission to proceed, and wait for it to finish.
            q_sync_adm_start.put(True)
            msg = q_sync_adm_done.get(timeout=5)
            if not msg:
                raise RuntimeError("Admission did not complete properly")
            return original_has_session(session_id)

        with patch.object(session_store, 'has_session', side_effect=_mock_has_session):
            tx_id = coordinator.isolate_device("dev1", {session: [op_iso_id]})
            q_res.put(("ISOLATED", tx_id))
    except Exception as e:
        q_res.put(("ERROR", str(type(e)) + ": " + str(e)))

def _worker_s_admission(storage_str: str, epoch: int, session: str, q_res: multiprocessing.Queue, q_sync_adm_start: multiprocessing.Queue, q_sync_adm_done: multiprocessing.Queue):
    try:
        sys.path.insert(0, str(_PYTHON_SRC))
        from holomed.persistence.sessions import DurableSessionStore

        store = DurableSessionStore(Path(storage_str), epoch_id=epoch)
        store.restore_session_from_disk(session)

        # Wait for isolation to release admission lock
        msg = q_sync_adm_start.get(timeout=10)
        if not msg:
            return

        # Race! We safely acquire the admission lock because isolation released it,
        # and we safely write because isolation hasn't locked the session yet.
        op_id, is_replay, res = store.record_operation_admitted(session, "ep2", "dev1", 1, epoch, "op_adm", "nonce_adm", "exec_adm", "cmd_adm")
        q_res.put(("ADMITTED", op_id))

        # Let isolation proceed
        q_sync_adm_done.put(True)
    except Exception as e:
        q_res.put(("ERROR", str(type(e)) + ": " + str(e)))
        q_sync_adm_done.put(False)

# ==============================================================================
# ROW T: Deterministic stale E1 crossing rollover boundary
# ==============================================================================
def _worker_t_reinit_e2(storage_str: str, q_res: multiprocessing.Queue, q_sync_e2_start: multiprocessing.Queue, q_sync_e2_done: multiprocessing.Queue):
    try:
        sys.path.insert(0, str(_PYTHON_SRC))
        from holomed.persistence.coordinator import DurableGlobalCoordinator
        from holomed.persistence.authority import ControllerAuthorityStore
        from holomed.persistence.sessions import DurableSessionStore
        from holomed.persistence.devices import DurableDeviceStore

        storage = Path(storage_str)
        authority = ControllerAuthorityStore(storage)
        session_store = DurableSessionStore(storage, epoch_id=1)
        device_store = DurableDeviceStore(storage / "devices", epoch_id=1)
        device_store.restore_device_from_disk('dev1')

        coordinator = DurableGlobalCoordinator(authority, device_store, session_store)

        # Wait for E1 to pause before its admission lock
        msg = q_sync_e2_start.get(timeout=5)
        if not msg:
            return

        new_epoch = coordinator.commit_device_ready("dev1", {})
        q_res.put(("REINIT_DONE", new_epoch))

        # Signal E1 to proceed
        q_sync_e2_done.put(True)
    except Exception as e:
        q_res.put(("ERROR", str(type(e)) + ": " + str(e)))
        q_sync_e2_done.put(False)

def _worker_t_admission_e1(storage_str: str, stale_epoch: int, session: str, q_res: multiprocessing.Queue, q_sync_e2_start: multiprocessing.Queue, q_sync_e2_done: multiprocessing.Queue):
    try:
        sys.path.insert(0, str(_PYTHON_SRC))
        from holomed.persistence.sessions import DurableSessionStore
        from holomed.persistence.authority import ControllerAuthorityStore
        from holomed.persistence.exceptions import PersistenceEpochMismatchError

        store = DurableSessionStore(Path(storage_str), epoch_id=stale_epoch)
        store.restore_session_from_disk(session)

        import contextlib
        original_hold = ControllerAuthorityStore.hold_authority

        @contextlib.contextmanager
        def _mock_hold_authority(self_instance, epoch_id):
            # E1 pauses before acquiring the lock and running validation
            q_sync_e2_start.put(True)
            msg = q_sync_e2_done.get(timeout=5)
            if not msg:
                raise RuntimeError("E2 failed to complete reinit")
            with original_hold(self_instance, epoch_id):
                yield

        with patch('holomed.persistence.authority.ControllerAuthorityStore.hold_authority', _mock_hold_authority):
            try:
                store.record_operation_admitted(session, "ep1", "dev1", 1, stale_epoch, "op_stale", "nonce_stale", "exec_stale", "cmd_stale")
                q_res.put("ERROR: ADMISSION_SUCCEEDED")
            except PersistenceEpochMismatchError as e:
                q_res.put(("REJECTED_EPOCH", str(e)))

    except Exception as e:
        q_res.put(("ERROR", str(type(e)) + ": " + str(e)))


class TestM49Phase63FailureMatrixCrossProcess:

    def test_d_crash_after_admission_validation_before_journal_append(self, tmp_path: Path):
        storage = lock_storage(tmp_path)
        epoch, session = _setup_store(storage)

        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue()
        p = ctx.Process(target=_worker_d_crash_after_validation, args=(str(storage), epoch, session, q))
        p.start()
        p.join(timeout=10)

        msgs = []
        while not q.empty():
            msgs.append(q.get())

        assert any(m[0] == "VALIDATED" for m in msgs), f"Did not reach validation, got {msgs}"

        from holomed.persistence.sessions import DurableSessionStore
        store = DurableSessionStore(storage, epoch_id=epoch)
        store.restore_session_from_disk(session)

        assert store.get_active_physical_operations() == 0, "Expected NO_APPEND after crash before append"

    def test_e_crash_after_journal_write_before_fsync_completion(self, tmp_path: Path):
        storage = lock_storage(tmp_path)
        epoch, session = _setup_store(storage)

        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue()
        p = ctx.Process(target=_worker_e_crash_before_fsync, args=(str(storage), epoch, session, q))
        p.start()
        p.join(timeout=10)

        msgs = []
        while not q.empty():
            msgs.append(q.get())

        assert any(m[0] == "FLUSHED_BEFORE_FSYNC" for m in msgs), f"Did not hook fsync, got {msgs}"

        # Verify EXACT journal proof
        from holomed.persistence.journal import JournalReader, JournalEntryType
        entries, _ = JournalReader.read_and_recover_journal(storage / f"{session}.jsonl")

        admissions = [e for e in entries if e.entry_type == JournalEntryType.OPERATION_ADMITTED]
        assert len(admissions) == 1, "Expected exactly one OPERATION_ADMITTED record"
        a = admissions[0]
        assert a.session_id == session
        assert a.payload.get("command_nonce") == "nonce_e"
        assert a.payload.get("physical_operation_id") == "op_e"
        assert a.payload.get("device_id") == "dev1"
        assert a.payload.get("device_epoch") == 1

        # Ensure session store parses it correctly
        from holomed.persistence.sessions import DurableSessionStore
        store = DurableSessionStore(storage, epoch_id=epoch)
        store.restore_session_from_disk(session)
        assert store.get_active_physical_operations() == 1, "Expected ADMITTED after OS process crash"

    def test_f_crash_after_successful_fsync(self, tmp_path: Path):
        storage = lock_storage(tmp_path)
        epoch, session = _setup_store(storage)

        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue()
        p = ctx.Process(target=_worker_f_crash_after_fsync, args=(str(storage), epoch, session, q))
        p.start()
        p.join(timeout=10)

        msgs = []
        while not q.empty():
            msgs.append(q.get())

        assert any(m[0] == "FSYNC_COMPLETED" for m in msgs), f"Did not complete fsync, got {msgs}"

        # Reconstruct and capture original operation ID
        from holomed.persistence.sessions import DurableSessionStore
        from holomed.persistence.journal import JournalReader, JournalEntryType
        entries, _ = JournalReader.read_and_recover_journal(storage / f"{session}.jsonl")
        admissions = [e for e in entries if e.entry_type == JournalEntryType.OPERATION_ADMITTED]
        assert len(admissions) == 1
        original_op_id = admissions[0].payload.get("physical_operation_id")

        store = DurableSessionStore(storage, epoch_id=epoch)
        store.restore_session_from_disk(session)

        assert store.get_active_physical_operations() == 1

        # Verify idempotency re-resolves the exact identity
        op_id, is_replay, resolution = store.record_operation_admitted(
            session, "ep1", "dev1", 1, epoch, "op_f", "nonce_f", "exec_f", "cmd_f"
        )
        assert is_replay is True
        assert resolution is None
        assert op_id == original_op_id, "Replay must return original physical_operation_id"
        assert store.get_active_physical_operations() == 1, "Capacity remains unchanged"

    def test_s_deterministic_isolation_admission_transaction_interleaving(self, tmp_path: Path):
        storage = lock_storage(tmp_path)
        epoch, session = _setup_store(storage)

        # Pre-admit a real operation to isolate
        from holomed.persistence.sessions import DurableSessionStore
        store_setup = DurableSessionStore(storage, epoch_id=epoch)
        store_setup.restore_session_from_disk(session)
        op_iso_id, _, _ = store_setup.record_operation_admitted(
            session, "ep1", "dev1", 1, epoch, "op_iso", "nonce_iso", "exec_iso", "cmd_iso"
        )
        assert store_setup.get_active_physical_operations() == 1

        ctx = multiprocessing.get_context("spawn")
        q_res = ctx.Queue()
        q_sync_adm_start = ctx.Queue()
        q_sync_adm_done = ctx.Queue()

        p_iso = ctx.Process(target=_worker_s_isolation, args=(str(storage), session, op_iso_id, q_res, q_sync_adm_start, q_sync_adm_done))
        p_adm = ctx.Process(target=_worker_s_admission, args=(str(storage), epoch, session, q_res, q_sync_adm_start, q_sync_adm_done))

        p_iso.start()
        p_adm.start()

        p_iso.join(timeout=10)
        p_adm.join(timeout=10)

        msgs = []
        while not q_res.empty():
            msgs.append(q_res.get())

        isolated = [m for m in msgs if isinstance(m, tuple) and m[0] == "ISOLATED"]
        admitted = [m for m in msgs if isinstance(m, tuple) and m[0] == "ADMITTED"]

        assert isolated, f"Isolation failed: {msgs}"
        assert admitted, f"Admission failed: {msgs}"
        tx_id = isolated[0][1]
        op_adm_id = admitted[0][1]

        from holomed.persistence.devices import DurableDeviceStore
        from holomed.persistence.models import TransactionState
        from holomed.persistence.journal import JournalReader, JournalEntryType

        device_store = DurableDeviceStore(storage / "devices", epoch_id=epoch)
        device_store.restore_device_from_disk('dev1')

        # A. Isolation transaction exists and is COMMITTED
        state = device_store._devices['dev1']
        assert state.isolation_transactions.get(tx_id) == TransactionState.COMMITTED

        store = DurableSessionStore(storage, epoch_id=epoch)
        store.restore_session_from_disk(session)

        active_reservations, terminated = store._reconstruct_reservations_locked()

        # B. Original isolation participant is terminated with exactly PHYSICALLY_ISOLATED
        found_term_iso = None
        for k, v in terminated.items():
            if v.get("operation_id") == op_iso_id or v.get("physical_operation_id") == op_iso_id:
                found_term_iso = v
                break

        assert found_term_iso is not None, "op_iso was not terminated"
        # The key might be terminal_state or resolution depending on the exact payload
        resolution = found_term_iso.get("terminal_state") or found_term_iso.get("resolution")
        assert resolution == "PHYSICALLY_ISOLATED", f"Expected PHYSICALLY_ISOLATED, got {resolution}"

        found_active_iso = None
        for k, v in active_reservations.items():
            if v.get("physical_operation_id") == op_iso_id:
                found_active_iso = op_iso_id
        assert found_active_iso is None

        # C. Concurrent new admission remains ACTIVE
        found_active_adm = None
        for k, v in active_reservations.items():
            if v.get("physical_operation_id") == op_adm_id:
                found_active_adm = op_adm_id
        assert found_active_adm == op_adm_id

        found_term_adm = None
        for k, v in terminated.items():
            if v.get("operation_id") == op_adm_id or v.get("physical_operation_id") == op_adm_id:
                found_term_adm = op_adm_id
        assert found_term_adm is None

        # D. Final capacity is exactly 1 (which is op_adm)
        assert store.get_active_physical_operations() == 1

        # E. Journal integrity
        entries, _ = JournalReader.read_and_recover_journal(storage / f"{session}.jsonl")

        admissions = [e for e in entries if e.entry_type == JournalEntryType.OPERATION_ADMITTED]
        terminations = [e for e in entries if e.entry_type == JournalEntryType.OPERATION_TERMINATED]

        iso_adm = [e for e in admissions if e.payload.get("physical_operation_id") == "op_iso"]
        adm_adm = [e for e in admissions if e.payload.get("physical_operation_id") == "op_adm"]
        iso_term = [e for e in terminations if e.payload.get("operation_id") == "op_iso" or e.payload.get("physical_operation_id") == "op_iso"]

        assert len(iso_adm) == 1, "Expected exactly one OPERATION_ADMITTED for op_iso"
        assert len(adm_adm) == 1, "Expected exactly one OPERATION_ADMITTED for op_adm"
        assert len(iso_term) == 1, "Expected exactly one OPERATION_TERMINATED for op_iso"
        term_payload = iso_term[0].payload
        assert term_payload.get("resolution") == "PHYSICALLY_ISOLATED" or term_payload.get("terminal_state") == "PHYSICALLY_ISOLATED", "Expected resolution to be PHYSICALLY_ISOLATED"

    def test_t_deterministic_stale_e1_activity_crossing_rollover_boundary(self, tmp_path: Path):
        storage = lock_storage(tmp_path)
        epoch, session = _setup_store(storage)

        ctx = multiprocessing.get_context("spawn")
        q_res = ctx.Queue()
        q_sync_e2_start = ctx.Queue()
        q_sync_e2_done = ctx.Queue()

        p_reinit = ctx.Process(target=_worker_t_reinit_e2, args=(str(storage), q_res, q_sync_e2_start, q_sync_e2_done))
        p_adm = ctx.Process(target=_worker_t_admission_e1, args=(str(storage), epoch, session, q_res, q_sync_e2_start, q_sync_e2_done))

        p_reinit.start()
        p_adm.start()

        p_reinit.join(timeout=10)
        p_adm.join(timeout=10)

        msgs = []
        while not q_res.empty():
            msgs.append(q_res.get())

        reinit_done = [m for m in msgs if isinstance(m, tuple) and m[0] == "REINIT_DONE"]
        rejected = [m for m in msgs if isinstance(m, tuple) and m[0] == "REJECTED_EPOCH"]

        assert reinit_done, f"Reinit failed: {msgs}"
        assert rejected, f"Admission was not rejected! {msgs}"
        new_epoch = reinit_done[0][1]
        assert new_epoch > epoch

        from holomed.persistence.sessions import DurableSessionStore
        from holomed.persistence.devices import DeviceJournalReader, DeviceJournalEntryType

        store = DurableSessionStore(storage, epoch_id=epoch)
        store.restore_session_from_disk(session)
        assert store.get_active_physical_operations() == 0, "Zero capacity released / admitted"

        entries, _ = DeviceJournalReader.read_and_recover_journal(storage / "devices" / "dev1.jsonl")
        ready_entries = [e for e in entries if e.entry_type == DeviceJournalEntryType.DEVICE_READY_COMMITTED]
        assert len(ready_entries) >= 1
        assert ready_entries[-1].payload.get("new_epoch_id") == new_epoch, "Device journal reflects E2"

    def test_u_adversarial_canonical_identity(self, tmp_path: Path):
        storage = lock_storage(tmp_path)
        epoch, session = _setup_store(storage)
        from holomed.persistence.sessions import DurableSessionStore
        from holomed.persistence.models import JournalEntryType
        from datetime import datetime, timezone
        
        store_setup = DurableSessionStore(storage, epoch_id=epoch)
        store_setup.restore_session_from_disk(session)
        # Pre-admit real op
        op_iso_id, _, _ = store_setup.record_operation_admitted(
            session, "ep1", "dev1", 1, epoch, "op_iso", "nonce_iso", "exec_iso", "cmd_iso"
        )
        assert store_setup.get_active_physical_operations() == 1
        
        # Now artificially write an adversarial termination record sharing the same operation_id and physical_operation_id
        # but with mismatched canonical fields (e.g., wrong device_id or nonce)
        writer = store_setup._writers[session]
        writer.append_entry(
            entry_type=JournalEntryType.OPERATION_TERMINATED,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            payload={
                "operation_id": op_iso_id,
                "physical_operation_id": op_iso_id,
                "device_id": "WRONG_DEVICE",
                "device_epoch": 1,
                "controller_epoch": epoch,
                "command_nonce": "nonce_iso",
                "resolution": "PHYSICALLY_ISOLATED"
            }
        )
        
        # Reconstruct reservations. 
        # The malicious record should NOT terminate the active reservation because the canon identity does not match.
        store_check = DurableSessionStore(storage, epoch_id=epoch)
        store_check.restore_session_from_disk(session)
        
        active, terminated = store_check._reconstruct_reservations_locked()
        
        # Should still be active, capacity should be 1
        assert store_check.get_active_physical_operations() == 1
        
        # Verify the true operation remains active
        canon = ("dev1", 1, epoch, op_iso_id, "nonce_iso")
        assert canon in active, "Original canonical identity should still be active"
        assert active[canon].get("resolution") is None, "Should not be terminated"
        
        # Now use coordinator to isolate a non-existent operation, it MUST fail closed.
        from holomed.persistence.coordinator import DurableGlobalCoordinator
        from holomed.persistence.authority import ControllerAuthorityStore
        from holomed.persistence.devices import DurableDeviceStore
        from holomed.persistence.exceptions import PersistenceValidationError
        
        authority = ControllerAuthorityStore(storage)
        devices = DurableDeviceStore(storage / "devices", epoch_id=epoch)
        devices.restore_device_from_disk("dev1")
        coordinator = DurableGlobalCoordinator(authority, devices, store_setup)
        
        import pytest
        with pytest.raises(PersistenceValidationError, match="Cannot isolate non-existent"):
            coordinator.isolate_device("dev1", {session: ["WRONG_OP_ID"]})

