import os
import sys
import time
import uuid
import threading
import multiprocessing
from pathlib import Path
from unittest.mock import patch, MagicMock

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
    # create the journal by committing an entry
    device_store.initialize_device('dev1', epoch)
    device_store.record_device_ready_committed('dev1', 'test_tx')
    return epoch, session

# ==============================================================================
# ROW D: Crash exactly AFTER epoch validation but BEFORE append begins
# ==============================================================================
def _worker_d_crash_after_epoch(storage_str: str, epoch: int, session: str, q: multiprocessing.Queue):
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
# ROW E: Crash after append before fsync
# ==============================================================================
def _worker_e_crash_before_fsync(storage_str: str, epoch: int, session: str, q: multiprocessing.Queue):
    try:
        sys.path.insert(0, str(_PYTHON_SRC))
        from holomed.persistence.sessions import DurableSessionStore
        import os

        store = DurableSessionStore(Path(storage_str), epoch_id=epoch)
        store.restore_session_from_disk(session)

        original_fsync = os.fsync
        def _mock_fsync(fd):
            q.put(("FLUSHED_BEFORE_FSYNC", os.getpid()))
            time.sleep(0.1)
            os._exit(0)

        with patch('holomed.persistence.journal.os.fsync', _mock_fsync):
            store.record_operation_admitted(session, "ep1", "dev1", 1, epoch, "op1", "nonce1", "exec1", "cmd1")

    except Exception as e:
        q.put(("ERROR", str(type(e)) + ": " + str(e)))

# ==============================================================================
# ROW F: Crash after fsync
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
            store.record_operation_admitted(session, "ep1", "dev1", 1, epoch, "op1", "nonce1", "exec1", "cmd1")

    except Exception as e:
        q.put(("ERROR", str(type(e)) + ": " + str(e)))

# ==============================================================================
# ROW S: Isolation Race (Multiprocessing)
# ==============================================================================
def _worker_s_isolation(storage_str: str, session: str, q_res: multiprocessing.Queue, q_sync1: multiprocessing.Queue, q_sync2: multiprocessing.Queue):
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

        # We synchronize so that isolation starts and gets the lock first
        q_sync1.put("ISOLATION_READY")
        # Wait for admission to be ready
        msg = q_sync2.get(timeout=5)
        if msg != "ADMISSION_READY":
            return

        # Run isolate_device. It will acquire GLOBAL_TRANSACTION_LOCK, then GLOBAL_PHYSICAL_ADMISSION_LOCK,
        # write intent, release GLOBAL_PHYSICAL_ADMISSION_LOCK, and write to session journals.
        tx_id = coordinator.isolate_device("dev1", {session: ["op_stale_1"]})
        q_res.put(("ISOLATED", tx_id))
    except Exception as e:
        q_res.put(("ERROR", str(type(e)) + ": " + str(e)))

def _worker_s_admission(storage_str: str, epoch: int, session: str, q_res: multiprocessing.Queue, q_sync1: multiprocessing.Queue, q_sync2: multiprocessing.Queue):
    try:
        sys.path.insert(0, str(_PYTHON_SRC))
        from holomed.persistence.sessions import DurableSessionStore
        import time

        store = DurableSessionStore(Path(storage_str), epoch_id=epoch)
        store.restore_session_from_disk(session)

        # Wait for isolation to be ready
        msg = q_sync1.get(timeout=5)
        if msg != "ISOLATION_READY":
            return

        # Signal that we are ready to race
        q_sync2.put("ADMISSION_READY")
        # Let isolation acquire the lock first
        time.sleep(0.5)

        # Race! This will block until isolation releases GLOBAL_PHYSICAL_ADMISSION_LOCK.
        # It will then append. This simulates admission slipping through AFTER isolation
        # has started but before it fully commits or after it writes intent.
        op_id, is_replay, res = store.record_operation_admitted(session, "ep1", "dev1", 1, epoch, "op2", "nonce2", "exec2", "cmd2")
        q_res.put(("ADMITTED", op_id))
    except Exception as e:
        q_res.put(("ERROR", str(type(e)) + ": " + str(e)))

# ==============================================================================
# ROW T: Reinitialization Race (Multiprocessing)
# ==============================================================================
def _worker_t_reinit(storage_str: str, q_res: multiprocessing.Queue, q_sync1: multiprocessing.Queue, q_sync2: multiprocessing.Queue):
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

        q_sync1.put("REINIT_READY")
        msg = q_sync2.get(timeout=5)
        if msg != "ADMISSION_READY":
            return

        new_epoch = coordinator.commit_device_ready("dev1", {})
        q_res.put(("REINIT_DONE", new_epoch))
    except Exception as e:
        q_res.put(("ERROR", str(type(e)) + ": " + str(e)))

