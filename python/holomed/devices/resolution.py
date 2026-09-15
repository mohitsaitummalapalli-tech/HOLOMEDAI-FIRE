"""HoloMed AI - Atomic synchronization boundary for physical execution resolution."""

import threading
from typing import Dict, Optional

from holomed.devices.interfaces import IExecutionResolutionGate
from holomed.devices.models import (
    AuthoritativeExecutionRecord,
    CommandState,
    ExecutionTelemetryEvent,
    EventSourceAuthority,
)


class ExecutionResolutionGate(IExecutionResolutionGate):
    """Atomic synchronization boundary for resolving terminal execution states.

    Guarantees:
    - Atomically serializes terminal telemetry vs control plane timeout.
    - Preserves monotonic event_sequence per execution.
    - Prevents mutating terminal states once reached.
    """

    def __init__(self) -> None:
        self._global_lock = threading.Lock()
        self._execution_locks: Dict[str, threading.Lock] = {}
        self._records: Dict[str, AuthoritativeExecutionRecord] = {}

    def _get_lock(self, execution_id: str) -> threading.Lock:
        with self._global_lock:
            if execution_id not in self._execution_locks:
                self._execution_locks[execution_id] = threading.Lock()
            return self._execution_locks[execution_id]

    def _get_or_create_record(self, execution_id: str, lifecycle_generation: int) -> AuthoritativeExecutionRecord:
        if execution_id not in self._records:
            self._records[execution_id] = AuthoritativeExecutionRecord(
                execution_id=execution_id,
                current_state=CommandState.ACCEPTED,
                latest_accepted_sequence=0,
                terminal_resolution_status=False,
                terminal_event_id=None,
                source_authority=None,
                lifecycle_generation=lifecycle_generation,
                timeout_status=False,
                quarantine_consequence=False,
            )
        return self._records[execution_id]

    def resolve_timeout(self, execution_id: str, lifecycle_generation: int) -> AuthoritativeExecutionRecord:
        """Atomically resolve a control-plane timeout."""
        lock = self._get_lock(execution_id)
        with lock:
            record = self._get_or_create_record(execution_id, lifecycle_generation)

            # Prevent cross-generation resolution
            if record.lifecycle_generation != lifecycle_generation:
                return record

            # If already terminal, safely ignored (return current state)
            if record.terminal_resolution_status:
                return record

            # Unresolved timeout -> FAULTED_UNKNOWN
            updated_record = AuthoritativeExecutionRecord(
                execution_id=execution_id,
                current_state=CommandState.FAULTED_UNKNOWN,
                latest_accepted_sequence=record.latest_accepted_sequence,
                terminal_resolution_status=True,
                terminal_event_id=record.terminal_event_id,
                source_authority=EventSourceAuthority.CONTROL_PLANE_TIMEOUT,
                lifecycle_generation=lifecycle_generation,
                timeout_status=True,
                quarantine_consequence=True,
            )
            self._records[execution_id] = updated_record
            return updated_record

    def resolve_terminal_event(self, event: ExecutionTelemetryEvent) -> AuthoritativeExecutionRecord:
        """Atomically resolve a physical terminal telemetry event."""
        execution_id = event.execution_id
        lock = self._get_lock(execution_id)
        with lock:
            record = self._get_or_create_record(execution_id, event.lifecycle_generation)

            # Prevent cross-generation resolution
            if record.lifecycle_generation != event.lifecycle_generation:
                return record

            # Sequence monotonicity check
            # We never accept events that are older than what we have processed.
            if event.event_sequence < record.latest_accepted_sequence:
                return record

            is_terminal = event.observed_state in (
                CommandState.COMPLETED,
                CommandState.FAILED,
                CommandState.PREEMPTED,
                CommandState.INTERLOCKED,
                CommandState.FAULTED_UNKNOWN
            )

            # If the record is already terminal
            if record.terminal_resolution_status:
                # Sequence collision detection
                if event.event_sequence == record.latest_accepted_sequence:
                    if record.current_state != event.observed_state:
                        # Conflicting state at the same sequence -> SEQUENCE COLLISION -> FAULTED_UNKNOWN
                        updated_record = AuthoritativeExecutionRecord(
                            execution_id=execution_id,
                            current_state=CommandState.FAULTED_UNKNOWN,
                            latest_accepted_sequence=event.event_sequence,
                            terminal_resolution_status=True,
                            terminal_event_id=record.terminal_event_id,
                            source_authority=record.source_authority,
                            lifecycle_generation=record.lifecycle_generation,
                            timeout_status=record.timeout_status,
                            quarantine_consequence=True,
                        )
                        self._records[execution_id] = updated_record
                        return updated_record
                return record

            # Not terminal yet
            # Determine quarantine consequence
            quarantine = event.observed_state in (CommandState.FAULTED_UNKNOWN, CommandState.INTERLOCKED)

            updated_record = AuthoritativeExecutionRecord(
                execution_id=execution_id,
                current_state=event.observed_state,
                latest_accepted_sequence=event.event_sequence,
                terminal_resolution_status=is_terminal,
                terminal_event_id=event.event_id if is_terminal else None,
                source_authority=event.source_authority,
                lifecycle_generation=record.lifecycle_generation,
                timeout_status=False,
                quarantine_consequence=quarantine,
            )
            self._records[execution_id] = updated_record
            return updated_record

    def claim_execution_ownership(self, execution_id: str, lifecycle_generation: int) -> bool:
        """Atomically claim execution ownership to prevent TOCTOU races with timeout."""
        lock = self._get_lock(execution_id)
        with lock:
            record = self._get_or_create_record(execution_id, lifecycle_generation)

            # Prevent cross-generation resolution
            if record.lifecycle_generation != lifecycle_generation:
                return False

            # If already terminal (e.g. by timeout), reject the claim
            if record.terminal_resolution_status:
                return False

            # Claim succeeds
            return True
