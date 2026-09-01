# -*- coding: utf-8 -*-
"""Tool Parameter Validation and Sandbox Security Scanner for M07."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping

from holomed.tools.exceptions import (
    ToolCapacityError,
    ToolSecurityError,
    ToolValidationError,
)
from holomed.tools.models import (
    MAX_TOOL_PARAMETER_BYTES,
    ToolDescriptor,
)

INJECTION_PATTERNS = (
    "<script",
    "exec(",
    "eval(",
    "import ",
    "__import__",
    "__class__",
    "__subclasses__",
    "subprocess",
    "os.system",
    ";",
    "|",
    "&&",
)


def scan_for_security_violations(val: Any) -> None:
    """Recursively scan parameters for dangerous injection patterns or non-finite numbers."""
    if isinstance(val, str):
        lower_s = val.lower()
        for pat in INJECTION_PATTERNS:
            if pat in lower_s:
                raise ToolSecurityError(f"Prohibited injection pattern {pat!r} detected in parameter")
    elif isinstance(val, (int, float)):
        if not math.isfinite(val):
            raise ToolValidationError(f"Non-finite numeric value in parameters: {val!r}")
    elif isinstance(val, (list, tuple)):
        for item in val:
            scan_for_security_violations(item)
    elif isinstance(val, (dict, Mapping)):
        for k, v in val.items():
            if not isinstance(k, str):
                raise ToolValidationError("Parameter dictionary keys must be strings")
            scan_for_security_violations(k)
            scan_for_security_violations(v)
    elif val is None or isinstance(val, bool):
        pass
    else:
        raise ToolSecurityError(f"Unsupported executable or dynamic parameter type: {type(val).__name__}")


def validate_tool_parameters(
    parameters: Mapping[str, Any],
    descriptor: ToolDescriptor,
) -> None:
    """Validate tool parameters against descriptor schema and security invariants."""
    # 1. Check required parameters
    for req in descriptor.required_parameters:
        if req not in parameters:
            raise ToolValidationError(
                f"Missing required parameter {req!r} for tool {descriptor.tool_id!r}"
            )

    # 2. Check for unknown parameter keys
    allowed_keys = set(descriptor.required_parameters) | set(descriptor.optional_parameters)
    for k in parameters.keys():
        if k not in allowed_keys:
            raise ToolValidationError(
                f"Unknown parameter key {k!r} for tool {descriptor.tool_id!r}"
            )

    # 3. Scan for security violations and non-finite values
    scan_for_security_violations(parameters)

    # 4. Enforce parameter size limit (16 KiB)
    param_bytes = json.dumps(dict(parameters), sort_keys=True, ensure_ascii=False).encode("utf-8")
    if len(param_bytes) > MAX_TOOL_PARAMETER_BYTES:
        raise ToolCapacityError(
            f"Parameter payload ({len(param_bytes)} bytes) exceeds limit of {MAX_TOOL_PARAMETER_BYTES} bytes"
        )
