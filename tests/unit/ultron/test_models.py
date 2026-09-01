# -*- coding: utf-8 -*-
"""Unit tests for M04 Ultron Models and Enums."""

from __future__ import annotations

import math
from types import MappingProxyType
import uuid
import pytest

from holomed.ultron.exceptions import UltronValidationError
from holomed.ultron.models import (
    ActionIntent,
    ActionType,
    ConflictSeverity,
    ConflictType,
    EntityType,
    ExternalReasoningResult,
    Modality,
    ModalityObservation,
    MultimodalContext,
    MultimodalEntity,
    ObservationConflict,
    ReasoningTrace,
)


def test_modality_enum_priorities() -> None:
    """Verify modality priority order (VISION=0, AUDIO=1, GESTURE=2)."""
    assert Modality.VISION.priority == 0
    assert Modality.AUDIO.priority == 1
    assert Modality.GESTURE.priority == 2


def test_conflict_type_and_severity_enums_completeness() -> None:
    """Verify D213 conflict enums."""
    assert ConflictType.SPATIAL_DISCREPANCY.value == "SPATIAL_DISCREPANCY"
    assert ConflictType.SEMANTIC_MISMATCH.value == "SEMANTIC_MISMATCH"
    assert ConflictType.TEMPORAL_OUTLIER.value == "TEMPORAL_OUTLIER"
    assert ConflictType.GESTURE_CONTRADICTION.value == "GESTURE_CONTRADICTION"
    assert ConflictType.MODALITY_DISAGREEMENT.value == "MODALITY_DISAGREEMENT"

    assert ConflictSeverity.LOW.value == "LOW"
    assert ConflictSeverity.MEDIUM.value == "MEDIUM"
    assert ConflictSeverity.HIGH.value == "HIGH"
    assert ConflictSeverity.CRITICAL.value == "CRITICAL"


def test_modality_observation_valid_construction() -> None:
    """Verify standard ModalityObservation creation."""
    obs = ModalityObservation(
        observation_id=str(uuid.uuid4()),
        modality=Modality.VISION,
        source_id="vision.camera",
        physical_id="usb://port1",
        epoch_id=1,
        sequence_number=10,
        timestamp_utc="2026-09-01T12:00:00.000000Z",
        confidence=0.95,
        payload={"key": "val"},
    )
    assert obs.modality == Modality.VISION
    assert obs.confidence == 0.95
    assert isinstance(obs.payload, MappingProxyType)


def test_modality_observation_validation_rejections() -> None:
    """Verify rejection of invalid fields."""
    valid_id = str(uuid.uuid4())
    ts = "2026-09-01T12:00:00.000000Z"

    # Bad UUID
    with pytest.raises(UltronValidationError):
        ModalityObservation(
            observation_id="invalid-uuid",
            modality=Modality.VISION,
            source_id="vision.camera",
            physical_id="phys",
            epoch_id=1,
            sequence_number=1,
            timestamp_utc=ts,
            confidence=0.9,
            payload={},
        )

    # Bad confidence (< 0 or > 1 or NaN)
    with pytest.raises(UltronValidationError):
        ModalityObservation(
            observation_id=valid_id,
            modality=Modality.VISION,
            source_id="vision.camera",
            physical_id="phys",
            epoch_id=1,
            sequence_number=1,
            timestamp_utc=ts,
            confidence=1.5,
            payload={},
        )

    with pytest.raises(UltronValidationError):
        ModalityObservation(
            observation_id=valid_id,
            modality=Modality.VISION,
            source_id="vision.camera",
            physical_id="phys",
            epoch_id=1,
            sequence_number=1,
            timestamp_utc=ts,
            confidence=float("nan"),
            payload={},
        )

    # Bad timestamp
    with pytest.raises(UltronValidationError):
        ModalityObservation(
            observation_id=valid_id,
            modality=Modality.VISION,
            source_id="vision.camera",
            physical_id="phys",
            epoch_id=1,
            sequence_number=1,
            timestamp_utc="not-a-timestamp",
            confidence=0.9,
            payload={},
        )


def test_multimodal_entity_immutability() -> None:
    """Verify MultimodalEntity converts and freezes inputs."""
    entity = MultimodalEntity(
        entity_id="entity_0001",
        entity_type=EntityType.HUMAN,
        observations=("obs_1", "obs_2"),
        confidence=0.88888,
        position=(1.0, 2.0, 3.0),
        velocity=(0.1, 0.0, 0.0),
        active_gestures=("PINCH",),
        acoustic_direction=(10.0, 5.0),
        source_modalities={Modality.VISION, Modality.GESTURE},
        epoch_id=1,
    )
    assert entity.confidence == 0.8889  # 4-decimal round
    assert isinstance(entity.source_modalities, frozenset)
    assert isinstance(entity.observations, tuple)
    assert entity.position == (1.0, 2.0, 3.0)


def test_external_reasoning_result_model() -> None:
    """Verify D217 ExternalReasoningResult contract."""
    res_id = str(uuid.uuid4())
    intent_id = str(uuid.uuid4())
    intent = ActionIntent(
        intent_id=intent_id,
        action_type=ActionType.SELECT,
        confidence=0.95,
        entity_id="entity_1",
        parameters={"target": "mesh"},
        explanation="Test select",
        epoch_id=1,
        timestamp_utc="2026-09-01T12:00:00.000000Z",
    )
    ext_res = ExternalReasoningResult(
        result_id=res_id,
        intents=(intent,),
        explanation="External AI reasoning explanation",
        confidence=0.92,
        metadata={"model": "mock_adapter"},
    )
    assert ext_res.result_id == res_id
    assert len(ext_res.intents) == 1
    assert isinstance(ext_res.metadata, MappingProxyType)
