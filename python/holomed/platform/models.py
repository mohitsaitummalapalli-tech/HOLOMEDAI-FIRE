# -*- coding: utf-8 -*-
"""M08 Master Application Supervisor Models, Enums, and Hard Bounds."""

from __future__ import annotations

from dataclasses import dataclass
import enum
import math
from types import MappingProxyType
from typing import Any, Mapping, Optional

from holomed.runtime.models import HealthStatus
from holomed.platform.exceptions import PlatformValidationError

# Canonical Hard Bounds
MAX_ACTIVE_PLATFORM_SESSIONS: int = 16
MAX_CYCLE_HISTORY: int = 256
MAX_RECORDED_PLATFORM_EVENTS: int = 1000
MAX_CYCLE_PAYLOAD_BYTES: int = 65536
MAX_PHASE_EXECUTION_BUDGET_MS: float = 50.0


class CycleStatus(str, enum.Enum):
    """Execution outcome status of an end-to-end platform cycle."""

    COMPLETED = "COMPLETED"
    DEGRADED = "DEGRADED"
    ABORTED = "ABORTED"
    FAILED = "FAILED"


class SessionStatus(str, enum.Enum):
    """Lifecycle status of a clinical platform session context."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class CycleSummary:
    """Immutable, bounded summary of an end-to-end execution cycle."""

    cycle_id: str
    epoch_id: int
    session_id: str
    sequence_number: int
    status: CycleStatus
    execution_time_ms: float
    phases_completed: tuple[str, ...]
    fused_entity_count: int
    tool_invocations_count: int
    xr_node_count: int
    confidence: float
    uncertainty_metric: float
    is_simulated: bool = True
    diagnostic_message: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.cycle_id, str) or not self.cycle_id.strip():
            raise PlatformValidationError("cycle_id must be non-empty string")
        if not isinstance(self.epoch_id, int) or self.epoch_id < 0:
            raise PlatformValidationError(f"epoch_id must be non-negative int, got {self.epoch_id}")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise PlatformValidationError("session_id must be non-empty string")
        if not isinstance(self.sequence_number, int) or not (0 <= self.sequence_number <= (2**63 - 1)):
            raise PlatformValidationError(
                f"sequence_number must be in [0, 2^63 - 1], got {self.sequence_number}"
            )
        if not isinstance(self.status, CycleStatus):
            raise PlatformValidationError(f"Invalid CycleStatus: {self.status}")

        for val, name in (
            (self.execution_time_ms, "execution_time_ms"),
            (self.confidence, "confidence"),
            (self.uncertainty_metric, "uncertainty_metric"),
        ):
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise PlatformValidationError(f"{name} must be finite float, got {val!r}")

        if not (0.0 <= self.confidence <= 1.0):
            raise PlatformValidationError(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        if not (0.0 <= self.uncertainty_metric <= 1.0):
            raise PlatformValidationError(
                f"uncertainty_metric must be in [0.0, 1.0], got {self.uncertainty_metric}"
            )

        object.__setattr__(self, "phases_completed", tuple(self.phases_completed))
        object.__setattr__(self, "execution_time_ms", round(float(self.execution_time_ms), 4))
        object.__setattr__(self, "confidence", round(float(self.confidence), 4))
        object.__setattr__(self, "uncertainty_metric", round(float(self.uncertainty_metric), 4))


@dataclass(frozen=True)
class SessionContext:
    """Immutable context descriptor for a clinical application session."""

    session_id: str
    epoch_id: int
    status: SessionStatus
    last_sequence: int
    created_timestamp_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise PlatformValidationError("session_id must be non-empty string")
        if not isinstance(self.epoch_id, int) or self.epoch_id < 0:
            raise PlatformValidationError(f"epoch_id must be non-negative int, got {self.epoch_id}")
        if not isinstance(self.status, SessionStatus):
            raise PlatformValidationError(f"Invalid SessionStatus: {self.status}")
        if not isinstance(self.last_sequence, int) or self.last_sequence < -1:
            raise PlatformValidationError(f"last_sequence must be >= -1, got {self.last_sequence}")


@dataclass(frozen=True)
class AggregateHealthReport:
    """Immutable whole-system aggregate health report."""

    status: HealthStatus
    service_states: Mapping[str, str]
    service_healths: Mapping[str, str]
    epoch_id: int
    is_resource_clean: bool
    diagnostics: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, HealthStatus):
            raise PlatformValidationError(f"Invalid HealthStatus: {self.status}")
        if not isinstance(self.service_states, MappingProxyType):
            object.__setattr__(self, "service_states", MappingProxyType(dict(self.service_states)))
        if not isinstance(self.service_healths, MappingProxyType):
            object.__setattr__(self, "service_healths", MappingProxyType(dict(self.service_healths)))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
