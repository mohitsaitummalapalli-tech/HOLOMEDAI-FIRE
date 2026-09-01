# -*- coding: utf-8 -*-
"""Unit tests for M08 Platform Models and Invariants."""

from __future__ import annotations

import pytest

from holomed.platform.exceptions import PlatformValidationError
from holomed.platform.models import (
    AggregateHealthReport,
    CycleStatus,
    CycleSummary,
    SessionContext,
    SessionStatus,
)
from holomed.runtime.models import HealthStatus


def test_cycle_summary_validation() -> None:
    """Verify CycleSummary validates bounds, finite floats, and metrics."""
    cs = CycleSummary(
        cycle_id="c_1",
        epoch_id=1,
        session_id="s_1",
        sequence_number=10,
        status=CycleStatus.COMPLETED,
        execution_time_ms=12.5,
        phases_completed=("PERCEPTION", "REASONING", "PRESENTATION"),
        fused_entity_count=3,
        tool_invocations_count=1,
        xr_node_count=5,
        confidence=0.95,
        uncertainty_metric=0.05,
    )
    assert cs.cycle_id == "c_1"
    assert cs.is_simulated is True
    assert cs.confidence == 0.95

    # Negative sequence
    with pytest.raises(PlatformValidationError):
        CycleSummary(
            cycle_id="c_1",
            epoch_id=1,
            session_id="s_1",
            sequence_number=-1,
            status=CycleStatus.COMPLETED,
            execution_time_ms=1.0,
            phases_completed=(),
            fused_entity_count=0,
            tool_invocations_count=0,
            xr_node_count=0,
            confidence=1.0,
            uncertainty_metric=0.0,
        )

    # Non-finite execution time
    with pytest.raises(PlatformValidationError):
        CycleSummary(
            cycle_id="c_1",
            epoch_id=1,
            session_id="s_1",
            sequence_number=0,
            status=CycleStatus.COMPLETED,
            execution_time_ms=float("nan"),
            phases_completed=(),
            fused_entity_count=0,
            tool_invocations_count=0,
            xr_node_count=0,
            confidence=1.0,
            uncertainty_metric=0.0,
        )


def test_session_context_validation() -> None:
    """Verify SessionContext validates session ID and status."""
    ctx = SessionContext(
        session_id="sess_0",
        epoch_id=1,
        status=SessionStatus.ACTIVE,
        last_sequence=-1,
        created_timestamp_utc="2026-09-01T20:00:00Z",
    )
    assert ctx.session_id == "sess_0"
    assert ctx.status == SessionStatus.ACTIVE

    with pytest.raises(PlatformValidationError):
        SessionContext(
            session_id="",
            epoch_id=1,
            status=SessionStatus.ACTIVE,
            last_sequence=0,
            created_timestamp_utc="ts",
        )
