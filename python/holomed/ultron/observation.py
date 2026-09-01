# -*- coding: utf-8 -*-
"""Multimodal Observation Normalization and Ingestion Pipeline for M04 Ultron."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping, Optional
import uuid

from holomed.ultron.exceptions import (
    UltronEpochMismatchError,
    UltronSequenceError,
    UltronValidationError,
)
from holomed.ultron.models import (
    Modality,
    ModalityObservation,
)
from holomed.ultron.serialization import serialize_ultron_payload


class ObservationNormalizer:
    """Normalizes domain-specific modality outputs into canonical ModalityObservation structures."""

    @staticmethod
    def from_vision(
        result: Any,
        source_id: str,
        physical_id: str,
        epoch_id: int,
        sequence_number: int,
        timestamp_utc: Optional[str] = None,
    ) -> ModalityObservation:
        """Construct a ModalityObservation from a VisionProcessingResult or vision landmark structure."""
        obs_id = str(uuid.uuid4())
        ts = timestamp_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        confidence = float(getattr(result, "confidence", 1.0))
        if hasattr(result, "quality") and hasattr(result.quality, "value"):
            q_val = result.quality.value
            if q_val == "DEGRADED":
                confidence *= 0.8
            elif q_val == "UNUSABLE":
                confidence = 0.0

        payload: dict[str, Any] = {
            "entity_count": len(getattr(result, "tracked_entities", ())),
            "landmark_count": len(getattr(result, "landmarks", ())),
        }
        if hasattr(result, "tracked_entities"):
            entities = []
            for e in result.tracked_entities:
                e_data = {
                    "track_id": str(e.track_id),
                    "confidence": float(e.confidence),
                }
                if hasattr(e, "pose") and e.pose is not None:
                    e_data["position"] = (float(e.pose.x), float(e.pose.y), float(e.pose.z))
                entities.append(e_data)
            payload["entities"] = entities

        return ModalityObservation(
            observation_id=obs_id,
            modality=Modality.VISION,
            source_id=source_id,
            physical_id=physical_id,
            epoch_id=epoch_id,
            sequence_number=sequence_number,
            timestamp_utc=ts,
            confidence=max(0.0, min(1.0, confidence)),
            payload=serialize_ultron_payload(payload),
        )

    @staticmethod
    def from_audio(
        result: Any,
        source_id: str,
        physical_id: str,
        epoch_id: int,
        sequence_number: int,
        timestamp_utc: Optional[str] = None,
    ) -> ModalityObservation:
        """Construct a ModalityObservation from an AudioProcessingResult or acoustic direction."""
        obs_id = str(uuid.uuid4())
        ts = timestamp_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        confidence = 1.0
        doa = getattr(result, "direction_of_arrival", None)
        if doa is not None and hasattr(doa, "confidence"):
            confidence = float(doa.confidence)

        payload: dict[str, Any] = {
            "active_tracks_count": len(getattr(result, "active_tracks", ())),
        }
        if doa is not None:
            payload["acoustic_direction"] = (float(doa.azimuth_deg), float(doa.elevation_deg))
            payload["direction_confidence"] = float(doa.confidence)

        return ModalityObservation(
            observation_id=obs_id,
            modality=Modality.AUDIO,
            source_id=source_id,
            physical_id=physical_id,
            epoch_id=epoch_id,
            sequence_number=sequence_number,
            timestamp_utc=ts,
            confidence=max(0.0, min(1.0, confidence)),
            payload=serialize_ultron_payload(payload),
        )

    @staticmethod
    def from_gesture(
        result: Any,
        source_id: str,
        physical_id: str,
        epoch_id: int,
        sequence_number: int,
        timestamp_utc: Optional[str] = None,
    ) -> ModalityObservation:
        """Construct a ModalityObservation from a GestureProcessingResult or HandObservation."""
        obs_id = str(uuid.uuid4())
        ts = timestamp_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        confidence = 1.0
        active_gestures = []
        g_res = getattr(result, "gesture_result", None)
        if g_res is not None:
            confidence = float(getattr(g_res, "confidence", 1.0))
            if hasattr(g_res, "gesture_type") and hasattr(g_res.gesture_type, "value"):
                active_gestures.append(g_res.gesture_type.value)

        payload: dict[str, Any] = {
            "active_gestures": active_gestures,
            "tracked_hands_count": len(getattr(result, "tracks", ())),
        }
        spatial = getattr(result, "spatial_interaction", None)
        if spatial is not None:
            if spatial.pinch_point is not None:
                payload["position"] = (float(spatial.pinch_point[0]), float(spatial.pinch_point[1]), float(spatial.pinch_point[2]))
            elif spatial.palm_center is not None:
                payload["position"] = (float(spatial.palm_center[0]), float(spatial.palm_center[1]), float(spatial.palm_center[2]))

        return ModalityObservation(
            observation_id=obs_id,
            modality=Modality.GESTURE,
            source_id=source_id,
            physical_id=physical_id,
            epoch_id=epoch_id,
            sequence_number=sequence_number,
            timestamp_utc=ts,
            confidence=max(0.0, min(1.0, confidence)),
            payload=serialize_ultron_payload(payload),
        )


class SessionSequenceTracker:
    """Verifies strict monotonic sequence numbering scoped by (modality, source_id, physical_id, epoch_id)."""

    def __init__(self) -> None:
        self._sequences: dict[tuple[str, str, str, int], int] = {}

    def validate_and_record(self, obs: ModalityObservation, current_epoch: int) -> None:
        """Validate epoch identity and sequence monotonicity. Raises on any violation."""
        if obs.epoch_id != current_epoch:
            raise UltronEpochMismatchError(
                f"Observation epoch {obs.epoch_id} does not match active service epoch {current_epoch}"
            )

        key = (obs.modality.value, obs.source_id, obs.physical_id, obs.epoch_id)
        if key in self._sequences:
            last_seq = self._sequences[key]
            if obs.sequence_number <= last_seq:
                raise UltronSequenceError(
                    f"Non-monotonic sequence number {obs.sequence_number} (last: {last_seq}) for session {key}"
                )
        self._sequences[key] = obs.sequence_number

    def clear(self) -> None:
        """Clear all tracked session sequences."""
        self._sequences.clear()
