# -*- coding: utf-8 -*-
"""M04 Ultron Core Models, Constants, and Protocols."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import enum
import math
from types import MappingProxyType
from typing import Any, Mapping, Optional, Protocol, Tuple

from holomed.protocol.validation import (
    validate_identifier,
    validate_timestamp_utc,
    validate_uuid_v4,
)
from holomed.ultron.exceptions import UltronValidationError

# Canonical Constants
MAX_FUSION_TIME_SKEW_MS: float = 100.0
MAX_MULTIMODAL_ENTITIES: int = 32
MAX_CONTEXT_OBSERVATIONS: int = 256
MAX_CONTEXT_CONFLICTS: int = 128
MAX_CONTEXT_HISTORY: int = 64
MAX_REASONING_TRACE_STEPS: int = 128
MAX_INTENT_KEYS: int = 2048
MAX_RECORDED_ULTRON_EVENTS: int = 1000
MAX_ULTRON_PAYLOAD_BYTES: int = 65536
MAX_REASONING_DEPTH: int = 8
MAX_REASONING_BUDGET_MS: float = 15.0
MAX_ENTITY_SPATIAL_DISTANCE_METERS: float = 0.50
MAX_ACOUSTIC_AZIMUTH_TOLERANCE_DEG: float = 25.0


class Modality(str, enum.Enum):
    """Canonical modality categories."""

    VISION = "VISION"
    AUDIO = "AUDIO"
    GESTURE = "GESTURE"

    @property
    def priority(self) -> int:
        """Modality priority order for deterministic tie-breaking (VISION=0, AUDIO=1, GESTURE=2)."""
        if self == Modality.VISION:
            return 0
        if self == Modality.AUDIO:
            return 1
        return 2


class EntityType(str, enum.Enum):
    """Canonical multimodal entity classification."""

    HUMAN = "HUMAN"
    HAND = "HAND"
    FACE = "FACE"
    OBJECT = "OBJECT"
    TOOL = "TOOL"
    AUDIO_SOURCE = "AUDIO_SOURCE"
    ANATOMICAL_REGION = "ANATOMICAL_REGION"
    UNKNOWN = "UNKNOWN"


class ConflictType(str, enum.Enum):
    """Types of cross-modal discrepancies."""

    SPATIAL_DISCREPANCY = "SPATIAL_DISCREPANCY"
    SEMANTIC_MISMATCH = "SEMANTIC_MISMATCH"
    TEMPORAL_OUTLIER = "TEMPORAL_OUTLIER"
    GESTURE_CONTRADICTION = "GESTURE_CONTRADICTION"
    MODALITY_DISAGREEMENT = "MODALITY_DISAGREEMENT"


class ConflictSeverity(str, enum.Enum):
    """Severity ratings for cross-modal discrepancies."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ActionType(str, enum.Enum):
    """High-level action intents synthesized by Ultron reasoning."""

    NO_OP = "NO_OP"
    HIGHLIGHT = "HIGHLIGHT"
    SELECT = "SELECT"
    FOCUS = "FOCUS"
    MOVE = "MOVE"
    ROTATE = "ROTATE"
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    QUERY = "QUERY"
    SPEAK = "SPEAK"
    REQUEST_CONFIRMATION = "REQUEST_CONFIRMATION"


