# -*- coding: utf-8 -*-
"""Bounded Clinical Session and Sequence Monotonicity Manager (D255)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from holomed.platform.exceptions import (
    PlatformCapacityError,
    PlatformEpochMismatchError,
    PlatformSequenceError,
    PlatformValidationError,
)
from holomed.platform.models import (
    MAX_ACTIVE_PLATFORM_SESSIONS,
    SessionContext,
    SessionStatus,
)


class SessionManager:
    """Manages active session contexts and enforces strict sequence monotonicity."""

    def __init__(self, epoch_id: int = 0) -> None:
        self._epoch_id = epoch_id
        self._sessions: dict[str, SessionContext] = {}

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    @property
    def sessions(self) -> tuple[SessionContext, ...]:
        sorted_keys = sorted(self._sessions.keys())
        return tuple(self._sessions[k] for k in sorted_keys)

    def start_session(self, session_id: str, epoch_id: int) -> SessionContext:
        """Initialize and register a new active clinical session context."""
        if epoch_id != self._epoch_id:
            raise PlatformEpochMismatchError(
                f"Session epoch {epoch_id} does not match active epoch {self._epoch_id}"
            )

        if session_id in self._sessions:
            existing = self._sessions[session_id]
            if existing.status == SessionStatus.ACTIVE:
                return existing

        if len(self._sessions) >= MAX_ACTIVE_PLATFORM_SESSIONS:
            raise PlatformCapacityError(
                f"Platform session capacity exceeded ({MAX_ACTIVE_PLATFORM_SESSIONS} max)"
            )

        now_utc = datetime.now(timezone.utc).isoformat()
        ctx = SessionContext(
            session_id=session_id,
            epoch_id=self._epoch_id,
            status=SessionStatus.ACTIVE,
            last_sequence=-1,
            created_timestamp_utc=now_utc,
        )
        self._sessions[session_id] = ctx
        return ctx

    def stop_session(self, session_id: str) -> SessionContext:
        """Mark an active session as stopped."""
        if session_id not in self._sessions:
            raise PlatformValidationError(f"Unknown session_id: {session_id!r}")

        cur = self._sessions[session_id]
        stopped = SessionContext(
            session_id=cur.session_id,
            epoch_id=cur.epoch_id,
            status=SessionStatus.STOPPED,
            last_sequence=cur.last_sequence,
            created_timestamp_utc=cur.created_timestamp_utc,
        )
        self._sessions[session_id] = stopped
        return stopped

    def get_session(self, session_id: str) -> SessionContext:
        """Retrieve session context or raise PlatformValidationError."""
        if session_id not in self._sessions:
            raise PlatformValidationError(f"Unknown session_id: {session_id!r}")
        return self._sessions[session_id]

    def has_session(self, session_id: str) -> bool:
        return session_id in self._sessions

    def validate_and_advance_sequence(
        self,
        session_id: str,
        epoch_id: int,
        sequence_number: int,
    ) -> None:
        """Validate sequence monotonicity and update last seen sequence (D255)."""
        if epoch_id != self._epoch_id:
            raise PlatformEpochMismatchError(
                f"Cycle epoch {epoch_id} does not match active epoch {self._epoch_id}"
            )

        if session_id not in self._sessions:
            self.start_session(session_id, epoch_id)

        session = self._sessions[session_id]
        if session.status != SessionStatus.ACTIVE:
            raise PlatformValidationError(f"Session {session_id!r} is not in ACTIVE state")

        if sequence_number <= session.last_sequence:
            raise PlatformSequenceError(
                f"Non-monotonic sequence number {sequence_number} <= last seen {session.last_sequence} "
                f"for session {session_id!r}"
            )

        updated = SessionContext(
            session_id=session.session_id,
            epoch_id=session.epoch_id,
            status=session.status,
            last_sequence=sequence_number,
            created_timestamp_utc=session.created_timestamp_utc,
        )
        self._sessions[session_id] = updated

    def evict_session(self, session_id: str) -> bool:
        """Evict a session context from memory, releasing capacity (M25)."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def reset(self, epoch_id: int) -> None:
        """Reset session state for a new epoch."""
        self._epoch_id = epoch_id
        self.clear()

    def clear(self) -> None:
        """Clear all registered sessions."""
        self._sessions.clear()
