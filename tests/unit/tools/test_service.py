# -*- coding: utf-8 -*-
"""Unit tests for ToolService Lifecycle, Dispatcher Routes, and Epoch Isolation (D243, D248)."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import ServiceState
from holomed.tools.exceptions import (
    ToolAuthorizationError,
    ToolEpochMismatchError,
    ToolLifecycleError,
)
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


def test_tool_service_lifecycle_and_resources(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify ToolService lifecycle and exact acquisition of 5 structural handles."""
    srv = ToolService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    assert srv.state == ServiceState.UNINITIALIZED

    srv.initialize(runtime_context)
    assert srv.state == ServiceState.INITIALIZED
    assert len(srv.resources.outstanding_handles) == 5

    # Register tool before start
    srv.register_tool(dummy_tool())

    srv.start()
    assert srv.state == ServiceState.STARTED
    assert srv.registry is not None and srv.registry.is_locked is True

    # Health check
    h = srv.health()
    assert h.status == HealthStatus.HEALTHY

    srv.stop()
    assert srv.state == ServiceState.STOPPED
    assert len(srv.resources.outstanding_handles) == 0


def test_d243_epoch_isolation_and_reset(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify D243 cross-epoch state clearing and epoch mismatch rejection."""
    srv = ToolService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.register_tool(dummy_tool())
    srv.start()

    ctx = ToolInvocationContext(
        invocation_id="inv_1",
        tool_id="anatomy.query_organ",
        epoch_id=runtime_context.epoch_id,
        session_id="sess_0",
        sequence_number=0,
        depth=1,
        parameters={"entity_id": "liver"},
        correlation_id="c",
        causation_id=None,
        timestamp_utc="ts",
    )
    # Verify direct call without capability fails closed
    with pytest.raises(ToolAuthorizationError):
        srv.invoke_tool(ctx)

    from holomed.execution._capability import _create_execution_capability
    cap = _create_execution_capability(
        service_instance_id=id(srv),
        session_id=ctx.session_id,
        action="TOOL_INVOCATION",
        sequence_number=ctx.sequence_number,
    )
    res = srv.invoke_tool(ctx, cap)
    assert res.status == ToolExecutionStatus.SUCCESS

    # Reset with wrong epoch raises ToolEpochMismatchError
    with pytest.raises(ToolEpochMismatchError):
        srv.reset(epoch_id=999)

    # Valid reset clears execution state
    srv.reset(epoch_id=runtime_context.epoch_id)
    assert srv.engine is not None and len(srv.engine.result_history) == 0

    srv.stop()


def test_d248_reentrancy_protection(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify D248 reentrant mutations are rejected by transaction guard."""
    srv = ToolService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.start()

    srv._in_transaction = True
    ctx = ToolInvocationContext(
        invocation_id="inv_1",
        tool_id="anatomy.query_organ",
        epoch_id=runtime_context.epoch_id,
        session_id="sess_0",
        sequence_number=0,
        depth=1,
        parameters={},
        correlation_id="c",
        causation_id=None,
        timestamp_utc="ts",
    )
    from holomed.execution._capability import _create_execution_capability
    cap = _create_execution_capability(
        service_instance_id=id(srv),
        session_id=ctx.session_id,
        action="TOOL_INVOCATION",
        sequence_number=ctx.sequence_number,
    )
    with pytest.raises(ToolLifecycleError):
        srv.invoke_tool(ctx, cap)

    srv._in_transaction = False
    srv.stop()


def test_dispatcher_routes_query_and_invoke_removed(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify dispatcher queries route cleanly and tools.invoke command is removed."""
    srv = ToolService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.register_tool(dummy_tool())
    message_dispatcher.start()
    srv.start()

    # Query tools.status
    q_status = create_query("tools.status", "test_client")
    resp_status = message_dispatcher.dispatch(q_status)
    assert resp_status.message_type.value == "RESPONSE"
    assert resp_status.payload["tool_count"] == 1

    # Query tools.registry
    q_reg = create_query("tools.registry", "test_client")
    resp_reg = message_dispatcher.dispatch(q_reg)
    assert resp_reg.message_type.value == "RESPONSE"
    assert len(resp_reg.payload["tools"]) == 1

    # In M21: tools.invoke is REMOVED from dispatcher, must raise UnroutableMessageError
    from holomed.core.exceptions import UnroutableMessageError
    cmd_invoke = create_command(
        "tools.invoke",
        "test_client",
        payload={
            "tool_id": "anatomy.query_organ",
            "parameters": {"entity_id": "heart"},
            "session_id": "sess_disp",
            "sequence_number": 0,
        },
    )
    with pytest.raises(UnroutableMessageError, match="tools.invoke"):
        message_dispatcher.dispatch(cmd_invoke)

    srv.stop()
