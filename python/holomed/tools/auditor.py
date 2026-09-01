# -*- coding: utf-8 -*-
"""Tool Consistency and Safety Audit Engine for M07."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from holomed.runtime.logging import SecretFilter
from holomed.tools.engine import ToolExecutionEngine
from holomed.tools.models import MAX_REGISTERED_TOOLS, ToolCategory
from holomed.tools.registry import ToolRegistry


class ToolConsistencyAuditor:
    """Performs deep validation of tool registry, safety categories, and execution invariants."""

    def __init__(self, secret_filter: Optional[SecretFilter] = None) -> None:
        self._secret_filter = secret_filter

    def audit_subsystem(
        self,
        registry: ToolRegistry,
        engine: ToolExecutionEngine,
        epoch_id: int,
    ) -> Mapping[str, Any]:
        """Perform comprehensive consistency audit and return sanitized diagnostics."""
        findings: list[str] = []

        # 1. Registry Uniqueness & Size
        if registry.tool_count > MAX_REGISTERED_TOOLS:
            findings.append(f"Tool registry count ({registry.tool_count}) exceeds limit ({MAX_REGISTERED_TOOLS})")

        tool_ids = [t.tool_id for t in registry.tools]
        if len(tool_ids) != len(set(tool_ids)):
            findings.append("Duplicate tool IDs detected in registry")

        # 2. Safety Classifications Check
        allowed_cats = set(ToolCategory)
        for t in registry.tools:
            if t.category not in allowed_cats:
                findings.append(f"Tool {t.tool_id} has prohibited category: {t.category}")

        # 3. Execution History Integrity
        for res in engine.result_history:
            if res.epoch_id != epoch_id:
                findings.append(f"Result {res.invocation_id} belongs to stale epoch {res.epoch_id} != {epoch_id}")

        sanitized_findings = [
            self._secret_filter.redact(f) if self._secret_filter else f
            for f in findings
        ]

        return {
            "audit_passed": len(findings) == 0,
            "registered_tools_count": registry.tool_count,
            "is_registry_locked": registry.is_locked,
            "results_history_count": len(engine.result_history),
            "findings_count": len(sanitized_findings),
            "findings": sanitized_findings,
        }
