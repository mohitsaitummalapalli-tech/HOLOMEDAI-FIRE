# -*- coding: utf-8 -*-
"""Unit tests for Clinical Safety Boundaries and Permitted Categories (D242)."""

from __future__ import annotations

import pytest

from holomed.tools.exceptions import ToolSafetyError
from holomed.tools.models import (
    ToolCategory,
    ToolDescriptor,
    ToolExecutionStatus,
    ToolResult,
    ToolSafetyClassification,
)


def test_d242_permitted_safe_categories() -> None:
    """Verify D242 permitted categories initialize cleanly."""
    for cat in ToolCategory:
        td = ToolDescriptor(
            tool_id=f"test.{cat.value.lower()}",
            name=cat.value,
            version="1.0",
            category=cat,
            safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            description="Safe tool",
            required_parameters=(),
            optional_parameters=(),
            max_execution_budget_ms=10.0,
            is_idempotent=True,
            handler=lambda ctx: ToolResult(ctx.invocation_id, "id", ToolExecutionStatus.SUCCESS, {}, 1.0, 1.0, 0.0, 1),
        )
        assert td.category == cat


def test_d242_prohibited_clinical_actions_rejected() -> None:
    """Verify D242 prohibited actions (cutting, surgery, ablation) cannot be passed as category."""
    with pytest.raises(ToolSafetyError):
        ToolDescriptor(
            tool_id="surgery.tissue_cutting",
            name="Surgical Cut",
            version="1.0",
            category="SURGICAL_CUTTING",  # type: ignore
            safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
            description="Dangerous",
            required_parameters=(),
            optional_parameters=(),
            max_execution_budget_ms=10.0,
            is_idempotent=False,
            handler=lambda ctx: ToolResult(ctx.invocation_id, "id", ToolExecutionStatus.SUCCESS, {}, 1.0, 1.0, 0.0, 1),
        )
