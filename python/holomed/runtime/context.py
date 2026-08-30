"""Immutable runtime context containing configuration, epoch identity, and trace propagation."""

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from holomed.configuration.models import AppConfig


@dataclass(frozen=True)
class TraceContext:
    """Correlation and causation trace identifiers for M00.2 protocol interop."""

    correlation_id: Optional[UUID | str] = None
    causation_id: Optional[UUID | str] = None


@dataclass(frozen=True)
class RuntimeContext:
    """Execution context injected into services during initialization."""

    app_config: AppConfig
    epoch_id: int
    trace_context: Optional[TraceContext] = None

    def with_trace(self, trace: TraceContext) -> "RuntimeContext":
        """Create a new RuntimeContext inheriting config and epoch_id with updated trace context."""
        return RuntimeContext(
            app_config=self.app_config,
            epoch_id=self.epoch_id,
            trace_context=trace,
        )
