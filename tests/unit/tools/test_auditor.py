# -*- coding: utf-8 -*-
"""Unit tests for ToolConsistencyAuditor."""

from __future__ import annotations

import pytest

from holomed.runtime.logging import SecretFilter
from holomed.tools.auditor import ToolConsistencyAuditor
from holomed.tools.engine import ToolExecutionEngine
from holomed.tools.models import (
    ToolCategory,
    ToolDescriptor,
    ToolExecutionStatus,
    ToolResult,
    ToolSafetyClassification,
)
from holomed.tools.registry import ToolRegistry


def test_tool_consistency_auditor_clean_report(secret_filter: SecretFilter) -> None:
    """Verify auditor generates clean reports on valid subsystems."""
    auditor = ToolConsistencyAuditor(secret_filter=secret_filter)
    reg = ToolRegistry()
    reg.register_tool(
        ToolDescriptor(
            tool_id="anatomy.query",
            name="Query",
            version="1.0",
            category=ToolCategory.QUERY_ANATOMY,
            safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            description="desc",
            required_parameters=(),
            optional_parameters=(),
            max_execution_budget_ms=10.0,
            is_idempotent=True,
            handler=lambda ctx: ToolResult(ctx.invocation_id, "anatomy.query", ToolExecutionStatus.SUCCESS, {}, 1.0, 1.0, 0.0, 1),
        )
    )
    engine = ToolExecutionEngine(epoch_id=1)

    report = auditor.audit_subsystem(reg, engine, epoch_id=1)
    assert report["audit_passed"] is True
    assert report["registered_tools_count"] == 1
    assert report["findings_count"] == 0
