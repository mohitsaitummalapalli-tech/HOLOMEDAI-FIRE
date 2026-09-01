# -*- coding: utf-8 -*-
"""Unit tests for M07 Tool Models, Invariants, and Hard Bounds."""

from __future__ import annotations

import pytest

from holomed.tools.exceptions import (
    ToolSafetyError,
    ToolValidationError,
)
from holomed.tools.models import (
    ToolCategory,
    ToolDescriptor,
    ToolExecutionStatus,
    ToolInvocationContext,
    ToolResult,
    ToolSafetyClassification,
)


def dummy_handler(ctx: ToolInvocationContext) -> ToolResult:
    return ToolResult("inv_0", "t_0", ToolExecutionStatus.SUCCESS, {}, 1.0, 1.0, 0.0, 1)


def test_tool_descriptor_validation() -> None:
    """Verify ToolDescriptor validates IDs, semantic versions, and bounds."""
    td = ToolDescriptor(
        tool_id="anatomy.measure_clearance",
        name="Measure Clearance",
        version="1.0",
        category=ToolCategory.MEASURE_DISTANCE,
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        description="Measures spatial clearance",
        required_parameters=("target_a", "target_b"),
        optional_parameters=(),
        max_execution_budget_ms=15.0,
        is_idempotent=True,
        handler=dummy_handler,
    )
    assert td.tool_id == "anatomy.measure_clearance"
    assert td.max_execution_budget_ms == 15.0

    # Invalid tool_id format (uppercase / special characters)
    with pytest.raises(ToolValidationError):
        ToolDescriptor(
            tool_id="INVALID_ID!",
            name="Bad",
            version="1.0",
            category=ToolCategory.MEASURE_DISTANCE,
            safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            description="Bad",
            required_parameters=(),
            optional_parameters=(),
            max_execution_budget_ms=10.0,
            is_idempotent=True,
            handler=dummy_handler,
        )

    # Invalid execution budget (> 50ms)
    with pytest.raises(ToolValidationError):
        ToolDescriptor(
            tool_id="valid.tool",
            name="Bad",
            version="1.0",
            category=ToolCategory.MEASURE_DISTANCE,
            safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            description="Bad",
            required_parameters=(),
            optional_parameters=(),
            max_execution_budget_ms=100.0,
            is_idempotent=True,
            handler=dummy_handler,
        )


def test_tool_invocation_context_validation() -> None:
    """Verify ToolInvocationContext validates sequence range and depths."""
    ctx = ToolInvocationContext(
        invocation_id="inv_123",
        tool_id="anatomy.query_organ",
        epoch_id=1,
        session_id="session_0",
        sequence_number=10,
        depth=1,
        parameters={"entity_id": "anat.liver"},
        correlation_id="corr_123",
        causation_id=None,
        timestamp_utc="2026-09-01T20:00:00.000000Z",
    )
    assert ctx.sequence_number == 10
    assert ctx.depth == 1

    # Negative sequence number
    with pytest.raises(ToolValidationError):
        ToolInvocationContext(
            invocation_id="inv_123",
            tool_id="anatomy.query_organ",
            epoch_id=1,
            session_id="session_0",
            sequence_number=-5,
            depth=1,
            parameters={},
            correlation_id="c",
            causation_id=None,
            timestamp_utc="ts",
        )

    # Invalid depth < 1
    with pytest.raises(ToolValidationError):
        ToolInvocationContext(
            invocation_id="inv_123",
            tool_id="anatomy.query_organ",
            epoch_id=1,
            session_id="session_0",
            sequence_number=0,
            depth=0,
            parameters={},
            correlation_id="c",
            causation_id=None,
            timestamp_utc="ts",
        )


def test_tool_result_validation() -> None:
    """Verify ToolResult validates confidence and uncertainty metrics."""
    res = ToolResult(
        invocation_id="inv_1",
        tool_id="tool_1",
        status=ToolExecutionStatus.SUCCESS,
        result_payload={"val": 42},
        execution_time_ms=2.5,
        confidence=0.95,
        uncertainty_metric=0.05,
        epoch_id=1,
    )
    assert res.confidence == 0.95
    assert res.is_simulated is True

    # Invalid confidence > 1.0
    with pytest.raises(ToolValidationError):
        ToolResult(
            invocation_id="inv_1",
            tool_id="tool_1",
            status=ToolExecutionStatus.SUCCESS,
            result_payload={},
            execution_time_ms=1.0,
            confidence=1.5,
            uncertainty_metric=0.0,
            epoch_id=1,
        )
