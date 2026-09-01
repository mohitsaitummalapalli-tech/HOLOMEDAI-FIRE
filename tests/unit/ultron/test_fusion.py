# -*- coding: utf-8 -*-
"""Unit tests for Multimodal Fusion Engine (D210, D211, D212)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType
import uuid
import pytest

from holomed.ultron.fusion import (
    MultimodalFusionEngine,
    compute_effective_confidence,
    compute_time_delta_ms,
    parse_utc_timestamp,
)
from holomed.ultron.models import (
    MAX_MULTIMODAL_ENTITIES,
    Modality,
    ModalityObservation,
)


def test_d210_timestamp_arithmetic() -> None:
    """Verify D210 strict UTC parsing and millisecond delta calculations."""
    ts1 = "2026-09-01T12:00:00.000000Z"
    ts2 = "2026-09-01T12:00:00.050000Z"
    ts3 = "2026-09-01T12:00:00.150000Z"

    dt1 = parse_utc_timestamp(ts1)
    dt2 = parse_utc_timestamp(ts2)
    dt3 = parse_utc_timestamp(ts3)

    delta_1_2 = compute_time_delta_ms(dt1, dt2)
    assert abs(delta_1_2 - 50.0) < 1e-6

    delta_1_3 = compute_time_delta_ms(dt1, dt3)
    assert abs(delta_1_3 - 150.0) < 1e-6
    assert delta_1_3 > 100.0  # Outside 100ms window


def test_d211_confidence_formulas() -> None:
    """Verify D211 closed-form confidence weighting formulas."""
    # 1. Delta = 0, no conflict: temporal_weight = 1.0, consistency = 1.0 -> 0.90
    conf1 = compute_effective_confidence(source_confidence=0.90, delta_ms=0.0, has_conflict=False)
    assert conf1 == 0.90

    # 2. Delta = 50ms, no conflict: temporal_weight = 0.5, consistency = 1.0 -> 0.90 * 0.5 = 0.45
    conf2 = compute_effective_confidence(source_confidence=0.90, delta_ms=50.0, has_conflict=False)
    assert conf2 == 0.45

    # 3. Delta = 0, with conflict: temporal = 1.0, consistency = 0.5 -> 0.90 * 0.5 = 0.45
    conf3 = compute_effective_confidence(source_confidence=0.90, delta_ms=0.0, has_conflict=True)
    assert conf3 == 0.45

    # 4. Delta = 100ms: temporal = 0.0 -> 0.0
    conf4 = compute_effective_confidence(source_confidence=0.90, delta_ms=100.0, has_conflict=False)
    assert conf4 == 0.0


def test_d212_entity_association_explicit_and_spatial() -> None:
    """Verify D212 association priority."""
    engine = MultimodalFusionEngine()

    ts = "2026-09-01T12:00:00.000000Z"
    obs_vision = ModalityObservation(
        observation_id=str(uuid.uuid4()),
        modality=Modality.VISION,
        source_id="vision.camera",
        physical_id="cam_0",
        epoch_id=1,
        sequence_number=1,
        timestamp_utc=ts,
        confidence=0.95,
        payload={"position": (0.0, 0.0, 1.0)},
    )
    # Gesture close spatially (dist = 0.10m < 0.50m)
    obs_gesture = ModalityObservation(
        observation_id=str(uuid.uuid4()),
        modality=Modality.GESTURE,
        source_id="gesture.tracker",
        physical_id="cam_0",
        epoch_id=1,
        sequence_number=1,
        timestamp_utc=ts,
        confidence=0.90,
        payload={"position": (0.05, 0.05, 1.0), "active_gestures": ["PINCH"]},
    )

    entities, _ = engine.fuse_observations([obs_vision, obs_gesture], current_epoch=1)
    # Should fuse into single entity
    assert len(entities) == 1
    assert entities[0].source_modalities == {Modality.VISION, Modality.GESTURE}
    assert "PINCH" in entities[0].active_gestures


def test_d212_thirty_two_entity_capacity_limit() -> None:
    """Verify MAX_MULTIMODAL_ENTITIES = 32 capacity cap with non-mutating drop."""
    engine = MultimodalFusionEngine()
    ts = "2026-09-01T12:00:00.000000Z"

    # Create 32 spatially separated observations
    obs_list = []
    for i in range(MAX_MULTIMODAL_ENTITIES):
        obs = ModalityObservation(
            observation_id=str(uuid.uuid4()),
            modality=Modality.VISION,
            source_id="vision.camera",
            physical_id="cam_0",
            epoch_id=1,
            sequence_number=i + 1,
            timestamp_utc=ts,
            confidence=0.90,
            payload={"position": (float(i) * 1.0, 0.0, 1.0)},  # 1.0m apart > 0.50m
        )
        obs_list.append(obs)

    entities, _ = engine.fuse_observations(obs_list, current_epoch=1)
    assert len(entities) == 32

    # 33rd observation arrives: non-matching
    extra_obs = ModalityObservation(
        observation_id=str(uuid.uuid4()),
        modality=Modality.VISION,
        source_id="vision.camera",
        physical_id="cam_0",
        epoch_id=1,
        sequence_number=100,
        timestamp_utc=ts,
        confidence=0.90,
        payload={"position": (100.0, 100.0, 1.0)},
    )
    entities, _ = engine.fuse_observations([extra_obs], current_epoch=1)
    assert len(entities) == 32  # Still 32, excess dropped non-mutating
