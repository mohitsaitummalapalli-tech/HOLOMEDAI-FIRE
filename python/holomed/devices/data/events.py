"""HoloMed AI - Device data event sink interfaces and test doubles."""

from __future__ import annotations

import abc
from typing import List

from holomed.protocol.models import MessageEnvelope


class IDeviceDataEventSink(abc.ABC):
    """Abstract sink for device data plane lifecycle and audit events."""

    @abc.abstractmethod
    def emit(self, event: MessageEnvelope) -> None:
        """Emit an event envelope."""
        pass


class NullDeviceDataEventSink(IDeviceDataEventSink):
    """Default no-op event sink."""

    def emit(self, event: MessageEnvelope) -> None:
        pass


class RecordingDeviceDataEventSink(IDeviceDataEventSink):
    """Test double recording all emitted event envelopes."""

    def __init__(self) -> None:
        self.events: List[MessageEnvelope] = []

    def emit(self, event: MessageEnvelope) -> None:
        self.events.append(event)

    def clear(self) -> None:
        self.events.clear()
