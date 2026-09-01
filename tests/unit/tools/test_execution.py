# -*- coding: utf-8 -*-
"""Unit tests for Tool Execution, Recursion Guards, and Cycle Detection (D241, D247)."""

from __future__ import annotations

import pytest

from holomed.tools.engine import ToolExecutionEngine
from holomed.tools.exceptions import (
    ToolCapacityError,
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


def make_context(tool_id: str, depth: int = 1, seq: int = 0) -> ToolInvocationContext:
    return ToolInvocationContext(
        invocation_id=f"inv_{tool_id}_{seq}",
        tool_id=tool_id,
        epoch_id=1,
        session_id="sess_0",
        sequence_number=seq,
        depth=depth,
        parameters={},
        correlation_id="c",
        causation_id=None,
        timestamp_utc="ts",
    )


def test_d241_recursion_depth_exceeded() -> None:
    """Verify D241 depth > 4 returns RECURSION_DEPTH_EXCEEDED."""
    engine = ToolExecutionEngine(epoch_id=1)
    reg = ToolRegistry()
    reg.register_tool(
        ToolDescriptor(
            tool_id="test.tool",
            name="Test",
            version="1.0",
            category=ToolCategory.QUERY_ANATOMY,
            safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            description="desc",
            required_parameters=(),
            optional_parameters=(),
            max_execution_budget_ms=10.0,
            is_idempotent=True,
            handler=lambda ctx: ToolResult(ctx.invocation_id, "test.tool", ToolExecutionStatus.SUCCESS, {}, 1.0, 1.0, 0.0, 1),
        )
    )

    # Depth 5 exceeds limit (MAX_TOOL_INVOCATION_DEPTH = 4)
    res = engine.execute_invocation(make_context("test.tool", depth=5), reg)
    assert res.status == ToolExecutionStatus.RECURSION_DEPTH_EXCEEDED


def test_d241_cycle_detection() -> None:
    """Verify D241 repeated tool in active chain returns CYCLE_DETECTED."""
    engine = ToolExecutionEngine(epoch_id=1)
    reg = ToolRegistry()
    reg.register_tool(
        ToolDescriptor(
            tool_id="tool.a",
            name="A",
            version="1.0",
            category=ToolCategory.QUERY_ANATOMY,
            safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            description="desc",
            required_parameters=(),
            optional_parameters=(),
            max_execution_budget_ms=10.0,
            is_idempotent=True,
            handler=lambda ctx: ToolResult(ctx.invocation_id, "tool.a", ToolExecutionStatus.SUCCESS, {}, 1.0, 1.0, 0.0, 1),
        )
    )

    # Calling tool.a when tool.a is already in call chain
    res = engine.execute_invocation(make_context("tool.a", depth=2), reg, active_chain=("tool.a",))
    assert res.status == ToolExecutionStatus.CYCLE_DETECTED


def test_d247_result_payload_overflow_rejected() -> None:
    """Verify D247 result payload exceeding 32 KiB raises ToolCapacityError."""
    engine = ToolExecutionEngine(epoch_id=1)
    reg = ToolRegistry()

    def oversized_handler(ctx: ToolInvocationContext) -> ToolResult:
        return ToolResult(
            invocation_id=ctx.invocation_id,
            tool_id="oversized.tool",
            status=ToolExecutionStatus.SUCCESS,
            result_payload={"data": "x" * 40000},
            execution_time_ms=1.0,
            confidence=1.0,
            uncertainty_metric=0.0,
            epoch_id=ctx.epoch_id,
        )

    reg.register_tool(
        ToolDescriptor(
            tool_id="oversized.tool",
            name="Oversized",
            version="1.0",
            category=ToolCategory.QUERY_ANATOMY,
            safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            description="desc",
            required_parameters=(),
            optional_parameters=(),
            max_execution_budget_ms=10.0,
            is_idempotent=True,
            handler=oversized_handler,
        )
    )

    with pytest.raises(ToolCapacityError):
        engine.execute_invocation(make_context("oversized.tool"), reg)
