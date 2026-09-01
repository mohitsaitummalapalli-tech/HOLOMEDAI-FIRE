# -*- coding: utf-8 -*-
"""Unit tests for Parameter Validation and Security Scanning (D246)."""

from __future__ import annotations

import pytest

from holomed.tools.exceptions import (
    ToolCapacityError,
    ToolSecurityError,
    ToolValidationError,
)
from holomed.tools.models import (
    ToolCategory,
    ToolDescriptor,
    ToolExecutionStatus,
    ToolResult,
    ToolSafetyClassification,
)
from holomed.tools.validation import validate_tool_parameters


def sample_descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        tool_id="sample.tool",
        name="Sample",
        version="1.0",
        category=ToolCategory.QUERY_ANATOMY,
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        description="desc",
        required_parameters=("organ_id",),
        optional_parameters=("depth_level",),
        max_execution_budget_ms=10.0,
        is_idempotent=True,
        handler=lambda ctx: ToolResult(ctx.invocation_id, "sample.tool", ToolExecutionStatus.SUCCESS, {}, 1.0, 1.0, 0.0, 1),
    )


def test_parameter_schema_keys() -> None:
    """Verify required keys must exist and unknown keys are rejected."""
    desc = sample_descriptor()

    # Valid parameters
    validate_tool_parameters({"organ_id": "liver", "depth_level": 2}, desc)

    # Missing required key
    with pytest.raises(ToolValidationError):
        validate_tool_parameters({"depth_level": 2}, desc)

    # Unknown key
    with pytest.raises(ToolValidationError):
        validate_tool_parameters({"organ_id": "liver", "unrecognized_key": 123}, desc)


def test_d246_security_injection_detection() -> None:
    """Verify D246 security scanner catches injection strings and non-finite values."""
    desc = sample_descriptor()

    # Non-finite number
    with pytest.raises(ToolValidationError):
        validate_tool_parameters({"organ_id": "liver", "depth_level": float("nan")}, desc)

    # Script injection pattern
    with pytest.raises(ToolSecurityError):
        validate_tool_parameters({"organ_id": "<script>alert(1)</script>"}, desc)

    # Subprocess / exec pattern
    with pytest.raises(ToolSecurityError):
        validate_tool_parameters({"organ_id": "liver; os.system('rm -rf /')"}, desc)


def test_parameter_payload_capacity_16kib() -> None:
    """Verify parameter payload capacity bound of 16 KiB."""
    desc = sample_descriptor()
    large_params = {"organ_id": "x" * 20000}
    with pytest.raises(ToolCapacityError):
        validate_tool_parameters(large_params, desc)
