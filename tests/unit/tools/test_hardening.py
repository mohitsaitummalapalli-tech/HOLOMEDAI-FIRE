# -*- coding: utf-8 -*-
"""Adversarial and Hardening Tests for M07 Tool Orchestration Subsystem."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.service import ServiceState
from holomed.tools.exceptions import (
    ToolLifecycleError,
    ToolSecurityError,
    ToolShutdownError,
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
from holomed.tools.validation import validate_tool_parameters


def test_d240_sandbox_security_primitive_audit() -> None:
    """Verify D240 security boundary catches execution injection attempts."""
    desc = ToolDescriptor(
        tool_id="test.sandbox",
        name="Sandbox",
        version="1.0",
        category=ToolCategory.QUERY_ANATOMY,
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        description="desc",
        required_parameters=(),
        optional_parameters=("cmd", "code"),
        max_execution_budget_ms=10.0,
        is_idempotent=True,
        handler=lambda ctx: ToolResult(ctx.invocation_id, "test.sandbox", ToolExecutionStatus.SUCCESS, {}, 1.0, 1.0, 0.0, 1),
    )

    with pytest.raises(ToolSecurityError):
        validate_tool_parameters({"cmd": "subprocess.Popen(['ls'])"}, desc)

    with pytest.raises(ToolSecurityError):
        validate_tool_parameters({"code": "eval('2 + 2')"}, desc)

    with pytest.raises(ToolSecurityError):
        validate_tool_parameters({"code": "__import__('os').system('ls')"}, desc)


def test_d244_registration_while_started_rejected(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify D244 registering tool while service is STARTED raises ToolLifecycleError."""
    srv = ToolService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.start()

    late_tool = ToolDescriptor(
        tool_id="late.tool",
        name="Late",
        version="1.0",
        category=ToolCategory.QUERY_ANATOMY,
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        description="desc",
        required_parameters=(),
        optional_parameters=(),
        max_execution_budget_ms=10.0,
        is_idempotent=True,
        handler=lambda ctx: ToolResult(ctx.invocation_id, "late.tool", ToolExecutionStatus.SUCCESS, {}, 1.0, 1.0, 0.0, 1),
    )

    with pytest.raises(ToolLifecycleError):
        srv.register_tool(late_tool)

    srv.stop()


def test_tool_shutdown_failure_aggregation(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify ToolShutdownError aggregates unreleased handle failures cleanly."""
    srv = ToolService(
        device_manager=device_manager,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.start()

    # Monkeypatch release to fail on a handle
    orig_release = srv.resources.release

    def faulty_release(res_id: str) -> None:
        if res_id == "tools.executor":
            raise RuntimeError(f"Simulated failure with secret SECRET_TOOL_TOKEN_999")
        orig_release(res_id)

    monkeypatch.setattr(srv.resources, "release", faulty_release)

    with pytest.raises(ToolShutdownError) as exc_info:
        srv.stop()

    assert srv.state == ServiceState.FAILED
    assert "SECRET_TOOL_TOKEN_999" not in str(exc_info.value)
