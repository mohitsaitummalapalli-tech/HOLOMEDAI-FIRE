# -*- coding: utf-8 -*-
"""Platform Event Sink and Audit Recording Infrastructure for M08."""

from __future__ import annotations

from holomed.protocol.models import MessageEnvelope
from holomed.platform.exceptions import PlatformCapacityError
from holomed.platform.models import MAX_RECORDED_PLATFORM_EVENTS


class RecordingPlatformEventSink:
    """Bounded in-memory event recording sink for test verification and auditing."""

    def __init__(self, capacity: int = MAX_RECORDED_PLATFORM_EVENTS) -> None:
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
        """Record an emitted event envelope or raise PlatformCapacityError on capacity breach."""
        if len(self._events) >= self._capacity:
            raise PlatformCapacityError(
                f"RecordingPlatformEventSink capacity exceeded ({self._capacity} events max)"
            )
        self._events.append(envelope)

    def export_events(self) -> tuple[MessageEnvelope, ...]:
        """Return an immutable snapshot of all recorded event envelopes."""
        return tuple(self._events)

    def clear(self) -> None:
        """Clear all recorded events."""
        self._events.clear()
