# -*- coding: utf-8 -*-
"""Bounded Tool Registry and Lifecycle Management for M07."""

from __future__ import annotations

from typing import Optional

from holomed.tools.exceptions import (
    ToolCapacityError,
    ToolDuplicateError,
    ToolLifecycleError,
    ToolValidationError,
)
from holomed.tools.models import (
    MAX_REGISTERED_TOOLS,
    ToolDescriptor,
)


class ToolRegistry:
    """Bounded, deterministic catalog of safe clinical tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDescriptor] = {}
        self._is_locked: bool = False

    @property
    def is_locked(self) -> bool:
        return self._is_locked

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    @property
    def tools(self) -> tuple[ToolDescriptor, ...]:
        """Return all registered tools sorted deterministically by tool_id ASC."""
        sorted_keys = sorted(self._tools.keys())
        return tuple(self._tools[k] for k in sorted_keys)

    def register_tool(self, descriptor: ToolDescriptor) -> None:
        """Register a tool descriptor into the catalog."""
        if self._is_locked:
            raise ToolLifecycleError("Cannot register tool: registry is locked")

        if len(self._tools) >= MAX_REGISTERED_TOOLS:
            raise ToolCapacityError(
                f"Tool registry capacity exceeded ({MAX_REGISTERED_TOOLS} tools max)"
            )

        if descriptor.tool_id in self._tools:
            raise ToolDuplicateError(f"Duplicate tool_id: {descriptor.tool_id!r}")

        self._tools[descriptor.tool_id] = descriptor

    def get_tool(self, tool_id: str) -> ToolDescriptor:
        """Retrieve tool by ID or raise ToolValidationError."""
        if tool_id not in self._tools:
            raise ToolValidationError(f"Unknown tool_id: {tool_id!r}")
        return self._tools[tool_id]

    def has_tool(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def lock(self) -> None:
        """Lock the registry against future mutations upon service startup."""
        self._is_locked = True

    def clear(self) -> None:
        """Clear all registered tools and unlock registry."""
        self._tools.clear()
        self._is_locked = False
