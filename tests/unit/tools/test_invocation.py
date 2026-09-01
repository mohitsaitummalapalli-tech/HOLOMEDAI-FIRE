# -*- coding: utf-8 -*-
"""Unit tests for Tool Invocation, Sequences, and Monotonicity (D245)."""

from __future__ import annotations

import pytest

from holomed.tools.engine import ToolExecutionEngine
from holomed.tools.exceptions import (
    ToolCapacityError,
    ToolSequenceError,
)
from holomed.tools.models import (
    ToolCategory,
    ToolDescriptor,
    ToolExecutionStatus,
    ToolInvocationContext,
    ToolResult,
    ToolSafetyClassification,
)
from holomed.tools.registry import ToolRegistry


def make_tool(tool_id: str) -> ToolDescriptor:
    return ToolDescriptor(
        tool_id=tool_id,
        name=tool_id,
        version="1.0",
        category=ToolCategory.QUERY_ANATOMY,
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        description="desc",
        required_parameters=(),
        optional_parameters=(),
        max_execution_budget_ms=10.0,
        is_idempotent=True,
        handler=lambda ctx: ToolResult(ctx.invocation_id, tool_id, ToolExecutionStatus.SUCCESS, {}, 1.0, 1.0, 0.0, 1),
    )


def make_context(seq: int, session: str = "sess_0", depth: int = 1) -> ToolInvocationContext:
    return ToolInvocationContext(
        invocation_id=f"inv_{seq}",
        tool_id="test.tool",
        epoch_id=1,
        session_id=session,
        sequence_number=seq,
        depth=depth,
        parameters={},
        correlation_id="c",
        causation_id=None,
        timestamp_utc="ts",
    )


def test_d245_sequence_monotonicity() -> None:
    """Verify D245 strict sequence progression per session context."""
    engine = ToolExecutionEngine(epoch_id=1)
    reg = ToolRegistry()
    reg.register_tool(make_tool("test.tool"))

    # Monotonic progression: 0 -> 1 -> 5
    engine.execute_invocation(make_context(seq=0), reg)
    engine.execute_invocation(make_context(seq=1), reg)
    engine.execute_invocation(make_context(seq=5), reg)

    # Duplicate sequence 5
    with pytest.raises(ToolSequenceError):
        engine.execute_invocation(make_context(seq=5), reg)

    # Decreasing sequence 3
    with pytest.raises(ToolSequenceError):
        engine.execute_invocation(make_context(seq=3), reg)


def test_session_capacity_limit_64() -> None:
    """Verify MAX_ACTIVE_SESSIONS = 64 capacity limit."""
    engine = ToolExecutionEngine(epoch_id=1)
    reg = ToolRegistry()
    reg.register_tool(make_tool("test.tool"))

    for i in range(64):
        engine.execute_invocation(make_context(seq=0, session=f"sess_{i}"), reg)

    # 65th session raises ToolCapacityError
    with pytest.raises(ToolCapacityError):
        engine.execute_invocation(make_context(seq=0, session="sess_overflow"), reg)
