# -*- coding: utf-8 -*-
"""M07 Tool Orchestration Test Fixtures."""

from __future__ import annotations

import pytest

from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.tools.models import (
    ToolCategory,
    ToolDescriptor,
    ToolExecutionStatus,
    ToolInvocationContext,
    ToolResult,
    ToolSafetyClassification,
)


@pytest.fixture
def runtime_context() -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed-Tools-Test",
        environment="TESTING",
        host="127.0.0.1",
        port=8000,
        log_level="DEBUG",
        gemini_api_key=None,
        protocol_version="1.0",
    )
    return RuntimeContext(app_config=config, epoch_id=1)


@pytest.fixture
def secret_filter() -> SecretFilter:
    return SecretFilter(secrets=["SECRET_TOOL_TOKEN_999"])


@pytest.fixture
def message_dispatcher(runtime_context: RuntimeContext) -> MessageDispatcher:
    disp = MessageDispatcher()
    disp.initialize(runtime_context)
    return disp


@pytest.fixture
def device_manager(runtime_context: RuntimeContext) -> DeviceManager:
    dm = DeviceManager()
    dm.initialize(runtime_context)
    dm.start()
    return dm


def dummy_query_handler(ctx: ToolInvocationContext) -> ToolResult:
    return ToolResult(
        invocation_id=ctx.invocation_id,
        tool_id=ctx.tool_id,
        status=ToolExecutionStatus.SUCCESS,
        result_payload={"organ_found": True, "entity_id": ctx.parameters.get("entity_id")},
        execution_time_ms=1.5,
        confidence=1.0,
        uncertainty_metric=0.0,
        epoch_id=ctx.epoch_id,
        is_simulated=True,
    )


@pytest.fixture
def sample_query_tool() -> ToolDescriptor:
    return ToolDescriptor(
        tool_id="anatomy.query_organ",
        name="Query Organ Entity",
        version="1.0",
        category=ToolCategory.QUERY_ANATOMY,
        safety_classification=ToolSafetyClassification.READ_ONLY_INFORMATIVE,
        description="Queries anatomical entities by identifier",
        required_parameters=("entity_id",),
        optional_parameters=("detail_level",),
        max_execution_budget_ms=20.0,
        is_idempotent=True,
        handler=dummy_query_handler,
    )
