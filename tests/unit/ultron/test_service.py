# -*- coding: utf-8 -*-
"""Unit tests for UltronService Lifecycle, Dispatcher Routes, and Public API (D218)."""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.service import ServiceState
from holomed.ultron.exceptions import (
    UltronEpochMismatchError,
    UltronLifecycleError,
    UltronShutdownError,
)
from holomed.ultron.models import ActionType, ModalityObservation
from holomed.ultron.service import STRUCTURAL_RESOURCE_IDS, UltronService


def test_service_lifecycle_full_flow(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
) -> None:
    """Verify standard UNINITIALIZED -> INITIALIZED -> STARTED -> STOPPED lifecycle with 5 handles."""
    service = UltronService(device_manager=device_manager, dispatcher=message_dispatcher)
    assert service.state == ServiceState.UNINITIALIZED

    # Stop before initialize is safe no-op
    service.stop()
    assert service.state == ServiceState.UNINITIALIZED

    # Initialize acquires exactly 5 structural handles
    service.initialize(runtime_context)
    assert service.state == ServiceState.INITIALIZED
    assert len(service.resources.outstanding_handles) == len(STRUCTURAL_RESOURCE_IDS)

    # Start service (acquires zero new handles)
    service.start()
    assert service.state == ServiceState.STARTED
    assert len(service.resources.outstanding_handles) == 5

    # Double start raises UltronLifecycleError
    with pytest.raises(UltronLifecycleError):
        service.start()

    # Health check
    h = service.health()
    assert h.status.name == "HEALTHY"

    # Stop service releases all 5 handles
    service.stop()
    assert service.state == ServiceState.STOPPED
    assert service.resources.is_empty is True

    # Idempotent stop
    service.stop()
    assert service.state == ServiceState.STOPPED


def test_service_teardown_failure_handling(runtime_context: RuntimeContext) -> None:
    """Verify resource release failure transitions service to FAILED and raises UltronShutdownError."""
    service = UltronService()
    service.initialize(runtime_context)
    service.start()

    # Monkeypatch release to fail
    def failing_release(res_id: str) -> None:
        raise RuntimeError(f"Simulated cleanup failure on {res_id}")

    service.resources.release = failing_release  # type: ignore

    with pytest.raises(UltronShutdownError) as exc_info:
        service.stop()

    assert service.state == ServiceState.FAILED
    assert len(exc_info.value.failures) == 5
    assert isinstance(exc_info.value.failures, tuple)


def test_d218_public_api_execution(
    runtime_context: RuntimeContext,
    sample_vision_observation: ModalityObservation,
    sample_gesture_observation: ModalityObservation,
) -> None:
    """Verify D218 public service APIs: ingest_observation, fuse, reason, step, reset."""
    service = UltronService()
    service.initialize(runtime_context)
    service.start()

    # 1. Ingest observations
    service.ingest_observation(sample_vision_observation)
    service.ingest_observation(sample_gesture_observation)

    # 2. Fuse
    ctx = service.fuse()
    assert len(ctx.entities) >= 1
    assert "PINCH" in ctx.active_gestures

    # 3. Reason
    intents = service.reason(depth=0)
    assert len(intents) >= 1
    assert intents[0].action_type == ActionType.SELECT

    # 4. Step
    step_intents = service.step()
    assert isinstance(step_intents, tuple)

    # 5. Reset
    service.reset(epoch_id=1)
    assert service.context_store.observation_count == 0

    # Reset with wrong epoch raises
    with pytest.raises(UltronEpochMismatchError):
        service.reset(epoch_id=999)

    service.stop()


def test_service_dispatcher_routes(
    runtime_context: RuntimeContext,
    device_manager: DeviceManager,
    message_dispatcher: MessageDispatcher,
    sample_gesture_observation: ModalityObservation,
) -> None:
    """Verify dispatcher query and command handlers."""
    service = UltronService(device_manager=device_manager, dispatcher=message_dispatcher)
    service.initialize(runtime_context)
    service.start()
    message_dispatcher.start()

    service.ingest_observation(sample_gesture_observation)
    service.step()

    # Query status
    q_status = create_query("ultron.status", "client", target="ultron_service", payload={})
    res_status = service.handle_status_query(q_status)
    assert res_status.payload["service_name"] == "ultron_service"
    assert res_status.payload["state"] == "STARTED"

    # Query context
    q_ctx = create_query("ultron.context", "client", target="ultron_service", payload={})
    res_ctx = service.handle_context_query(q_ctx)
    assert res_ctx.payload["entities_count"] >= 1

    # Query reasoning
    q_reason = create_query("ultron.reasoning", "client", target="ultron_service", payload={})
    res_reason = service.handle_reasoning_query(q_reason)
    assert res_reason.payload["traces_count"] >= 1

    # Query audit
    q_audit = create_query("ultron.audit", "client", target="ultron_service", payload={})
    res_audit = service.handle_audit_query(q_audit)
    assert res_audit.payload["is_consistent"] is True

    # Command reset
    c_reset = create_command("ultron.reset", "client", target="ultron_service", payload={"epoch_id": 1})
    res_reset = service.handle_reset_command(c_reset)
    assert res_reset.payload["reset_completed"] is True

    service.stop()
