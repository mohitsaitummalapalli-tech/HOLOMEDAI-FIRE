# -*- coding: utf-8 -*-
"""Internal Gateway Capability for Query Authorization.

This module provides an internal, unexported object-capability ensuring that
external queries can only be dispatched by an authoritative Gateway session.
"""

from __future__ import annotations

import uuid
from typing import Any

from holomed.gateway.exceptions import (
    GatewayAuthorizationError,
    GatewayValidationError,
)

# Unexported sentinel key known strictly to this module and GatewayService
_INTERNAL_GATEWAY_KEY = object()


class _GatewayCapability:
    """Non-reusable internal capability for gateway queries.

    Invariants:
    - Unexported from holomed.gateway.__all__.
    - Not constructible through public APIs without the internal module key.
    - Not serializable or replayable.
    - Bound to service instance, session_id, and action.
    - Strictly single-use: invalidated immediately upon consumption at dispatcher gate.
    """

    def __init__(
        self,
        internal_key: Any,
        service_instance_id: int,
        session_id: str,
        action: str,
    ) -> None:
        if internal_key is not _INTERNAL_GATEWAY_KEY:
            raise GatewayAuthorizationError(
                "Direct external construction of _GatewayCapability is strictly prohibited"
            )
        if not isinstance(service_instance_id, int):
            raise GatewayValidationError("service_instance_id must be an integer")
        if not isinstance(session_id, str) or not session_id.strip():
            raise GatewayValidationError("session_id must be a non-empty string")
        if not isinstance(action, str) or not action.strip():
            raise GatewayValidationError("action must be a non-empty string")

        self._service_instance_id = service_instance_id
        self._session_id = session_id
        self._action = action
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
    def transaction_id(self) -> str:
        return self._transaction_id

    @property
    def is_active(self) -> bool:
        return self._is_active

    def invalidate(self) -> None:
        """Permanently invalidate this capability, rendering it non-reusable."""
        self._is_active = False

    def __getstate__(self) -> dict[str, Any]:
        raise TypeError("_GatewayCapability cannot be serialized")

    def __setstate__(self, state: dict[str, Any]) -> None:
        raise TypeError("_GatewayCapability cannot be serialized")

    def __repr__(self) -> str:
        return (
            f"<_GatewayCapability active={self._is_active} "
            f"session={self._session_id!r} action={self._action!r} "
            f"txn={self._transaction_id}>"
        )
