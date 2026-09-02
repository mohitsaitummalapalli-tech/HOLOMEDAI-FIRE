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
        self._interlocks: dict[str, SafetyInterlock] = {}
        self._staged_recovery_prior: Optional[dict[str, SafetyInterlock]] = None

    @property
    def interlock_count(self) -> int:
        return len(self._interlocks)

    @property
    def all_interlocks(self) -> tuple[SafetyInterlock, ...]:
        return tuple(self._interlocks.values())

    def register_interlock(self, interlock: SafetyInterlock) -> None:
        """Register or update an evaluated interlock state."""
        self._interlocks[interlock.interlock_id] = interlock

    def has_blocking_interlock(self) -> bool:
        """Return True if any active interlock is tripped with BLOCKING or CRITICAL severity."""
        return any(
            not it.status and it.severity in (InterlockSeverity.BLOCKING, InterlockSeverity.CRITICAL)
            for it in self._interlocks.values()
        )

    def has_critical_interlock(self) -> bool:
        """Return True if any active interlock is tripped with CRITICAL severity."""
        return any(
            not it.status and it.severity == InterlockSeverity.CRITICAL
            for it in self._interlocks.values()
        )

    def get_tripped_interlocks(self) -> tuple[SafetyInterlock, ...]:
        """Return all currently tripped interlocks sorted by descending severity precedence."""
        tripped = [it for it in self._interlocks.values() if not it.status]
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
        cleared: list[str] = []
        for iid in interlock_ids:
            if not isinstance(iid, str) or not iid.strip():
                raise WorkflowValidationError(f"Invalid interlock_id: {iid!r}")
            if iid not in self._interlocks:
                raise WorkflowSafetyInterlockError(f"Interlock {iid!r} not found in interlock engine")
            it = self._interlocks[iid]
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
        self._staged_recovery_prior = dict(self._interlocks)

        for iid in cleared:
            del self._interlocks[iid]

        # Ensure no active blocking or critical interlocks remain
        if self.has_critical_interlock() or self.has_blocking_interlock():
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
            self._interlocks = self._staged_recovery_prior
            self._staged_recovery_prior = None


    def clear(self) -> None:
        self._interlocks.clear()
