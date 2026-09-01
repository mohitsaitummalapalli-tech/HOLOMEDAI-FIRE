# -*- coding: utf-8 -*-
"""Unit tests for CycleCoordinator and Cycle Transaction Semantics (D251)."""

from __future__ import annotations

import pytest

from holomed.platform.cycle import CycleCoordinator
from holomed.platform.models import (
    MAX_CYCLE_HISTORY,
    CycleStatus,
)
from holomed.platform.session import SessionManager
from holomed.runtime.service import IService


def test_d251_clean_cycle_execution(mock_domain_services: dict[str, IService]) -> None:
    """Verify clean cycle execution marks all phases and outputs COMPLETED."""
    cc = CycleCoordinator(epoch_id=1)
    sm = SessionManager(epoch_id=1)

    summary = cc.execute_cycle(
        session_manager=sm,
        session_id="s_0",
        sequence_number=0,
        services=mock_domain_services,
    )

    assert summary.status == CycleStatus.COMPLETED
    assert summary.confidence == 1.0
    assert summary.uncertainty_metric == 0.0
    assert "PERCEPTION" in summary.phases_completed
    assert "REASONING" in summary.phases_completed
    assert "PRESENTATION" in summary.phases_completed


def test_d251_degraded_cycle_on_missing_perception(mock_domain_services: dict[str, IService]) -> None:
    """Verify D251 missing vision/audio results in DEGRADED cycle and uncertainty elevation."""
    cc = CycleCoordinator(epoch_id=1)
    sm = SessionManager(epoch_id=1)

    # Remove vision service
    degraded_services = dict(mock_domain_services)
    del degraded_services["vision_service"]

    summary = cc.execute_cycle(
        session_manager=sm,
        session_id="s_0",
        sequence_number=0,
        services=degraded_services,
    )

    assert summary.status == CycleStatus.DEGRADED
    assert summary.confidence < 1.0
    assert summary.uncertainty_metric > 0.0
    assert "PERCEPTION" not in summary.phases_completed


def test_cycle_history_ring_buffer_limit_256(mock_domain_services: dict[str, IService]) -> None:
    """Verify MAX_CYCLE_HISTORY = 256 bounded ring buffer."""
    cc = CycleCoordinator(epoch_id=1)
    sm = SessionManager(epoch_id=1)

    for i in range(MAX_CYCLE_HISTORY + 10):
        cc.execute_cycle(
            session_manager=sm,
            session_id=f"sess_{i % 5}",
            sequence_number=i,
            services=mock_domain_services,
        )

    assert len(cc.cycle_history) == 256
