# -*- coding: utf-8 -*-
"""HoloMed AI Ultron Subsystem (M04 — Multimodal Intelligence & Deterministic Reasoning Foundation)."""

from __future__ import annotations

from holomed.ultron.conflicts import ConflictDetector, arbitrate_conflict
from holomed.ultron.context import MultimodalContextStore
from holomed.ultron.events import RecordingUltronEventSink
from holomed.ultron.exceptions import (
    UltronCapacityError,
    UltronEpochMismatchError,
    UltronError,
    UltronIntentConflictError,
    UltronLifecycleError,
    UltronReasoningDepthError,
    UltronReasoningError,
    UltronResourceIntegrityError,
    UltronSequenceError,
    UltronShutdownError,
    UltronValidationError,
)
from holomed.ultron.fusion import (
    MultimodalFusionEngine,
    compute_effective_confidence,
    compute_time_delta_ms,
    parse_utc_timestamp,
)
from holomed.ultron.intents import IntentManager
from holomed.ultron.models import (
    MAX_ACOUSTIC_AZIMUTH_TOLERANCE_DEG,
    MAX_CONTEXT_CONFLICTS,
    MAX_CONTEXT_HISTORY,
    MAX_CONTEXT_OBSERVATIONS,
    MAX_ENTITY_SPATIAL_DISTANCE_METERS,
    MAX_FUSION_TIME_SKEW_MS,
    MAX_INTENT_KEYS,
    MAX_MULTIMODAL_ENTITIES,
    MAX_REASONING_BUDGET_MS,
    MAX_REASONING_DEPTH,
    MAX_REASONING_TRACE_STEPS,
    MAX_RECORDED_ULTRON_EVENTS,
    MAX_ULTRON_PAYLOAD_BYTES,
    ActionIntent,
    ActionType,
    ConflictSeverity,
    ConflictType,
    EntityType,
    ExternalReasoningAdapter,
    ExternalReasoningResult,
    Modality,
    ModalityObservation,
    MultimodalContext,
    MultimodalEntity,
    ObservationConflict,
    ReasoningTrace,
)
from holomed.ultron.observation import ObservationNormalizer, SessionSequenceTracker
from holomed.ultron.reasoning import (
    DEFAULT_REASONING_RULES,
    DeterministicRuleEngine,
    ReasoningRule,
    RuleAction,
    RuleCondition,
)
from holomed.ultron.serialization import (
    canonical_json_parameters,
    serialize_ultron_bytes,
    serialize_ultron_payload,
)
from holomed.ultron.service import UltronService

__all__ = [
    # Exceptions
    "UltronError",
    "UltronLifecycleError",
    "UltronValidationError",
    "UltronCapacityError",
    "UltronEpochMismatchError",
    "UltronSequenceError",
    "UltronReasoningError",
    "UltronReasoningDepthError",
    "UltronIntentConflictError",
    "UltronResourceIntegrityError",
    "UltronShutdownError",
    # Constants
    "MAX_FUSION_TIME_SKEW_MS",
    "MAX_MULTIMODAL_ENTITIES",
    "MAX_CONTEXT_OBSERVATIONS",
    "MAX_CONTEXT_CONFLICTS",
    "MAX_CONTEXT_HISTORY",
    "MAX_REASONING_TRACE_STEPS",
    "MAX_INTENT_KEYS",
    "MAX_RECORDED_ULTRON_EVENTS",
    "MAX_ULTRON_PAYLOAD_BYTES",
    "MAX_REASONING_DEPTH",
    "MAX_REASONING_BUDGET_MS",
    "MAX_ENTITY_SPATIAL_DISTANCE_METERS",
    "MAX_ACOUSTIC_AZIMUTH_TOLERANCE_DEG",
    # Enums
    "Modality",
    "EntityType",
    "ConflictType",
    "ConflictSeverity",
    "ActionType",
    # Data Models
    "ModalityObservation",
    "MultimodalEntity",
    "ObservationConflict",
    "ActionIntent",
    "MultimodalContext",
    "ReasoningTrace",
    "ExternalReasoningResult",
    "ExternalReasoningAdapter",
    # Serialization
    "serialize_ultron_payload",
    "serialize_ultron_bytes",
    "canonical_json_parameters",
    # Components
    "ObservationNormalizer",
    "SessionSequenceTracker",
    "MultimodalFusionEngine",
    "MultimodalContextStore",
    "ConflictDetector",
    "arbitrate_conflict",
    "DeterministicRuleEngine",
    "ReasoningRule",
    "RuleCondition",
    "RuleAction",
    "DEFAULT_REASONING_RULES",
    "IntentManager",
    "RecordingUltronEventSink",
    "UltronService",
    # Fusion helpers
    "parse_utc_timestamp",
    "compute_time_delta_ms",
    "compute_effective_confidence",
]