@dataclass(frozen=True)
class ModalityObservation:
    """Canonical normalized multimodal observation ingested by Ultron."""

    observation_id: str
    modality: Modality
    source_id: str
    physical_id: str
    epoch_id: int
    sequence_number: int
    timestamp_utc: str
    confidence: float
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        try:
            validate_uuid_v4(self.observation_id, "observation_id")
        except Exception as e:
            raise UltronValidationError(f"Invalid observation_id UUIDv4: {e}") from e

        if not isinstance(self.modality, Modality):
            raise UltronValidationError(f"Invalid modality: {self.modality!r}")

        try:
            validate_identifier(self.source_id, "source_id")
        except Exception as e:
            raise UltronValidationError(f"Invalid source_id identifier: {e}") from e

        if not isinstance(self.physical_id, str) or not self.physical_id.strip():
            raise UltronValidationError("physical_id must be a non-empty string")

        if not isinstance(self.epoch_id, int) or self.epoch_id < 0:
            raise UltronValidationError("epoch_id must be a non-negative integer")

        if not isinstance(self.sequence_number, int) or not (0 <= self.sequence_number <= 9223372036854775807):
            raise UltronValidationError(f"sequence_number out of range: {self.sequence_number}")

        try:
            validate_timestamp_utc(self.timestamp_utc)
            # Ensure exact M00.2 ISO 8601 UTC representation
            datetime.strptime(self.timestamp_utc, "%Y-%m-%dT%H:%M:%S.%fZ")
        except Exception as e:
            raise UltronValidationError(f"Invalid timestamp_utc: {e}") from e

        if not isinstance(self.confidence, (int, float)) or not math.isfinite(self.confidence):
            raise UltronValidationError("confidence must be a finite float")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise UltronValidationError(f"confidence must be in [0.0, 1.0], got {self.confidence}")

        if not isinstance(self.payload, MappingProxyType):
            object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class MultimodalEntity:
    """Multimodal tracked entity state synthesized from multi-source observations."""

    entity_id: str
    entity_type: EntityType
    observations: tuple[str, ...]
    confidence: float
    position: Optional[tuple[float, float, float]]
    velocity: Optional[tuple[float, float, float]]
    active_gestures: tuple[str, ...]
    acoustic_direction: Optional[tuple[float, float]]
    source_modalities: frozenset[Modality]
    epoch_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.entity_id, str) or not self.entity_id:
            raise UltronValidationError("entity_id must be a non-empty string")
        if not isinstance(self.entity_type, EntityType):
            raise UltronValidationError(f"Invalid entity_type: {self.entity_type}")
        if not isinstance(self.confidence, (int, float)) or not math.isfinite(self.confidence):
            raise UltronValidationError("confidence must be finite")
        object.__setattr__(self, "confidence", round(max(0.0, min(1.0, float(self.confidence))), 4))
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "active_gestures", tuple(self.active_gestures))
        object.__setattr__(self, "source_modalities", frozenset(self.source_modalities))
        if self.position is not None:
            if len(self.position) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in self.position):
                raise UltronValidationError("position must be a tuple of 3 finite floats")
            object.__setattr__(self, "position", tuple(float(v) for v in self.position))
        if self.velocity is not None:
            if len(self.velocity) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in self.velocity):
                raise UltronValidationError("velocity must be a tuple of 3 finite floats")
            object.__setattr__(self, "velocity", tuple(float(v) for v in self.velocity))
        if self.acoustic_direction is not None:
            if len(self.acoustic_direction) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in self.acoustic_direction):
                raise UltronValidationError("acoustic_direction must be a tuple of 2 finite floats")
            object.__setattr__(self, "acoustic_direction", tuple(float(v) for v in self.acoustic_direction))


@dataclass(frozen=True)
class ObservationConflict:
    """Audit record capturing a detected discrepancy between modality observations."""

    conflict_id: str
    entity_id: str
    modalities: tuple[Modality, ...]
    conflict_type: ConflictType
    severity: ConflictSeverity
    selected_source: Optional[Modality]
    explanation: str

    def __post_init__(self) -> None:
        if not isinstance(self.conflict_id, str) or not self.conflict_id:
            raise UltronValidationError("conflict_id must be non-empty")
        if not isinstance(self.entity_id, str) or not self.entity_id:
            raise UltronValidationError("entity_id must be non-empty")
        if not isinstance(self.conflict_type, ConflictType):
            raise UltronValidationError(f"Invalid conflict_type: {self.conflict_type}")
        if not isinstance(self.severity, ConflictSeverity):
            raise UltronValidationError(f"Invalid severity: {self.severity}")
        object.__setattr__(self, "modalities", tuple(self.modalities))