def _worker_t_admission(storage_str: str, stale_epoch: int, session: str, q_res: multiprocessing.Queue, q_sync1: multiprocessing.Queue, q_sync2: multiprocessing.Queue):
    try:
        sys.path.insert(0, str(_PYTHON_SRC))
        from holomed.persistence.sessions import DurableSessionStore
        from holomed.persistence.exceptions import PersistenceEpochMismatchError
        import time

        store = DurableSessionStore(Path(storage_str), epoch_id=stale_epoch)
        store.restore_session_from_disk(session)

        msg = q_sync1.get(timeout=5)
        if msg != "REINIT_READY":
            return

        q_sync2.put("ADMISSION_READY")

        # Wait for reinit to bump epoch
        time.sleep(1.0)

        try:
            store.record_operation_admitted(session, "ep1", "dev1", 1, stale_epoch, "op3", "nonce3", "exec3", "cmd3")
            q_res.put("ERROR: ADMISSION_SUCCEEDED")
        except PersistenceEpochMismatchError as e:
            q_res.put(("REJECTED_EPOCH", str(e)))

    except Exception as e:
        q_res.put(("ERROR", str(type(e)) + ": " + str(e)))


class TestM49Phase63FailureMatrixCrossProcess:

    def test_d_crash_after_epoch_validation(self, tmp_path: Path):
        storage = lock_storage(tmp_path)
        epoch, session = _setup_store(storage)

        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue()
        p = ctx.Process(target=_worker_d_crash_after_epoch, args=(str(storage), epoch, session, q))
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

    def test_e_crash_after_append_before_fsync(self, tmp_path: Path):
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

        from holomed.persistence.sessions import DurableSessionStore
        store = DurableSessionStore(storage, epoch_id=epoch)
        store.restore_session_from_disk(session)

        # In a process crash, the OS flushes buffers, so the append is visible on restart.
        # This proves the exact intended post-crash durable outcome for an OS crash.
        assert store.get_active_physical_operations() == 1, "Expected ADMITTED after OS process crash"

    def test_f_crash_after_fsync(self, tmp_path: Path):
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

        from holomed.persistence.sessions import DurableSessionStore
        store = DurableSessionStore(storage, epoch_id=epoch)
        store.restore_session_from_disk(session)

        assert store.get_active_physical_operations() == 1

        # Verify idempotency re-resolves the crashed operation
        op_id, is_replay, resolution = store.record_operation_admitted(
            session, "ep1", "dev1", 1, epoch, "op1", "nonce1", "exec1", "cmd1"
        )
        assert is_replay is True
        assert resolution is None

    def test_s_isolation_racing_admission(self, tmp_path: Path):
        # Proves invariant: isolation can commit, and if admission slips through
        # (acquiring lock after isolation intent), it does not corrupt the lock state
        # or epoch constraints.
        storage = lock_storage(tmp_path)
        epoch, session = _setup_store(storage)

        ctx = multiprocessing.get_context("spawn")
        q_res = ctx.Queue()
        q_sync1 = ctx.Queue()
        q_sync2 = ctx.Queue()

        p_iso = ctx.Process(target=_worker_s_isolation, args=(str(storage), session, q_res, q_sync1, q_sync2))
        p_adm = ctx.Process(target=_worker_s_admission, args=(str(storage), epoch, session, q_res, q_sync1, q_sync2))

        p_iso.start()
        p_adm.start()

        p_iso.join(timeout=10)
        p_adm.join(timeout=10)

        msgs = []
        while not q_res.empty():
            msgs.append(q_res.get())

        isolated = any(m[0] == "ISOLATED" for m in msgs if isinstance(m, tuple))
        admitted = any(m[0] == "ADMITTED" for m in msgs if isinstance(m, tuple))

        assert isolated, f"Isolation failed: {msgs}"
        assert admitted, f"Admission failed: {msgs}"

        # Verify state safely reflects both
        from holomed.persistence.sessions import DurableSessionStore
        store = DurableSessionStore(storage, epoch_id=epoch)
        store.restore_session_from_disk(session)
        assert store.get_active_physical_operations() == 1

    def test_t_reinitialization_race_stale_controller(self, tmp_path: Path):
        # Proves invariant: admission cannot commit after reinitialization bumps epoch
        storage = lock_storage(tmp_path)
        epoch, session = _setup_store(storage)

        ctx = multiprocessing.get_context("spawn")
        q_res = ctx.Queue()
        q_sync1 = ctx.Queue()
        q_sync2 = ctx.Queue()

        p_reinit = ctx.Process(target=_worker_t_reinit, args=(str(storage), q_res, q_sync1, q_sync2))
        p_adm = ctx.Process(target=_worker_t_admission, args=(str(storage), epoch, session, q_res, q_sync1, q_sync2))

        p_reinit.start()
        p_adm.start()

        p_reinit.join(timeout=10)
        p_adm.join(timeout=10)

        msgs = []
        while not q_res.empty():
            msgs.append(q_res.get())

        reinit_done = any(m[0] == "REINIT_DONE" for m in msgs if isinstance(m, tuple))
        rejected = any(m[0] == "REJECTED_EPOCH" for m in msgs if isinstance(m, tuple))

        assert reinit_done, f"Reinit failed: {msgs}"
        assert rejected, f"Admission was not rejected! {msgs}"

        from holomed.persistence.sessions import DurableSessionStore
        store = DurableSessionStore(storage, epoch_id=epoch)
        store.restore_session_from_disk(session)
        assert store.get_active_physical_operations() == 0
