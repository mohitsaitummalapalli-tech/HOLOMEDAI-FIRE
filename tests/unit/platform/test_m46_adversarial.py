import threading
import time
import pytest
from holomed.platform.barrier import SessionLifecycleGate, StaleCommitError
from holomed.platform.session import SessionManager
from holomed.runtime.service import ServiceState

def test_01_stale_generation_rejected():
    gate = SessionLifecycleGate("sess_1")
    gen = gate.generation
    
    gate.mark_terminated()
    
    def dummy_commit():
        return []
        
    with pytest.raises(StaleCommitError):
        gate.execute_commit(gen, dummy_commit)

def test_02_commit_first_stop_waits():
    gate = SessionLifecycleGate("sess_1")
    gen = gate.generation
    
    commit_entered = threading.Event()
    commit_can_finish = threading.Event()
    
    def slow_commit():
        commit_entered.set()
        commit_can_finish.wait(timeout=2)
        return []
        
    def run_commit():
        gate.execute_commit(gen, slow_commit)
        
    t = threading.Thread(target=run_commit)
    t.start()
    
    assert commit_entered.wait(timeout=2)
    
    transition_done = []
    def run_transition():
        gate.mark_terminated()
        transition_done.append(True)
        
    t2 = threading.Thread(target=run_transition)
    t2.start()
    
    time.sleep(0.1)
    assert len(transition_done) == 0
    
    commit_can_finish.set()
    t.join()
    t2.join()
    
    assert len(transition_done) == 1
    assert not gate.is_active

def test_03_stop_first_commit_fails():
    gate = SessionLifecycleGate("sess_1")
    gen = gate.generation
    
    gate.mark_terminated()
    
    commit_result = []
    def run_commit():
        def dummy_commit():
            return []
        try:
            res = gate.execute_commit(gen, dummy_commit)
            commit_result.append(res)
        except StaleCommitError as e:
            commit_result.append(e)
            
    t2 = threading.Thread(target=run_commit)
    t2.start()
    t2.join()
    
    assert len(commit_result) == 1
    assert type(commit_result[0]) is StaleCommitError

def test_04_reentrant_event_emission_safety():
    gate = SessionLifecycleGate("sess_1")
    gen = gate.generation
    
    def event_subscriber():
        gate.mark_terminated()
        
    def commit_fn():
        return [("event1", {})]
        
    events = gate.execute_commit(gen, commit_fn)
    assert events is not None
    for _ in events:
        event_subscriber()
        
    assert not gate.is_active
    
def test_05_authoritative_paths_evict():
    manager = SessionManager(epoch_id=0)
    sess = manager.start_session("sess_1", 0)
    
    gate = manager.get_lifecycle_gate("sess_1")
    assert gate.is_active
    
    manager.evict_session("sess_1")
    assert not gate.is_active

def test_06_authoritative_paths_reset():
    manager = SessionManager(epoch_id=0)
    sess = manager.start_session("sess_1", 0)
    
    gate = manager.get_lifecycle_gate("sess_1")
    assert gate.is_active
    
    manager.reset(1)
    assert not gate.is_active

def test_07_authoritative_paths_clear():
    manager = SessionManager(epoch_id=0)
    sess = manager.start_session("sess_1", 0)
    
    gate = manager.get_lifecycle_gate("sess_1")
    assert gate.is_active
    
    manager.clear()
    assert not gate.is_active
    
def test_08_starvation_prevention():
    gate = SessionLifecycleGate("sess_1")
    gen = gate.generation
    
    keep_committing = True
    commits_done = []
    
    def run_telemetry():
        while keep_committing:
            def dummy_commit():
                return []
            if gate.execute_commit(gen, dummy_commit) == []:
                commits_done.append(1)
            time.sleep(0.001)
            
    t = threading.Thread(target=run_telemetry)
    t.start()
    
    time.sleep(0.05)
    assert len(commits_done) > 0
    
    gate.mark_terminated()
    
    assert not gate.is_active
    
    keep_committing = False
    t.join()

def test_09_isolation():
    gate1 = SessionLifecycleGate("sess_1")
    gate2 = SessionLifecycleGate("sess_2")
    
    gen1 = gate1.generation
    
    commit_entered = threading.Event()
    commit_can_finish = threading.Event()
    
    def slow_commit():
        commit_entered.set()
        commit_can_finish.wait(timeout=2)
        return []
        
    def run_commit():
        gate1.execute_commit(gen1, slow_commit)
        
    t = threading.Thread(target=run_commit)
    t.start()
    
    assert commit_entered.wait(timeout=2)
    
    start_time = time.time()
    gate2.mark_terminated()
    duration = time.time() - start_time
    
    assert duration < 0.1
    assert not gate2.is_active
    
    commit_can_finish.set()
    t.join()
    assert gate1.is_active

def test_10_gate_pure_computation_exception():
    gate = SessionLifecycleGate("sess_1")
    gen = gate.generation
    
    def failing_commit():
        raise ValueError("Oops")
        
    with pytest.raises(ValueError):
        gate.execute_commit(gen, failing_commit)
        
    def good_commit():
        return []
        
    assert gate.execute_commit(gen, good_commit) == []
