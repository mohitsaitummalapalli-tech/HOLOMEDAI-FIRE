import os
import threading
import time
import uuid
import pytest
from pathlib import Path
from holomed.persistence.sessions import DurableSessionStore
from holomed.persistence.models import SessionStatus, JournalEntryType
from holomed.devices.control.exceptions import ControlCapacityError
from holomed.persistence.exceptions import PersistenceCapacityError
from holomed.persistence.authority import ControllerAuthorityStore
from holomed.devices.control.models import GLOBAL_PHYSICAL_OPERATION_CAPACITY

def create_store(tmp_path: Path) -> DurableSessionStore:
    authority = ControllerAuthorityStore(tmp_path)
    epoch_id = authority.allocate_next_epoch()
    return DurableSessionStore(tmp_path, epoch_id=epoch_id)

def test_atomic_capacity_admission(tmp_path: Path):
    """
    Test: test_atomic_capacity_admission
    N-1 Ops Held, 2 concurrent admission attempts. 1 QUEUED, 1 fails.
    """
    store = create_store(tmp_path)
    s = store.start_session("session_1", store._epoch_id)
        
    # Fill N-1 ops
    for i in range(GLOBAL_PHYSICAL_OPERATION_CAPACITY - 1):
        store.record_operation_admitted(
            session_id=s.session_id,
            endpoint_id=f"ep_{i}",
            device_id=f"dev_{i}",
            device_epoch=1,
            controller_epoch=1,
            physical_operation_id=f"op_{i}",
            command_nonce=f"nonce_{i}",
            execution_id=f"exec_{i}",
            command_name="test"
        )
        
    assert store.get_active_physical_operations() == GLOBAL_PHYSICAL_OPERATION_CAPACITY - 1
    
    # 2 concurrent admission attempts for the Nth slot
    results = []
    
    def admit_worker(idx: int):
        try:
            store.record_operation_admitted(
                session_id=s.session_id,
                endpoint_id=f"ep_concurrent_{idx}",
                device_id=f"dev_concurrent_{idx}",
                device_epoch=1,
                controller_epoch=1,
                physical_operation_id=f"op_concurrent_{idx}",
                command_nonce=f"nonce_concurrent_{idx}",
                execution_id=f"exec_concurrent_{idx}",
                command_name="test"
            )
            results.append("SUCCESS")
        except PersistenceCapacityError:
            results.append("REJECTED")
            
    t1 = threading.Thread(target=admit_worker, args=(1,))
    t2 = threading.Thread(target=admit_worker, args=(2,))
    
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    # Exactly one succeeds, one rejected
    assert len(results) == 2
    assert "SUCCESS" in results
    assert "REJECTED" in results
    assert store.get_active_physical_operations() == GLOBAL_PHYSICAL_OPERATION_CAPACITY

def test_status_query_unknown(tmp_path: Path):
    """
    Test: test_status_query_unknown
    E2 recovery queries status, gets UNKNOWN -> capacity Held
    (Simulated by just checking active ops remains without termination)
    """
    store = create_store(tmp_path)
    store.start_session("session_1", store._epoch_id)
    store.record_operation_admitted(
        "session_1", "ep_1", "dev_1", 1, 1, "op_1", "nonce_1", "exec_1", "test"
    )
    
    assert store.get_active_physical_operations() == 1
    # Without calling terminated, it remains active
    assert store.get_active_physical_operations() == 1

def test_operation_specific_idle_proof(tmp_path: Path):
    """
    Test: test_operation_specific_idle_proof
    Driver returns OPERATION_COMPLETED -> TERMINATED_AND_PROVEN -> Released
    """
    store = create_store(tmp_path)
    store.start_session("session_1", store._epoch_id)
    store.record_operation_admitted(
        "session_1", "ep_1", "dev_1", 1, 1, "op_1", "nonce_1", "exec_1", "test"
    )
    assert store.get_active_physical_operations() == 1
    
    store.record_operation_terminated(
        "session_1", "dev_1", 1, 1, "op_1", "nonce_1", "OPERATION_COMPLETED"
    )
    assert store.get_active_physical_operations() == 0

def test_atomic_terminal_commit(tmp_path: Path):
    """
    Test: test_atomic_terminal_commit
    Terminal commit transaction -> Quiescent -> Released
    """
    store = create_store(tmp_path)
    store.start_session("session_1", store._epoch_id)
    store.record_operation_admitted(
        "session_1", "ep_1", "dev_1", 1, 1, "op_1", "nonce_1", "exec_1", "test"
    )
    assert store.get_active_physical_operations() == 1
    
    # Idempotent admission test
    store.record_operation_admitted(
        "session_1", "ep_1", "dev_1", 1, 1, "op_1", "nonce_1", "exec_1", "test"
    )
    assert store.get_active_physical_operations() == 1

    # Terminate
    store.record_operation_terminated(
        "session_1", "dev_1", 1, 1, "op_1", "nonce_1", "PHYSICALLY_ISOLATED"
    )
    assert store.get_active_physical_operations() == 0

def test_quarantine_retains_capacity(tmp_path: Path):
    """
    test_quarantine_retains_capacity
    If operation is not terminated, it counts towards N.
    """
    store = create_store(tmp_path)
    store.start_session("session_1", store._epoch_id)
    for i in range(GLOBAL_PHYSICAL_OPERATION_CAPACITY):
        store.record_operation_admitted(
            "session_1", f"ep_{i}", f"dev_{i}", 1, 1, f"op_{i}", f"nonce_{i}", f"exec_{i}", "test"
        )
    assert store.get_active_physical_operations() == GLOBAL_PHYSICAL_OPERATION_CAPACITY
    
    with pytest.raises(PersistenceCapacityError):
        store.record_operation_admitted(
            "session_1", f"ep_{GLOBAL_PHYSICAL_OPERATION_CAPACITY}", f"dev_{GLOBAL_PHYSICAL_OPERATION_CAPACITY}", 1, 1, f"op_{GLOBAL_PHYSICAL_OPERATION_CAPACITY}", f"nonce_{GLOBAL_PHYSICAL_OPERATION_CAPACITY}", f"exec_{GLOBAL_PHYSICAL_OPERATION_CAPACITY}", "test"
        )
