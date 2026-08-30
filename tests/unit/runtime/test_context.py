"""Unit tests for RuntimeContext trace propagation and epoch identity."""

from dataclasses import FrozenInstanceError
import uuid
import pytest

from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel
from holomed.runtime.context import RuntimeContext, TraceContext


def test_runtime_context_immutability_and_trace_propagation() -> None:
    """Assert RuntimeContext is frozen and with_trace() creates new derived instances."""
    config = AppConfig(
        app_name="App",
        environment=EnvironmentProfile.DEVELOPMENT,
        host="127.0.0.1",
        port=8000,
        log_level=LogLevel.INFO,
    )
    ctx = RuntimeContext(app_config=config, epoch_id=42)

    assert ctx.epoch_id == 42
    assert ctx.app_config == config
    assert ctx.trace_context is None

    with pytest.raises((FrozenInstanceError, AttributeError)):
        ctx.epoch_id = 99  # type: ignore

    # Propagate trace
    corr_id = uuid.uuid4()
    caus_id = uuid.uuid4()
    trace = TraceContext(correlation_id=corr_id, causation_id=caus_id)

    ctx_traced = ctx.with_trace(trace)
    assert ctx_traced is not ctx
    assert ctx_traced.epoch_id == 42
    assert ctx_traced.app_config == config
    assert ctx_traced.trace_context == trace
    assert ctx.trace_context is None  # Original context unchanged
