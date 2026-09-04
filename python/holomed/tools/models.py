# -*- coding: utf-8 -*-
"""M07 Tool Orchestration Models, Enums, and Hard Bounds."""

from __future__ import annotations

from dataclasses import dataclass
import enum
import math
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from holomed.tools.exceptions import (
    ToolSafetyError,
    ToolValidationError,
)

# Canonical Hard Bounds
MAX_REGISTERED_TOOLS: int = 32
MAX_TOOL_INVOCATION_DEPTH: int = 4
MAX_TOOL_STEPS_PER_CYCLE: int = 10
MAX_ACTIVE_SESSIONS: int = 64
MAX_RESULT_HISTORY: int = 256
MAX_RECORDED_TOOL_EVENTS: int = 1000
MAX_TOOL_PARAMETER_BYTES: int = 16384  # 16 KiB
MAX_TOOL_RESULT_BYTES: int = 32768     # 32 KiB
MAX_TOOL_PAYLOAD_BYTES: int = 65536    # 64 KiB

TOOL_ID_REGEX = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
VERSION_REGEX = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class ToolCategory(str, enum.Enum):
    """Categorical classification of safe clinical tools."""

    QUERY_ANATOMY = "QUERY_ANATOMY"
    MEASURE_DISTANCE = "MEASURE_DISTANCE"
    HIGHLIGHT_STRUCTURE = "HIGHLIGHT_STRUCTURE"
    ADJUST_VIEWPORT = "ADJUST_VIEWPORT"
    CAPTURE_TELEMETRY = "CAPTURE_TELEMETRY"


class ToolSafetyClassification(str, enum.Enum):
    """Safety grading of allowable in-process tool operations."""

    READ_ONLY_INFORMATIVE = "READ_ONLY_INFORMATIVE"
    VISUALIZATION_ADJUSTMENT = "VISUALIZATION_ADJUSTMENT"
    TELEMETRY_RECORDING = "TELEMETRY_RECORDING"


class ToolExecutionStatus(str, enum.Enum):
    """Outcome status of deterministic tool invocation."""

    SUCCESS = "SUCCESS"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    RECURSION_DEPTH_EXCEEDED = "RECURSION_DEPTH_EXCEEDED"
    CYCLE_DETECTED = "CYCLE_DETECTED"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    BLOCKED_UNSAFE = "BLOCKED_UNSAFE"


@dataclass(frozen=True)
class ToolInvocationContext:
    """Immutable execution context for a single tool call."""

    invocation_id: str
    tool_id: str
    epoch_id: int
    session_id: str
    sequence_number: int
    depth: int
    parameters: Mapping[str, Any]
    correlation_id: str
    causation_id: Optional[str]
    timestamp_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.invocation_id, str) or not self.invocation_id.strip():
            raise ToolValidationError("invocation_id must be non-empty string")
        if not isinstance(self.tool_id, str) or not TOOL_ID_REGEX.match(self.tool_id):
            raise ToolValidationError(f"Invalid tool_id format: {self.tool_id!r}")
        if not isinstance(self.epoch_id, int) or self.epoch_id < 0:
            raise ToolValidationError(f"epoch_id must be non-negative integer, got {self.epoch_id}")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ToolValidationError("session_id must be non-empty string")
        if not isinstance(self.sequence_number, int) or not (0 <= self.sequence_number <= (2**63 - 1)):
            raise ToolValidationError(
                f"sequence_number must be in [0, 2^63 - 1], got {self.sequence_number}"
            )
        if not isinstance(self.depth, int) or self.depth < 1:
            raise ToolValidationError(f"depth must be positive integer >= 1, got {self.depth}")

        if not isinstance(self.parameters, MappingProxyType):
            object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


@dataclass(frozen=True)
class ToolResult:
    """Immutable result of a tool execution."""

    invocation_id: str
    tool_id: str
    status: ToolExecutionStatus
    result_payload: Mapping[str, Any]
    execution_time_ms: float
    confidence: float
    uncertainty_metric: float
    epoch_id: int
    session_id: str = "default_session"
    is_simulated: bool = True
    diagnostic_message: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.invocation_id, str) or not self.invocation_id.strip():
            raise ToolValidationError("invocation_id must be non-empty")
        if not isinstance(self.tool_id, str) or not self.tool_id.strip():
            raise ToolValidationError("tool_id must be non-empty")
        if not isinstance(self.status, ToolExecutionStatus):
            raise ToolValidationError(f"Invalid status: {self.status}")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ToolValidationError("session_id must be non-empty string")

        for val, name in (
            (self.execution_time_ms, "execution_time_ms"),
            (self.confidence, "confidence"),
            (self.uncertainty_metric, "uncertainty_metric"),
        ):
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise ToolValidationError(f"{name} must be finite float, got {val!r}")

        if not (0.0 <= self.confidence <= 1.0):
            raise ToolValidationError(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        if not (0.0 <= self.uncertainty_metric <= 1.0):
            raise ToolValidationError(
                f"uncertainty_metric must be in [0.0, 1.0], got {self.uncertainty_metric}"
            )

        object.__setattr__(self, "execution_time_ms", round(float(self.execution_time_ms), 4))
        object.__setattr__(self, "confidence", round(float(self.confidence), 4))
        object.__setattr__(self, "uncertainty_metric", round(float(self.uncertainty_metric), 4))

        if not isinstance(self.result_payload, MappingProxyType):
            object.__setattr__(self, "result_payload", MappingProxyType(dict(self.result_payload)))


@dataclass(frozen=True)
class ToolDescriptor:
    """Immutable descriptor defining tool contract and handler."""

    tool_id: str
    name: str
    version: str
    category: ToolCategory
    safety_classification: ToolSafetyClassification
    description: str
    required_parameters: tuple[str, ...]
    optional_parameters: tuple[str, ...]
    max_execution_budget_ms: float
    is_idempotent: bool
    handler: Callable[[ToolInvocationContext], ToolResult]

    def __post_init__(self) -> None:
        if not isinstance(self.tool_id, str) or not TOOL_ID_REGEX.match(self.tool_id):
            raise ToolValidationError(f"Invalid tool_id format: {self.tool_id!r}")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ToolValidationError("name must be non-empty string")
        if not isinstance(self.version, str) or not VERSION_REGEX.match(self.version):
            raise ToolValidationError(f"Invalid version format: {self.version!r}")
        if not isinstance(self.category, ToolCategory):
            raise ToolSafetyError(f"Category {self.category!r} is not a recognized safe ToolCategory")
        if not isinstance(self.safety_classification, ToolSafetyClassification):
            raise ToolSafetyError(f"Invalid safety classification: {self.safety_classification!r}")
        if not isinstance(self.max_execution_budget_ms, (int, float)) or not (
            0.0 < self.max_execution_budget_ms <= 50.0
        ):
            raise ToolValidationError(
                f"max_execution_budget_ms must be in (0.0, 50.0], got {self.max_execution_budget_ms}"
            )
        if not callable(self.handler):
            raise ToolValidationError("handler must be a callable")

        object.__setattr__(self, "required_parameters", tuple(self.required_parameters))
        object.__setattr__(self, "optional_parameters", tuple(self.optional_parameters))
