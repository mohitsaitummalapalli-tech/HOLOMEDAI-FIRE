# -*- coding: utf-8 -*-
"""Test Fixtures for M11 Gateway Test Suite."""

from __future__ import annotations

import pytest

from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter


@pytest.fixture
def runtime_context() -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed-Gateway-Test",
        environment="TESTING",
        host="127.0.0.1",
        port=8088,
        log_level="DEBUG",
        gemini_api_key=None,
        protocol_version="1.0",
    )
    return RuntimeContext(app_config=config, epoch_id=1)


@pytest.fixture
def secret_filter() -> SecretFilter:
    return SecretFilter(secrets=["TOP_SECRET_GATEWAY_CREDENTIAL"])


@pytest.fixture
def message_dispatcher(runtime_context: RuntimeContext) -> MessageDispatcher:
    disp = MessageDispatcher()
    disp.initialize(runtime_context)
    return disp
