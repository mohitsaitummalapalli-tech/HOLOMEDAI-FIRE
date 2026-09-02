# -*- coding: utf-8 -*-
"""Unit Tests for _ExecutionCapability and Anti-Replay Invariants."""

import pickle
import pytest

from holomed.execution._capability import (
    _ExecutionCapability,
    _create_execution_capability,
)
from holomed.execution.exceptions import (
    ExecutionAuthorizationError,
    ExecutionValidationError,
)


def test_capability_creation_via_internal_factory() -> None:
    cap = _create_execution_capability(
        service_instance_id=12345,
        session_id="session-01",
        action="TOOL_INVOCATION",
        sequence_number=1,
    )
    assert cap.is_active is True
    assert cap.service_instance_id == 12345
    assert cap.session_id == "session-01"
    assert cap.action == "TOOL_INVOCATION"
    assert cap.sequence_number == 1
    assert isinstance(cap.transaction_id, str) and len(cap.transaction_id) > 0


def test_direct_external_construction_rejected() -> None:
    with pytest.raises(ExecutionAuthorizationError, match="Direct external construction"):
        _ExecutionCapability(
            internal_key="unauthorized",
            service_instance_id=12345,
            session_id="session-01",
            action="TOOL_INVOCATION",
            sequence_number=1,
        )


def test_capability_validation_guards() -> None:
    with pytest.raises(ExecutionValidationError):
        _create_execution_capability(12345, "", "TOOL_INVOCATION", 1)
    with pytest.raises(ExecutionValidationError):
        _create_execution_capability(12345, "session-01", "", 1)
    with pytest.raises(ExecutionValidationError):
        _create_execution_capability(12345, "session-01", "TOOL_INVOCATION", -1)


def test_capability_invalidation_and_single_use() -> None:
    cap = _create_execution_capability(12345, "session-01", "TOOL_INVOCATION", 1)
    assert cap.is_active is True
    cap.invalidate()
    assert cap.is_active is False


def test_capability_non_serializable() -> None:
    cap = _create_execution_capability(12345, "session-01", "TOOL_INVOCATION", 1)
    with pytest.raises(TypeError, match="_ExecutionCapability cannot be serialized"):
        pickle.dumps(cap)
