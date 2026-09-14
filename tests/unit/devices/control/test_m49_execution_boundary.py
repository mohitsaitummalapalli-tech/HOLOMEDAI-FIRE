import time
import pytest
import threading
from unittest.mock import patch

from holomed.devices.models import (
    EndpointLease,
    PhysicalCommand,
    SubmissionStatus,
)
from holomed.devices.simulated import SimulatedPhysicalEndpoint, WorkerState

@pytest.fixture
def active_endpoint() -> SimulatedPhysicalEndpoint:
    ep = SimulatedPhysicalEndpoint("ep_1", "dev_1", queue_capacity=2)
    lease = EndpointLease(
        session_id="session_1",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id="exec_1",
        capability_scope=frozenset(["test"]),
        endpoint_id="ep_1",
        device_id="dev_1"
    )
    ep.acquire_lease(lease)
    yield ep
    ep.stop_worker()

def _make_command(seq: int, exec_id: str) -> PhysicalCommand:
    return PhysicalCommand(
        session_id="session_1",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id=exec_id,
        capability_scope=frozenset(["test"]),
        command_sequence=seq,
        operation="test",
        parameters={},
        endpoint_id="ep_1"
    )

def test_duplicate_submission(active_endpoint: SimulatedPhysicalEndpoint) -> None:
    cmd1 = _make_command(1, "exec_1")
    res1 = active_endpoint.submit_command(cmd1)
    assert res1.status == SubmissionStatus.ACCEPTED

    res2 = active_endpoint.submit_command(cmd1)
    assert res2.status == SubmissionStatus.DUPLICATE_REJECTED

def test_queue_capacity_bounded(active_endpoint: SimulatedPhysicalEndpoint) -> None:
    cmds = [_make_command(i, f"exec_{i}") for i in range(1, 5)]
    res1 = active_endpoint.submit_command(cmds[0])
    res2 = active_endpoint.submit_command(cmds[1])
    res3 = active_endpoint.submit_command(cmds[2])
    res4 = active_endpoint.submit_command(cmds[3])

    assert res1.status == SubmissionStatus.ACCEPTED
    assert SubmissionStatus.QUEUE_FULL in [res2.status, res3.status, res4.status]

def test_stop_scope(active_endpoint: SimulatedPhysicalEndpoint) -> None:
    active_endpoint.request_stop("exec_A")
    assert "exec_A" in active_endpoint._stop_requests
    assert "exec_B" not in active_endpoint._stop_requests

def test_shutdown_racing_with_submit(active_endpoint: SimulatedPhysicalEndpoint) -> None:
    active_endpoint.stop_worker()
    cmd = _make_command(1, "exec_1")
    res = active_endpoint.submit_command(cmd)
    assert res.status in (SubmissionStatus.SHUTTING_DOWN, SubmissionStatus.WORKER_UNAVAILABLE)

def test_worker_death_racing(active_endpoint: SimulatedPhysicalEndpoint) -> None:
    active_endpoint._shutdown_event.set()
    if active_endpoint._worker_thread:
        active_endpoint._worker_thread.join()
    cmd = _make_command(1, "exec_1")
    res = active_endpoint.submit_command(cmd)
    assert res.status == SubmissionStatus.WORKER_UNAVAILABLE


# --- DETERMINISTIC CONCURRENCY & RACE PROOFS ---

def test_scenario_a_request_stop_independent_of_execution(active_endpoint: SimulatedPhysicalEndpoint) -> None:
    worker_reached_driver = threading.Event()
    test_can_proceed = threading.Event()

    def fake_sleep(secs):
        worker_reached_driver.set()
        test_can_proceed.wait(timeout=2.0)

    with patch("holomed.devices.simulated.time.sleep", side_effect=fake_sleep):
        active_endpoint.submit_command(_make_command(1, "exec_a"))
        assert worker_reached_driver.wait(timeout=1.0)

        # Test request_stop(A) while driver is blocked
        start = time.perf_counter()
        active_endpoint.request_stop("exec_a")
        dur = time.perf_counter() - start

        test_can_proceed.set()
        active_endpoint._command_queue.join()

        assert dur < 0.1, "request_stop blocked on execution"

