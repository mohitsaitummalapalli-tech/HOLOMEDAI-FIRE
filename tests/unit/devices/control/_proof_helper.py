import sys
import os
import time
from pathlib import Path

from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.authority import ControllerAuthorityStore
from holomed.persistence.exceptions import PersistenceCapacityError, PersistenceLifecycleError

def run_epoch_race_worker_a(store_path: str, epoch: int):
    path = Path(store_path)
    store = DurableSessionStore(path, epoch_id=epoch)
    while True:
        try:
            store.restore_session_from_disk("proof_session")
            break
        except PermissionError:
            time.sleep(0.01)
            
    # Process A: acquires epoch authority lock, signals LOCK_HELD
    # We monkeypatch ControllerAuthorityStore._acquire_lock
    original_acquire = ControllerAuthorityStore._acquire_lock
    
    def slow_acquire(self, fd):
        original_acquire(self, fd)
        (path / "lock_held.flag").touch()
        # Wait for B to attempt acquire
        while not (path / "acquire_attempted.flag").exists():
            time.sleep(0.01)
        # Signal release and then release
        (path / "a_can_release.flag").touch()
        
    ControllerAuthorityStore._acquire_lock = slow_acquire
    
    try:
        store.record_operation_admitted(
            "proof_session", "ep_a", "dev_a", 1, epoch, "op_a", "nonce_a", "exec_a", "test"
        )
        print("SUCCESS")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"FAILED {e}")

def run_epoch_race_worker_b(store_path: str):
    path = Path(store_path)
    authority = ControllerAuthorityStore(path)
    
    # Wait for A to hold the lock
    while not (path / "lock_held.flag").exists():
        time.sleep(0.01)
            
    original_acquire = ControllerAuthorityStore._acquire_lock
    def test_acquire(self, fd):
        # Process B signals ACQUIRE_ATTEMPTED (using non-blocking check)
        try:
            if os.name == "nt":
                import msvcrt
                pos = os.lseek(fd, 0, os.SEEK_CUR)
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                os.lseek(fd, pos, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                raise RuntimeError("Lock was not held by A!")
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                raise RuntimeError("Lock was not held by A!")
        except OSError:
            pass # Proven locked!
            
        (path / "acquire_attempted.flag").touch()
        
        # remains blocked while A holds lock (conceptually we wait for A to signal release)
        while not (path / "a_can_release.flag").exists():
            time.sleep(0.01)
            
        original_acquire(self, fd)
        (path / "acquired.flag").touch()
        
    ControllerAuthorityStore._acquire_lock = test_acquire
    authority.allocate_next_epoch()
    print("ADVANCED")
    
def run_capacity_worker(store_path: str, epoch: int, endpoint_id: str, op_id: str):
    path = Path(store_path)
    store = DurableSessionStore(path, epoch_id=epoch)
    while True:
        try:
            store.restore_session_from_disk("proof_session")
            break
        except PermissionError:
            time.sleep(0.01)
    
    
    while True:
        try:
            store.record_operation_admitted(
                "proof_session", endpoint_id, f"dev_{op_id}", 1, 1, op_id, f"nonce_{op_id}", f"exec_{op_id}", "test"
            )
            print("ADMITTED")
            break
        except PersistenceCapacityError:
            print("CAPACITY_REJECTED")
            break
        except PermissionError:
            # High contention timeout, retry
            time.sleep(0.1)
        except Exception as e:
            print(f"UNEXPECTED_ERROR {e}")
            break

def run_concurrency_worker(store_path: str, epoch: int, op_id: str):
    path = Path(store_path)
    store = DurableSessionStore(path, epoch_id=epoch)
    while True:
        try:
            store.restore_session_from_disk("proof_session")
            break
        except PermissionError:
            time.sleep(0.01)
    for i in range(3):
        success = False
        for _ in range(200): # High retry count for heavy contention
            try:
                store.record_operation_admitted(
                    "proof_session", f"ep_{op_id}_{i}", f"dev_{op_id}", 1, 1, f"op_{op_id}_{i}", f"nonce_{op_id}_{i}", f"exec_{op_id}_{i}", "test"
                )
                print("ADMITTED")
                success = True
                break
            except PersistenceLifecycleError:
                time.sleep(0.01)
            except Exception as e:
                print(f"FAILED {type(e).__name__}: {e}")
                break
        if not success:
            print("FAILED Timeout resolving concurrency")

def run_admission_termination_race_a(store_path: str, epoch: int, op_id: str):
    path = Path(store_path)
    store = DurableSessionStore(path, epoch_id=epoch)
    while True:
        try:
            store.restore_session_from_disk("proof_session")
            break
        except PermissionError:
            time.sleep(0.01)
    for _ in range(10):
        try:
            store.record_operation_terminated("proof_session", "dev_t", 1, 1, "op_t", "nonce_t", "OPERATION_COMPLETED")
            print("TERMINATED")
        except Exception as e:
            print(f"FAILED {type(e).__name__}: {e}")
        time.sleep(0.01)

def run_admission_termination_race_b(store_path: str, epoch: int, op_id: str):
    path = Path(store_path)
    store = DurableSessionStore(path, epoch_id=epoch)
    while True:
        try:
            store.restore_session_from_disk("proof_session")
            break
        except PermissionError:
            time.sleep(0.01)
    for _ in range(10):
        try:
            store.record_operation_admitted(
                "proof_session", "ep_a", "dev_a", 1, 1, op_id, "nonce_a", "exec_a", "test"
            )
            print("ADMITTED")
        except Exception as e:
            print(f"FAILED {type(e).__name__}: {e}")
        time.sleep(0.01)

if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'race_a':
        run_epoch_race_worker_a(sys.argv[2], int(sys.argv[3]))
    elif cmd == 'race_b':
        run_epoch_race_worker_b(sys.argv[2])
    elif cmd == 'capacity':
        run_capacity_worker(sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5])
    elif cmd == 'concurrency':
        run_concurrency_worker(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    elif cmd == 'adm_term_race_a':
        run_admission_termination_race_a(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    elif cmd == 'adm_term_race_b':
        run_admission_termination_race_b(sys.argv[2], int(sys.argv[3]), sys.argv[4])
