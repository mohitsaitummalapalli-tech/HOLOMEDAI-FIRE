# -*- coding: utf-8 -*-
"""Durable Controller Epoch Authority (M49.3.5 Phase 2)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import contextlib

from holomed.persistence.exceptions import (
    PersistenceEpochMismatchError,
    PersistenceLifecycleError,
    PersistenceResourceIntegrityError,
)


class ControllerAuthorityStore:
    """Manages the durable cross-process controller epoch authority.
    
    Provides atomic allocation and retrieval of the global epoch using file locking.
    """

    def __init__(self, storage_root: Path) -> None:
        self._storage_root = Path(storage_root)
        self._epoch_path = self._storage_root / "controller_epoch.json"
        self._lock_path = self._storage_root / ".epoch.lock"
        self._global_admission_lock_path = self._storage_root / ".physical_admission.lock"

        if not self._storage_root.exists():
            self._storage_root.mkdir(parents=True, exist_ok=True)

    def _acquire_lock(self, fd: int) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                pos = os.lseek(fd, 0, os.SEEK_CUR)
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                os.lseek(fd, pos, os.SEEK_SET)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as e:
            raise PersistenceLifecycleError(
                "Concurrent access rejected: failed to acquire exclusive lock on epoch authority"
            ) from e

    def _release_lock(self, fd: int) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                pos = os.lseek(fd, 0, os.SEEK_CUR)
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                os.lseek(fd, pos, os.SEEK_SET)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass

    def _read_current_epoch_unlocked(self, allow_missing: bool = False) -> int:
        """Read the current epoch from disk without acquiring the lock."""
        if not self._epoch_path.exists():
            if allow_missing:
                return 0
            raise PersistenceResourceIntegrityError("Missing epoch authority file")
        try:
            with open(self._epoch_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "epoch_id" not in data:
                    raise PersistenceResourceIntegrityError("Malformed epoch authority: missing 'epoch_id'")
                if not isinstance(data["epoch_id"], int):
                    raise PersistenceResourceIntegrityError("Malformed epoch authority: 'epoch_id' must be an integer")
                return data["epoch_id"]
        except json.JSONDecodeError as e:
            raise PersistenceResourceIntegrityError("Malformed epoch authority JSON") from e
        except OSError as e:
            raise PersistenceResourceIntegrityError("Unreadable epoch authority file") from e

    def allocate_next_epoch(self) -> int:
        """Atomically allocate and durably store the next monotonic epoch."""
        if not self._lock_path.exists():
            self._lock_path.touch()

        with open(self._lock_path, "a") as lock_file:
            fd = lock_file.fileno()
            self._acquire_lock(fd)
            try:
                current_epoch = self._read_current_epoch_unlocked(allow_missing=True)
                next_epoch = current_epoch + 1
                
                with open(self._epoch_path, "w", encoding="utf-8") as f:
                    json.dump({"epoch_id": next_epoch}, f)
                    f.flush()
                    os.fsync(f.fileno())
                    
                return next_epoch
            finally:
                self._release_lock(fd)

    def read_current_epoch(self) -> int:
        """Read the current authoritative epoch."""
        return self._read_current_epoch_unlocked(allow_missing=False)

    def assert_authoritative(self, epoch_id: int) -> None:
        """Assert that the provided epoch matches the durable authoritative epoch."""
        if not self._lock_path.exists():
            self._lock_path.touch()

        with open(self._lock_path, "a") as lock_file:
            fd = lock_file.fileno()
            self._acquire_lock(fd)
            try:
                current_epoch = self._read_current_epoch_unlocked(allow_missing=False)
                if epoch_id != current_epoch:
                    raise PersistenceEpochMismatchError(
                        f"Stale epoch {epoch_id} rejected; authoritative epoch is {current_epoch}"
                    )
            finally:
                self._release_lock(fd)

    @contextlib.contextmanager
    def hold_authority(self, epoch_id: int):
        """
        Hold the epoch authority lock exclusively, validating the epoch is current.
        Used to prevent TOC-TOU races during capacity-affecting mutations.
        """
        if not self._lock_path.exists():
            self._lock_path.touch()
            
        with open(self._lock_path, "a") as lock_file:
            fd = lock_file.fileno()
            self._acquire_lock(fd)
            try:
                current_epoch = self._read_current_epoch_unlocked(allow_missing=False)
                if epoch_id != current_epoch:
                    raise PersistenceEpochMismatchError(
                        f"Stale epoch {epoch_id} rejected; authoritative epoch is {current_epoch}"
                    )
                yield
            finally:
                self._release_lock(fd)

    @contextlib.contextmanager
    def _get_global_admission_lock(self):
        """Acquire the global physical admission lock across all sessions."""
        if not self._global_admission_lock_path.exists():
            self._global_admission_lock_path.touch()
            
        with open(self._global_admission_lock_path, "a") as lock_file:
            fd = lock_file.fileno()
            self._acquire_lock(fd)
            try:
                yield
            finally:
                self._release_lock(fd)
