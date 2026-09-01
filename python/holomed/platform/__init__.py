# -*- coding: utf-8 -*-
"""HoloMed AI Master Application Supervisor & Platform Integration Foundation (M08)."""

from __future__ import annotations

from holomed.platform.auditor import PlatformConsistencyAuditor
from holomed.platform.cycle import CycleCoordinator
from holomed.platform.events import RecordingPlatformEventSink
from holomed.platform.exceptions import (
    PlatformCapacityError,
    PlatformCycleError,
    PlatformEpochMigrationError,
    PlatformEpochMismatchError,
    PlatformError,
    PlatformLifecycleError,
    PlatformResourceIntegrityError,
    PlatformSecurityError,
    PlatformSequenceError,
    PlatformShutdownError,
    PlatformValidationError,
)
from holomed.platform.health import HealthAggregator
from holomed.platform.models import (
    MAX_ACTIVE_PLATFORM_SESSIONS,
    MAX_CYCLE_HISTORY,
    MAX_CYCLE_PAYLOAD_BYTES,
    MAX_PHASE_EXECUTION_BUDGET_MS,
    MAX_RECORDED_PLATFORM_EVENTS,
    AggregateHealthReport,
    CycleStatus,
    CycleSummary,
    SessionContext,
    SessionStatus,
)
from holomed.platform.serialization import (
    serialize_platform_bytes,
    serialize_platform_payload,
)
from holomed.platform.service import PlatformService
from holomed.platform.session import SessionManager

__all__ = [
    # Exceptions
    "PlatformError",
    "PlatformLifecycleError",
    "PlatformValidationError",
    "PlatformCapacityError",
    "PlatformResourceIntegrityError",
    "PlatformShutdownError",
    "PlatformEpochMismatchError",
    "PlatformSequenceError",
    "PlatformEpochMigrationError",
    "PlatformCycleError",
    "PlatformSecurityError",
    # Constants
    "MAX_ACTIVE_PLATFORM_SESSIONS",
    "MAX_CYCLE_HISTORY",
    "MAX_RECORDED_PLATFORM_EVENTS",
    "MAX_CYCLE_PAYLOAD_BYTES",
    "MAX_PHASE_EXECUTION_BUDGET_MS",
    # Enums
    "CycleStatus",
    "SessionStatus",
    # Models
    "CycleSummary",
    "SessionContext",
    "AggregateHealthReport",
    # Subsystems
    "SessionManager",
    "CycleCoordinator",
    "HealthAggregator",
    "PlatformConsistencyAuditor",
    "RecordingPlatformEventSink",
    "serialize_platform_payload",
    "serialize_platform_bytes",
    "PlatformService",
]