@dataclass(frozen=True)
class ActionIntent:
    """Immutable, non-executing intent produced by Ultron reasoning rules."""

    intent_id: str
    action_type: ActionType
    confidence: float
    entity_id: Optional[str]
    parameters: Mapping[str, Any]
    explanation: str
    epoch_id: int
    timestamp_utc: str

    def __post_init__(self) -> None:
        try:
            validate_uuid_v4(self.intent_id, "intent_id")
        except Exception as e:
            raise UltronValidationError(f"Invalid intent_id UUIDv4: {e}") from e

        if not isinstance(self.action_type, ActionType):
            raise UltronValidationError(f"Invalid action_type: {self.action_type}")
        if not isinstance(self.confidence, (int, float)) or not math.isfinite(self.confidence):
            raise UltronValidationError("confidence must be finite")
        object.__setattr__(self, "confidence", round(max(0.0, min(1.0, float(self.confidence))), 4))
        try:
            validate_timestamp_utc(self.timestamp_utc)
        except Exception as e:
            raise UltronValidationError(f"Invalid timestamp_utc: {e}") from e
        if not isinstance(self.parameters, MappingProxyType):
            object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


@dataclass(frozen=True)
class MultimodalContext:
    """Immutable snapshot of the active multimodal world state."""

    epoch_id: int
    timestamp_utc: str
    observations: tuple[ModalityObservation, ...]
    entities: tuple[MultimodalEntity, ...]
    conflicts: tuple[ObservationConflict, ...]
    active_gestures: tuple[str, ...]
    vision_features: Mapping[str, Any]
    audio_features: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "observations", tuple(self.observations))
        object.__setattr__(self, "entities", tuple(self.entities))
        object.__setattr__(self, "conflicts", tuple(self.conflicts))
        object.__setattr__(self, "active_gestures", tuple(self.active_gestures))
        if not isinstance(self.vision_features, MappingProxyType):
            object.__setattr__(self, "vision_features", MappingProxyType(dict(self.vision_features)))
        if not isinstance(self.audio_features, MappingProxyType):
            object.__setattr__(self, "audio_features", MappingProxyType(dict(self.audio_features)))


@dataclass(frozen=True)
class ReasoningTrace:
    """Auditable trace of rule executions, conflicts, and intents for a reasoning cycle."""

    trace_id: str
    epoch_id: int
    input_observation_ids: tuple[str, ...]
    fired_rule_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...]
    intent_ids: tuple[str, ...]
    processing_time_ms: float
    degraded: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_observation_ids", tuple(self.input_observation_ids))
        object.__setattr__(self, "fired_rule_ids", tuple(self.fired_rule_ids))
        object.__setattr__(self, "conflict_ids", tuple(self.conflict_ids))
        object.__setattr__(self, "intent_ids", tuple(self.intent_ids))


@dataclass(frozen=True)
class ExternalReasoningResult:
    """Structured response contract for external AI / LLM adapters."""

    result_id: str
    intents: tuple[ActionIntent, ...]
    explanation: str
    confidence: float
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        try:
            validate_uuid_v4(self.result_id, "result_id")
        except Exception as e:
            raise UltronValidationError(f"Invalid result_id UUIDv4: {e}") from e
        object.__setattr__(self, "intents", tuple(self.intents))
        if not isinstance(self.confidence, (int, float)) or not math.isfinite(self.confidence):
            raise UltronValidationError("confidence must be finite")
        object.__setattr__(self, "confidence", round(max(0.0, min(1.0, float(self.confidence))), 4))
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


class ExternalReasoningAdapter(Protocol):
    """Protocol boundary for future external AI reasoning engines."""

    def reason(self, context: MultimodalContext) -> ExternalReasoningResult:
        """Perform reasoning over context and return structured external reasoning result."""
        ...
