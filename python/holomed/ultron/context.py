# -*- coding: utf-8 -*-
"""Bounded Multimodal Context Manager for M04 Ultron."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Optional, Sequence

from holomed.ultron.models import (
    MAX_CONTEXT_CONFLICTS,
    MAX_CONTEXT_HISTORY,
    MAX_CONTEXT_OBSERVATIONS,
    ModalityObservation,
    MultimodalContext,
    MultimodalEntity,
    ObservationConflict,
)


class MultimodalContextStore:
    """Bounded, thread-free in-memory context store managing observations, entities, and conflicts."""

    def __init__(self) -> None:
        self._observations: deque[ModalityObservation] = deque(maxlen=MAX_CONTEXT_OBSERVATIONS)
        self._conflicts: deque[ObservationConflict] = deque(maxlen=MAX_CONTEXT_CONFLICTS)
        self._history: deque[MultimodalContext] = deque(maxlen=MAX_CONTEXT_HISTORY)
        self._vision_features: dict[str, object] = {}
        self._audio_features: dict[str, object] = {}

    @property
    def observation_count(self) -> int:
        return len(self._observations)

    @property
    def conflict_count(self) -> int:
        return len(self._conflicts)

    @property
    def history_count(self) -> int:
        return len(self._history)

    def record_observation(self, observation: ModalityObservation) -> None:
        """Add an observation to the bounded FIFO observation store."""
        self._observations.append(observation)
        if observation.modality.value == "VISION":
            self._vision_features = dict(observation.payload)
        elif observation.modality.value == "AUDIO":
            self._audio_features = dict(observation.payload)

    def record_conflict(self, conflict: ObservationConflict) -> None:
        """Add an observation conflict to the bounded FIFO conflict store."""
        self._conflicts.append(conflict)

    def capture_snapshot(
        self,
        epoch_id: int,
        entities: Sequence[MultimodalEntity],
        active_gestures: Sequence[str] = (),
    ) -> MultimodalContext:
        """Produce an immutable snapshot of the active context and store it in bounded history."""
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        ctx = MultimodalContext(
            epoch_id=epoch_id,
            timestamp_utc=now_utc,
            observations=tuple(self._observations),
            entities=tuple(entities),
            conflicts=tuple(self._conflicts),
            active_gestures=tuple(active_gestures),
            vision_features=MappingProxyType(dict(self._vision_features)),
            audio_features=MappingProxyType(dict(self._audio_features)),
        )
        self._history.append(ctx)
        return ctx

    def clear(self) -> None:
        """Clear all active observations, conflicts, features, and context history."""
        self._observations.clear()
        self._conflicts.clear()
        self._history.clear()
        self._vision_features.clear()
        self._audio_features.clear()
