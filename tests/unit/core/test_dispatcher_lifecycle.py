"""Tests for M00.4 MessageDispatcher Lifecycle, Resource Accounting, and Health."""

import pytest
from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel, SecretString
from holomed.core.dispatcher import MessageDispatcher
from holomed.core.exceptions import DispatcherLifecycleError, ResourceCleanupRequiredError
from holomed.core.models import DispatcherState
from holomed.protocol.builders import create_command
from holomed.protocol.models import MessageEnvelope
from holomed.runtime.context import RuntimeContext
from holomed.runtime.exceptions import ServiceShutdownError
from holomed.runtime.models import HealthStatus, ResourceStatus


def create_test_context(epoch_id: int = 1, secret: str = "super_secret_token_123") -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed AI",
        environment=EnvironmentProfile.TESTING,
        host="127.0.0.1",
        port=8000,
        log_level=LogLevel.DEBUG,
        gemini_api_key=SecretString(secret),
    )
    return RuntimeContext(app_config=config, epoch_id=epoch_id)


def dummy_handler(env: MessageEnvelope) -> MessageEnvelope:
    return env


class TestDispatcherLifecycle:
    """Test state machine transitions and registration boundaries."""

    def test_full_clean_lifecycle(self) -> None:
        dispatcher = MessageDispatcher()
        assert dispatcher.state == DispatcherState.UNINITIALIZED
        assert dispatcher.resources.is_empty is True

        ctx = create_test_context(epoch_id=1)
        dispatcher.initialize(ctx)
        assert dispatcher.state == DispatcherState.INITIALIZED
        assert len(dispatcher.resources.outstanding_handles) == 5

        # Verify exactly the 5 structural handles were acquired
        acquired_ids = {h.resource_id for h in dispatcher.resources.outstanding_handles}
        expected_ids = {
            "queue.dead_letter",
            "registry.command",
            "registry.query",
            "registry.subscription",
            "registry.correlation",
        }
        assert acquired_ids == expected_ids

        # Start dispatcher: verifies resources_before == resources_after
        res_before = frozenset(dispatcher.resources.outstanding_handles)
        dispatcher.start()
        res_after = frozenset(dispatcher.resources.outstanding_handles)
        assert dispatcher.state == DispatcherState.STARTED
        assert res_before == res_after

        # Stop dispatcher: releases all 5 resources
        dispatcher.stop()
        assert dispatcher.state == DispatcherState.STOPPED
        assert dispatcher.resources.is_empty is True

    def test_state_boundary_enforcement(self) -> None:
        dispatcher = MessageDispatcher()

        # Cannot start from UNINITIALIZED
        with pytest.raises(DispatcherLifecycleError):
            dispatcher.start()

        # Cannot register static handlers in UNINITIALIZED
        with pytest.raises(DispatcherLifecycleError):
            dispatcher.register_command_handler("test.cmd", dummy_handler, "svc")

        # Initialize
        ctx = create_test_context()
        dispatcher.initialize(ctx)

        # Cannot dispatch in INITIALIZED
        cmd = create_command("test.cmd", "src")
        with pytest.raises(DispatcherLifecycleError):
            dispatcher.dispatch(cmd)

        # Cannot register dynamic correlation listener in INITIALIZED
        with pytest.raises(DispatcherLifecycleError):
            dispatcher.register_correlation_listener(cmd.correlation_id, lambda e: None, 5.0)

        # Start
        dispatcher.start()

        # Cannot register static handlers in STARTED
        with pytest.raises(DispatcherLifecycleError):
            dispatcher.register_command_handler("new.cmd", dummy_handler, "svc")

        with pytest.raises(DispatcherLifecycleError):
            dispatcher.subscribe_event("new.**", lambda e: None, "svc")

        # Stop
        dispatcher.stop()

        # Cannot dispatch in STOPPED
        with pytest.raises(DispatcherLifecycleError):
            dispatcher.dispatch(cmd)

    def test_teardown_failure_and_retry_cleanup(self) -> None:
        dispatcher = MessageDispatcher()
        ctx = create_test_context()
        dispatcher.initialize(ctx)
        dispatcher.start()

        # Simulate release failure on one resource by monkeypatching
        orig_release = dispatcher.resources.release
        fail_target = "registry.query"

        def failing_release(res_id: str) -> None:
            if res_id == fail_target:
                raise RuntimeError(f"Simulated release failure on {res_id}")
            orig_release(res_id)

        dispatcher.resources.release = failing_release  # type: ignore[assignment]

        with pytest.raises(ServiceShutdownError) as exc_info:
            dispatcher.stop()

        assert dispatcher.state == DispatcherState.FAILED
        assert not dispatcher.resources.is_empty
        assert len(dispatcher.resources.outstanding_handles) == 1
        assert list(dispatcher.resources.outstanding_handles)[0].resource_id == fail_target

        # Calling retry_cleanup while release still fails raises ResourceCleanupRequiredError
        with pytest.raises(ResourceCleanupRequiredError):
            dispatcher.retry_cleanup()
        assert dispatcher.state == DispatcherState.FAILED

        # Restore original release and retry_cleanup
        dispatcher.resources.release = orig_release  # type: ignore[assignment]
        dispatcher.retry_cleanup()

        # When clean, resources are empty but engine remains FAILED
        assert dispatcher.resources.is_empty is True
        assert dispatcher.state == DispatcherState.FAILED

        # Explicit stop transitions FAILED to STOPPED once clean
        dispatcher.stop()
        assert dispatcher.state == DispatcherState.STOPPED

    def test_health_evaluation(self) -> None:
        dispatcher = MessageDispatcher()
        h_uninit = dispatcher.health()
        assert h_uninit.status == HealthStatus.HEALTHY
        assert "uninitialized" in h_uninit.message

        ctx = create_test_context(secret="api_key_xyz987")
        dispatcher.initialize(ctx)
        dispatcher.start()

        h_started = dispatcher.health()
        assert h_started.status == HealthStatus.HEALTHY
        assert "operational" in h_started.message
        # Confirm no secret leaks
        assert "api_key_xyz987" not in h_started.message

        dispatcher.stop()
        h_stopped = dispatcher.health()
        assert h_stopped.status == HealthStatus.HEALTHY
