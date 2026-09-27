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
        # Test-only instrumentation seam. None in production.
        # When set, called with a phase name string at migration boundaries.
        self._migration_test_hook: Any = None

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
        
        # Write domain initialization marker for fresh devices
        # so restore_device_from_disk knows this isn't a partially-migrated legacy journal.
        writer.append_entry(
            entry_type=DeviceJournalEntryType.DEVICE_EPOCH_DOMAIN_INITIALIZED,
            timestamp_utc=now_utc,
            payload={"migrated_epoch": epoch_id}
        )
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

    def advance_device_epoch(self, device_id: str, new_epoch: int) -> None:
        """Update the store's knowledge of the device's authoritative epoch."""
        if device_id not in self._devices or device_id not in self._writers:
            raise PersistenceValidationError(f"Device {device_id!r} not found")
        
        # Re-initialize the writer with the new epoch so append_entry validations pass
        old_writer = self._writers[device_id]
        
        def _get_authoritative_device_epoch() -> int:
            from holomed.persistence.authority import DeviceEpochAuthority
            return DeviceEpochAuthority(self._storage_root).read_current_device_epoch(device_id)
            
        new_writer = DeviceJournalWriter(
            self._storage_root,
            device_id,
            new_epoch,
            authoritative_epoch_provider=_get_authoritative_device_epoch
        )
        new_writer._entry_count = old_writer._entry_count
        new_writer._last_entry_hash = old_writer._last_entry_hash
        new_writer._last_sequence = old_writer._last_sequence
        self._writers[device_id] = new_writer
        
        # Update record
        rec = self._devices[device_id]
        self._devices[device_id] = DurableDeviceRecord(
            device_id=rec.device_id,
            epoch_id=new_epoch,
            created_timestamp_utc=rec.created_timestamp_utc,
            last_sequence=rec.last_sequence,
            schema_version=rec.schema_version,
            metadata=rec.metadata,
            isolation_transactions=rec.isolation_transactions,
        )

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

        # Non-mutating journal existence check
        if not journal_path.exists() or journal_path.stat().st_size == 0:
            raise PersistenceValidationError(f"No journal records found for device {device_id!r}")

        from holomed.persistence.authority import DeviceEpochAuthority
        from holomed.persistence.exceptions import PersistenceResourceIntegrityError, PersistenceResourceMissingError, PersistenceLifecycleError
        import json, os
        from datetime import datetime, timezone

        device_authority = DeviceEpochAuthority(self._storage_root)

        lock_path = device_authority._get_lock_path(device_id)
        if not lock_path.exists():
            lock_path.touch()

        # The entire migration classification + action must occur under the lock.
        with open(lock_path, "a") as lock_file:
            fd = lock_file.fileno()
            device_authority._acquire_lock(fd)
            try:
                # --- Fresh durable journal read under lock ---
                entries, _ = DeviceJournalReader.read_and_recover_journal(journal_path)
                if not entries:
                    raise PersistenceValidationError(
                        f"No journal records found for device {device_id!r} (under lock)"
                    )

                marker_entries = [
                    e for e in entries
                    if e.entry_type == DeviceJournalEntryType.DEVICE_EPOCH_DOMAIN_INITIALIZED
                ]
                marker_entry = marker_entries[0] if marker_entries else None

                if len(marker_entries) > 1:
                    raise PersistenceLifecycleError(
                        f"Ambiguous legacy history: {device_id} has multiple DEVICE_EPOCH_DOMAIN_INITIALIZED markers"
                    )

                # --- Fresh durable authority read under lock ---
                try:
                    # The caller already holds DEVICE_EPOCH_AUTHORITY_LOCK
                    device_epoch = device_authority._read_current_epoch_unlocked(
                        device_id,
                        allow_missing=False,
                    )
                    authority_missing = False
                except PersistenceResourceMissingError:
                    device_epoch = None
                    authority_missing = True
                except PersistenceResourceIntegrityError as e:
                    raise PersistenceLifecycleError(f"Corrupt device epoch authority for {device_id}: {e}") from e

                # --- Migration state classification (all under lock) ---
                if authority_missing and not marker_entry:
                    # CASE A: Pure legacy state.
                    # Calculate legacy epoch, persist authority, append marker, all while locked.
                    legacy_epochs = [e.epoch_id for e in entries]
                    device_epoch = max(legacy_epochs) if legacy_epochs else 0

                    # 1. Write authority
                    epoch_path = device_authority._get_epoch_path(device_id)
                    with open(epoch_path, "w", encoding="utf-8") as f:
                        json.dump({"device_epoch": device_epoch}, f)
                        f.flush()
                        os.fsync(f.fileno())

                    if self._migration_test_hook is not None:
                        self._migration_test_hook("after_authority_fsync")

                    # 2. Append marker
                    writer = DeviceJournalWriter(
                        self._storage_root,
                        device_id,
                        device_epoch,
                        authoritative_epoch_provider=None,
                    )
                    writer._entry_count = len(entries)
                    writer._last_entry_hash = entries[-1].sha256_hash
                    writer._last_sequence = entries[-1].sequence_number

                    new_marker = writer.append_entry(
                        entry_type=DeviceJournalEntryType.DEVICE_EPOCH_DOMAIN_INITIALIZED,
                        timestamp_utc=datetime.now(timezone.utc).isoformat(),
                        payload={"migrated_epoch": device_epoch}
                    )
                    entries.append(new_marker)
                    marker_entry = new_marker

                    if self._migration_test_hook is not None:
                        self._migration_test_hook("after_marker_append")

                    # --- Durable post-migration verification ---
                    # Re-read both durable sources while still holding the lock.
                    verified_epoch = device_authority._read_current_epoch_unlocked(device_id, allow_missing=False)
                    verified_journal_path = self._storage_root / f"{device_id}.jsonl"
                    verified_entries, _ = DeviceJournalReader.read_and_recover_journal(verified_journal_path)
                    verified_markers = [
                        e for e in verified_entries
                        if e.entry_type == DeviceJournalEntryType.DEVICE_EPOCH_DOMAIN_INITIALIZED
                    ]
                    if len(verified_markers) != 1:
                        raise PersistenceResourceIntegrityError(
                            f"Post-migration verification failed for {device_id}: "
                            f"expected exactly 1 DEVICE_EPOCH_DOMAIN_INITIALIZED marker, found {len(verified_markers)}"
                        )
                    verified_marker_epoch = verified_markers[0].payload.get("migrated_epoch")
                    if verified_epoch != verified_marker_epoch:
                        raise PersistenceResourceIntegrityError(
                            f"Post-migration verification failed for {device_id}: "
                            f"authority epoch {verified_epoch} != marker epoch {verified_marker_epoch}"
                        )

                elif not authority_missing and not marker_entry:
                    # CASE B: Authority exists, but no marker
                    raise PersistenceLifecycleError(
                        f"Ambiguous legacy history: {device_id} has device_epoch.json but no DEVICE_EPOCH_DOMAIN_INITIALIZED marker"
                    )

                elif authority_missing and marker_entry:
                    # CASE C: Marker exists, authority missing
                    raise PersistenceLifecycleError(
                        f"Ambiguous legacy history: {device_id} has DEVICE_EPOCH_DOMAIN_INITIALIZED marker but no device_epoch.json"
                    )

                else:
                    # CASE D, E, F: Both exist. Validate agreement.
                    assert marker_entry is not None  # guaranteed by control flow
                    marker_epoch = marker_entry.payload.get("migrated_epoch")
                    if marker_epoch != device_epoch:
                        raise PersistenceResourceIntegrityError(
                            f"Migration mismatch for {device_id}: marker epoch {marker_epoch} != authority epoch {device_epoch}"
                        )
            finally:
                device_authority._release_lock(fd)

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

        # All branches above either assign device_epoch or raise.
        assert device_epoch is not None

        record = DurableDeviceRecord(
            device_id=device_id,
            epoch_id=device_epoch,
            created_timestamp_utc=first_entry.timestamp_utc,
            last_sequence=last_seq,
            schema_version=PERSISTENCE_SCHEMA_VERSION,
            isolation_transactions=isolation_transactions,
        )

        writer = DeviceJournalWriter(
            self._storage_root,
            device_id,
            device_epoch,
            authoritative_epoch_provider=lambda: DeviceEpochAuthority(self._storage_root).read_current_device_epoch(device_id),
        )
        writer._entry_count = len(entries)
        writer._last_entry_hash = entries[-1].sha256_hash
        writer._last_sequence = last_seq

        self._devices[device_id] = record
        self._writers[device_id] = writer
        return record
