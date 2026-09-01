# -*- coding: utf-8 -*-
"""Ultron Event Sink and Protocol Dispatch Structures for M04."""

from __future__ import annotations

from holomed.protocol.models import MessageEnvelope
from holomed.ultron.exceptions import UltronCapacityError
from holomed.ultron.models import MAX_RECORDED_ULTRON_EVENTS


class RecordingUltronEventSink:
    """Thread-free, bounded in-memory event recording sink for test assertions and auditing."""

    def __init__(self, capacity: int = MAX_RECORDED_ULTRON_EVENTS) -> None:
        self._capacity = capacity
        self._events: list[MessageEnvelope] = []

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def count(self) -> int:
        return len(self._events)

    @property
    def is_full(self) -> bool:
        return len(self._events) >= self._capacity

    def record_event(self, envelope: MessageEnvelope) -> None:
        """Record an emitted event envelope or raise UltronCapacityError on capacity breach."""
        if len(self._events) >= self._capacity:
            raise UltronCapacityError(
                f"RecordingUltronEventSink capacity exceeded ({self._capacity} events max)"
            )
        self._events.append(envelope)

    def export_events(self) -> tuple[MessageEnvelope, ...]:
        """Return an immutable snapshot of all recorded event envelopes."""
        return tuple(self._events)

    def clear(self) -> None:
        """Clear all recorded events."""
        self._events.clear()
