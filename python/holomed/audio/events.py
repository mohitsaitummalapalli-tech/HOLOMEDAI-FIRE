# -*- coding: utf-8 -*-
"""M02 Audio Subsystem Bounded Event Sink.

Implements RecordingAudioEventSink with strict 1000-event capacity limit (D193, §46).
"""

from typing import Sequence
from holomed.audio.exceptions import AudioCapacityError
from holomed.audio.models import MAX_AUDIO_EVENTS
from holomed.protocol.models import MessageEnvelope


class RecordingAudioEventSink:
    """Bounded, immutable-exporting event sink for audio pipeline events."""

    def __init__(self, capacity: int = MAX_AUDIO_EVENTS) -> None:
        self._capacity: int = capacity
        self._events: list[MessageEnvelope] = []

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def count(self) -> int:
        return len(self._events)

    def record_event(self, envelope: MessageEnvelope) -> None:
        """Record an event envelope, raising AudioCapacityError non-mutating if full."""
        if len(self._events) >= self._capacity:
            raise AudioCapacityError(
                f"RecordingAudioEventSink capacity exceeded ({self._capacity} events). "
                f"Event {envelope.message_id} rejected."
            )
        self._events.append(envelope)

    def get_events(self) -> tuple[MessageEnvelope, ...]:
        """Expose an immutable tuple of recorded event envelopes."""
        return tuple(self._events)

    def clear(self) -> None:
        """Clear all recorded events."""
        self._events.clear()
