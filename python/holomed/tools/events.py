# -*- coding: utf-8 -*-
"""Tool Event Sink and Audit Recording Infrastructure for M07."""

from __future__ import annotations

from holomed.protocol.models import MessageEnvelope
from holomed.tools.exceptions import ToolCapacityError
from holomed.tools.models import MAX_RECORDED_TOOL_EVENTS


class RecordingToolEventSink:
    """Bounded in-memory event recording sink for test verification and auditing."""

    def __init__(self, capacity: int = MAX_RECORDED_TOOL_EVENTS) -> None:
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
        """Record an emitted event envelope or raise ToolCapacityError on capacity breach."""
        if len(self._events) >= self._capacity:
            raise ToolCapacityError(
                f"RecordingToolEventSink capacity exceeded ({self._capacity} events max)"
            )
        self._events.append(envelope)

    def export_events(self) -> tuple[MessageEnvelope, ...]:
        """Return an immutable snapshot of all recorded event envelopes."""
        return tuple(self._events)

    def clear(self) -> None:
        """Clear all recorded events."""
        self._events.clear()
