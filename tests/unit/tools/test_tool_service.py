# -*- coding: utf-8 -*-
"""Unit tests for M31 ToolService dispatcher route hardening and tools.reset removal."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.exceptions import TopicValidationError, UnroutableMessageError
from holomed.devices.manager import DeviceManager
from holomed.execution._capability import _create_execution_capability
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.service import ServiceState
from holomed.tools.models import (
    ToolCategory,
    ToolDescriptor,
    ToolExecutionStatus,
    ToolInvocationContext,
    ToolResult,
    ToolSafetyClassification,
)
from holomed.tools.service import ToolService


def dummy_tool() -> ToolDescriptor:
    return ToolDescriptor(
        tool_id="anatomy.query_organ",
        name="Query Organ",
        version="1.0",
        category=ToolCategory.QUERY_ANATOMY,
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        description="desc",
        required_parameters=("entity_id",),
        optional_parameters=(),
        max_execution_budget_ms=10.0,
        is_idempotent=True,
        handler=lambda ctx: ToolResult(
            ctx.invocation_id,
            "anatomy.query_organ",
            ToolExecutionStatus.SUCCESS,
            {"found": True},
            1.0,
            1.0,
            0.0,
            ctx.epoch_id,
        ),
    )


def test_tools_reset_not_registered_on_dispatcher(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify M31: tools.reset is no longer registered on MessageDispatcher."""
    srv = ToolService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    # Pre-populate session sequences in engine
    assert srv._engine is not None
    srv._engine._session_sequences["session_A"] = 42

    # Attempt to dispatch tools.reset
    cmd = create_command("tools.reset", "operator", payload={"epoch_id": 1})
    with pytest.raises(UnroutableMessageError):
        message_dispatcher.dispatch(cmd)

    # Verify session sequence was NOT cleared
    assert srv._engine._session_sequences.get("session_A") == 42

    srv.stop()


def test_approved_tool_query_routes_remain_registered(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify tools.status, tools.registry, and tools.result remain registered and functional."""
    srv = ToolService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.register_tool(dummy_tool())
    message_dispatcher.start()
    srv.start()

    # 1. tools.status query
    q_status = create_query("tools.status", "client", payload={})
    resp_status = message_dispatcher.dispatch(q_status)
    assert resp_status is not None
    assert resp_status.message_type.value == "RESPONSE"
    assert resp_status.payload["tool_count"] == 1

    # 2. tools.registry query
    q_reg = create_query("tools.registry", "client", payload={})
    resp_reg = message_dispatcher.dispatch(q_reg)
    assert resp_reg is not None
    assert resp_reg.message_type.value == "RESPONSE"
    assert len(resp_reg.payload["tools"]) == 1

    # 3. tools.result query (not found)
    q_res = create_query("tools.result", "client", payload={"invocation_id": "nonexistent"})
    resp_res = message_dispatcher.dispatch(q_res)
    assert resp_res is not None
    assert resp_res.message_type.value == "ERROR"
    assert resp_res.payload["error_code"] == "ERR_RESULT_NOT_FOUND"

    srv.stop()


def test_internal_tool_lifecycle_preserved(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify internal ToolService.evict_session, reset, and clear continue functioning cleanly."""
    srv = ToolService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    assert srv._engine is not None
    srv._engine._session_sequences["session_to_evict"] = 10
    srv._engine._session_sequences["session_to_keep"] = 20

    # 1. evict_session under M29 capability
    cap = _create_execution_capability(
        service_instance_id=id(srv),
        session_id="session_to_evict",
        action="SESSION_TEARDOWN",
        sequence_number=1,
    )
    evicted = srv.evict_session("session_to_evict", cap)
    assert evicted is True
    assert "session_to_evict" not in srv._engine._session_sequences
    assert srv._engine._session_sequences.get("session_to_keep") == 20

    # 2. internal reset for matching epoch
    srv.reset(epoch_id=1)
    assert srv._epoch_id == 1
    assert len(srv._engine._session_sequences) == 0

    srv.stop()
