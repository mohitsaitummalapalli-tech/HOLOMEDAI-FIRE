# -*- coding: utf-8 -*-
"""Cross-Modal Conflict Detection and Arbitration for M04 Ultron."""

from __future__ import annotations

import math
from typing import Optional, Sequence
import uuid

from holomed.ultron.models import (
    ConflictSeverity,
    ConflictType,
    Modality,
    ModalityObservation,
    ObservationConflict,
)


def compute_arbitration_priority(
    obs: ModalityObservation,
    effective_confidence: float,
) -> tuple[int, int, str, int, str]:
    """Sort key for conflict arbitration according to D216.
    
    Higher score should win:
    - If confidence difference > 0.05, higher effective confidence wins.
    - If confidence difference <= 0.05, GESTURE > VISION > AUDIO.
    - If same modality: timestamp DESC.
    - If same timestamp: sequence_number DESC.
    - Final tie-break: observation_id ASC.
    """
    # Modality priority: GESTURE (2) > VISION (1) > AUDIO (0)
    mod_score = 2 if obs.modality == Modality.GESTURE else (1 if obs.modality == Modality.VISION else 0)
    return (
        int(round(effective_confidence * 100)),  # bucketed/quantized
        mod_score,
        obs.timestamp_utc,
        obs.sequence_number,
        obs.observation_id,
    )


def arbitrate_conflict(
    obs_a: ModalityObservation,
    conf_a: float,
    obs_b: ModalityObservation,
    conf_b: float,
) -> Modality:
    """Arbitrate between two conflicting modality observations according to D216."""
    diff = abs(conf_a - conf_b)
    if diff > 0.05:
        return obs_a.modality if conf_a > conf_b else obs_b.modality

    # Near tie (<= 0.05): GESTURE > VISION > AUDIO
    mod_order = {Modality.GESTURE: 2, Modality.VISION: 1, Modality.AUDIO: 0}
    if mod_order[obs_a.modality] != mod_order[obs_b.modality]:
        return obs_a.modality if mod_order[obs_a.modality] > mod_order[obs_b.modality] else obs_b.modality

    # Same modality: timestamp DESC
    if obs_a.timestamp_utc != obs_b.timestamp_utc:
        return obs_a.modality if obs_a.timestamp_utc > obs_b.timestamp_utc else obs_b.modality

    # Sequence DESC
    if obs_a.sequence_number != obs_b.sequence_number:
        return obs_a.modality if obs_a.sequence_number > obs_b.sequence_number else obs_b.modality

    # Final tie-break: observation_id ASC
    return obs_a.modality if obs_a.observation_id < obs_b.observation_id else obs_b.modality


class ConflictDetector:
    """Detects and arbitrates cross-modal discrepancies across active observations."""

    @staticmethod
    def detect_conflicts(
        observations: Sequence[ModalityObservation],
        effective_confidences: dict[str, float],
        entity_id: str,
    ) -> list[ObservationConflict]:
        """Detect pairwise discrepancies and return typed ObservationConflict records."""
        conflicts: list[ObservationConflict] = []
        if len(observations) < 2:
            return conflicts

        for i in range(len(observations)):
            for j in range(i + 1, len(observations)):
                obs_a = observations[i]
                obs_b = observations[j]
                if obs_a.modality == obs_b.modality:
                    continue

                pos_a = obs_a.payload.get("position")
                pos_b = obs_b.payload.get("position")
                if pos_a is not None and pos_b is not None:
                    # Spatial discrepancy check
                    dx = float(pos_a[0]) - float(pos_b[0])
                    dy = float(pos_a[1]) - float(pos_b[1])
                    dz = float(pos_a[2]) - float(pos_b[2])
                    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
                    if dist > 0.50:  # MAX_ENTITY_SPATIAL_DISTANCE_METERS
                        conf_a = effective_confidences.get(obs_a.observation_id, obs_a.confidence)
                        conf_b = effective_confidences.get(obs_b.observation_id, obs_b.confidence)
                        winner = arbitrate_conflict(obs_a, conf_a, obs_b, conf_b)
                        severity = ConflictSeverity.CRITICAL if dist > 1.0 else ConflictSeverity.HIGH
                        c_id = str(uuid.uuid4())
                        conflicts.append(
                            ObservationConflict(
                                conflict_id=c_id,
                                entity_id=entity_id,
                                modalities=(obs_a.modality, obs_b.modality),
                                conflict_type=ConflictType.SPATIAL_DISCREPANCY,
                                severity=severity,
                                selected_source=winner,
                                explanation=(
                                    f"Spatial distance ({dist:.3f}m) exceeds 0.50m threshold between "
                                    f"{obs_a.modality.value} and {obs_b.modality.value}. "
                                    f"Arbitrated to {winner.value}."
                                ),
                            )
                        )

        return conflicts
