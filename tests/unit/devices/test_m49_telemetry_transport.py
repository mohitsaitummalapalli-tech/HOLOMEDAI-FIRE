"""Tests for M49.3.3.2 Bounded Non-Blocking Telemetry Transport."""

import threading
from typing import Any

import pytest

from holomed.devices.models import (
    CommandState,
    EventSourceAuthority,
    ExecutionTelemetryEvent,
)
from holomed.devices.transport import TelemetryTransport

def _create_event(state: CommandState, seq: int = 1) -> ExecutionTelemetryEvent:
    return ExecutionTelemetryEvent(
        evidence_generation=1,
        cryptographic_signature=None,
        fencing_challenge=None,
        event_id=f"evt-{seq}",
        endpoint_id="end-1",
        session_id="sess-1",
        lifecycle_generation=1,
        endpoint_lease_generation=1,
        execution_id="exec-1",
        command_sequence=1,
        event_sequence=seq,
        event_type="test_event",
        observed_state=state,
        source_authority=EventSourceAuthority.HARDWARE_DRIVER,
        source_origin="driver",
        timestamp_utc="2026-09-15T00:00:00Z",
        payload={"data": 1}
    )


def test_transport_normal_capacity_drop():
    transport = TelemetryTransport(normal_capacity=5, critical_capacity=5)
    pub = transport.publisher

    for i in range(5):
        pub.publish(_create_event(CommandState.RUNNING, i + 1))

    pub.publish(_create_event(CommandState.RUNNING, 6))

    assert transport.has_critical_loss is False

    events = transport.drain_normal()
    assert len(events) == 5
    assert events[-1].event_sequence == 5


def test_transport_critical_capacity_loss():
    transport = TelemetryTransport(normal_capacity=5, critical_capacity=5)
    pub = transport.publisher

    for i in range(5):
        pub.publish(_create_event(CommandState.COMPLETED, i + 1))

    assert transport.has_critical_loss is False

    pub.publish(_create_event(CommandState.COMPLETED, 6))

    assert transport.has_critical_loss is True

    events = transport.drain_critical()
    assert len(events) == 5
    assert events[-1].event_sequence == 5


def test_transport_isolation():
    transport = TelemetryTransport(normal_capacity=5, critical_capacity=5)
    pub = transport.publisher

    for i in range(10):
        pub.publish(_create_event(CommandState.RUNNING, i + 1))

    pub.publish(_create_event(CommandState.COMPLETED, 100))

    assert transport.has_critical_loss is False
    assert len(transport.drain_normal()) == 5
    assert len(transport.drain_critical()) == 1


def test_transport_shutdown_race():
    transport = TelemetryTransport(normal_capacity=1000, critical_capacity=1000)
    pub = transport.publisher

    def worker():
        for i in range(1, 501):
            pub.publish(_create_event(CommandState.RUNNING, i))

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)

    t1.start()
    t2.start()

    transport.shutdown()

    t1.join()
    t2.join()

    events = transport.drain_normal()
    assert len(events) <= 1000

    pub.publish(_create_event(CommandState.RUNNING, 9999))
    assert len(transport.drain_normal()) == 0


def test_publisher_failure_isolation():
    transport = TelemetryTransport()
    pub = transport.publisher

    pub.publish(None)  # type: ignore
    pub.publish("not an event")  # type: ignore

    assert len(transport.drain_normal()) == 0
    assert len(transport.drain_critical()) == 0
    assert transport.has_critical_loss is False


def test_adversarial_consumer_blocked():
    """
    Prove that a completely blocked/undequeued transport consumer
    does not block the physical execution worker.
    """
    transport = TelemetryTransport(normal_capacity=10, critical_capacity=10)
    pub = transport.publisher

    barrier = threading.Barrier(2)
    producer_finished = threading.Event()

    def producer():
        barrier.wait()
        for i in range(1, 101):
            pub.publish(_create_event(CommandState.RUNNING, i))
            pub.publish(_create_event(CommandState.COMPLETED, i))
        producer_finished.set()

    def consumer():
        barrier.wait()
        # Simulate consumer not draining
        pass

    t1 = threading.Thread(target=producer)
    t2 = threading.Thread(target=consumer)

    t1.start()
    t2.start()

    finished_in_time = producer_finished.wait(timeout=2.0)
    assert finished_in_time is True, "Producer blocked on saturated queues!"

    t1.join()
    t2.join()

    assert transport.has_critical_loss is True
    assert len(transport.drain_normal()) == 10
    assert len(transport.drain_critical()) == 10


def test_integrity_fifo_and_no_mutation():
    transport = TelemetryTransport(normal_capacity=50, critical_capacity=50)
    pub = transport.publisher

    seqs = [5, 2, 8, 1]
    events = []
    for s in seqs:
        evt = _create_event(CommandState.RUNNING, s)
        events.append(evt)
        pub.publish(evt)

    drained = transport.drain_normal()
    assert len(drained) == 4

    for i in range(4):
        assert drained[i] is events[i]
        assert drained[i].event_sequence == seqs[i]


def test_concurrent_normal_publishers():
    transport = TelemetryTransport(normal_capacity=10000)
    pub = transport.publisher

    def worker(start: int):
        for i in range(1, 501):
            pub.publish(_create_event(CommandState.RUNNING, start + i))

    threads = [threading.Thread(target=worker, args=(i*1000,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(transport.drain_normal()) == 5000


def test_concurrent_critical_publishers():
    transport = TelemetryTransport(normal_capacity=100, critical_capacity=10000)
    pub = transport.publisher

    def worker(start: int):
        for i in range(1, 101):
            pub.publish(_create_event(CommandState.COMPLETED, start + i))

    threads = [threading.Thread(target=worker, args=(i*1000,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(transport.drain_critical()) == 1000
    assert transport.has_critical_loss is False
