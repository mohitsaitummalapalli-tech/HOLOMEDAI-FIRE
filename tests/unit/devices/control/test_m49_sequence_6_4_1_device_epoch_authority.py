import json
import multiprocessing
import os
import time
from pathlib import Path
from multiprocessing import Barrier

import pytest

from holomed.persistence.authority import ControllerAuthorityStore, DeviceEpochAuthority
from holomed.persistence.exceptions import (
    PersistenceEpochMismatchError,
    PersistenceLifecycleError,
    PersistenceResourceIntegrityError,
)

def _allocate_in_process(storage_root, device_id, barrier, queue):
    """Helper for testing cross-process allocation"""
    try:
        authority = DeviceEpochAuthority(storage_root)
        barrier.wait(timeout=5)
        epoch = authority.allocate_next_device_epoch(device_id)
        queue.put(epoch)
    except Exception as e:
        queue.put(e)

def _hold_authority_and_wait(storage_root, device_id, epoch, barrier, queue):
    try:
        authority = DeviceEpochAuthority(storage_root)
        with authority.hold_device_epoch_authority(device_id, epoch):
            barrier.wait(timeout=5)
            # Sleep slightly to let the other process try to grab it
            time.sleep(0.5)
            queue.put("held")
    except Exception as e:
        queue.put(e)

def _allocate_while_held(storage_root, device_id, barrier, queue):
    try:
        authority = DeviceEpochAuthority(storage_root)
        barrier.wait(timeout=5)
        # Should block until the holding process releases
        epoch = authority.allocate_next_device_epoch(device_id)
        queue.put(epoch)
    except Exception as e:
        queue.put(e)

def test_device_epoch_authority_initialization(tmp_path):
    authority = DeviceEpochAuthority(tmp_path)
    # Shouldn't create devices/<device_id> until used
    assert (tmp_path / "devices").exists()

def test_device_epoch_allocation_is_monotonic(tmp_path):
    authority = DeviceEpochAuthority(tmp_path)
    device_id = "dev_123"
    
    e1 = authority.allocate_next_device_epoch(device_id)
    e2 = authority.allocate_next_device_epoch(device_id)
    e3 = authority.allocate_next_device_epoch(device_id)
    
    assert e1 == 1
    assert e2 == 2
    assert e3 == 3

def test_device_epoch_allocation_is_durable(tmp_path):
    authority = DeviceEpochAuthority(tmp_path)
    device_id = "dev_durability"
    
    e1 = authority.allocate_next_device_epoch(device_id)
    assert e1 == 1
    
    # Read directly from disk
    with open(tmp_path / "devices" / device_id / "device_epoch.json") as f:
        data = json.load(f)
        assert data["device_epoch"] == 1

def test_device_epoch_read_survives_new_process(tmp_path):
    auth1 = DeviceEpochAuthority(tmp_path)
    auth1.allocate_next_device_epoch("dev1")
    auth1.allocate_next_device_epoch("dev1")
    
    auth2 = DeviceEpochAuthority(tmp_path)
    epoch = auth2.read_current_device_epoch("dev1")
    assert epoch == 2

