# -*- coding: utf-8 -*-
"""Bounded, immutable-exporting event sink for gesture pipeline events."""

from __future__ import annotations

from holomed.gesture.exceptions import GestureCapacityError
from holomed.gesture.models import MAX_RECORDED_GESTURE_EVENTS
from holomed.protocol.models import MessageEnvelope


class RecordingGestureEventSink:
    """Bounded, immutable-exporting event sink for gesture pipeline events."""

    def __init__(self, capacity: int = MAX_RECORDED_GESTURE_EVENTS) -> None:
        self._capacity: int = capacity
        self._events: list[MessageEnvelope] = []

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def count(self) -> int:
        return len(self._events)

    def record_event(self, envelope: MessageEnvelope) -> None:
        """Record an event envelope, raising GestureCapacityError non-mutating if full."""
        if len(self._events) >= self._capacity:
            raise GestureCapacityError(
                f"RecordingGestureEventSink capacity exceeded ({self._capacity} events). "
                f"Event {envelope.message_id} rejected."
            )
        self._events.append(envelope)

    def get_events(self) -> tuple[MessageEnvelope, ...]:
        """Expose an immutable tuple of recorded event envelopes."""
        return tuple(self._events)

    def clear(self) -> None:
        """Clear all recorded events."""
        self._events.clear()
