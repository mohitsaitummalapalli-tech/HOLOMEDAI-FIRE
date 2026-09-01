# -*- coding: utf-8 -*-
"""Unit tests for PlatformService Lifecycle, Dispatcher Integration, and Reentrancy (D253)."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.platform.exceptions import PlatformLifecycleError
from holomed.platform.models import CycleStatus
from holomed.platform.service import PlatformService
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus
from holomed.runtime.service import IService, ServiceState


def test_platform_service_lifecycle_and_resources(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    mock_domain_services: dict[str, IService],
) -> None:
    """Verify PlatformService lifecycle and exact acquisition of 5 structural handles."""
    srv = PlatformService(
        services=mock_domain_services,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    assert srv.state == ServiceState.UNINITIALIZED

    srv.initialize(runtime_context)
    assert srv.state == ServiceState.INITIALIZED
    assert len(srv.resources.outstanding_handles) == 5

    message_dispatcher.start()
    srv.start()
    assert srv.state == ServiceState.STARTED

    h = srv.health()
    assert h.status == HealthStatus.HEALTHY

    srv.stop()
    assert srv.state == ServiceState.STOPPED
    assert len(srv.resources.outstanding_handles) == 0


def test_d253_reentrancy_guard(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    mock_domain_services: dict[str, IService],
) -> None:
    """Verify D253 reentrant mutations are rejected by transaction guard."""
    srv = PlatformService(
        services=mock_domain_services,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    srv._in_transaction = True
    with pytest.raises(PlatformLifecycleError):
        srv.tick(session_id="s0", sequence_number=0)

    srv._in_transaction = False
    srv.stop()


def test_dispatcher_routes_query_and_cycle(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
    mock_domain_services: dict[str, IService],
) -> None:
    """Verify dispatcher queries and commands route cleanly to PlatformService."""
    srv = PlatformService(
        services=mock_domain_services,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()

    # Query status
    q_status = create_query("platform.status", "client")
    resp_status = message_dispatcher.dispatch(q_status)
    assert resp_status.message_type.value == "RESPONSE"
    assert resp_status.payload["service_name"] == "platform_service"

    # Start session
    cmd_start = create_command("platform.session.start", "client", payload={"session_id": "sess_disp"})
    resp_start = message_dispatcher.dispatch(cmd_start)
    assert resp_start.message_type.value == "RESPONSE"

    # Execute cycle
    cmd_cycle = create_command(
        "platform.cycle",
        "client",
        payload={"session_id": "sess_disp", "sequence_number": 0},
    )
    resp_cycle = message_dispatcher.dispatch(cmd_cycle)
    assert resp_cycle.message_type.value == "RESPONSE"
    assert resp_cycle.payload["status"] == "COMPLETED"

    srv.stop()
