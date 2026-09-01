# -*- coding: utf-8 -*-
"""Unit tests for Bounded Multimodal Context Store."""

from __future__ import annotations

import uuid
import pytest

from holomed.ultron.context import MultimodalContextStore
from holomed.ultron.models import (
    MAX_CONTEXT_CONFLICTS,
    MAX_CONTEXT_HISTORY,
    MAX_CONTEXT_OBSERVATIONS,
    ConflictSeverity,
    ConflictType,
    EntityType,
    Modality,
    ModalityObservation,
    MultimodalEntity,
    ObservationConflict,
)


def test_context_store_bounded_capacities() -> None:
    """Verify FIFO bounding for observations (256), conflicts (128), and history (64)."""
    store = MultimodalContextStore()

    # Add 300 observations
    for i in range(300):
        obs = ModalityObservation(
            observation_id=str(uuid.uuid4()),
            modality=Modality.VISION,
            source_id="vision.camera",
            physical_id="phys",
            epoch_id=1,
            sequence_number=i + 1,
            timestamp_utc="2026-09-01T12:00:00.000000Z",
            confidence=0.9,
            payload={},
        )
        store.record_observation(obs)

    assert store.observation_count == MAX_CONTEXT_OBSERVATIONS  # 256

    # Add 150 conflicts
    for i in range(150):
        c = ObservationConflict(
            conflict_id=str(uuid.uuid4()),
            entity_id="e_1",
            modalities=(Modality.VISION, Modality.GESTURE),
            conflict_type=ConflictType.SPATIAL_DISCREPANCY,
            severity=ConflictSeverity.LOW,
            selected_source=Modality.GESTURE,
            explanation="test",
        )
        store.record_conflict(c)

    assert store.conflict_count == MAX_CONTEXT_CONFLICTS  # 128

    # Capture 70 snapshots
    dummy_entity = MultimodalEntity(
        entity_id="e_1",
        entity_type=EntityType.HUMAN,
        observations=(),
        confidence=0.9,
        position=None,
        velocity=None,
        active_gestures=(),
        acoustic_direction=None,
        source_modalities=frozenset({Modality.VISION}),
        epoch_id=1,
    )
    for _ in range(70):
        store.capture_snapshot(epoch_id=1, entities=[dummy_entity])

    assert store.history_count == MAX_CONTEXT_HISTORY  # 64


def test_context_store_clear() -> None:
    """Verify clean state clear."""
    store = MultimodalContextStore()
    obs = ModalityObservation(
        observation_id=str(uuid.uuid4()),
        modality=Modality.AUDIO,
        source_id="audio.mic",
        physical_id="phys",
        epoch_id=1,
        sequence_number=1,
        timestamp_utc="2026-09-01T12:00:00.000000Z",
        confidence=0.8,
        payload={},
    )
    store.record_observation(obs)
    assert store.observation_count == 1
    store.clear()
    assert store.observation_count == 0
