# -*- coding: utf-8 -*-
"""HoloMed AI Clinical Session Persistence & Event Journaling Foundation (M09)."""

from __future__ import annotations

from holomed.persistence.audit import DurableAuditStore
from holomed.persistence.events import RecordingPersistenceEventSink
from holomed.persistence.exceptions import (
    PersistenceCapacityError,
    PersistenceCorruptionError,
    PersistenceEpochMismatchError,
    PersistenceError,
    PersistenceLifecycleError,
    PersistenceReplayError,
    PersistenceResourceIntegrityError,
    PersistenceSchemaVersionError,
    PersistenceSecurityError,
    PersistenceSequenceError,
    PersistenceShutdownError,
    PersistenceValidationError,
)
from holomed.persistence.journal import JournalReader, JournalWriter
from holomed.persistence.models import (
    MAX_AUDIT_RECORDS,
    MAX_DURABLE_SESSIONS,
    MAX_JOURNAL_ENTRIES_PER_SESSION,
    MAX_JOURNAL_FILE_BYTES,
    MAX_JOURNAL_RECORD_BYTES,
    MAX_RECORDED_PERSISTENCE_EVENTS,
    MAX_REPLAY_RECORDS,
    PERSISTENCE_SCHEMA_VERSION,
    DurableAuditRecord,
    DurableSessionRecord,
    JournalEntry,
    JournalEntryType,
    PersistenceHealthStatus,
    ReplayStatus,
    ReplayVerificationReport,
)
from holomed.persistence.replay import ReplayEngine
from holomed.persistence.serialization import (
    compute_entry_hash,
    filter_observational_fields,
    serialize_canonical_bytes,
)
from holomed.persistence.service import PersistenceService
from holomed.persistence.sessions import DurableSessionStore

__all__ = [
    # Exceptions
    "PersistenceError",
    "PersistenceLifecycleError",
    "PersistenceValidationError",
    "PersistenceCapacityError",
    "PersistenceResourceIntegrityError",
    "PersistenceShutdownError",
    "PersistenceEpochMismatchError",
    "PersistenceSequenceError",
    "PersistenceSecurityError",
    "PersistenceCorruptionError",
    "PersistenceSchemaVersionError",
    "PersistenceReplayError",
    # Constants
    "PERSISTENCE_SCHEMA_VERSION",
    "MAX_DURABLE_SESSIONS",
    "MAX_JOURNAL_ENTRIES_PER_SESSION",
    "MAX_JOURNAL_RECORD_BYTES",
    "MAX_JOURNAL_FILE_BYTES",
    "MAX_RECORDED_PERSISTENCE_EVENTS",
    "MAX_REPLAY_RECORDS",
    "MAX_AUDIT_RECORDS",
    # Enums
    "JournalEntryType",
    "ReplayStatus",
    "PersistenceHealthStatus",
    # Models
    "DurableSessionRecord",
    "JournalEntry",
    "DurableAuditRecord",
    "ReplayVerificationReport",
    # Components
    "JournalWriter",
    "JournalReader",
    "DurableSessionStore",
    "DurableAuditStore",
    "ReplayEngine",
    "RecordingPersistenceEventSink",
    "PersistenceService",
    # Serialization
    "compute_entry_hash",
    "filter_observational_fields",
    "serialize_canonical_bytes",
]