def test_scenario_b_submit_command_independent_of_execution(active_endpoint: SimulatedPhysicalEndpoint) -> None:
    worker_reached_driver = threading.Event()
    test_can_proceed = threading.Event()

    def fake_sleep(secs):
        worker_reached_driver.set()
        test_can_proceed.wait(timeout=2.0)

    with patch("holomed.devices.simulated.time.sleep", side_effect=fake_sleep):
        active_endpoint.submit_command(_make_command(1, "exec_a"))
        assert worker_reached_driver.wait(timeout=1.0)

        # Test submit_command(B) while driver is blocked
        start = time.perf_counter()
        active_endpoint.submit_command(_make_command(2, "exec_b"))
        dur = time.perf_counter() - start

        test_can_proceed.set()
        active_endpoint._command_queue.join()

        assert dur < 0.1, "submit_command blocked on execution"

def test_race_lease_revoked_after_claim(active_endpoint: SimulatedPhysicalEndpoint) -> None:
    worker_reached_driver = threading.Event()
    test_can_proceed = threading.Event()
    sleep_calls = []

    def fake_sleep(secs):
        sleep_calls.append(1)
        worker_reached_driver.set()
        test_can_proceed.wait(timeout=2.0)

    with patch("holomed.devices.simulated.time.sleep", side_effect=fake_sleep):
        active_endpoint.submit_command(_make_command(1, "exec_a"))
        assert worker_reached_driver.wait(timeout=1.0)

        # Revoke lease AFTER A claims execution
        active_endpoint.release_lease("session_1")
        test_can_proceed.set()
        active_endpoint._command_queue.join()

        assert len(sleep_calls) == 5, "Command A was discarded instead of finishing execution"

def test_race_lease_revoked_before_claim(active_endpoint: SimulatedPhysicalEndpoint) -> None:
    worker_reached_driver = threading.Event()
    test_can_proceed = threading.Event()
    sleep_calls = []

    def fake_sleep(secs):
        sleep_calls.append(1)
        worker_reached_driver.set()
        test_can_proceed.wait(timeout=2.0)

    with patch("holomed.devices.simulated.time.sleep", side_effect=fake_sleep):
        # Block worker with dummy
        active_endpoint.submit_command(_make_command(1, "exec_dummy"))
        assert worker_reached_driver.wait(timeout=1.0)

        # Submit A (will be queued, not claimed)
        active_endpoint.submit_command(_make_command(2, "exec_a"))

        # Revoke lease BEFORE A claims execution
        active_endpoint.release_lease("session_1")

        worker_reached_driver.clear()
        test_can_proceed.set()

        active_endpoint._command_queue.join()

        # Only the dummy should have slept (5 calls for dummy). A should be skipped at dequeue.
        assert len(sleep_calls) == 5, "Command A executed despite being stale at dequeue"

def test_race_stop_after_claim(active_endpoint: SimulatedPhysicalEndpoint) -> None:
    worker_reached_driver = threading.Event()
    test_can_proceed = threading.Event()
    sleep_calls = []

    def fake_sleep(secs):
        sleep_calls.append(1)
        worker_reached_driver.set()
        test_can_proceed.wait(timeout=2.0)

    with patch("holomed.devices.simulated.time.sleep", side_effect=fake_sleep):
        active_endpoint.submit_command(_make_command(1, "exec_a"))
        assert worker_reached_driver.wait(timeout=1.0)

        # Request stop AFTER A claims execution
        active_endpoint.request_stop("exec_a")

        test_can_proceed.set()
        active_endpoint._command_queue.join()

        # Loop breaks early, so sleep is called only 1 time instead of 5
        assert len(sleep_calls) == 1, "Stop was not honored during physical execution"

def test_race_stop_before_claim(active_endpoint: SimulatedPhysicalEndpoint) -> None:
    worker_reached_driver = threading.Event()
    test_can_proceed = threading.Event()
    sleep_calls = []

    def fake_sleep(secs):
        sleep_calls.append(1)
        worker_reached_driver.set()
        test_can_proceed.wait(timeout=2.0)

    with patch("holomed.devices.simulated.time.sleep", side_effect=fake_sleep):
        active_endpoint.submit_command(_make_command(1, "exec_dummy"))
        assert worker_reached_driver.wait(timeout=1.0)

        active_endpoint.submit_command(_make_command(2, "exec_a"))

        # Request stop BEFORE A claims execution
        active_endpoint.request_stop("exec_a")

        worker_reached_driver.clear()
        test_can_proceed.set()

        active_endpoint._command_queue.join()

        # Dummy calls sleep 5 times. A should call it 0 times, because it's stopped before execution.
        assert len(sleep_calls) == 5, "Command A physically executed despite prior stop request"
