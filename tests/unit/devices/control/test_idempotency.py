"""Unit tests for M00.6 IdempotencyTracker ring buffer."""

import pytest

from holomed.devices.control.exceptions import IdempotencyConflictError
from holomed.devices.control.idempotency import IdempotencyTracker, compute_payload_hash


def test_idempotency_same_message_id_same_payload() -> None:
    tracker = IdempotencyTracker(max_keys=10)
    msg_id = "11111111-1111-4111-8111-111111111111"
    payload = {"device_id": "cam1", "command": "reset", "parameters": {"mode": 1}}
    response_payload = {"status": "ok", "code": 0}

    # Not cached initially
    assert tracker.get(msg_id, payload) is None

    # Record response
    tracker.record(msg_id, payload, response_payload)

    # Subsequent query returns cached response
    cached = tracker.get(msg_id, payload)
    assert cached is not None
    assert cached.message_id == msg_id
    assert cached.response_payload["status"] == "ok"


def test_idempotency_same_message_id_conflicting_payload() -> None:
    tracker = IdempotencyTracker(max_keys=10)
    msg_id = "11111111-1111-4111-8111-111111111111"
    payload1 = {"device_id": "cam1", "command": "reset", "parameters": {"mode": 1}}
    payload2 = {"device_id": "cam1", "command": "reset", "parameters": {"mode": 2}}

    tracker.record(msg_id, payload1, {"status": "ok"})

    # Conflict raises IdempotencyConflictError
    with pytest.raises(IdempotencyConflictError, match="conflicting payload"):
        tracker.get(msg_id, payload2)


def test_idempotency_bounded_fifo_eviction() -> None:
    tracker = IdempotencyTracker(max_keys=3)

    # Insert 3 entries
    for i in range(1, 4):
        mid = f"mid_{i}"
        tracker.record(mid, {"val": i}, {"res": i})

    assert len(tracker) == 3
    assert tracker.get("mid_1", {"val": 1}) is not None

    # Insert 4th entry -> mid_1 evicted
    tracker.record("mid_4", {"val": 4}, {"res": 4})
    assert len(tracker) == 3
    assert tracker.get("mid_1", {"val": 1}) is None
    assert tracker.get("mid_2", {"val": 2}) is not None
    assert tracker.get("mid_4", {"val": 4}) is not None


def test_idempotency_clear() -> None:
    tracker = IdempotencyTracker(max_keys=10)
    tracker.record("mid_1", {"k": "v"}, {"res": 1})
    assert len(tracker) == 1
    tracker.clear()
    assert len(tracker) == 0
