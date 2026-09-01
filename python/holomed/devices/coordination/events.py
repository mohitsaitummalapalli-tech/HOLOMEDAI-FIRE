"""HoloMed AI - Coordination event sinks and bounded recording sinks."""

from __future__ import annotations

import abc
from typing import List, Tuple

from holomed.devices.coordination.exceptions import DeviceCoordinationCapacityError
from holomed.devices.coordination.models import MAX_RECORDED_COORDINATION_EVENTS
from holomed.protocol.models import MessageEnvelope


class IDeviceCoordinationEventSink(abc.ABC):
    """Hardware-independent event sink for device coordination events."""

    @abc.abstractmethod
    def emit(self, envelope: MessageEnvelope) -> None:
        """Emit a validated coordination message envelope."""


class NullDeviceCoordinationEventSink(IDeviceCoordinationEventSink):
    """No-op event sink discarding all emitted coordination events."""

    def emit(self, envelope: MessageEnvelope) -> None:
        pass


class RecordingDeviceCoordinationEventSink(IDeviceCoordinationEventSink):
    """Bounded recording event sink for testing and verification."""

    def __init__(self) -> None:
        self._events: List[MessageEnvelope] = []

    def emit(self, envelope: MessageEnvelope) -> None:
        """Record an envelope, rejecting with capacity error on event 1001."""
        if len(self._events) >= MAX_RECORDED_COORDINATION_EVENTS:
            raise DeviceCoordinationCapacityError(
                f"RecordingDeviceCoordinationEventSink capacity exceeded ({MAX_RECORDED_COORDINATION_EVENTS})"
            )
        self._events.append(envelope)

    @property
    def events(self) -> Tuple[MessageEnvelope, ...]:
        """Immutable view of recorded message envelopes."""
        return tuple(self._events)

    def clear(self) -> None:
        """Reset the recorded event buffer to empty."""
        self._events.clear()

    def __len__(self) -> int:
        return len(self._events)

    def __bool__(self) -> bool:
        return True
