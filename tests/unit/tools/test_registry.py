# -*- coding: utf-8 -*-
"""Unit tests for ToolRegistry Lifecycle and Capacity (D244)."""

from __future__ import annotations

import pytest

from holomed.tools.exceptions import (
    ToolCapacityError,
    ToolDuplicateError,
    ToolLifecycleError,
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
from holomed.tools.registry import ToolRegistry


def make_test_tool(tool_id: str) -> ToolDescriptor:
    def handler(ctx: ToolInvocationContext) -> ToolResult:
        return ToolResult(ctx.invocation_id, tool_id, ToolExecutionStatus.SUCCESS, {}, 1.0, 1.0, 0.0, 1)

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
        handler=handler,
    )


def test_registry_registration_and_lexicographic_ordering() -> None:
    """Verify tool registration and deterministic sorting by tool_id ASC."""
    reg = ToolRegistry()
    reg.register_tool(make_test_tool("xr.highlight"))
    reg.register_tool(make_test_tool("anatomy.query"))

    assert reg.tool_count == 2
    assert reg.tools[0].tool_id == "anatomy.query"
    assert reg.tools[1].tool_id == "xr.highlight"


def test_registry_duplicate_rejection() -> None:
    """Verify duplicate tool_id registration is rejected."""
    reg = ToolRegistry()
    reg.register_tool(make_test_tool("anatomy.query"))
    with pytest.raises(ToolDuplicateError):
        reg.register_tool(make_test_tool("anatomy.query"))


def test_d244_registry_lock_lifecycle() -> None:
    """Verify D244 registry mutation after lock() raises ToolLifecycleError."""
    reg = ToolRegistry()
    reg.register_tool(make_test_tool("tool.one"))
    reg.lock()
    assert reg.is_locked is True

    # Registration after lock must fail
    with pytest.raises(ToolLifecycleError):
        reg.register_tool(make_test_tool("tool.two"))


def test_registry_capacity_limit_32() -> None:
    """Verify MAX_REGISTERED_TOOLS = 32 capacity bound."""
    reg = ToolRegistry()
    for i in range(32):
        reg.register_tool(make_test_tool(f"tool.t{i:02d}"))

    assert reg.tool_count == 32

    # 33rd tool raises ToolCapacityError
    with pytest.raises(ToolCapacityError):
        reg.register_tool(make_test_tool("tool.t32"))
