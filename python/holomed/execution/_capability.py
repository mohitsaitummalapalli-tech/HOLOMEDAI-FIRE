# -*- coding: utf-8 -*-
"""Internal Execution Capability for Clinical Execution Gateway.

This module provides an internal, unexported object-capability ensuring that
underlying execution primitives (NavigationService.submit_pose, NavigationService.evaluate,
and ToolService.invoke_tool) can only execute within an active, authoritative
ClinicalExecutionGatewayService transaction.
"""

from __future__ import annotations

import uuid
from typing import Any

from holomed.execution.exceptions import (
    ExecutionAuthorizationError,
    ExecutionValidationError,
)

# Unexported sentinel key known strictly to this module and ClinicalExecutionGatewayService
_INTERNAL_EXECUTION_KEY = object()


class _ExecutionCapability:
    """Non-reusable internal capability for synchronous clinical execution transactions.

    Invariants:
    - Unexported from holomed.execution.__all__.
    - Not constructible through public APIs without the internal module key.
    - Not serializable or replayable.
    - Bound to service instance, session_id, action, sequence_number, and transaction_id.
    - Strictly single-use: invalidated immediately upon commit, abort, or transaction exit.
    """

    def __init__(
        self,
        internal_key: Any,
        service_instance_id: int,
        session_id: str,
        action: str,
        sequence_number: int,
    ) -> None:
        if internal_key is not _INTERNAL_EXECUTION_KEY:
            raise ExecutionAuthorizationError(
                "Direct external construction of _ExecutionCapability is strictly prohibited"
            )
        if not isinstance(service_instance_id, int):
            raise ExecutionValidationError("service_instance_id must be an integer")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ExecutionValidationError("session_id must be a non-empty string")
        if not isinstance(action, str) or not action.strip():
            raise ExecutionValidationError("action must be a non-empty string")
        if not isinstance(sequence_number, int) or sequence_number < 0:
            raise ExecutionValidationError("sequence_number must be an integer >= 0")

        self._service_instance_id = service_instance_id
        self._session_id = session_id
        self._action = action
        self._sequence_number = sequence_number
        self._transaction_id = str(uuid.uuid4())
        self._is_active = True

    @property
    def service_instance_id(self) -> int:
        return self._service_instance_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def action(self) -> str:
        return self._action

    @property
    def sequence_number(self) -> int:
        return self._sequence_number

    @property
    def transaction_id(self) -> str:
        return self._transaction_id

    @property
    def is_active(self) -> bool:
        return self._is_active

    def invalidate(self) -> None:
        """Permanently invalidate this capability, rendering it non-reusable."""
        self._is_active = False

    def __getstate__(self) -> dict[str, Any]:
        raise TypeError("_ExecutionCapability cannot be serialized")

    def __setstate__(self, state: dict[str, Any]) -> None:
        raise TypeError("_ExecutionCapability cannot be serialized")

    def __repr__(self) -> str:
        return (
            f"<_ExecutionCapability active={self._is_active} "
            f"session={self._session_id!r} action={self._action!r} "
            f"seq={self._sequence_number} tx={self._transaction_id}>"
        )


def _create_execution_capability(
    service_instance_id: int,
    session_id: str,
    action: str,
    sequence_number: int,
) -> _ExecutionCapability:
    """Internal factory called strictly by ClinicalExecutionGatewayService."""
    return _ExecutionCapability(
        internal_key=_INTERNAL_EXECUTION_KEY,
        service_instance_id=service_instance_id,
        session_id=session_id,
        action=action,
        sequence_number=sequence_number,
    )
