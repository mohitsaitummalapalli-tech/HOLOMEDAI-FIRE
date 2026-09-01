"""HoloMed AI - Device Orchestration Event Sinks."""

from __future__ import annotations

import abc
from typing import List, Tuple

from holomed.devices.orchestration.exceptions import DeviceOrchestrationCapacityError
from holomed.devices.orchestration.models import MAX_RECORDED_ORCHESTRATION_EVENTS
from holomed.protocol.models import MessageEnvelope


class IDeviceOrchestrationEventSink(abc.ABC):
    """Authoritative sink interface for orchestration event envelopes."""

    @abc.abstractmethod
    def emit(self, envelope: MessageEnvelope) -> None:
        """Process or forward a single event envelope."""


class NullDeviceOrchestrationEventSink(IDeviceOrchestrationEventSink):
    """No-op sink discarding all emitted orchestration events."""

    def emit(self, envelope: MessageEnvelope) -> None:
        pass

    def __bool__(self) -> bool:
        return True


class RecordingDeviceOrchestrationEventSink(IDeviceOrchestrationEventSink):
    """Bounded recording sink storing up to MAX_RECORDED_ORCHESTRATION_EVENTS envelopes."""

    def __init__(self, max_capacity: int = MAX_RECORDED_ORCHESTRATION_EVENTS) -> None:
        self._max_capacity = max_capacity
        self._events: List[MessageEnvelope] = []

    def emit(self, envelope: MessageEnvelope) -> None:
        """Record an event envelope or raise DeviceOrchestrationCapacityError on overflow."""
        if len(self._events) >= self._max_capacity:
            raise DeviceOrchestrationCapacityError(
                f"RecordingDeviceOrchestrationEventSink capacity exceeded ({self._max_capacity})"
            )
        self._events.append(envelope)

    @property
    def events(self) -> Tuple[MessageEnvelope, ...]:
        """Immutable view of recorded event envelopes."""
        return tuple(self._events)

    def clear(self) -> None:
        """Clear recorded events."""
        self._events.clear()

    def __len__(self) -> int:
        return len(self._events)

    def __bool__(self) -> bool:
        return True
