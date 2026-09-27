# -*- coding: utf-8 -*-
"""Durable Global Coordinator Overlay (M49.3.5 Sequence 5)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence, Dict

from holomed.persistence.authority import ControllerAuthorityStore
from holomed.persistence.devices import DurableDeviceStore
from holomed.persistence.exceptions import (
    PersistenceLifecycleError,
    PersistenceResourceIntegrityError,
)
from holomed.persistence.models import (
    TransactionState,
    DeviceJournalEntryType,
    JournalEntryType,
)
from holomed.persistence.sessions import DurableSessionStore


class DurableGlobalCoordinator:
    """Computes effective operation states based on device global state and session state.

    Implements the Sequence 5 lock hierarchy:
    1. GLOBAL_TRANSACTION_LOCK
    2. EPOCH AUTHORITY LOCK
    3. GLOBAL PHYSICAL ADMISSION LOCK
    4. SESSION_JOURNAL_LOCK
    """

    def __init__(
        self,
        authority_store: ControllerAuthorityStore,
        device_store: DurableDeviceStore,
        session_store: DurableSessionStore,
    ) -> None:
        self._authority_store = authority_store
        self._device_store = device_store
        self._session_store = session_store

    def isolate_device(
        self,
        device_id: str,
        affected_operations_map: Mapping[str, Sequence[str]]
    ) -> str:
        """Execute a physical isolation transaction across the device and affected sessions.

        Args:
            device_id: The ID of the device to isolate.
            affected_operations_map: A mapping of session_id to a list of operation_ids
                                     that are pending physical isolation.

        Returns:
            The transaction ID of the committed isolation transaction.
        """
        # We assume the caller (e.g. ReconciliationDaemon) has ALREADY captured
        # the affected_operations_map under the ADMISSION LOCK, or that it is safe
        # to freeze it. The Sequence 5 report mandates:
        # 1 -> 3 -> release 3 -> 4.

        tx_id = str(uuid.uuid4())

        # 1. Acquire GLOBAL_TRANSACTION_LOCK
        with self._authority_store._get_global_transaction_lock():
            frozen_participants = {}

            # 2. Acquire GLOBAL PHYSICAL ADMISSION LOCK (to freeze set conceptually or assert state)
            with self._authority_store._get_global_admission_lock():
                active_reservations, _ = self._session_store._reconstruct_reservations_locked()

                for session_id, operation_ids in affected_operations_map.items():
                    if not self._session_store.has_session(session_id):
                        from holomed.persistence.exceptions import PersistenceValidationError
                        raise PersistenceValidationError(f"Cannot isolate non-existent session {session_id!r}")

                    frozen_participants[session_id] = []
                    session_active = {k: v for k, v in active_reservations.items() if v.get("_original_session_id") == session_id}

                    for op_id in operation_ids:
                        matches = []
                        for k, v in session_active.items():
                            if v.get("operation_id") == op_id or v.get("physical_operation_id") == op_id:
                                matches.append(v)

                        if len(matches) == 0:
                            from holomed.persistence.exceptions import PersistenceValidationError
                            raise PersistenceValidationError(f"Cannot isolate non-existent or inactive physical operation {op_id!r} in session {session_id!r}")
                        if len(matches) > 1:
                            raise PersistenceResourceIntegrityError(f"Ambiguous identifier {op_id!r} matched multiple active operations in session {session_id!r}")

                        frozen_participants[session_id].append(matches[0])

                # Write INTENT to device journal
                self._device_store._record_device_journal(
                    device_id=device_id,
                    entry_type=DeviceJournalEntryType.ISOLATION_TRANSACTION_INTENTED,
                    payload={"transaction_id": tx_id}
                )

            # 3. Admission Lock is released. Now acquire SESSION_JOURNAL_LOCK per session.
            # We use ONLY the frozen identities to write termination records.
            for session_id, participants in frozen_participants.items():
                writer = self._session_store._writers.get(session_id)
                if not writer:
                    raise PersistenceLifecycleError(f"Writer unavailable for frozen participant session {session_id!r}")

                for resolved_payload in participants:
                    writer.append_entry(
                        entry_type=JournalEntryType.OPERATION_TERMINATED,
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        payload={
                            "operation_id": resolved_payload.get("operation_id"),
                            "device_id": resolved_payload.get("device_id"),
                            "device_epoch": resolved_payload.get("device_epoch"),
                            "controller_epoch": resolved_payload.get("controller_epoch"),
                            "physical_operation_id": resolved_payload.get("physical_operation_id"),
                            "command_nonce": resolved_payload.get("command_nonce"),
                            "resolution": "PHYSICALLY_ISOLATED",
                            "isolation_transaction_id": tx_id
                        }
                    )

            # 5. Write PARTICIPANTS_PREPARED to device journal
            participant_summary = {
                k: list(v) for k, v in affected_operations_map.items()
            }
            self._device_store._record_device_journal(
                device_id=device_id,
                entry_type=DeviceJournalEntryType.ISOLATION_PARTICIPANTS_PREPARED,
                payload={
                    "transaction_id": tx_id,
                    "participants": participant_summary
                }
            )

            # 6. Write COMMITTED to device journal
            self._device_store._record_device_journal(
                device_id=device_id,
                entry_type=DeviceJournalEntryType.ISOLATION_TRANSACTION_COMMITTED,
                payload={"transaction_id": tx_id}
            )

        return tx_id

    def is_operation_effectively_isolated(
        self,
        session_id: str,
        operation_id: str,
        isolation_transaction_id: str
    ) -> bool:
        """Determine if an operation is effectively isolated.

        Requires that the isolation_transaction_id has a COMMITTED record
        in the device journal for the device it targeted.
        """
        # Check all devices to see if this transaction committed.
        for device_id, state in self._device_store._devices.items():
            tx_state = state.isolation_transactions.get(isolation_transaction_id)
            if tx_state == TransactionState.COMMITTED:
                return True
        return False

    def commit_device_ready(self, device_id: str, hardware_evidence: dict) -> int:
        """Commit DEVICE_READY to the device journal for the current authoritative epoch.

        Implements lock hierarchy: 1. GLOBAL_TRANSACTION_LOCK -> 2. EPOCH AUTHORITY LOCK.

        Args:
            device_id: Device to mark ready.
            hardware_evidence: Unforgeable hardware evidence proving physical state safety.

        Returns:
            The authoritative epoch_id.
        """
        from holomed.persistence.authority import DeviceEpochAuthority
        device_authority = DeviceEpochAuthority(self._device_store._storage_root)

        with self._authority_store._get_global_transaction_lock():
            # The epoch must have been allocated via allocate_device_restart_epoch already.
            new_epoch_id = device_authority.read_current_device_epoch(device_id)
            
            # Update writer epoch so it doesn't fail validation
            self._device_store.advance_device_epoch(device_id, new_epoch_id)

            # Commit DEVICE_READY(E2) to device journal
            self._device_store._record_device_journal(
                device_id=device_id,
                entry_type=DeviceJournalEntryType.DEVICE_READY_COMMITTED,
                payload={"new_epoch_id": new_epoch_id, "hardware_evidence": hardware_evidence}
            )

        return new_epoch_id

    def allocate_device_restart_epoch(self, device_id: str) -> int:
        """Allocate a new authoritative device epoch for a restarting device.
        
        This delegates strictly to DeviceEpochAuthority and does NOT mutate
        the ControllerAuthorityStore, enforcing independent domain authorities.
        """
        from holomed.persistence.authority import DeviceEpochAuthority
        device_authority = DeviceEpochAuthority(self._device_store._storage_root)
        return device_authority.allocate_next_device_epoch(device_id)

