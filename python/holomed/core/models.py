"""M00.4 Core data models — enums, frozen dataclasses, descriptors, limits.

All public collections are exposed as tuple, MappingProxyType, or immutable
defensive copies.  Every dataclass uses ``@dataclass(frozen=True)``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from holomed.protocol.models import MessageEnvelope

# ---------------------------------------------------------------------------
# Capacity / limit constants
# ---------------------------------------------------------------------------

MAX_TOPIC_LENGTH: int = 128
MAX_SEGMENT_LENGTH: int = 64
MAX_PAYLOAD_BYTES: int = 1_048_576  # 1 MiB
MAX_RECURSION_DEPTH: int = 16
MAX_CORRELATION_LISTENERS: int = 1024
MAX_PIPELINE_STAGES: int = 64
DEFAULT_FUTURE_TOLERANCE_SECONDS: float = 5.0
DEFAULT_MAX_AGE_SECONDS: float = 300.0
DEFAULT_DLQ_CAPACITY: int = 1000


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DeadLetterReason(enum.Enum):
    """Authoritative dead-letter reasons."""

    NO_HANDLER = "NO_HANDLER"
    INVALID_HANDLER_RESPONSE = "INVALID_HANDLER_RESPONSE"
    HANDLER_EXCEPTION = "HANDLER_EXCEPTION"
    CORRUPTED_ENVELOPE = "CORRUPTED_ENVELOPE"
    FUTURE_TIMESTAMP_EXCEEDED = "FUTURE_TIMESTAMP_EXCEEDED"
    MESSAGE_EXPIRED = "MESSAGE_EXPIRED"
    RECURSION_DEPTH_EXCEEDED = "RECURSION_DEPTH_EXCEEDED"
    CYCLE_DETECTED = "CYCLE_DETECTED"
    CORRELATION_TIMEOUT = "CORRELATION_TIMEOUT"
    SUBSCRIBER_EXCEPTION = "SUBSCRIBER_EXCEPTION"


class DeadLetterOverflowPolicy(enum.Enum):
    """Overflow behaviour when the dead-letter queue reaches capacity."""

    DROP_OLDEST = "DROP_OLDEST"
    REJECT_NEW = "REJECT_NEW"


class DispatcherState(enum.Enum):
    """Lifecycle states for the MessageDispatcher."""

    UNINITIALIZED = "UNINITIALIZED"
    INITIALIZED = "INITIALIZED"
    STARTED = "STARTED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class PipelineStageStatus(enum.Enum):
    """Execution status for an individual pipeline stage."""

    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


# ---------------------------------------------------------------------------
# Handler / callback type aliases
# ---------------------------------------------------------------------------

MessageHandler = Callable[[MessageEnvelope], MessageEnvelope]
EventHandler = Callable[[MessageEnvelope], None]
CorrelationCallback = Callable[[MessageEnvelope], None]
StageExecutor = Callable[[Mapping[str, Any]], Mapping[str, Any]]
StageRollback = Callable[[Mapping[str, Any]], None]


# ---------------------------------------------------------------------------
# Subscription descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandRegistration:
    """Immutable command handler registration descriptor."""

    topic: str
    handler: MessageHandler
    service_name: str
    registration_index: int


@dataclass(frozen=True)
class QueryRegistration:
    """Immutable query handler registration descriptor."""

    topic: str
    handler: MessageHandler
    service_name: str
    registration_index: int


@dataclass(frozen=True)
class EventSubscription:
    """Immutable event subscription descriptor."""

    pattern: str
    handler: EventHandler
    service_name: str
    registration_index: int


# ---------------------------------------------------------------------------
# Correlation listener
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorrelationListener:
    """Immutable correlation listener descriptor."""

    correlation_id: str
    callback: CorrelationCallback
    deadline_utc: datetime
    registered_utc: datetime


# ---------------------------------------------------------------------------
# Dead-letter record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeadLetterRecord:
    """Immutable dead-letter queue entry."""

    envelope: Optional[MessageEnvelope]
    reason: DeadLetterReason
    diagnostic: str
    timestamp_utc: str


# ---------------------------------------------------------------------------
# Dispatch result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DispatchResult:
    """Immutable result of a single dispatch operation."""

    delivered: bool
    dead_lettered: bool
    results: tuple[MessageEnvelope, ...] = ()


# ---------------------------------------------------------------------------
# Pipeline descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineStageDescriptor:
    """Immutable pipeline stage descriptor."""

    name: str
    execute: StageExecutor
    rollback: Optional[StageRollback] = None
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineStageResult:
    """Immutable result record for a single pipeline stage execution."""

    stage_name: str
    stage_index: int
    status: PipelineStageStatus
    output: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.output, MappingProxyType):
            object.__setattr__(self, "output", MappingProxyType(dict(self.output)))


@dataclass(frozen=True)
class PipelineResult:
    """Immutable aggregate result of a pipeline execution."""

    stages: tuple[PipelineStageResult, ...]
    success: bool
