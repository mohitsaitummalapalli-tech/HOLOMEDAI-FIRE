# -*- coding: utf-8 -*-
"""Safety Interlock Evaluation and Precedence Engine for M10."""

from __future__ import annotations

from typing import Sequence

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

    def clear(self) -> None:
        self._interlocks.clear()
