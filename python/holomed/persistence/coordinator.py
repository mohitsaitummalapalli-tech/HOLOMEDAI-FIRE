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
            # 2. Acquire GLOBAL PHYSICAL ADMISSION LOCK (to freeze set conceptually or assert state)
            # In our implementation, the caller may have passed the snapshot, but we re-acquire
            # to ensure admission barrier is maintained during intent write if necessary.
            with self._authority_store._get_global_admission_lock():
                # Write INTENT to device journal
                self._device_store._record_device_journal(
                    device_id=device_id,
                    entry_type=DeviceJournalEntryType.ISOLATION_TRANSACTION_INTENTED,
                    payload={"transaction_id": tx_id}
                )

            # 3. Admission Lock is released. Now acquire SESSION_JOURNAL_LOCK per session.
            # (In our system, writers are locked per session automatically by the writer mechanism,
            # or we explicitly grab the session writer).
            for session_id, operation_ids in affected_operations_map.items():
                if not self._session_store.has_session(session_id):
                    continue

                writer = self._session_store._writers.get(session_id)
                if not writer:
                    continue

                # 4. Acquire SESSION_JOURNAL_LOCK (writer handles its own file locking during append)
                for op_id in operation_ids:
                    # Write Tentative PREPARED (represented as OPERATION_TERMINATED with PHYSICALLY_ISOLATED
                    # but tied to the tx_id, which only becomes effective upon device commit).
                    from datetime import datetime, timezone
                    writer.append_entry(
                        entry_type=JournalEntryType.OPERATION_TERMINATED,
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        payload={
                            "operation_id": op_id,
                            "terminal_state": "PHYSICALLY_ISOLATED",
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
        """Allocate a new epoch and commit DEVICE_READY to the device journal.
        
        Implements lock hierarchy: 1. GLOBAL_TRANSACTION_LOCK -> 2. EPOCH AUTHORITY LOCK.
        
        Args:
            device_id: Device to mark ready.
            hardware_evidence: Unforgeable hardware evidence proving physical state safety.
            
        Returns:
            The newly allocated epoch_id.
        """
        with self._authority_store._get_global_transaction_lock():
            # Atomically allocate epoch under EPOCH AUTHORITY LOCK
            new_epoch = self._authority_store.allocate_next_epoch()
            
            # Commit DEVICE_READY(E2) to device journal
            self._device_store._record_device_journal(
                device_id=device_id,
                entry_type=DeviceJournalEntryType.DEVICE_READY_COMMITTED,
                payload={"new_epoch_id": new_epoch, "hardware_evidence": hardware_evidence}
            )
            
        return new_epoch

