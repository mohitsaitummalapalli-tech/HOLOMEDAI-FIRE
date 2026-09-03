# -*- coding: utf-8 -*-
"""Safety Interlock Evaluation and Precedence Engine for M10."""

from __future__ import annotations

from typing import Sequence

from holomed.workflow._transaction import _RecoveryTransactionCapability
from holomed.workflow.exceptions import (
    WorkflowAuthorizationError,
    WorkflowSafetyInterlockError,
    WorkflowValidationError,
)
from holomed.workflow.models import (
    InterlockSeverity,
    SafetyInterlock,
)

SEVERITY_PRECEDENCE = {
    InterlockSeverity.CRITICAL: 4,
    InterlockSeverity.BLOCKING: 3,
    InterlockSeverity.WARNING: 2,
    InterlockSeverity.INFO: 1,
}


class SafetyInterlockEngine:
    """Evaluates and tracks active safety interlocks with mathematical precedence."""

    def __init__(self) -> None:
        self._session_interlocks: dict[str, dict[str, SafetyInterlock]] = {}
        self._staged_recovery_prior: Optional[dict[str, dict[str, SafetyInterlock]]] = None

    @property
    def _interlocks(self) -> dict[str, SafetyInterlock]:
        """Backward-compatible view of all interlocks flattened by interlock_id."""
        flat: dict[str, SafetyInterlock] = {}
        for s in self._session_interlocks.values():
            flat.update(s)
        return flat

    @property
    def interlock_count(self) -> int:
        return sum(len(s) for s in self._session_interlocks.values())

    @property
    def all_interlocks(self) -> tuple[SafetyInterlock, ...]:
        return tuple(it for s in self._session_interlocks.values() for it in s.values())

    def register_interlock(self, interlock: SafetyInterlock) -> None:
        """Register or update an evaluated interlock state partitioned by session."""
        sid = interlock.session_id
        if sid not in self._session_interlocks:
            self._session_interlocks[sid] = {}
        self._session_interlocks[sid][interlock.interlock_id] = interlock

    def has_blocking_interlock(self, session_id: Optional[str] = None) -> bool:
        """Return True if any active interlock is tripped with BLOCKING or CRITICAL severity."""
        if session_id is not None:
            sess_dict = self._session_interlocks.get(session_id, {})
            return any(
                not it.status and it.severity in (InterlockSeverity.BLOCKING, InterlockSeverity.CRITICAL)
                for it in sess_dict.values()
            )
        return any(
            not it.status and it.severity in (InterlockSeverity.BLOCKING, InterlockSeverity.CRITICAL)
            for s in self._session_interlocks.values()
            for it in s.values()
        )

    def has_critical_interlock(self, session_id: Optional[str] = None) -> bool:
        """Return True if any active interlock is tripped with CRITICAL severity."""
        if session_id is not None:
            sess_dict = self._session_interlocks.get(session_id, {})
            return any(
                not it.status and it.severity == InterlockSeverity.CRITICAL
                for it in sess_dict.values()
            )
        return any(
            not it.status and it.severity == InterlockSeverity.CRITICAL
            for s in self._session_interlocks.values()
            for it in s.values()
        )

    def get_tripped_interlocks(self, session_id: Optional[str] = None) -> tuple[SafetyInterlock, ...]:
        """Return all currently tripped interlocks sorted by descending severity precedence."""
        if session_id is not None:
            tripped = [it for it in self._session_interlocks.get(session_id, {}).values() if not it.status]
        else:
            tripped = [it for s in self._session_interlocks.values() for it in s.values() if not it.status]
        return tuple(
            sorted(
                tripped,
                key=lambda it: SEVERITY_PRECEDENCE.get(it.severity, 0),
                reverse=True,
            )
        )

    def stage_recovery_clearance(
        self,
        capability: _RecoveryTransactionCapability,
        interlock_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Stage removal of explicit recovery-eligible interlocks using internal transaction capability."""
        if not isinstance(capability, _RecoveryTransactionCapability) or not capability.is_active:
            raise WorkflowAuthorizationError("Valid active _RecoveryTransactionCapability required")
        if not interlock_ids:
            raise WorkflowValidationError("interlock_ids cannot be empty")

        session_id = capability.session_id
        all_interlocks_map = self._interlocks
        cleared: list[str] = []
        for iid in interlock_ids:
            if not isinstance(iid, str) or not iid.strip():
                raise WorkflowValidationError(f"Invalid interlock_id: {iid!r}")
            if iid not in all_interlocks_map:
                raise WorkflowSafetyInterlockError(f"Interlock {iid!r} not found in interlock engine")
            it = all_interlocks_map[iid]
            if it.session_id != session_id:
                raise WorkflowSafetyInterlockError(
                    f"Interlock {iid!r} belongs to session {it.session_id!r}, not active session {session_id!r}"
                )
            eligible_sources = {
                "drift",
                "drift_service",
                "proximity",
                "proximity_service",
                "checkpoint",
                "recovery",
                "recovery_service",
                "registration",
                "registration_service",
            }
            if it.source_service.lower() not in eligible_sources and "recovery" not in it.condition_name.lower():
                raise WorkflowSafetyInterlockError(
                    f"Interlock {iid!r} with source {it.source_service!r} is not eligible for recovery clearance"
                )
            cleared.append(iid)

        # Capture snapshot of prior interlocks before mutation
        self._staged_recovery_prior = {sid: dict(interlocks) for sid, interlocks in self._session_interlocks.items()}

        for iid in cleared:
            if session_id in self._session_interlocks and iid in self._session_interlocks[session_id]:
                del self._session_interlocks[session_id][iid]
        if session_id in self._session_interlocks and not self._session_interlocks[session_id]:
            del self._session_interlocks[session_id]

        # Ensure no active blocking or critical interlocks remain for this session
        if self.has_critical_interlock(session_id) or self.has_blocking_interlock(session_id):
            self.abort_recovery_clearance(capability)
            raise WorkflowSafetyInterlockError(
                "Active blocking or critical safety interlocks remain after clearing recovery interlocks"
            )

        return tuple(cleared)

    def commit_recovery_clearance(
        self,
        capability: _RecoveryTransactionCapability,
    ) -> None:
        """Commit staged recovery clearance."""
        if not isinstance(capability, _RecoveryTransactionCapability) or not capability.is_active:
            raise WorkflowAuthorizationError("Valid active _RecoveryTransactionCapability required")
        self._staged_recovery_prior = None

    def abort_recovery_clearance(
        self,
        capability: Optional[_RecoveryTransactionCapability] = None,
    ) -> None:
        """Abort staged recovery clearance, restoring pre-staged state."""
        if self._staged_recovery_prior is not None:
            self._session_interlocks = self._staged_recovery_prior
            self._staged_recovery_prior = None

    def evict_session(self, session_id: str) -> bool:
        """Evict all session-scoped safety interlocks, releasing state (M27)."""
        if not isinstance(session_id, str) or not session_id.strip():
            return False
        return self._session_interlocks.pop(session_id, None) is not None

    def clear(self) -> None:
        self._session_interlocks.clear()
        self._staged_recovery_prior = None
