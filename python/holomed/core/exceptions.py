"""M00.4 Core exception hierarchy.

Every caught underlying exception that becomes a domain exception preserves:
- original exception reference
- explicit ``raise ... from original_exception``
- deterministic diagnostic information
"""

from holomed.common.exceptions import HoloMedError
from holomed.runtime.exceptions import ResourceCleanupRequiredError


class CoreError(HoloMedError):
    """Root exception for all M00.4 core subsystem errors."""

    pass


class TopicValidationError(CoreError):
    """Raised when a topic string or pattern violates the topic grammar."""

    pass


class SubscriptionError(CoreError):
    """Base exception for subscription registration failures."""

    pass


class DuplicateSubscriptionError(SubscriptionError):
    """Raised when a duplicate command/query handler is registered for the same topic."""

    pass


class SubscriptionCapacityError(SubscriptionError):
    """Raised when subscription capacity limits are exceeded."""

    pass


class UnroutableMessageError(CoreError):
    """Raised when no handler exists for a COMMAND, QUERY, RESPONSE, or ERROR message."""

    pass


class InvalidHandlerResponseError(CoreError):
    """Raised when a handler returns a malformed response envelope."""

    pass


class HandlerExecutionError(CoreError):
    """Raised when a handler raises an exception during execution."""

    pass


class DispatcherLifecycleError(CoreError):
    """Raised when a dispatcher operation is attempted in an invalid lifecycle state."""

    pass


class RecursionDepthExceededError(CoreError):
    """Raised when dispatch recursion exceeds the maximum allowed depth."""

    pass


class CycleDetectedError(CoreError):
    """Raised when a causal message cycle is detected during dispatch."""

    pass


class CorrelationError(CoreError):
    """Raised for correlation listener registration or routing failures."""

    pass


class PayloadValidationError(CoreError):
    """Raised when payload canonical serialisation or byte-size validation fails."""

    pass


class TimestampValidationError(CoreError):
    """Raised when a message timestamp exceeds future tolerance or max age."""

    pass


class DeadLetterCapacityError(CoreError):
    """Raised when the dead-letter queue is full under REJECT_NEW policy."""

    pass


class PipelineError(CoreError):
    """Base exception for pipeline coordinator failures."""

    pass


class PipelineTopologyError(PipelineError):
    """Raised when pipeline stage topology is invalid (cycles, missing deps)."""

    pass


class PipelineStageExecutionError(PipelineError):
    """Raised when a pipeline stage fails during execution.

    Attributes:
        stage_name: Name of the failed stage.
        stage_index: Execution index of the failed stage.
        original_stage_error: The exception raised by the stage.
    """

    def __init__(
        self,
        message: str,
        *,
        stage_name: str,
        stage_index: int,
        original_stage_error: Exception,
    ) -> None:
        super().__init__(message)
        self.stage_name = stage_name
        self.stage_index = stage_index
        self.original_stage_error = original_stage_error


class PipelineRollbackError(PipelineError):
    """Raised when one or more rollback stages fail after a pipeline execution failure.

    Attributes:
        stage_name: Name of the originally failed execution stage.
        stage_index: Execution index of the originally failed stage.
        original_stage_error: The exception raised by the original stage.
        rollback_failures: Tuple of (stage_name, stage_index, exception) for each failed rollback.
    """

    def __init__(
        self,
        message: str,
        *,
        stage_name: str,
        stage_index: int,
        original_stage_error: Exception,
        rollback_failures: tuple[tuple[str, int, Exception], ...],
    ) -> None:
        super().__init__(message)
        self.stage_name = stage_name
        self.stage_index = stage_index
        self.original_stage_error = original_stage_error
        self.rollback_failures = rollback_failures
