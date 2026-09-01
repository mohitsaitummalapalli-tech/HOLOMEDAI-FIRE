# -*- coding: utf-8 -*-
"""Adversarial and Hardening Tests for M08 Platform Supervisor."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.platform.exceptions import (
    PlatformLifecycleError,
    PlatformShutdownError,
)
from holomed.platform.service import PlatformService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.service import IService, ServiceState


def test_platform_lifecycle_edge_cases(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    mock_domain_services: dict[str, IService],
) -> None:
    """Verify stop-before-init, double-start, and double-stop."""
    srv = PlatformService(
        services=mock_domain_services,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    # Stop before init is safe no-op
    srv.stop()
    assert srv.state in (ServiceState.UNINITIALIZED, ServiceState.STOPPED)

    srv.initialize(runtime_context)
    srv.start()

    # Double start raises PlatformLifecycleError
    with pytest.raises(PlatformLifecycleError):
        srv.start()

    # First stop
    srv.stop()
    assert srv.state == ServiceState.STOPPED

    # Double stop is safe no-op
    srv.stop()
    assert srv.state == ServiceState.STOPPED


def test_platform_teardown_failure_aggregation(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    mock_domain_services: dict[str, IService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify PlatformShutdownError aggregates teardown errors with secret redaction."""
    srv = PlatformService(
        services=mock_domain_services,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.start()

    orig_release = srv.resources.release

    def failing_release(res_id: str) -> None:
        if res_id == "platform.metrics":
            raise RuntimeError(f"Simulated failure with secret SECRET_PLATFORM_KEY_999")
        orig_release(res_id)

    monkeypatch.setattr(srv.resources, "release", failing_release)

    with pytest.raises(PlatformShutdownError) as exc_info:
        srv.stop()

    assert srv.state == ServiceState.FAILED
    assert "SECRET_PLATFORM_KEY_999" not in str(exc_info.value)
