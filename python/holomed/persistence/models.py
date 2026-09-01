# -*- coding: utf-8 -*-
"""Immutable Data Models, Hard Bounds, and Enums for M09 Persistence Subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
import enum
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, Optional

from holomed.persistence.exceptions import PersistenceValidationError
from holomed.platform.models import SessionStatus

# Hard Architectural Bounds
PERSISTENCE_SCHEMA_VERSION: str = "1.0"
MAX_DURABLE_SESSIONS: int = 16
MAX_JOURNAL_ENTRIES_PER_SESSION: int = 10000
MAX_JOURNAL_RECORD_BYTES: int = 65536
MAX_JOURNAL_FILE_BYTES: int = 671088640  # 640 MiB
MAX_RECORDED_PERSISTENCE_EVENTS: int = 1000
MAX_REPLAY_RECORDS: int = 10000
MAX_AUDIT_RECORDS: int = 1000

SESSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
HASH_REGEX = re.compile(r"^[0-9a-f]{64}$")


class JournalEntryType(str, enum.Enum):
    """Semantic category of append-only journal entries."""

    SESSION_STARTED = "SESSION_STARTED"
    CYCLE_COMPLETED = "CYCLE_COMPLETED"
    CYCLE_DEGRADED = "CYCLE_DEGRADED"
    AUDIT_SNAPSHOT = "AUDIT_SNAPSHOT"
    EPOCH_MIGRATED = "EPOCH_MIGRATED"
    SESSION_CLOSED = "SESSION_CLOSED"


class ReplayStatus(str, enum.Enum):
    """Execution status of deterministic verification replay."""

    VERIFIED = "VERIFIED"
    DIVERGENCE_DETECTED = "DIVERGENCE_DETECTED"
    CORRUPTED = "CORRUPTED"
    EMPTY = "EMPTY"


class PersistenceHealthStatus(str, enum.Enum):
    """Health classification of the persistence subsystem."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    FAILED = "FAILED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True)
class DurableSessionRecord:
    """Immutable persistent metadata representing a clinical session."""

    session_id: str
    epoch_id: int
    status: SessionStatus
    created_timestamp_utc: str
    closed_timestamp_utc: Optional[str] = None
    total_cycles: int = 0
    last_sequence: int = -1
    schema_version: str = PERSISTENCE_SCHEMA_VERSION
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise PersistenceValidationError(f"Invalid session_id syntax: {self.session_id!r}")
        if self.epoch_id < 0:
            raise PersistenceValidationError(f"epoch_id must be non-negative, got {self.epoch_id}")
        if self.total_cycles < 0:
            raise PersistenceValidationError(f"total_cycles must be non-negative, got {self.total_cycles}")
        if self.last_sequence < -1:
            raise PersistenceValidationError(f"last_sequence cannot be < -1, got {self.last_sequence}")
        if self.schema_version != PERSISTENCE_SCHEMA_VERSION:
            raise PersistenceValidationError(f"Unsupported schema version: {self.schema_version!r}")
        if not isinstance(self.metadata, (dict, MappingProxyType)):
            raise PersistenceValidationError("metadata must be a mapping")
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class JournalEntry:
    """Immutable cryptographically chained append-only journal record."""

    entry_id: str
    entry_type: JournalEntryType
    schema_version: str
    timestamp_utc: str
    epoch_id: int
    session_id: str
    sequence_number: int
    payload: Mapping[str, Any]
    sha256_hash: str
    previous_entry_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.entry_id, str) or not self.entry_id.strip():
            raise PersistenceValidationError("entry_id must be a non-empty string")
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise PersistenceValidationError(f"Invalid session_id syntax: {self.session_id!r}")
        if self.epoch_id < 0:
            raise PersistenceValidationError(f"epoch_id must be non-negative, got {self.epoch_id}")
        if self.sequence_number < -1:
            raise PersistenceValidationError(f"sequence_number cannot be < -1, got {self.sequence_number}")
        if not HASH_REGEX.match(self.sha256_hash):
            raise PersistenceValidationError(f"Invalid sha256_hash format: {self.sha256_hash!r}")
        if not HASH_REGEX.match(self.previous_entry_hash):
            raise PersistenceValidationError(f"Invalid previous_entry_hash format: {self.previous_entry_hash!r}")
        if not isinstance(self.payload, (dict, MappingProxyType)):
            raise PersistenceValidationError("payload must be a mapping")
        if not isinstance(self.payload, MappingProxyType):
            object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class DurableAuditRecord:
    """Immutable durable consistency audit record."""

    audit_id: str
    epoch_id: int
    timestamp_utc: str
    audit_passed: bool
    required_services_count: int
    present_services_count: int
    active_sessions_count: int
    cycle_history_count: int
    findings: tuple[str, ...]
    schema_version: str = PERSISTENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.audit_id, str) or not self.audit_id.strip():
            raise PersistenceValidationError("audit_id must be non-empty")
        if self.epoch_id < 0:
            raise PersistenceValidationError("epoch_id must be non-negative")
        if not isinstance(self.findings, tuple):
            object.__setattr__(self, "findings", tuple(self.findings))


@dataclass(frozen=True)
class ReplayVerificationReport:
    """Immutable report evaluating replay integrity against historical journal records."""

    session_id: str
    total_entries_verified: int
    initial_sequence: int
    final_sequence: int
    hash_chain_valid: bool
    state_divergences: tuple[str, ...]
    replay_status: ReplayStatus

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not SESSION_ID_REGEX.match(self.session_id):
            raise PersistenceValidationError(f"Invalid session_id syntax: {self.session_id!r}")
        if self.total_entries_verified < 0:
            raise PersistenceValidationError("total_entries_verified must be non-negative")
        if not isinstance(self.state_divergences, tuple):
            object.__setattr__(self, "state_divergences", tuple(self.state_divergences))
