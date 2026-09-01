# -*- coding: utf-8 -*-
"""Bounded In-Memory Event Sink for M10 Workflow Subsystem."""

from __future__ import annotations

from holomed.protocol.models import MessageEnvelope
from holomed.workflow.exceptions import WorkflowCapacityError
from holomed.workflow.models import MAX_WORKFLOW_EVENT_LOG


class RecordingWorkflowEventSink:
    """Bounded in-memory event recording sink for test verification and telemetry."""

    def __init__(self, capacity: int = MAX_WORKFLOW_EVENT_LOG) -> None:
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
        """Record an emitted event envelope or raise WorkflowCapacityError on capacity breach."""
        if len(self._events) >= self._capacity:
            raise WorkflowCapacityError(
                f"RecordingWorkflowEventSink capacity exceeded ({self._capacity} events max)"
            )
        self._events.append(envelope)

    def export_events(self) -> tuple[MessageEnvelope, ...]:
        """Return an immutable snapshot of all recorded event envelopes."""
        return tuple(self._events)

    def clear(self) -> None:
        """Clear all recorded events."""
        self._events.clear()
