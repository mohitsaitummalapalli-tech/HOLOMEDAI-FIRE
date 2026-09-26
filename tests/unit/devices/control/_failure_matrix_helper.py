import os
import sys
import time
from pathlib import Path
from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.authority import ControllerAuthorityStore
import json

def crash(code=1):
    os._exit(code)

if __name__ == "__main__":
    action = sys.argv[1]
    store_path = Path(sys.argv[2])
    session_id = "matrix_session"
    
    if action == "crash_after_lock":
        # C. crash after lock acquisition
        store = DurableSessionStore(store_path, epoch_id=1)
        store.restore_session_from_disk(session_id)
        fd = store._acquire_global_lock()
        try:
            print("LOCKED")
            sys.stdout.flush()
            time.sleep(1)
            crash()
        finally:
            store._release_global_lock(fd)
                
    elif action == "crash_before_fsync":
        # E. crash after admission append before fsync
        store = DurableSessionStore(store_path, epoch_id=1)
        store.restore_session_from_disk(session_id)
        
        # Monkeypatch JournalWriter.append_entry
        writer = store._writers[session_id]
        original_append = writer.append_entry
        def mocked_append(*args, **kwargs):
            # Do the write manually but DON'T fsync
            entry = original_append(*args, **kwargs)
            # Actually JournalWriter `append_entry` does os.fsync(f.fileno()) internally
            # We can't easily stop it without monkeypatching os.fsync
            return entry
            
        import os
        original_fsync = os.fsync
        def mocked_fsync(fd):
            print("WROTE")
            sys.stdout.flush()
            time.sleep(0.5)
            crash()
            
        os.fsync = mocked_fsync
        store.record_operation_admitted(session_id, "ep_e", "dev_e", 1, 1, "op_e", "nonce_e", "exec_e", "test_cmd")
        
    elif action == "crash_after_fsync":
        # F. crash after fsync
        store = DurableSessionStore(store_path, epoch_id=1)
        store.restore_session_from_disk(session_id)
        
        import os
        original_fsync = os.fsync
        def mocked_fsync(fd):
            original_fsync(fd)
            print("FSYNCED")
            sys.stdout.flush()
            crash()
            
        os.fsync = mocked_fsync
        store.record_operation_admitted(session_id, "ep_f", "dev_f", 1, 1, "op_f", "nonce_f", "exec_f", "test_cmd")
        
    elif action == "wait_and_crash":
        # B. crash while waiting for lock
        # Wait until lock is available, but actually we want to crash while blocked.
        store = DurableSessionStore(store_path, epoch_id=1)
        store.restore_session_from_disk(session_id)
        print("WAITING")
        sys.stdout.flush()
        fd = store._acquire_global_lock()
        try:
            print("ACQUIRED")
            sys.stdout.flush()
            crash()
        finally:
            store._release_global_lock(fd)
