# -*- coding: utf-8 -*-
"""Unit tests for SessionManager, Sequence Monotonicity, and Capacity Limits (D255)."""

from __future__ import annotations

import pytest

from holomed.platform.exceptions import (
    PlatformCapacityError,
    PlatformSequenceError,
)
from holomed.platform.models import (
    MAX_ACTIVE_PLATFORM_SESSIONS,
    SessionStatus,
)
from holomed.platform.session import SessionManager


def test_d255_sequence_monotonicity() -> None:
    """Verify D255 strictly monotonic progression per session context."""
    sm = SessionManager(epoch_id=1)
    sm.start_session("s_1", epoch_id=1)

    # Valid increasing sequence: 0 -> 1 -> 5
    sm.validate_and_advance_sequence("s_1", epoch_id=1, sequence_number=0)
    sm.validate_and_advance_sequence("s_1", epoch_id=1, sequence_number=1)
    sm.validate_and_advance_sequence("s_1", epoch_id=1, sequence_number=5)

    # Duplicate sequence 5
    with pytest.raises(PlatformSequenceError):
        sm.validate_and_advance_sequence("s_1", epoch_id=1, sequence_number=5)

    # Decreasing sequence 3
    with pytest.raises(PlatformSequenceError):
        sm.validate_and_advance_sequence("s_1", epoch_id=1, sequence_number=3)


def test_session_capacity_limit_16() -> None:
    """Verify MAX_ACTIVE_PLATFORM_SESSIONS = 16 capacity bound."""
    sm = SessionManager(epoch_id=1)
    for i in range(MAX_ACTIVE_PLATFORM_SESSIONS):
        sm.start_session(f"sess_{i}", epoch_id=1)

    assert sm.session_count == 16

    # 17th session raises PlatformCapacityError
    with pytest.raises(PlatformCapacityError):
        sm.start_session("sess_overflow", epoch_id=1)
