# -*- coding: utf-8 -*-
"""Synchronous Deterministic Tool Execution Engine for M07."""

from __future__ import annotations

import json
import time
from types import MappingProxyType
from typing import Mapping, Optional

from holomed.tools.exceptions import (
    ToolCapacityError,
    ToolEpochMismatchError,
    ToolRecursionError,
    ToolSecurityError,
    ToolSequenceError,
    ToolValidationError,
)
from holomed.tools.models import (
    MAX_ACTIVE_SESSIONS,
    MAX_RESULT_HISTORY,
    MAX_TOOL_INVOCATION_DEPTH,
    MAX_TOOL_RESULT_BYTES,
    MAX_TOOL_STEPS_PER_CYCLE,
    ToolExecutionStatus,
    ToolInvocationContext,
    ToolResult,
)
from holomed.tools.registry import ToolRegistry
from holomed.tools.validation import validate_tool_parameters


class ToolExecutionEngine:
    """Synchronous in-process tool executor enforcing call depth, sequences, and budgets."""

    def __init__(self, epoch_id: int = 0) -> None:
        self._epoch_id = epoch_id
        self._session_sequences: dict[str, int] = {}
        self._result_history: list[ToolResult] = []

    @property
    def result_history(self) -> tuple[ToolResult, ...]:
        return tuple(self._result_history)

    def execute_invocation(
        self,
        context: ToolInvocationContext,
        registry: ToolRegistry,
        active_chain: tuple[str, ...] = (),
        step_count: int = 0,
    ) -> ToolResult:
        """Execute a tool invocation context through all security, sequence, and recursion gates."""
        # 1. Epoch Isolation
        if context.epoch_id != self._epoch_id:
            raise ToolEpochMismatchError(
                f"Invocation epoch {context.epoch_id} does not match engine epoch {self._epoch_id}"
            )

        # 2. Session Sequence Monotonicity
        if context.session_id not in self._session_sequences:
            if len(self._session_sequences) >= MAX_ACTIVE_SESSIONS:
                raise ToolCapacityError(f"Active session capacity exceeded ({MAX_ACTIVE_SESSIONS} max)")
            self._session_sequences[context.session_id] = -1

        last_seq = self._session_sequences[context.session_id]
        if context.sequence_number <= last_seq:
            raise ToolSequenceError(
                f"Non-monotonic sequence number {context.sequence_number} <= last seen {last_seq} "
                f"for session {context.session_id!r}"
            )
        self._session_sequences[context.session_id] = context.sequence_number

        # 3. Recursion & Cycle Guards
        if context.depth > MAX_TOOL_INVOCATION_DEPTH or step_count >= MAX_TOOL_STEPS_PER_CYCLE:
            res = ToolResult(
                invocation_id=context.invocation_id,
                tool_id=context.tool_id,
                status=ToolExecutionStatus.RECURSION_DEPTH_EXCEEDED,
                result_payload={},
                execution_time_ms=0.0,
                confidence=0.0,
                uncertainty_metric=1.0,
                epoch_id=self._epoch_id,
                is_simulated=True,
                diagnostic_message=f"Invocation depth {context.depth} or step count {step_count} exceeded bounds",
            )
            self._record_result(res)
            return res

        if context.tool_id in active_chain:
            res = ToolResult(
                invocation_id=context.invocation_id,
                tool_id=context.tool_id,
                status=ToolExecutionStatus.CYCLE_DETECTED,
                result_payload={},
                execution_time_ms=0.0,
                confidence=0.0,
                uncertainty_metric=1.0,
                epoch_id=self._epoch_id,
                is_simulated=True,
                diagnostic_message=f"Recursive cycle detected involving tool {context.tool_id!r}",
            )
            self._record_result(res)
            return res

        # 4. Tool Descriptor Lookup
        if not registry.has_tool(context.tool_id):
            raise ToolValidationError(f"Tool {context.tool_id!r} is not registered in catalog")
        descriptor = registry.get_tool(context.tool_id)

        # 5. Parameter Validation
        validate_tool_parameters(context.parameters, descriptor)

        # 6. Synchronous In-Process Execution with Observational Timing
        start_time = time.perf_counter()
        try:
            handler_result = descriptor.handler(context)
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            # 7. Validate Result Payload Size (32 KiB)
            raw_res_bytes = json.dumps(dict(handler_result.result_payload), sort_keys=True, ensure_ascii=False).encode("utf-8")
            if len(raw_res_bytes) > MAX_TOOL_RESULT_BYTES:
                raise ToolCapacityError(
                    f"Result payload size ({len(raw_res_bytes)} bytes) exceeds limit of {MAX_TOOL_RESULT_BYTES} bytes"
                )

            final_result = ToolResult(
                invocation_id=context.invocation_id,
                tool_id=context.tool_id,
                status=handler_result.status,
                result_payload=handler_result.result_payload,
                execution_time_ms=elapsed_ms,
                confidence=handler_result.confidence,
                uncertainty_metric=handler_result.uncertainty_metric,
                epoch_id=self._epoch_id,
                is_simulated=handler_result.is_simulated,
                diagnostic_message=handler_result.diagnostic_message,
            )
            self._record_result(final_result)
            return final_result

        except (ToolCapacityError, ToolSecurityError, ToolSequenceError, ToolEpochMismatchError):
            raise
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            error_res = ToolResult(
                invocation_id=context.invocation_id,
                tool_id=context.tool_id,
                status=ToolExecutionStatus.EXECUTION_ERROR,
                result_payload={},
                execution_time_ms=elapsed_ms,
                confidence=0.0,
                uncertainty_metric=1.0,
                epoch_id=self._epoch_id,
                is_simulated=True,
                diagnostic_message=f"Handler raised {type(e).__name__}",
            )
            self._record_result(error_res)
            return error_res

    def _record_result(self, result: ToolResult) -> None:
        """Store tool result in bounded ring buffer."""
        if len(self._result_history) >= MAX_RESULT_HISTORY:
            self._result_history.pop(0)
        self._result_history.append(result)

    def evict_session(self, session_id: str) -> bool:
        """Evict session sequence tracking state, releasing capacity (M29).

        Surgically removes session_id from _session_sequences without altering
        other active sessions or global result history. Returns True if evicted,
        False if session_id was not registered.
        """
        if session_id in self._session_sequences:
            del self._session_sequences[session_id]
            return True
        return False

    def reset(self, epoch_id: int) -> None:
        """Reset execution state for a new epoch."""
        self._epoch_id = epoch_id
        self.clear()

    def clear(self) -> None:
        """Clear all active sessions and result history."""
        self._session_sequences.clear()
        self._result_history.clear()
