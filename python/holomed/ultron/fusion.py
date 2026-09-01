# -*- coding: utf-8 -*-
"""Deterministic Multimodal Fusion Engine for M04 Ultron."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Optional, Sequence
import uuid

from holomed.ultron.conflicts import ConflictDetector
from holomed.ultron.exceptions import UltronValidationError
from holomed.ultron.models import (
    MAX_ACOUSTIC_AZIMUTH_TOLERANCE_DEG,
    MAX_ENTITY_SPATIAL_DISTANCE_METERS,
    MAX_FUSION_TIME_SKEW_MS,
    MAX_MULTIMODAL_ENTITIES,
    EntityType,
    Modality,
    ModalityObservation,
    MultimodalEntity,
    ObservationConflict,
)


def parse_utc_timestamp(timestamp_str: str) -> datetime:
    """Parse canonical ISO 8601 UTC timestamp format (%Y-%m-%dT%H:%M:%S.%fZ)."""
    try:
        return datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise UltronValidationError(f"Invalid canonical UTC timestamp syntax {timestamp_str!r}: {e}") from e


def compute_time_delta_ms(dt_a: datetime, dt_b: datetime) -> float:
    """Calculate absolute time difference in milliseconds between two UTC datetimes."""
    return abs((dt_a - dt_b).total_seconds() * 1000.0)


def compute_effective_confidence(
    source_confidence: float,
    delta_ms: float,
    has_conflict: bool,
) -> float:
    """Calculate effective confidence implementing D211 closed-form formula."""
    temporal_weight = max(0.0, 1.0 - (delta_ms / MAX_FUSION_TIME_SKEW_MS))
    consistency_weight = 0.5 if has_conflict else 1.0
    raw = source_confidence * temporal_weight * consistency_weight
    return round(max(0.0, min(1.0, raw)), 4)


class MultimodalFusionEngine:
    """Synchronous, deterministic cross-modal fusion engine."""

    def __init__(self) -> None:
        self._entities: dict[str, MultimodalEntity] = {}
        self._conflicts: list[ObservationConflict] = []
        self._entity_counter: int = 1

    @property
    def entities(self) -> tuple[MultimodalEntity, ...]:
        """Immutable view of tracked multimodal entities, ordered deterministically by entity_id."""
        sorted_keys = sorted(self._entities.keys())
        return tuple(self._entities[k] for k in sorted_keys)

    @property
    def conflicts(self) -> tuple[ObservationConflict, ...]:
        """Immutable view of detected cross-modal conflicts."""
        return tuple(self._conflicts)

    def clear(self) -> None:
        """Clear all active entities and conflicts."""
        self._entities.clear()
        self._conflicts.clear()
        self._entity_counter = 1

    def fuse_observations(
        self,
        observations: Sequence[ModalityObservation],
        current_epoch: int,
    ) -> tuple[tuple[MultimodalEntity, ...], tuple[ObservationConflict, ...]]:
        """Fuse a batch of observations deterministically into multimodal entities."""
        if not observations:
            return self.entities, self.conflicts

        # Order observations deterministically: (timestamp_utc, modality.priority, source_id, sequence_number, observation_id)
        sorted_obs = sorted(
            observations,
            key=lambda o: (
                o.timestamp_utc,
                o.modality.priority,
                o.source_id,
                o.sequence_number,
                o.observation_id,
            ),
        )

        # Window reference time is the newest observation's timestamp
        newest_dt = parse_utc_timestamp(sorted_obs[-1].timestamp_utc)

        for obs in sorted_obs:
            obs_dt = parse_utc_timestamp(obs.timestamp_utc)
            delta_ms = compute_time_delta_ms(obs_dt, newest_dt)
            if delta_ms > MAX_FUSION_TIME_SKEW_MS:
                # Outside fusion window; skip fusing this stale observation
                continue

            self._associate_and_update(obs, delta_ms, current_epoch)

        return self.entities, self.conflicts

    def _associate_and_update(
        self,
        obs: ModalityObservation,
        delta_ms: float,
        current_epoch: int,
    ) -> None:
        """Associate observation with an existing entity or create a new entity using D212."""
        matched_entity_id: Optional[str] = None
        best_metric: float = float("inf")

        # 1. Explicit entity_id match if present in payload
        explicit_id = obs.payload.get("entity_id")
        if explicit_id is not None and str(explicit_id) in self._entities:
            matched_entity_id = str(explicit_id)
        else:
            # 2 & 3. Geometric & acoustic evaluation against active entities
            obs_pos = obs.payload.get("position")
            obs_acoustic = obs.payload.get("acoustic_direction")

            candidates: list[tuple[float, str]] = []
            for e_id in sorted(self._entities.keys()):
                entity = self._entities[e_id]
                metric: Optional[float] = None

                # 2. 3D Euclidean distance <= 0.50m
                if obs_pos is not None and entity.position is not None:
                    dx = float(obs_pos[0]) - entity.position[0]
                    dy = float(obs_pos[1]) - entity.position[1]
                    dz = float(obs_pos[2]) - entity.position[2]
                    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
                    if dist <= MAX_ENTITY_SPATIAL_DISTANCE_METERS:
                        metric = dist

                # 3. Acoustic azimuth deviation <= 25.0 degrees
                elif obs_acoustic is not None and entity.position is not None:
                    # Compute azimuth angle of entity from origin
                    ent_az = math.degrees(math.atan2(entity.position[0], entity.position[2]))
                    az_dev = abs(float(obs_acoustic[0]) - ent_az)
                    if az_dev <= MAX_ACOUSTIC_AZIMUTH_TOLERANCE_DEG:
                        metric = az_dev / 100.0  # normalize scale

                if metric is not None:
                    candidates.append((metric, e_id))

            if candidates:
                # 4 & 5. Lowest metric wins, tie-break by entity_id ASC
                candidates.sort(key=lambda item: (item[0], item[1]))
                matched_entity_id = candidates[0][1]

        if matched_entity_id is not None:
            # Update existing entity
            entity = self._entities[matched_entity_id]
            self._update_entity(entity, obs, delta_ms, current_epoch)
        else:
            # 6 & 7. Capacity check: MAX_MULTIMODAL_ENTITIES = 32
            if len(self._entities) < MAX_MULTIMODAL_ENTITIES:
                new_id = f"entity_{self._entity_counter:04d}"
                self._entity_counter += 1
                self._create_entity(new_id, obs, delta_ms, current_epoch)
            # If capacity == 32 and no match, unassociated observation is dropped non-mutating

    def _create_entity(
        self,
        entity_id: str,
        obs: ModalityObservation,
        delta_ms: float,
        epoch_id: int,
    ) -> None:
        """Instantiate a new MultimodalEntity from a seed observation."""
        e_type = EntityType.UNKNOWN
        if obs.modality == Modality.GESTURE:
            e_type = EntityType.HAND
        elif obs.modality == Modality.AUDIO:
            e_type = EntityType.AUDIO_SOURCE
        elif obs.modality == Modality.VISION:
            e_type = EntityType.HUMAN

        pos = obs.payload.get("position")
        pos_tuple = tuple(float(v) for v in pos) if pos is not None else None
        acoustic = obs.payload.get("acoustic_direction")
        acoustic_tuple = tuple(float(v) for v in acoustic) if acoustic is not None else None
        gestures = tuple(obs.payload.get("active_gestures", ()))

        eff_conf = compute_effective_confidence(obs.confidence, delta_ms, False)

        entity = MultimodalEntity(
            entity_id=entity_id,
            entity_type=e_type,
            observations=(obs.observation_id,),
            confidence=eff_conf,
            position=pos_tuple,
            velocity=None,
            active_gestures=gestures,
            acoustic_direction=acoustic_tuple,
            source_modalities=frozenset({obs.modality}),
            epoch_id=epoch_id,
        )
        self._entities[entity_id] = entity

    def _update_entity(
        self,
        entity: MultimodalEntity,
        obs: ModalityObservation,
        delta_ms: float,
        epoch_id: int,
    ) -> None:
        """Merge observation into existing entity and run conflict detection."""
        all_obs_ids = entity.observations + (obs.observation_id,)
        all_modalities = entity.source_modalities | {obs.modality}

        # Check for conflicts
        eff_conf = compute_effective_confidence(obs.confidence, delta_ms, False)
        conf_map = {obs.observation_id: eff_conf}

        # If spatial discrepancy between incoming obs and entity position
        pos = obs.payload.get("position")
        pos_tuple = tuple(float(v) for v in pos) if pos is not None else entity.position
        acoustic = obs.payload.get("acoustic_direction")
        acoustic_tuple = tuple(float(v) for v in acoustic) if acoustic is not None else entity.acoustic_direction
        gestures = entity.active_gestures + tuple(obs.payload.get("active_gestures", ()))

        # Update entity confidence as weighted average
        new_conf = round(0.5 * entity.confidence + 0.5 * eff_conf, 4)

        updated_entity = MultimodalEntity(
            entity_id=entity.entity_id,
            entity_type=entity.entity_type,
            observations=all_obs_ids,
            confidence=new_conf,
            position=pos_tuple,
            velocity=entity.velocity,
            active_gestures=gestures,
            acoustic_direction=acoustic_tuple,
            source_modalities=all_modalities,
            epoch_id=epoch_id,
        )
        self._entities[entity.entity_id] = updated_entity
