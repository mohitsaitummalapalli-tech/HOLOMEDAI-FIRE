# -*- coding: utf-8 -*-
"""Unit tests for Fail-Stop Epoch Migration Protocol (D250)."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.platform.exceptions import (
    PlatformEpochMigrationError,
    PlatformEpochMismatchError,
    PlatformLifecycleError,
)
from holomed.platform.service import PlatformService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.service import IService, ServiceState


def test_d250_successful_epoch_migration(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    mock_domain_services: dict[str, IService],
) -> None:
    """Verify D250 valid migration advances epoch across all underlying services."""
    srv = PlatformService(
        services=mock_domain_services,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.start()

    # Target epoch must equal current + 1 (1 + 1 = 2)
    srv.migrate_epoch(target_epoch_id=2)
    assert srv._epoch_id == 2

    # Check underlying services received reset(2)
    ultron = mock_domain_services["ultron_service"]
    assert getattr(ultron, "reset_called_with_epoch") == 2

    srv.stop()


def test_d250_invalid_target_epoch_rejected(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    mock_domain_services: dict[str, IService],
) -> None:
    """Verify D250 non-consecutive target epoch raises PlatformEpochMismatchError."""
    srv = PlatformService(
        services=mock_domain_services,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.start()

    # Skipping epoch: 1 -> 5 must fail
    with pytest.raises(PlatformEpochMismatchError):
        srv.migrate_epoch(target_epoch_id=5)

    srv.stop()


def test_d250_service_failure_triggers_fail_stop(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    mock_domain_services: dict[str, IService],
) -> None:
    """Verify D250 failure during child service reset enters FAILED state and blocks cycles."""
    srv = PlatformService(
        services=mock_domain_services,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    srv.start()

    # Make ultron_service fail during reset
    ultron = mock_domain_services["ultron_service"]

    def failing_reset(epoch: int) -> None:
        raise RuntimeError("Simulated reset crash")

    setattr(ultron, "reset", failing_reset)

    with pytest.raises(PlatformEpochMigrationError):
        srv.migrate_epoch(target_epoch_id=2)

    assert srv.state == ServiceState.FAILED

    # Subsequent cycles must be blocked with PlatformLifecycleError
    with pytest.raises(PlatformLifecycleError):
        srv.tick(session_id="s0", sequence_number=0)

    srv.stop()
