# -*- coding: utf-8 -*-
"""HoloMed AI Tool Orchestration & Clinical Agent Foundation (M07)."""

from __future__ import annotations

from holomed.tools.auditor import ToolConsistencyAuditor
from holomed.tools.engine import ToolExecutionEngine
from holomed.tools.events import RecordingToolEventSink
from holomed.tools.exceptions import (
    ToolCapacityError,
    ToolDuplicateError,
    ToolEpochMismatchError,
    ToolError,
    ToolLifecycleError,
    ToolRecursionError,
    ToolResourceIntegrityError,
    ToolSafetyError,
    ToolSecurityError,
    ToolSequenceError,
    ToolShutdownError,
    ToolValidationError,
)
from holomed.tools.models import (
    MAX_ACTIVE_SESSIONS,
    MAX_RECORDED_TOOL_EVENTS,
    MAX_REGISTERED_TOOLS,
    MAX_RESULT_HISTORY,
    MAX_TOOL_INVOCATION_DEPTH,
    MAX_TOOL_PARAMETER_BYTES,
    MAX_TOOL_PAYLOAD_BYTES,
    MAX_TOOL_RESULT_BYTES,
    MAX_TOOL_STEPS_PER_CYCLE,
    ToolCategory,
    ToolDescriptor,
    ToolExecutionStatus,
    ToolInvocationContext,
    ToolResult,
    ToolSafetyClassification,
)
from holomed.tools.registry import ToolRegistry
from holomed.tools.serialization import (
    serialize_tool_bytes,
    serialize_tool_payload,
)
from holomed.tools.service import ToolService

__all__ = [
    # Exceptions
    "ToolError",
    "ToolLifecycleError",
    "ToolValidationError",
    "ToolCapacityError",
    "ToolResourceIntegrityError",
    "ToolShutdownError",
    "ToolEpochMismatchError",
    "ToolDuplicateError",
    "ToolSequenceError",
    "ToolRecursionError",
    "ToolSafetyError",
    "ToolSecurityError",
    # Constants
    "MAX_REGISTERED_TOOLS",
    "MAX_TOOL_INVOCATION_DEPTH",
    "MAX_TOOL_STEPS_PER_CYCLE",
    "MAX_ACTIVE_SESSIONS",
    "MAX_RESULT_HISTORY",
    "MAX_RECORDED_TOOL_EVENTS",
    "MAX_TOOL_PARAMETER_BYTES",
    "MAX_TOOL_RESULT_BYTES",
    "MAX_TOOL_PAYLOAD_BYTES",
    # Enums
    "ToolCategory",
    "ToolSafetyClassification",
    "ToolExecutionStatus",
    # Models
    "ToolDescriptor",
    "ToolInvocationContext",
    "ToolResult",
    # Subsystems
    "ToolRegistry",
    "ToolExecutionEngine",
    "ToolConsistencyAuditor",
    "RecordingToolEventSink",
    "serialize_tool_payload",
    "serialize_tool_bytes",
    "ToolService",
]
