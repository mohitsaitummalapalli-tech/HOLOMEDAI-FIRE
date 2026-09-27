import multiprocessing
from pathlib import Path

def _child_worker(storage_root: str, device_id: str, results_queue: multiprocessing.Queue):
    from holomed.persistence.authority import DeviceEpochAuthority
    try:
        auth = DeviceEpochAuthority(Path(storage_root))
        epoch = auth.allocate_next_device_epoch(device_id)
        results_queue.put(("success", epoch))
    except Exception as e:
        results_queue.put(("error", str(e)))

def test_device_epoch_concurrency_proof(tmp_path):
    storage_root = tmp_path / "devices"
    storage_root.mkdir(parents=True)
    device_id = "test-device-concurrent-123"
    
    ctx = multiprocessing.get_context('spawn')
    queue = ctx.Queue()
    
    processes = []
    # Spawn 10 concurrent processes
    for _ in range(10):
        p = ctx.Process(target=_child_worker, args=(str(storage_root), device_id, queue))
        processes.append(p)
        p.start()
        
    for p in processes:
        p.join()
        
    epochs = set()
    errors = []
    
    while not queue.empty():
        status, val = queue.get()
        if status == "success":
            epochs.add(val)
        else:
            errors.append(val)
            
    assert not errors, f"Errors occurred during concurrent allocation: {errors}"
    assert len(epochs) == 10, f"Expected 10 distinct epochs, got {len(epochs)}: {epochs}"
    
    # Verify the sequential nature
    for i in range(1, 11):
        assert i in epochs, f"Missing epoch {i} in allocated set"
        
    # Read the final authoritative epoch
    from holomed.persistence.authority import DeviceEpochAuthority
    auth = DeviceEpochAuthority(storage_root)
    final_epoch = auth.read_current_device_epoch(device_id)
    assert final_epoch == 10, f"Expected final epoch 10, got {final_epoch}"
