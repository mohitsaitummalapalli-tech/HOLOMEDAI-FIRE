# -*- coding: utf-8 -*-
"""Bounded Durable Device Store and State Manager for M09."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
from types import MappingProxyType
from typing import Any, Mapping, Optional

from holomed.persistence.exceptions import (
    PersistenceCapacityError,
    PersistenceEpochMismatchError,
    PersistenceLifecycleError,
    PersistenceValidationError,
)
from holomed.persistence.device_journal import DeviceJournalReader, DeviceJournalWriter
from holomed.persistence.models import (
    PERSISTENCE_SCHEMA_VERSION,
    DurableDeviceRecord,
    DeviceJournalEntryType,
    TransactionState,
)


class DurableDeviceStore:
    """Manages full lifecycle and durable persistence of holomed devices."""

    def __init__(self, storage_root: Path, epoch_id: int = 0) -> None:
        self._storage_root = Path(storage_root)
        self._epoch_id = epoch_id
        
        self._devices: dict[str, DurableDeviceRecord] = {}
        self._writers: dict[str, DeviceJournalWriter] = {}

        if not self._storage_root.exists():
            self._storage_root.mkdir(parents=True)

    def get_device(self, device_id: str) -> DurableDeviceRecord:
        if device_id not in self._devices:
            raise PersistenceValidationError(f"Device {device_id!r} not found")
        return self._devices[device_id]

    def has_device(self, device_id: str) -> bool:
        return device_id in self._devices

    def initialize_device(self, device_id: str, epoch_id: int, metadata: Optional[Mapping[str, Any]] = None) -> DurableDeviceRecord:
        if epoch_id != self._epoch_id:
            raise PersistenceEpochMismatchError(
                f"Device epoch {epoch_id} does not match active storage epoch {self._epoch_id}"
            )

        if device_id in self._devices:
            return self._devices[device_id]

        writer = DeviceJournalWriter(
            self._storage_root,
            device_id,
            epoch_id,
            authoritative_epoch_provider=lambda: self._epoch_id,
        )
        writer.initialize_storage()

        now_utc = datetime.now(timezone.utc).isoformat()
        rec = DurableDeviceRecord(
            device_id=device_id,
            epoch_id=epoch_id,
            created_timestamp_utc=now_utc,
            last_sequence=-1,
            schema_version=PERSISTENCE_SCHEMA_VERSION,
            metadata=MappingProxyType(dict(metadata or {})),
        )

        self._devices[device_id] = rec
        self._writers[device_id] = writer
        return rec

    def record_device_quarantined(self, device_id: str, reason: str) -> None:
        self._append_device_entry(device_id, DeviceJournalEntryType.DEVICE_QUARANTINED, {"reason": reason})

    def record_isolation_intented(self, device_id: str, tx_id: str) -> None:
        self._append_device_entry(device_id, DeviceJournalEntryType.ISOLATION_TRANSACTION_INTENTED, {"tx_id": tx_id})

    def record_isolation_participants_prepared(self, device_id: str, tx_id: str) -> None:
        self._append_device_entry(device_id, DeviceJournalEntryType.ISOLATION_PARTICIPANTS_PREPARED, {"tx_id": tx_id})

    def record_isolation_committed(self, device_id: str, tx_id: str) -> None:
        self._append_device_entry(device_id, DeviceJournalEntryType.ISOLATION_TRANSACTION_COMMITTED, {"tx_id": tx_id})

    def record_isolation_aborted(self, device_id: str, tx_id: str, reason: str) -> None:
        self._append_device_entry(device_id, DeviceJournalEntryType.ISOLATION_TRANSACTION_ABORTED, {"tx_id": tx_id, "reason": reason})

    def record_device_ready_committed(self, device_id: str, tx_id: str) -> None:
        self._append_device_entry(device_id, DeviceJournalEntryType.DEVICE_READY_COMMITTED, {"tx_id": tx_id})

    def _append_device_entry(self, device_id: str, entry_type: DeviceJournalEntryType, payload: dict) -> None:
        if device_id not in self._devices:
            raise PersistenceValidationError(f"Device {device_id!r} not found")
        writer = self._writers[device_id]
        now_utc = datetime.now(timezone.utc).isoformat()
        
        entry = writer.append_entry(
            entry_type=entry_type,
            timestamp_utc=now_utc,
            payload=payload,
        )
        
        rec = self._devices[device_id]
        updated = DurableDeviceRecord(
            device_id=rec.device_id,
            epoch_id=rec.epoch_id,
            created_timestamp_utc=rec.created_timestamp_utc,
            last_sequence=entry.sequence_number,
            schema_version=rec.schema_version,
            metadata=rec.metadata,
            isolation_transactions=rec.isolation_transactions,
        )
        self._devices[device_id] = updated
    def _record_device_journal(self, device_id: str, entry_type: DeviceJournalEntryType, payload: dict) -> None:
        if device_id not in self._devices or device_id not in self._writers:
            raise PersistenceValidationError(f"Device {device_id!r} is not active in store")
        writer = self._writers[device_id]
        from datetime import datetime, timezone
        entry = writer.append_entry(
            entry_type=entry_type,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            payload=payload
        )
        rec = self._devices[device_id]
        
        isolation_transactions = dict(rec.isolation_transactions)
        if entry_type == DeviceJournalEntryType.ISOLATION_TRANSACTION_INTENTED:
            tx_id = payload.get("transaction_id")
            if tx_id:
                isolation_transactions[tx_id] = TransactionState.INTENTED
        elif entry_type == DeviceJournalEntryType.ISOLATION_PARTICIPANTS_PREPARED:
            tx_id = payload.get("transaction_id")
            if tx_id:
                isolation_transactions[tx_id] = TransactionState.PARTICIPANTS_PREPARED
        elif entry_type == DeviceJournalEntryType.ISOLATION_TRANSACTION_COMMITTED:
            tx_id = payload.get("transaction_id")
            if tx_id:
                isolation_transactions[tx_id] = TransactionState.COMMITTED
        
        updated = DurableDeviceRecord(
            device_id=rec.device_id,
            epoch_id=rec.epoch_id,
            created_timestamp_utc=rec.created_timestamp_utc,
            last_sequence=entry.sequence_number,
            schema_version=rec.schema_version,
            metadata=rec.metadata,
            isolation_transactions=isolation_transactions
        )
        self._devices[device_id] = updated

    def restore_device_from_disk(self, device_id: str) -> DurableDeviceRecord:
        if device_id in self._devices:
            return self._devices[device_id]

        journal_path = self._storage_root / f"{device_id}.jsonl"
        entries, _ = DeviceJournalReader.read_and_recover_journal(journal_path)

        if not entries:
            raise PersistenceValidationError(f"No journal records found for device {device_id!r}")

        first_entry = entries[0]
        last_seq = entries[-1].sequence_number if entries else -1

        isolation_transactions = {}
        for entry in entries:
            if entry.entry_type == DeviceJournalEntryType.ISOLATION_TRANSACTION_INTENTED:
                tx_id = entry.payload.get("transaction_id")
                if tx_id:
                    isolation_transactions[tx_id] = TransactionState.INTENTED
            elif entry.entry_type == DeviceJournalEntryType.ISOLATION_PARTICIPANTS_PREPARED:
                tx_id = entry.payload.get("transaction_id")
                if tx_id:
                    isolation_transactions[tx_id] = TransactionState.PARTICIPANTS_PREPARED
            elif entry.entry_type == DeviceJournalEntryType.ISOLATION_TRANSACTION_COMMITTED:
                tx_id = entry.payload.get("transaction_id")
                if tx_id:
                    isolation_transactions[tx_id] = TransactionState.COMMITTED

        record = DurableDeviceRecord(
            device_id=device_id,
            epoch_id=first_entry.epoch_id,
            created_timestamp_utc=first_entry.timestamp_utc,
            last_sequence=last_seq,
            schema_version=PERSISTENCE_SCHEMA_VERSION,
            isolation_transactions=isolation_transactions,
        )

        writer = DeviceJournalWriter(
            self._storage_root,
            device_id,
            self._epoch_id,
            authoritative_epoch_provider=lambda: self._epoch_id,
        )
        writer._entry_count = len(entries)
        writer._last_entry_hash = entries[-1].sha256_hash
        writer._last_sequence = last_seq

        self._devices[device_id] = record
        self._writers[device_id] = writer
        return record
