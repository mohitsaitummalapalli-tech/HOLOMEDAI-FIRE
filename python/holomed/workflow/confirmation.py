# -*- coding: utf-8 -*-
"""Human-in-the-Loop Confirmation Management for M10."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
import uuid

from holomed.workflow.exceptions import (
    WorkflowCapacityError,
    WorkflowConfirmationError,
    WorkflowEpochMismatchError,
    WorkflowSessionError,
)
from holomed.workflow.models import (
    MAX_PENDING_CONFIRMATIONS,
    ConfirmationRequest,
    ConfirmationResponse,
    WorkflowPhase,
)


class ConfirmationManager:
    """Manages explicit human approval requests and responses."""

    def __init__(self, session_id: str, epoch_id: int) -> None:
        self._session_id = session_id
        self._epoch_id = epoch_id
        self._pending: dict[str, ConfirmationRequest] = {}
        self._resolved: dict[str, tuple[ConfirmationRequest, ConfirmationResponse]] = {}

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def request_confirmation(
        self,
        workflow_id: str,
        target_phase: WorkflowPhase,
        rationale: str,
        sequence_number: int,
    ) -> ConfirmationRequest:
        """Create and register a pending human confirmation request."""
        if len(self._pending) >= MAX_PENDING_CONFIRMATIONS:
            raise WorkflowCapacityError(
                f"Pending confirmation limit ({MAX_PENDING_CONFIRMATIONS}) reached"
            )

        cid = str(uuid.uuid4())
        now_utc = datetime.now(timezone.utc).isoformat()
        req = ConfirmationRequest(
            confirmation_id=cid,
            session_id=self._session_id,
            epoch_id=self._epoch_id,
            workflow_id=workflow_id,
            target_phase=target_phase,
            rationale=rationale,
            timestamp_utc=now_utc,
            sequence_number=sequence_number,
        )
        self._pending[cid] = req
        return req

    def resolve_confirmation(
        self,
        response: ConfirmationResponse,
    ) -> ConfirmationRequest:
        """Validate operator response and resolve pending request."""
        if response.epoch_id != self._epoch_id:
            raise WorkflowEpochMismatchError(
                f"Response epoch {response.epoch_id} does not match active epoch {self._epoch_id}"
            )
        if response.session_id != self._session_id:
            raise WorkflowSessionError(
                f"Response session {response.session_id} does not match active session {self._session_id}"
            )
        if response.confirmation_id not in self._pending:
            raise WorkflowConfirmationError(
                f"Unknown or already resolved confirmation_id: {response.confirmation_id}"
            )

        req = self._pending.pop(response.confirmation_id)
        self._resolved[response.confirmation_id] = (req, response)
        return req

    def is_phase_confirmed(self, target_phase: WorkflowPhase) -> bool:
        """Check if target_phase has an approved, non-rejected confirmation."""
        return any(
            req.target_phase == target_phase and resp.approved
            for req, resp in self._resolved.values()
        )

    def get_pending(self, confirmation_id: str) -> Optional[ConfirmationRequest]:
        return self._pending.get(confirmation_id)

    def clear(self) -> None:
        self._pending.clear()
        self._resolved.clear()
