# -*- coding: utf-8 -*-
"""Unit tests for Multimodal Observation Normalization and Sequence Tracking."""

from __future__ import annotations

from types import SimpleNamespace
import uuid
import pytest

from holomed.ultron.exceptions import (
    UltronEpochMismatchError,
    UltronSequenceError,
)
from holomed.ultron.models import Modality
from holomed.ultron.observation import ObservationNormalizer, SessionSequenceTracker


def test_observation_normalizer_vision() -> None:
    """Verify normalizing mock Vision result."""
    mock_res = SimpleNamespace(
        confidence=0.9,
        tracked_entities=[SimpleNamespace(track_id="track_1", confidence=0.95, pose=SimpleNamespace(x=0.1, y=0.2, z=1.0))],
        landmarks=[],
    )
    obs = ObservationNormalizer.from_vision(
        result=mock_res,
        source_id="vision.camera",
        physical_id="cam_phys_0",
        epoch_id=1,
        sequence_number=1,
    )
    assert obs.modality == Modality.VISION
    assert obs.confidence == 0.9
    assert obs.payload["entity_count"] == 1


def test_observation_normalizer_audio() -> None:
    """Verify normalizing mock Audio result."""
    mock_res = SimpleNamespace(
        direction_of_arrival=SimpleNamespace(azimuth_deg=15.0, elevation_deg=0.0, confidence=0.85),
        active_tracks=[],
    )
    obs = ObservationNormalizer.from_audio(
        result=mock_res,
        source_id="audio.microphone",
        physical_id="mic_phys_0",
        epoch_id=1,
        sequence_number=1,
    )
    assert obs.modality == Modality.AUDIO
    assert list(obs.payload["acoustic_direction"]) == [15.0, 0.0]


def test_observation_normalizer_gesture() -> None:
    """Verify normalizing mock Gesture result."""
    mock_res = SimpleNamespace(
        gesture_result=SimpleNamespace(gesture_type=SimpleNamespace(value="PINCH"), confidence=0.92),
        tracks=[],
        spatial_interaction=SimpleNamespace(pinch_point=(0.0, 0.1, 0.9), palm_center=None),
    )
    obs = ObservationNormalizer.from_gesture(
        result=mock_res,
        source_id="gesture.tracker",
        physical_id="cam_phys_0",
        epoch_id=1,
        sequence_number=1,
    )
    assert obs.modality == Modality.GESTURE
    assert obs.payload["active_gestures"] == ["PINCH"]
    assert list(obs.payload["position"]) == [0.0, 0.1, 0.9]


def test_session_sequence_monotonicity_and_rejection(sample_vision_observation) -> None:
    """Verify strictly increasing sequence numbers per session."""
    tracker = SessionSequenceTracker()
    tracker.validate_and_record(sample_vision_observation, current_epoch=1)

    # Replay same sequence number
    with pytest.raises(UltronSequenceError):
        tracker.validate_and_record(sample_vision_observation, current_epoch=1)

    # Decreasing sequence number
    lower_obs = SimpleNamespace(
        modality=sample_vision_observation.modality,
        source_id=sample_vision_observation.source_id,
        physical_id=sample_vision_observation.physical_id,
        epoch_id=sample_vision_observation.epoch_id,
        sequence_number=0,
    )
    with pytest.raises(UltronSequenceError):
        tracker.validate_and_record(lower_obs, current_epoch=1)

    # Epoch mismatch
    with pytest.raises(UltronEpochMismatchError):
        tracker.validate_and_record(sample_vision_observation, current_epoch=2)
