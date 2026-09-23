# -*- coding: utf-8 -*-
"""Bounded Durable Session Store and State Manager for M09."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import glob
import os
from types import MappingProxyType
from typing import Any, Mapping, Optional

from holomed.persistence.exceptions import (
    PersistenceCapacityError,
    PersistenceEpochMismatchError,
    PersistenceLifecycleError,
    PersistenceSequenceError,
    PersistenceValidationError,
    PersistenceTerminationConflictError,
    PersistenceIdentityReuseError,
)
from holomed.persistence.authority import ControllerAuthorityStore
from holomed.persistence.journal import JournalReader, JournalWriter
from holomed.persistence.models import (
    MAX_DURABLE_SESSIONS,
    PERSISTENCE_SCHEMA_VERSION,
    DurableSessionRecord,
    JournalEntryType,
)
from holomed.platform.models import CycleSummary, SessionStatus


class DurableSessionStore:
    """Manages full lifecycle and durable persistence of holomed sessions.
    
    Provides bounded capacity, epoch-based validation, and crash-safe journal appending.
    """

    def __init__(self, storage_root: Path, epoch_id: int = 0) -> None:
        self._storage_root = Path(storage_root)
        self._epoch_id = epoch_id
        
        # session_id -> DurableSessionRecord
        self._sessions: dict[str, DurableSessionRecord] = {}
        # session_id -> JournalWriter
        self._writers: dict[str, JournalWriter] = {}
        
        self._authority = ControllerAuthorityStore(self._storage_root)
        self._global_lock_path = self._storage_root / ".physical_admission.lock"

        if not self._storage_root.exists():
            self._storage_root.mkdir(parents=True)

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    @property
    def active_session_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.status == SessionStatus.ACTIVE)

    def get_session(self, session_id: str) -> DurableSessionRecord:
        """Retrieve registered session record or raise PersistenceValidationError."""
        if session_id not in self._sessions:
            raise PersistenceValidationError(f"Session {session_id!r} not found")
        return self._sessions[session_id]

    def has_session(self, session_id: str) -> bool:
        return session_id in self._sessions

    def list_sessions(self) -> tuple[DurableSessionRecord, ...]:
        """Return sorted snapshot of all registered session records."""
        sorted_keys = sorted(self._sessions.keys())
        return tuple(self._sessions[k] for k in sorted_keys)

    def start_session(
        self,
        session_id: str,
        epoch_id: int,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> DurableSessionRecord:
        """Initialize or retrieve an active session record."""
        if epoch_id != self._epoch_id:
            raise PersistenceEpochMismatchError(
                f"Session epoch {epoch_id} does not match active storage epoch {self._epoch_id}"
            )

        if session_id in self._sessions:
            rec = self._sessions[session_id]
            if rec.status == SessionStatus.ACTIVE:
                return rec

        if len(self._sessions) >= MAX_DURABLE_SESSIONS:
            raise PersistenceCapacityError(
                f"Durable session capacity exceeded ({MAX_DURABLE_SESSIONS} max)"
            )

        writer = JournalWriter(
            self._storage_root,
            session_id,
            epoch_id,
            authoritative_epoch_provider=lambda: self._epoch_id,
        )
        writer.initialize_storage()

        now_utc = datetime.now(timezone.utc).isoformat()
        rec = DurableSessionRecord(
            session_id=session_id,
            epoch_id=epoch_id,
            status=SessionStatus.ACTIVE,
            created_timestamp_utc=now_utc,
            closed_timestamp_utc=None,
            total_cycles=0,
            last_sequence=-1,
            schema_version=PERSISTENCE_SCHEMA_VERSION,
            metadata=MappingProxyType(dict(metadata or {})),
        )

        # Append SESSION_STARTED to journal
        writer.append_entry(
            entry_type=JournalEntryType.SESSION_STARTED,
            sequence_number=-1,
            timestamp_utc=now_utc,
            payload={"action": "SESSION_STARTED", "session_id": session_id},
        )

        self._sessions[session_id] = rec
        self._writers[session_id] = writer
        return rec

    def record_cycle(
        self,
        session_id: str,
        sequence_number: int,
        summary: CycleSummary,
    ) -> DurableSessionRecord:
        """Record a completed or degraded cycle summary into the session journal."""
        if session_id not in self._sessions:
            raise PersistenceValidationError(f"Session {session_id!r} not registered")

        rec = self._sessions[session_id]
        if rec.status != SessionStatus.ACTIVE:
            raise PersistenceLifecycleError(f"Cannot record cycle for session {session_id!r} in state {rec.status.name}")

        if sequence_number <= rec.last_sequence:
            raise PersistenceSequenceError(
                f"Sequence {sequence_number} must strictly exceed last sequence {rec.last_sequence}"
            )

        writer = self._writers[session_id]
        now_utc = datetime.now(timezone.utc).isoformat()
        entry_type = (
            JournalEntryType.CYCLE_COMPLETED
            if summary.status.value == "COMPLETED"
            else JournalEntryType.CYCLE_DEGRADED
        )

        payload = {
            "cycle_id": summary.cycle_id,
            "sequence_number": sequence_number,
            "status": summary.status.value,
            "execution_time_ms": summary.execution_time_ms,
            "phases_completed": list(summary.phases_completed),
            "fused_entity_count": summary.fused_entity_count,
            "tool_invocations_count": summary.tool_invocations_count,
            "xr_node_count": summary.xr_node_count,
            "is_simulated": summary.is_simulated,
            "confidence": summary.confidence,
            "uncertainty_metric": summary.uncertainty_metric,
            "diagnostic_message": summary.diagnostic_message,
        }

        writer.append_entry(
            entry_type=entry_type,
            sequence_number=sequence_number,
            timestamp_utc=now_utc,
            payload=payload,
            entry_id=summary.cycle_id,
        )

        updated = DurableSessionRecord(
            session_id=rec.session_id,
            epoch_id=rec.epoch_id,
            status=rec.status,
            created_timestamp_utc=rec.created_timestamp_utc,
            closed_timestamp_utc=rec.closed_timestamp_utc,
            total_cycles=rec.total_cycles + 1,
            last_sequence=sequence_number,
            schema_version=rec.schema_version,
            metadata=rec.metadata,
        )
        self._sessions[session_id] = updated
        return updated

    def _acquire_global_lock(self):
        """Acquire the global physical admission lock."""
        if not self._storage_root.exists():
            self._storage_root.mkdir(parents=True)
        fd = os.open(str(self._global_lock_path), os.O_RDWR | os.O_CREAT)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _release_global_lock(self, fd):
        """Release the global physical admission lock."""
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _reconstruct_reservations_locked(self) -> tuple[dict[tuple, dict], dict[tuple, str]]:
        """Reconstruct active reservations and terminated identities from durable journals."""
        active_reservations = {}
        terminated_identities = {}
        for jpath in glob.glob(str(self._storage_root / "*.jsonl")):
            session_id = os.path.basename(jpath).replace(".jsonl", "")
            entries, _ = JournalReader.read_and_recover_journal(Path(jpath))
            for entry in entries:
                if entry.entry_type == JournalEntryType.OPERATION_ADMITTED:
                    payload = entry.payload
                    canon = (
                        payload.get("device_id"),
                        payload.get("device_epoch"),
                        payload.get("controller_epoch"),
                        payload.get("physical_operation_id"),
                        payload.get("command_nonce")
                    )
                    active_reservations[canon] = dict(payload)
                elif entry.entry_type == JournalEntryType.OPERATION_TERMINATED:
                    payload = entry.payload
                    canon = (
                        payload.get("device_id"),
                        payload.get("device_epoch"),
                        payload.get("controller_epoch"),
                        payload.get("physical_operation_id"),
                        payload.get("command_nonce")
                    )
                    if canon in active_reservations:
                        del active_reservations[canon]
                    terminated_identities[canon] = payload.get("resolution")
        return active_reservations, terminated_identities

    def get_active_physical_operations(self) -> int:
        """Returns the total number of physical operations that still require capacity.
        Note: This is an un-locked best-effort count.
        """
        fd = self._acquire_global_lock()
        try:
            active, _ = self._reconstruct_reservations_locked()
            return len(active)
        finally:
            self._release_global_lock(fd)

    def record_operation_admitted(
        self,
        session_id: str,
        endpoint_id: str,
        device_id: str,
        device_epoch: int,
        controller_epoch: int,
        physical_operation_id: str,
        command_nonce: str,
        execution_id: str,
        command_name: str
    ) -> None:
        """Atomically record physical operation admission in the durable journal with global lock."""
        if session_id not in self._sessions:
            raise PersistenceValidationError(f"Session {session_id!r} not found")
            
        rec = self._sessions[session_id]
        if rec.status != SessionStatus.ACTIVE:
            raise PersistenceLifecycleError(f"Cannot record admission for inactive session {session_id!r}")
            
        if controller_epoch != self._epoch_id:
            raise PersistenceEpochMismatchError(
                f"Canonical physical identity controller epoch {controller_epoch} does not match authoritative epoch {self._epoch_id}"
            )
            
        canonical_identity = (device_id, device_epoch, controller_epoch, physical_operation_id, command_nonce)
        from holomed.devices.control.models import GLOBAL_PHYSICAL_OPERATION_CAPACITY, ENDPOINT_LOCAL_PHYSICAL_CAPACITY
        
        with self._authority.hold_authority(self._epoch_id):
            fd = self._acquire_global_lock()
            try:
                active, terminated = self._reconstruct_reservations_locked()
                
                if canonical_identity in terminated:
                    raise PersistenceIdentityReuseError(
                        f"Canonical identity {canonical_identity} has already been terminated. Reuse is forbidden."
                    )
                    
                if canonical_identity in active:
                    return  # Idempotent admission (already admitted globally)
                    
                # Verify endpoint capacity
                endpoint_count = sum(1 for ops in active.values() if ops.get("endpoint_id") == endpoint_id)
                if endpoint_count >= ENDPOINT_LOCAL_PHYSICAL_CAPACITY:
                    raise PersistenceCapacityError(
                        f"Endpoint {endpoint_id} already has {endpoint_count} active reservations, max is {ENDPOINT_LOCAL_PHYSICAL_CAPACITY}."
                    )
                
                # Verify global capacity
                if len(active) >= GLOBAL_PHYSICAL_OPERATION_CAPACITY:
                    raise PersistenceCapacityError(
                        f"Global physical capacity exceeded ({GLOBAL_PHYSICAL_OPERATION_CAPACITY} max active operations)"
                    )
                    
                # Write to session journal (the tail will be reconstructed properly within JournalWriter's append_entry)
                now_utc = datetime.now(timezone.utc).isoformat()
                writer = self._writers[session_id]
                
                payload = {
                    "endpoint_id": endpoint_id,
                    "device_id": device_id,
                    "device_epoch": device_epoch,
                    "controller_epoch": controller_epoch,
                    "physical_operation_id": physical_operation_id,
                    "command_nonce": command_nonce,
                    "execution_id": execution_id,
                    "command_name": command_name
                }
                
                entry = writer.append_entry(
                    entry_type=JournalEntryType.OPERATION_ADMITTED,
                    sequence_number=None, # Will be ignored and reconstructed by JournalWriter
                    timestamp_utc=now_utc,
                    payload=payload,
                    entry_id=physical_operation_id,
                )
                
                updated = DurableSessionRecord(
                    session_id=rec.session_id,
                    epoch_id=rec.epoch_id,
                    status=rec.status,
                    created_timestamp_utc=rec.created_timestamp_utc,
                    closed_timestamp_utc=rec.closed_timestamp_utc,
                    total_cycles=rec.total_cycles,
                    last_sequence=entry.sequence_number,
                    schema_version=rec.schema_version,
                    metadata=rec.metadata,
                )
                self._sessions[session_id] = updated
            finally:
                self._release_global_lock(fd)

    def record_operation_terminated(
        self,
        session_id: str,
        device_id: str,
        device_epoch: int,
        controller_epoch: int,
        physical_operation_id: str,
        command_nonce: str,
        resolution: str
    ) -> None:
        """Atomically record physical operation termination in the durable journal with global lock."""
        invalid_resolutions = {
            "QUARANTINED", "RECOVERY_REQUIRED", "UNKNOWN", "ISOLATION_PREPARE", "DISPATCHING", "RUNNING"
        }
        if resolution in invalid_resolutions:
            raise PersistenceValidationError(f"Cannot terminate operation with non-terminal resolution {resolution!r}")

        if session_id not in self._sessions:
            raise PersistenceValidationError(f"Session {session_id!r} not found")
            
        rec = self._sessions[session_id]
        if rec.status != SessionStatus.ACTIVE:
            raise PersistenceLifecycleError(f"Cannot record termination for inactive session {session_id!r}")
            
        if controller_epoch != self._epoch_id:
            
            raise PersistenceEpochMismatchError(
            
                f'Canonical physical identity controller epoch {controller_epoch} does not match authoritative epoch {self._epoch_id}'
            
            )

            
        canonical_identity = (device_id, device_epoch, controller_epoch, physical_operation_id, command_nonce)
        
        with self._authority.hold_authority(self._epoch_id):
            fd = self._acquire_global_lock()
            try:
                active, terminated = self._reconstruct_reservations_locked()
                
                if canonical_identity in terminated:
                    if terminated[canonical_identity] != resolution:
                        raise PersistenceLifecycleError(
                            f"Conflict: Operation {canonical_identity} already terminated with {terminated[canonical_identity]}, "
                            f"cannot terminate again with {resolution}"
                        )
                    return  # Idempotent termination (already terminated globally)
                    
                if canonical_identity not in active:
                    raise PersistenceTerminationConflictError(
                        f"Cannot terminate canonical identity {canonical_identity} that is not currently admitted."
                    )
                    
                now_utc = datetime.now(timezone.utc).isoformat()
                writer = self._writers[session_id]
                
                payload = {
                    "device_id": device_id,
                    "device_epoch": device_epoch,
                    "controller_epoch": controller_epoch,
                    "physical_operation_id": physical_operation_id,
                    "command_nonce": command_nonce,
                    "resolution": resolution
                }
                
                entry = writer.append_entry(
                    entry_type=JournalEntryType.OPERATION_TERMINATED,
                    sequence_number=None, # Will be ignored and reconstructed by JournalWriter
                    timestamp_utc=now_utc,
                    payload=payload,
                    entry_id=physical_operation_id,
                )
                
                updated = DurableSessionRecord(
                    session_id=rec.session_id,
                    epoch_id=rec.epoch_id,
                    status=rec.status,
                    created_timestamp_utc=rec.created_timestamp_utc,
                    closed_timestamp_utc=rec.closed_timestamp_utc,
                    total_cycles=rec.total_cycles,
                    last_sequence=entry.sequence_number,
                    schema_version=rec.schema_version,
                    metadata=rec.metadata,
                )
                self._sessions[session_id] = updated
            finally:
                self._release_global_lock(fd)

    def close_session(self, session_id: str) -> DurableSessionRecord:
        """Mark a session as STOPPED idempotently."""
        if session_id not in self._sessions:
            raise PersistenceValidationError(f"Session {session_id!r} not found")

        rec = self._sessions[session_id]
        if rec.status == SessionStatus.STOPPED:
            return rec

        now_utc = datetime.now(timezone.utc).isoformat()
        writer = self._writers.get(session_id)
        if writer is not None:
            writer.append_entry(
                entry_type=JournalEntryType.SESSION_CLOSED,
                sequence_number=rec.last_sequence,
                timestamp_utc=now_utc,
                payload={"action": "SESSION_CLOSED", "session_id": session_id},
            )

        stopped = DurableSessionRecord(
            session_id=rec.session_id,
            epoch_id=rec.epoch_id,
            status=SessionStatus.STOPPED,
            created_timestamp_utc=rec.created_timestamp_utc,
            closed_timestamp_utc=now_utc,
            total_cycles=rec.total_cycles,
            last_sequence=rec.last_sequence,
            schema_version=rec.schema_version,
            metadata=rec.metadata,
        )
        self._sessions[session_id] = stopped
        return stopped

    def restore_session_from_disk(self, session_id: str) -> DurableSessionRecord:
        """Recover and reconstruct session metadata from an on-disk journal file."""
        if session_id in self._sessions:
            return self._sessions[session_id]

        if len(self._sessions) >= MAX_DURABLE_SESSIONS:
            raise PersistenceCapacityError(f"Durable session limit reached ({MAX_DURABLE_SESSIONS})")

        journal_path = self._storage_root / f"{session_id}.jsonl"
        entries, _ = JournalReader.read_and_recover_journal(journal_path)

        if not entries:
            raise PersistenceValidationError(f"No journal records found for session {session_id!r}")

        first_entry = entries[0]
        last_cycle_seq = -1
        cycle_count = 0
        status = SessionStatus.ACTIVE
        closed_ts: Optional[str] = None
        
        for e in entries:
            if e.entry_type in (JournalEntryType.CYCLE_COMPLETED, JournalEntryType.CYCLE_DEGRADED, JournalEntryType.OPERATION_ADMITTED, JournalEntryType.OPERATION_TERMINATED):
                if e.entry_type in (JournalEntryType.CYCLE_COMPLETED, JournalEntryType.CYCLE_DEGRADED):
                    cycle_count += 1
                if e.sequence_number > last_cycle_seq:
                    last_cycle_seq = e.sequence_number
            elif e.entry_type == JournalEntryType.SESSION_CLOSED:
                status = SessionStatus.STOPPED
                closed_ts = e.timestamp_utc

        record = DurableSessionRecord(
            session_id=session_id,
            epoch_id=first_entry.epoch_id,
            status=status,
            created_timestamp_utc=first_entry.timestamp_utc,
            closed_timestamp_utc=closed_ts,
            total_cycles=cycle_count,
            last_sequence=last_cycle_seq,
            schema_version=PERSISTENCE_SCHEMA_VERSION,
        )

        writer = JournalWriter(
            self._storage_root,
            session_id,
            first_entry.epoch_id,
            authoritative_epoch_provider=lambda: self._epoch_id,
        )
        writer._entry_count = len(entries)
        writer._last_entry_hash = entries[-1].sha256_hash
        writer._last_sequence = last_cycle_seq

        self._sessions[session_id] = record
        self._writers[session_id] = writer
        return record

    def evict_session(self, session_id: str) -> bool:
        """Evict stopped session from in-memory cache, releasing capacity (M35).

        Returns:
            True if session was in memory and evicted.
            False if session was unknown or already evicted.

        Raises:
            PersistenceLifecycleError: If session is currently ACTIVE.
        """
        if session_id not in self._sessions:
            return False

        rec = self._sessions[session_id]
        if rec.status == SessionStatus.ACTIVE:
            raise PersistenceLifecycleError(
                f"Cannot evict active session {session_id!r}; session must be STOPPED first"
            )

        if session_id in self._writers:
            writer = self._writers[session_id]
            writer.flush()
            writer.close()
            del self._writers[session_id]

        del self._sessions[session_id]
        return True

    def clear(self) -> None:
        """Clear transient session mappings (does not delete disk files)."""
        self._sessions.clear()
        self._writers.clear()
