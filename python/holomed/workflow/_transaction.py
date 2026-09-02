# -*- coding: utf-8 -*-
"""Internal M10 Transaction Capability for Recovery Resumption.

This module provides an internal, unexported object-capability ensuring that
workflow state resumption (RECOVERY_REQUIRED -> NAVIGATION) and recovery interlock
clearance can only be performed within an authoritative, active WorkflowService
transaction.
"""

from __future__ import annotations

import uuid
from typing import Any

from holomed.workflow.exceptions import (
    WorkflowAuthorizationError,
    WorkflowValidationError,
)

# Unexported sentinel key known only to this module and WorkflowService
_INTERNAL_CAPABILITY_KEY = object()


class _RecoveryTransactionCapability:
    """Non-reusable internal capability for synchronous M10 recovery transactions.

    Invariants:
    - Unexported from holomed.workflow.
    - Not constructible through public APIs without the internal module key.
    - Not serializable or replayable.
    - Bound to service instance, session_id, workflow_id, recovery_revision, and sequence_number.
    - Strictly single-use: invalidated immediately upon commit, abort, or transaction exit.
    """

    def __init__(
        self,
        internal_key: Any,
        service_instance_id: int,
        session_id: str,
        workflow_id: str,
        recovery_revision: int,
        sequence_number: int,
    ) -> None:
        if internal_key is not _INTERNAL_CAPABILITY_KEY:
            raise WorkflowAuthorizationError(
                "Direct external construction of _RecoveryTransactionCapability is strictly prohibited"
            )
        if not isinstance(service_instance_id, int):
            raise WorkflowValidationError("service_instance_id must be an integer")
        if not isinstance(session_id, str) or not session_id.strip():
            raise WorkflowValidationError("session_id must be a non-empty string")
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise WorkflowValidationError("workflow_id must be a non-empty string")
        if not isinstance(recovery_revision, int) or recovery_revision < 1:
            raise WorkflowValidationError("recovery_revision must be an integer >= 1")
        if not isinstance(sequence_number, int) or sequence_number < 1:
            raise WorkflowValidationError("sequence_number must be an integer >= 1")

        self._service_instance_id = service_instance_id
        self._session_id = session_id
        self._workflow_id = workflow_id
        self._recovery_revision = recovery_revision
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
    def workflow_id(self) -> str:
        return self._workflow_id

    @property
    def recovery_revision(self) -> int:
        return self._recovery_revision

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
        raise TypeError("_RecoveryTransactionCapability cannot be serialized")

    def __setstate__(self, state: dict[str, Any]) -> None:
        raise TypeError("_RecoveryTransactionCapability cannot be deserialized")

    def __repr__(self) -> str:
        return f"<_RecoveryTransactionCapability tx={self._transaction_id} active={self._is_active}>"


def _create_recovery_capability(
    service_instance_id: int,
    session_id: str,
    workflow_id: str,
    recovery_revision: int,
    sequence_number: int,
) -> _RecoveryTransactionCapability:
    """Internal factory called only by WorkflowService."""
    return _RecoveryTransactionCapability(
        internal_key=_INTERNAL_CAPABILITY_KEY,
        service_instance_id=service_instance_id,
        session_id=session_id,
        workflow_id=workflow_id,
        recovery_revision=recovery_revision,
        sequence_number=sequence_number,
    )
