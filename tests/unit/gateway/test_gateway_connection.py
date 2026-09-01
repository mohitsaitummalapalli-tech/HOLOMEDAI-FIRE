# -*- coding: utf-8 -*-
"""Unit Tests for GatewayConnection Lifecycle, Backpressure, and Sequence Tracking."""

from __future__ import annotations

import pytest

from holomed.gateway.connection import GatewayConnection
from holomed.gateway.exceptions import (
    GatewayLifecycleError,
    GatewayQueueOverflowError,
)
from holomed.gateway.models import (
    MAX_CLIENT_QUEUE,
    ClientRole,
    ClientSession,
    ConnectionState,
)
from holomed.gateway.transports import create_memory_transport_pair
from holomed.protocol.builders import create_command, create_error_response, create_event


def test_connection_lifecycle_progression() -> None:
    """Verify clean progression from CONNECTING to ACTIVE and CLOSED."""
    gw_side, _ = create_memory_transport_pair()
    conn = GatewayConnection(gw_side)
    assert conn.state == ConnectionState.CONNECTING

    session = ClientSession(
        client_id="unity_hmd_01",
        client_role=ClientRole.XR_DISPLAY,
        session_id="sess_01",
        epoch_id=1,
        connected_at_utc="2026-09-01T20:00:00Z",
        remote_address="memory://client",
    )
    conn.attach_session(session)
    assert conn.state == ConnectionState.ACTIVE
    assert conn.client_id == "unity_hmd_01"

    conn.close()
    assert conn.state == ConnectionState.CLOSED
    assert gw_side.is_closed is True


def test_backpressure_transient_frame_eviction() -> None:
    """Verify filling queue with transient visual frames evicts oldest frames when full (D285)."""
    gw_side, _ = create_memory_transport_pair()
    conn = GatewayConnection(gw_side)
    session = ClientSession("client_01", ClientRole.XR_DISPLAY, "s1", 1, "now", "addr")
    conn.attach_session(session)

    # Fill queue to capacity (64) with transient presentation frames
    for i in range(MAX_CLIENT_QUEUE):
        env = create_event("xr.presentation.frame", "xr_service", payload={"frame_idx": i})
        conn.enqueue_envelope(env)

    assert conn.queue_depth == MAX_CLIENT_QUEUE
    assert conn.stats.dropped_frames_total == 0

    # Enqueue 65th frame -> Should evict oldest frame (idx 0)
    overflow_env = create_event("xr.presentation.frame", "xr_service", payload={"frame_idx": 999})
    conn.enqueue_envelope(overflow_env)

    assert conn.queue_depth == MAX_CLIENT_QUEUE
    assert conn.stats.dropped_frames_total == 1


def test_backpressure_never_drops_critical_workflow_events() -> None:
    """Verify critical events (abort, confirmations) are never dropped."""
    gw_side, _ = create_memory_transport_pair()
    conn = GatewayConnection(gw_side)
    session = ClientSession("client_02", ClientRole.XR_DISPLAY, "s1", 1, "now", "addr")
    conn.attach_session(session)

    # Enqueue 1 critical event
    crit = create_event("workflow.aborted", "workflow_service", payload={"reason": "Emergency"})
    conn.enqueue_envelope(crit)

    # Fill rest with transient frames
    for i in range(MAX_CLIENT_QUEUE - 1):
        conn.enqueue_envelope(create_event("xr.presentation.frame", "xr_service", payload={"idx": i}))

    assert conn.queue_depth == MAX_CLIENT_QUEUE

    # Enqueue additional transient frame -> evicts a transient frame, NOT the critical frame!
    conn.enqueue_envelope(create_event("xr.presentation.frame", "xr_service", payload={"idx": 100}))
    assert conn.queue_depth == MAX_CLIENT_QUEUE

    # First envelope in queue remains the critical event
    assert conn._egress_queue[0][0].message_name == "workflow.aborted"


def test_backpressure_queue_saturation_disconnects_client() -> None:
    """Verify saturating queue with 64 critical messages causes disconnection on overflow (D289)."""
    gw_side, _ = create_memory_transport_pair()
    conn = GatewayConnection(gw_side)
    session = ClientSession("client_03", ClientRole.XR_DISPLAY, "s1", 1, "now", "addr")
    conn.attach_session(session)

    # Fill queue to capacity (64) with unevictable critical events
    for i in range(MAX_CLIENT_QUEUE):
        crit = create_event("workflow.aborted", "workflow_service", payload={"idx": i})
        conn.enqueue_envelope(crit)

    assert conn.queue_depth == MAX_CLIENT_QUEUE

    # 65th critical message cannot evict anything -> GatewayQueueOverflowError and closed!
    with pytest.raises(GatewayQueueOverflowError):
        conn.enqueue_envelope(create_event("workflow.aborted", "workflow_service", payload={"idx": 999}))

    assert conn.state == ConnectionState.CLOSED


def test_sequence_number_monotonicity() -> None:
    """Verify non-monotonic sequence numbers raise GatewayLifecycleError."""
    gw_side, _ = create_memory_transport_pair()
    conn = GatewayConnection(gw_side)

    conn.validate_sequence_number(1)
    conn.validate_sequence_number(2)
    conn.validate_sequence_number(5)

    with pytest.raises(GatewayLifecycleError):
        conn.validate_sequence_number(5)

    with pytest.raises(GatewayLifecycleError):
        conn.validate_sequence_number(4)
