# -*- coding: utf-8 -*-
"""Unit tests for Conflict Detection and Arbitration (D216)."""

from __future__ import annotations

import uuid
import pytest

from holomed.ultron.conflicts import ConflictDetector, arbitrate_conflict
from holomed.ultron.models import (
    ConflictSeverity,
    ConflictType,
    Modality,
    ModalityObservation,
)


def test_d216_arbitration_hierarchy() -> None:
    """Verify D216 5-stage arbitration hierarchy."""
    ts_early = "2026-09-01T12:00:00.000000Z"
    ts_late = "2026-09-01T12:00:00.050000Z"

    id_v1 = str(uuid.uuid4())
    id_g1 = str(uuid.uuid4())

    # Case 1: Confidence difference > 0.05 -> higher confidence wins
    obs_vision = ModalityObservation(
        observation_id=id_v1,
        modality=Modality.VISION,
        source_id="vision.camera",
        physical_id="phys",
        epoch_id=1,
        sequence_number=1,
        timestamp_utc=ts_early,
        confidence=0.90,
        payload={},
    )
    obs_gesture = ModalityObservation(
        observation_id=id_g1,
        modality=Modality.GESTURE,
        source_id="gesture.tracker",
        physical_id="phys",
        epoch_id=1,
        sequence_number=1,
        timestamp_utc=ts_early,
        confidence=0.80,
        payload={},
    )
    # Difference = 0.10 > 0.05 -> VISION wins (0.90 vs 0.80)
    assert arbitrate_conflict(obs_vision, 0.90, obs_gesture, 0.80) == Modality.VISION

    # Case 2: Confidence difference <= 0.05 -> GESTURE > VISION > AUDIO
    # Vision 0.82 vs Gesture 0.80 (diff = 0.02 <= 0.05) -> GESTURE wins
    assert arbitrate_conflict(obs_vision, 0.82, obs_gesture, 0.80) == Modality.GESTURE

    # Audio 0.82 vs Vision 0.80 (diff = 0.02 <= 0.05) -> VISION > AUDIO
    obs_audio = ModalityObservation(
        observation_id=str(uuid.uuid4()),
        modality=Modality.AUDIO,
        source_id="audio.mic",
        physical_id="phys",
        epoch_id=1,
        sequence_number=1,
        timestamp_utc=ts_early,
        confidence=0.82,
        payload={},
    )
    assert arbitrate_conflict(obs_audio, 0.82, obs_vision, 0.80) == Modality.VISION

    # Case 3: Same modality, equal confidence -> timestamp DESC
    obs_v_late = ModalityObservation(
        observation_id=str(uuid.uuid4()),
        modality=Modality.VISION,
        source_id="vision.camera",
        physical_id="phys",
        epoch_id=1,
        sequence_number=2,
        timestamp_utc=ts_late,
        confidence=0.90,
        payload={},
    )
    # obs_v_late is newer than obs_vision
    assert arbitrate_conflict(obs_vision, 0.90, obs_v_late, 0.90) == Modality.VISION


def test_conflict_detector_spatial_discrepancy() -> None:
    """Verify spatial discrepancy detection exceeding 0.50m."""
    ts = "2026-09-01T12:00:00.000000Z"
    id_v = str(uuid.uuid4())
    id_g = str(uuid.uuid4())
    obs_v = ModalityObservation(
        observation_id=id_v,
        modality=Modality.VISION,
        source_id="vision.camera",
        physical_id="phys",
        epoch_id=1,
        sequence_number=1,
        timestamp_utc=ts,
        confidence=0.90,
        payload={"position": (0.0, 0.0, 1.0)},
    )
    obs_g = ModalityObservation(
        observation_id=id_g,
        modality=Modality.GESTURE,
        source_id="gesture.tracker",
        physical_id="phys",
        epoch_id=1,
        sequence_number=1,
        timestamp_utc=ts,
        confidence=0.88,
        payload={"position": (1.5, 0.0, 1.0)},  # 1.5m away > 0.50m
    )

    conflicts = ConflictDetector.detect_conflicts([obs_v, obs_g], {id_v: 0.90, id_g: 0.88}, "e_1")
    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == ConflictType.SPATIAL_DISCREPANCY
    assert conflicts[0].severity == ConflictSeverity.CRITICAL  # > 1.0m
    assert conflicts[0].selected_source == Modality.GESTURE  # diff 0.02 <= 0.05, so GESTURE wins