def test_device_epoch_cross_process_allocation_is_exclusive(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    
    device_id = "dev_concurrent"
    
    p1 = ctx.Process(target=_allocate_in_process, args=(tmp_path, device_id, barrier, queue))
    p2 = ctx.Process(target=_allocate_in_process, args=(tmp_path, device_id, barrier, queue))
    
    p1.start()
    p2.start()
    p1.join()
    p2.join()
    
    results = [queue.get(), queue.get()]
    # Should get exactly 1 and 2 in some order
    assert set(results) == {1, 2}

def test_stale_device_epoch_is_rejected(tmp_path):
    authority = DeviceEpochAuthority(tmp_path)
    device_id = "dev_stale"
    
    authority.allocate_next_device_epoch(device_id)
    authority.allocate_next_device_epoch(device_id)  # currently 2
    
    with pytest.raises(PersistenceEpochMismatchError):
        authority.assert_authoritative_device_epoch(device_id, 1)

def test_non_authoritative_device_epoch_is_rejected(tmp_path):
    authority = DeviceEpochAuthority(tmp_path)
    device_id = "dev_future"
    
    authority.allocate_next_device_epoch(device_id)
    
    with pytest.raises(PersistenceEpochMismatchError):
        authority.assert_authoritative_device_epoch(device_id, 99)

def test_device_epoch_authority_lock_prevents_toc_tou(tmp_path):
    authority = DeviceEpochAuthority(tmp_path)
    device_id = "dev_toctou"
    epoch = authority.allocate_next_device_epoch(device_id)
    
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    
    p1 = ctx.Process(target=_hold_authority_and_wait, args=(tmp_path, device_id, epoch, barrier, queue))
    p2 = ctx.Process(target=_allocate_while_held, args=(tmp_path, device_id, barrier, queue))
    
    p1.start()
    p2.start()
    
    p1.join()
    p2.join()
    
    r1 = queue.get()
    r2 = queue.get()
    
    # p1 should return "held", p2 should return the new epoch 2, meaning p2 waited for p1.
    assert set([r1, r2]) == {"held", 2}
    
def test_corrupt_device_epoch_fails_closed(tmp_path):
    authority = DeviceEpochAuthority(tmp_path)
    device_id = "dev_corrupt"
    
    authority.allocate_next_device_epoch(device_id)
    
    # Corrupt the file
    epoch_file = tmp_path / "devices" / device_id / "device_epoch.json"
    with open(epoch_file, "w") as f:
        f.write("{corrupt_json: }")
        
    with pytest.raises(PersistenceResourceIntegrityError):
        authority.read_current_device_epoch(device_id)
        
    with open(epoch_file, "w") as f:
        json.dump({"wrong_key": 5}, f)
        
    with pytest.raises(PersistenceResourceIntegrityError):
        authority.read_current_device_epoch(device_id)

    with open(epoch_file, "w") as f:
        json.dump({"device_epoch": -1}, f)
        
    with pytest.raises(PersistenceResourceIntegrityError):
        authority.read_current_device_epoch(device_id)

def test_missing_device_epoch_fails_closed(tmp_path):
    authority = DeviceEpochAuthority(tmp_path)
    device_id = "dev_missing"
    
    with pytest.raises(PersistenceResourceIntegrityError):
        authority.read_current_device_epoch(device_id)

def test_device_epochs_are_independent_per_device(tmp_path):
    authority = DeviceEpochAuthority(tmp_path)
    
    a1 = authority.allocate_next_device_epoch("devA")
    a2 = authority.allocate_next_device_epoch("devA")
    
    b1 = authority.allocate_next_device_epoch("devB")
    
    assert a1 == 1
    assert a2 == 2
    assert b1 == 1
    
    assert authority.read_current_device_epoch("devA") == 2
    assert authority.read_current_device_epoch("devB") == 1

def test_controller_epoch_changes_do_not_change_device_epoch(tmp_path):
    controller_auth = ControllerAuthorityStore(tmp_path)
    device_auth = DeviceEpochAuthority(tmp_path)
    
    device_auth.allocate_next_device_epoch("devA")
    c1 = controller_auth.allocate_next_epoch()
    
    assert device_auth.read_current_device_epoch("devA") == 1
    
    c2 = controller_auth.allocate_next_epoch()
    
    assert device_auth.read_current_device_epoch("devA") == 1
    assert c2 == 2

def test_device_epoch_changes_do_not_allocate_controller_epoch(tmp_path):
    controller_auth = ControllerAuthorityStore(tmp_path)
    device_auth = DeviceEpochAuthority(tmp_path)
    
    c1 = controller_auth.allocate_next_epoch()
    
    device_auth.allocate_next_device_epoch("devA")
    device_auth.allocate_next_device_epoch("devA")
    
    assert controller_auth.read_current_epoch() == c1
    assert device_auth.read_current_device_epoch("devA") == 2
