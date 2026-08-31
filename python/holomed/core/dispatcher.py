"""M00.4 Message Dispatcher — in-process deterministic message router.

Implements ``IService`` from ``holomed.runtime.service``.

Lifecycle states:
  * UNINITIALIZED
  * INITIALIZED
  * STARTED
  * STOPPED
  * FAILED

Static registrations (INITIALIZED only):
  * ``register_command_handler``
  * ``register_query_handler``
  * ``subscribe_event``

Dynamic operations (STARTED only):
  * ``register_correlation_listener``
  * ``prune_expired_listeners``
  * ``dispatch``

Resource accounting:
  Acquires exactly these 5 handles in ``initialize()``:
    * ``queue.dead_letter``
    * ``registry.command``
    * ``registry.query``
    * ``registry.subscription``
    * ``registry.correlation``
  No resource acquisition in ``start()`` or operational message paths.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import uuid

from holomed.core.dead_letter import DeadLetterQueue
from holomed.core.exceptions import (
    CorrelationError,
    CycleDetectedError,
    DispatcherLifecycleError,
    InvalidHandlerResponseError,
    PayloadValidationError,
    RecursionDepthExceededError,
    TimestampValidationError,
    UnroutableMessageError,
)
from holomed.core.models import (
    DEFAULT_DLQ_CAPACITY,
    DEFAULT_FUTURE_TOLERANCE_SECONDS,
    DEFAULT_MAX_AGE_SECONDS,
    MAX_CORRELATION_LISTENERS,
    MAX_PAYLOAD_BYTES,
    MAX_RECURSION_DEPTH,
    CommandRegistration,
    CorrelationCallback,
    CorrelationListener,
    DeadLetterOverflowPolicy,
    DeadLetterReason,
    DeadLetterRecord,
    DispatcherState,
    EventHandler,
    EventSubscription,
    MessageHandler,
    QueryRegistration,
)
from holomed.core.subscription import SubscriptionRegistry
from holomed.protocol.builders import create_error_response
from holomed.protocol.codec import serialize_envelope
from holomed.protocol.models import (
    CURRENT_PROTOCOL_VERSION,
    ErrorPayload,
    MessageEnvelope,
    MessageType,
)
from holomed.protocol.validation import (
    validate_envelope,
    validate_uuid_v4,
)
from holomed.runtime.context import RuntimeContext
from holomed.runtime.exceptions import (
    ResourceCleanupRequiredError,
    ServiceShutdownError,
    ShutdownFailureRecord,
)
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ResourceStatus,
    ServiceHealth,
)
from holomed.runtime.service import IService


class MessageDispatcher(IService):
    """Authoritative in-process message routing engine."""

    SERVICE_NAME = "core.dispatcher"
    RESOURCE_HANDLES = (
        "queue.dead_letter",
        "registry.command",
        "registry.query",
        "registry.subscription",
        "registry.correlation",
    )

    def __init__(
        self,
        *,
        dlq_capacity: int = DEFAULT_DLQ_CAPACITY,
        dlq_overflow_policy: DeadLetterOverflowPolicy = DeadLetterOverflowPolicy.DROP_OLDEST,
        future_tolerance_seconds: float = DEFAULT_FUTURE_TOLERANCE_SECONDS,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._dlq_capacity = dlq_capacity
        self._dlq_overflow_policy = dlq_overflow_policy
        self._future_tolerance_seconds = future_tolerance_seconds
        self._max_age_seconds = max_age_seconds

        self._state = DispatcherState.UNINITIALIZED
        self._secret_filter = secret_filter or SecretFilter()
        self._logger = logger or StructuredLogger(self.SERVICE_NAME, secret_filter=self._secret_filter)

        self._resources = OwnedResourceSet(self.SERVICE_NAME, epoch_id=0)
        self._dlq = DeadLetterQueue(capacity=dlq_capacity, overflow_policy=dlq_overflow_policy)
        self._subscription_registry = SubscriptionRegistry()
        self._correlation_listeners: dict[str, CorrelationListener] = {}

        # In-flight recursion and cycle tracking
        self._in_flight: list[MessageEnvelope] = []
        self._causal_map: dict[str, str] = {}  # child_id -> parent_causation_id

        # Injection hook for deterministic time testing
        self._time_provider: Optional[Callable[[], datetime]] = None

    # -----------------------------------------------------------------------
    # IService interface implementation
    # -----------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.SERVICE_NAME

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ()

    @property
    def resources(self) -> OwnedResourceSet:
        return self._resources

    @property
    def state(self) -> DispatcherState:
        return self._state

    @property
    def dead_letter_queue(self) -> DeadLetterQueue:
        return self._dlq

    @property
    def subscription_registry(self) -> SubscriptionRegistry:
        return self._subscription_registry

    @property
    def correlation_listeners(self) -> Mapping[str, CorrelationListener]:
        return MappingProxyType(dict(self._correlation_listeners))

    def set_time_provider(self, provider: Optional[Callable[[], datetime]]) -> None:
        """Inject deterministic clock provider for testing."""
        self._time_provider = provider

    def _current_time_utc(self) -> datetime:
        if self._time_provider is not None:
            t = self._time_provider()
            if t.tzinfo is None:
                return t.replace(tzinfo=timezone.utc)
            return t.astimezone(timezone.utc)
        return datetime.now(timezone.utc)

    def initialize(self, context: RuntimeContext) -> None:
        """Acquire owned structural handles and transition to INITIALIZED."""
        if self._state not in (DispatcherState.UNINITIALIZED, DispatcherState.STOPPED):
            if self._state == DispatcherState.FAILED:
                if not self._resources.is_empty:
                    unreleased = [h.resource_id for h in self._resources.outstanding_handles]
                    raise ResourceCleanupRequiredError(
                        f"Cannot reinitialize failed dispatcher with unreleased resources: {unreleased}"
                    )
            else:
                raise DispatcherLifecycleError(
                    f"Cannot initialize dispatcher from state: {self._state.name}"
                )

        # Update secrets from configuration if present
        if context.app_config.gemini_api_key:
            self._secret_filter.set_secrets((context.app_config.gemini_api_key,))

        # Initialize resources for the active epoch
        self._resources = OwnedResourceSet(self.SERVICE_NAME, epoch_id=context.epoch_id)
        for handle_name in self.RESOURCE_HANDLES:
            self._resources.acquire(handle_name)

        self._state = DispatcherState.INITIALIZED
        self._logger.info("MessageDispatcher initialized", event="dispatcher_initialized")

    def start(self) -> None:
        """Seal static registrations and transition to STARTED."""
        if self._state == DispatcherState.STARTED:
            return
        if self._state != DispatcherState.INITIALIZED:
            raise DispatcherLifecycleError(
                f"Cannot start dispatcher from state: {self._state.name}"
            )

        self._subscription_registry.seal()
        self._state = DispatcherState.STARTED
        self._logger.info("MessageDispatcher started", event="dispatcher_started")

    def stop(self) -> None:
        """Tear down all internal containers and release resources."""
        if self._state in (DispatcherState.UNINITIALIZED, DispatcherState.STOPPED):
            self._state = DispatcherState.STOPPED
            return

        failures: list[ShutdownFailureRecord] = []
        attempt_idx = 0

        # Step 1: clear internal collections with individual error containment
        try:
            self._in_flight.clear()
            self._causal_map.clear()
        except Exception as e:
            failures.append(
                ShutdownFailureRecord(
                    service_name=self.SERVICE_NAME,
                    original_exception=e,
                    execution_index=attempt_idx,
                    unreleased_resources=(),
                )
            )
            attempt_idx += 1

        try:
            self._correlation_listeners.clear()
        except Exception as e:
            failures.append(
                ShutdownFailureRecord(
                    service_name=self.SERVICE_NAME,
                    original_exception=e,
                    execution_index=attempt_idx,
                    unreleased_resources=(),
                )
            )
            attempt_idx += 1

        try:
            self._subscription_registry.clear()
        except Exception as e:
            failures.append(
                ShutdownFailureRecord(
                    service_name=self.SERVICE_NAME,
                    original_exception=e,
                    execution_index=attempt_idx,
                    unreleased_resources=(),
                )
            )
            attempt_idx += 1

        try:
            self._dlq.clear()
        except Exception as e:
            failures.append(
                ShutdownFailureRecord(
                    service_name=self.SERVICE_NAME,
                    original_exception=e,
                    execution_index=attempt_idx,
                    unreleased_resources=(),
                )
            )
            attempt_idx += 1

        # Step 2: Release each tracked resource handle
        for handle_name in reversed(self.RESOURCE_HANDLES):
            try:
                self._resources.release(handle_name)
            except Exception as e:
                self._resources.mark_release_failed(handle_name, str(e))
                failures.append(
                    ShutdownFailureRecord(
                        service_name=self.SERVICE_NAME,
                        original_exception=e,
                        execution_index=attempt_idx,
                        unreleased_resources=(handle_name,),
                    )
                )
                attempt_idx += 1

        if failures or not self._resources.is_empty:
            self._state = DispatcherState.FAILED
            raise ServiceShutdownError(
                f"Teardown failed on {len(failures)} operations in MessageDispatcher",
                failures=tuple(failures),
            )

        self._state = DispatcherState.STOPPED
        self._logger.info("MessageDispatcher stopped cleanly", event="dispatcher_stopped")

    def retry_cleanup(self) -> None:
        """Retry releasing outstanding dirty resources in FAILED state."""
        if self._state != DispatcherState.FAILED:
            raise DispatcherLifecycleError(
                f"retry_cleanup requires FAILED state, current: {self._state.name}"
            )

        failures: list[ShutdownFailureRecord] = []
        attempt_idx = 0

        # Target ONLY dirty resources in deterministic order
        dirty_handles = sorted(
            [h.resource_id for h in self._resources.outstanding_handles]
        )

        for handle_name in dirty_handles:
            try:
                self._resources.release(handle_name)
            except Exception as e:
                self._resources.mark_release_failed(handle_name, str(e))
                failures.append(
                    ShutdownFailureRecord(
                        service_name=self.SERVICE_NAME,
                        original_exception=e,
                        execution_index=attempt_idx,
                        unreleased_resources=(handle_name,),
                    )
                )
                attempt_idx += 1

        # Re-clear internal containers if any items remain
        try:
            self._correlation_listeners.clear()
            self._subscription_registry.clear()
            self._dlq.clear()
            self._in_flight.clear()
            self._causal_map.clear()
        except Exception as e:
            failures.append(
                ShutdownFailureRecord(
                    service_name=self.SERVICE_NAME,
                    original_exception=e,
                    execution_index=attempt_idx,
                    unreleased_resources=(),
                )
            )

        if failures or not self._resources.is_empty:
            unreleased = [h.resource_id for h in self._resources.outstanding_handles]
            err = ResourceCleanupRequiredError(
                f"Cleanup retry incomplete; unreleased resources remain: {unreleased}"
            )
            if failures:
                sh_err = ServiceShutdownError(
                    f"Cleanup retry failed on {len(failures)} operations",
                    failures=tuple(failures),
                )
                err.__cause__ = sh_err
            raise err

        # When clean, the state remains FAILED until explicitly stopped or reinitialized
        self._logger.info("MessageDispatcher retry_cleanup succeeded; resources clean", event="cleanup_clean")

    def health(self) -> ServiceHealth:
        """Synchronously evaluate in-process dispatcher health."""
        now_str = self._current_time_utc().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        try:
            if self._state == DispatcherState.FAILED:
                status = HealthStatus.FAILED
                msg = f"Dispatcher in FAILED state (outstanding resources: {len(self._resources.outstanding_handles)})"
            elif self._state == DispatcherState.STARTED:
                status = HealthStatus.HEALTHY
                msg = (
                    f"Dispatcher operational (commands={self._subscription_registry.command_count}, "
                    f"queries={self._subscription_registry.query_count}, "
                    f"events={self._subscription_registry.event_subscription_count}, "
                    f"dlq_records={self._dlq.count})"
                )
            elif self._state == DispatcherState.INITIALIZED:
                status = HealthStatus.HEALTHY
                msg = "Dispatcher initialized and ready for start"
            elif self._state == DispatcherState.STOPPED:
                status = HealthStatus.HEALTHY
                msg = "Dispatcher stopped"
            else:
                status = HealthStatus.HEALTHY
                msg = "Dispatcher uninitialized"

            redacted_msg = self._secret_filter.redact(msg)
            return ServiceHealth(
                name=self.SERVICE_NAME,
                status=status,
                message=redacted_msg,
                timestamp_utc=now_str,
            )
        except Exception as e:
            redacted_err = self._secret_filter.redact(str(e))
            return ServiceHealth(
                name=self.SERVICE_NAME,
                status=HealthStatus.FAILED,
                message=f"Health check raised exception: {redacted_err}",
                timestamp_utc=now_str,
            )

    # -----------------------------------------------------------------------
    # Static Registrations (INITIALIZED only)
    # -----------------------------------------------------------------------

    def register_command_handler(
        self,
        topic: str,
        handler: MessageHandler,
        service_name: str,
    ) -> CommandRegistration:
        """Register command handler. Permitted ONLY in INITIALIZED state."""
        if self._state != DispatcherState.INITIALIZED:
            raise DispatcherLifecycleError(
                f"register_command_handler allowed ONLY in INITIALIZED state, current: {self._state.name}"
            )
        return self._subscription_registry.register_command(topic, handler, service_name)

    def register_query_handler(
        self,
        topic: str,
        handler: MessageHandler,
        service_name: str,
    ) -> QueryRegistration:
        """Register query handler. Permitted ONLY in INITIALIZED state."""
        if self._state != DispatcherState.INITIALIZED:
            raise DispatcherLifecycleError(
                f"register_query_handler allowed ONLY in INITIALIZED state, current: {self._state.name}"
            )
        return self._subscription_registry.register_query(topic, handler, service_name)

    def subscribe_event(
        self,
        pattern: str,
        handler: EventHandler,
        service_name: str,
    ) -> EventSubscription:
        """Subscribe event handler. Permitted ONLY in INITIALIZED state."""
        if self._state != DispatcherState.INITIALIZED:
            raise DispatcherLifecycleError(
                f"subscribe_event allowed ONLY in INITIALIZED state, current: {self._state.name}"
            )
        return self._subscription_registry.subscribe_event(pattern, handler, service_name)

    # -----------------------------------------------------------------------
    # Dynamic Registrations & Pruning (STARTED only)
    # -----------------------------------------------------------------------

    def register_correlation_listener(
        self,
        correlation_id: str,
        callback: CorrelationCallback,
        timeout_seconds: float,
    ) -> CorrelationListener:
        """Register dynamic one-shot correlation listener. Permitted ONLY in STARTED state."""
        if self._state != DispatcherState.STARTED:
            raise DispatcherLifecycleError(
                f"register_correlation_listener allowed ONLY in STARTED state, current: {self._state.name}"
            )
        if not callable(callback):
            raise CorrelationError("Callback must be callable")
        if timeout_seconds <= 0:
            raise CorrelationError(f"timeout_seconds must be positive, got {timeout_seconds}")

        # Validate correlation_id format (UUIDv4)
        try:
            validate_uuid_v4(correlation_id, "correlation_id")
        except Exception as e:
            raise CorrelationError(f"Invalid correlation_id UUIDv4 format: {correlation_id!r}") from e

        # Duplicate check
        if correlation_id in self._correlation_listeners:
            raise CorrelationError(f"Duplicate correlation listener for correlation_id '{correlation_id}'")

        # Capacity check
        if len(self._correlation_listeners) >= MAX_CORRELATION_LISTENERS:
            raise CorrelationError(
                f"Correlation listener capacity ({MAX_CORRELATION_LISTENERS}) exceeded"
            )

        now = self._current_time_utc()
        from datetime import timedelta
        deadline = now + timedelta(seconds=timeout_seconds)

        listener = CorrelationListener(
            correlation_id=correlation_id,
            callback=callback,
            deadline_utc=deadline,
            registered_utc=now,
        )
        self._correlation_listeners[correlation_id] = listener
        return listener

    def prune_expired_listeners(self, now_utc: Optional[datetime] = None) -> tuple[str, ...]:
        """Prune timed-out correlation listeners in deterministic order (STARTED only)."""
        if self._state != DispatcherState.STARTED:
            raise DispatcherLifecycleError(
                f"prune_expired_listeners allowed ONLY in STARTED state, current: {self._state.name}"
            )

        current_time = now_utc if now_utc is not None else self._current_time_utc()
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        else:
            current_time = current_time.astimezone(timezone.utc)

        # Deterministic sort order: deadline_utc ASC, then correlation_id ASC
        sorted_listeners = sorted(
            self._correlation_listeners.values(),
            key=lambda l: (l.deadline_utc, l.correlation_id),
        )

        expired_ids: list[str] = []
        for listener in sorted_listeners:
            if current_time >= listener.deadline_utc:
                self._correlation_listeners.pop(listener.correlation_id, None)
                diag = self._secret_filter.redact(
                    f"Correlation listener timed out for correlation_id '{listener.correlation_id}'"
                )
                self._dlq.record(
                    envelope=None,
                    reason=DeadLetterReason.CORRELATION_TIMEOUT,
                    diagnostic=diag,
                    now_utc=current_time,
                )
                expired_ids.append(listener.correlation_id)

        return tuple(expired_ids)

    # -----------------------------------------------------------------------
    # Message Dispatch Engine (STARTED only)
    # -----------------------------------------------------------------------

    def dispatch(
        self,
        envelope: MessageEnvelope,
        *,
        now_utc: Optional[datetime] = None,
    ) -> Optional[MessageEnvelope]:
        """Route message according to M00.4 specification. Permitted ONLY in STARTED state."""
        if self._state != DispatcherState.STARTED:
            raise DispatcherLifecycleError(
                f"dispatch allowed ONLY in STARTED state, current: {self._state.name}"
            )

        current_time = now_utc if now_utc is not None else self._current_time_utc()
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        else:
            current_time = current_time.astimezone(timezone.utc)

        # 1. Structural Envelope Validation
        validate_envelope(envelope)

        # 2. Canonical Payload Serialisation & Size Validation
        self._validate_payload_size(envelope, current_time)

        # 3. Timestamp Validation
        self._validate_timestamp(envelope, current_time)

        # 4. Recursion Depth Safety Check (Attempted depth 17 rejected before push)
        if len(self._in_flight) >= MAX_RECURSION_DEPTH:
            diag = self._secret_filter.redact(
                f"Dispatch recursion depth limit of {MAX_RECURSION_DEPTH} exceeded (in-flight={len(self._in_flight)})"
            )
            self._dlq.record(
                envelope=envelope,
                reason=DeadLetterReason.RECURSION_DEPTH_EXCEEDED,
                diagnostic=diag,
                now_utc=current_time,
            )
            raise RecursionDepthExceededError(diag)

        # 5. Cycle Detection (Direct Re-entry & Causal Ancestry)
        self._check_cycle(envelope, current_time)

        # Push to in-flight chain
        self._in_flight.append(envelope)
        if envelope.causation_id:
            self._causal_map[envelope.message_id] = envelope.causation_id

        try:
            if envelope.message_type == MessageType.COMMAND:
                return self._dispatch_command(envelope, current_time)
            elif envelope.message_type == MessageType.QUERY:
                return self._dispatch_query(envelope, current_time)
            elif envelope.message_type == MessageType.EVENT:
                self._dispatch_event(envelope, current_time)
                return None
            elif envelope.message_type in (MessageType.RESPONSE, MessageType.ERROR):
                self._dispatch_response_or_error(envelope, current_time)
                return None
            else:
                diag = self._secret_filter.redact(f"Unsupported message_type: {envelope.message_type}")
                self._dlq.record(envelope=envelope, reason=DeadLetterReason.NO_HANDLER, diagnostic=diag, now_utc=current_time)
                raise UnroutableMessageError(diag)
        finally:
            # Pop from in-flight chain
            self._in_flight.pop()

    # -----------------------------------------------------------------------
    # Internal routing subroutines
    # -----------------------------------------------------------------------

    def _dispatch_command(self, envelope: MessageEnvelope, current_time: datetime) -> MessageEnvelope:
        reg = self._subscription_registry.lookup_command(envelope.message_name)
        if reg is None:
            diag = self._secret_filter.redact(f"No command handler registered for topic '{envelope.message_name}'")
            self._dlq.record(envelope=envelope, reason=DeadLetterReason.NO_HANDLER, diagnostic=diag, now_utc=current_time)
            raise UnroutableMessageError(diag)

        try:
            result = reg.handler(envelope)
        except (RecursionDepthExceededError, CycleDetectedError):
            raise
        except Exception as e:
            return self._handle_handler_exception(envelope, e, current_time)

        self._validate_handler_postconditions(envelope, result, current_time)
        return result

    def _dispatch_query(self, envelope: MessageEnvelope, current_time: datetime) -> MessageEnvelope:
        reg = self._subscription_registry.lookup_query(envelope.message_name)
        if reg is None:
            diag = self._secret_filter.redact(f"No query handler registered for topic '{envelope.message_name}'")
            self._dlq.record(envelope=envelope, reason=DeadLetterReason.NO_HANDLER, diagnostic=diag, now_utc=current_time)
            raise UnroutableMessageError(diag)

        try:
            result = reg.handler(envelope)
        except (RecursionDepthExceededError, CycleDetectedError):
            raise
        except Exception as e:
            return self._handle_handler_exception(envelope, e, current_time)

        self._validate_handler_postconditions(envelope, result, current_time)
        return result

    def _dispatch_event(self, envelope: MessageEnvelope, current_time: datetime) -> None:
        subscribers = self._subscription_registry.matching_event_subscriptions(envelope.message_name)
        if not subscribers:
            # Zero subscribers = valid no-op, must NOT create DLQ noise
            return

        for sub in subscribers:
            try:
                sub.handler(envelope)
            except Exception as e:
                # Event subscriber exception: redact, write DLQ SUBSCRIBER_EXCEPTION, continue
                diag = self._secret_filter.redact(
                    f"Event subscriber '{sub.service_name}' failed on topic '{envelope.message_name}': {e}"
                )
                self._dlq.record(
                    envelope=envelope,
                    reason=DeadLetterReason.SUBSCRIBER_EXCEPTION,
                    diagnostic=diag,
                    now_utc=current_time,
                )
                self._logger.error(
                    "Event subscriber exception during fan-out",
                    extra={"subscriber": sub.service_name, "error": diag},
                    exc_info=True,
                )
                # Continue fan-out to remaining subscribers

    def _dispatch_response_or_error(self, envelope: MessageEnvelope, current_time: datetime) -> None:
        corr_id = envelope.correlation_id
        listener = self._correlation_listeners.pop(corr_id, None)
        if listener is None:
            diag = self._secret_filter.redact(
                f"No correlation listener registered for correlation_id '{corr_id}'"
            )
            self._dlq.record(
                envelope=envelope,
                reason=DeadLetterReason.NO_HANDLER,
                diagnostic=diag,
                now_utc=current_time,
            )
            raise UnroutableMessageError(diag)

        if current_time >= listener.deadline_utc:
            # Expired listener: DLQ CORRELATION_TIMEOUT, do not invoke
            diag = self._secret_filter.redact(
                f"Correlation listener expired prior to receiving response for correlation_id '{corr_id}'"
            )
            self._dlq.record(
                envelope=envelope,
                reason=DeadLetterReason.CORRELATION_TIMEOUT,
                diagnostic=diag,
                now_utc=current_time,
            )
            return

        # Invoke callback (one-shot already removed before callback execution)
        listener.callback(envelope)

    # -----------------------------------------------------------------------
    # Validation helpers
    # -----------------------------------------------------------------------

    def _validate_payload_size(self, envelope: MessageEnvelope, current_time: datetime) -> None:
        try:
            serialized_payload = json.dumps(
                envelope.payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
        except Exception as e:
            diag = self._secret_filter.redact(f"Canonical payload serialization failed: {e}")
            self._dlq.record(
                envelope=envelope,
                reason=DeadLetterReason.CORRUPTED_ENVELOPE,
                diagnostic=diag,
                now_utc=current_time,
            )
            raise PayloadValidationError(diag) from e

        encoded_bytes = serialized_payload.encode("utf-8")
        if len(encoded_bytes) > MAX_PAYLOAD_BYTES:
            diag = self._secret_filter.redact(
                f"Payload size ({len(encoded_bytes)} bytes) exceeds maximum limit of {MAX_PAYLOAD_BYTES} bytes"
            )
            self._dlq.record(
                envelope=envelope,
                reason=DeadLetterReason.CORRUPTED_ENVELOPE,
                diagnostic=diag,
                now_utc=current_time,
            )
            raise PayloadValidationError(diag)

    def _validate_timestamp(self, envelope: MessageEnvelope, current_time: datetime) -> None:
        try:
            msg_dt = datetime.strptime(envelope.timestamp_utc, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                tzinfo=timezone.utc
            )
        except Exception as e:
            diag = self._secret_filter.redact(f"Timestamp parse error: {e}")
            self._dlq.record(
                envelope=envelope,
                reason=DeadLetterReason.CORRUPTED_ENVELOPE,
                diagnostic=diag,
                now_utc=current_time,
            )
            raise TimestampValidationError(diag) from e

        # Future tolerance check: > +5.0 sec => FUTURE_TIMESTAMP_EXCEEDED
        future_diff = (msg_dt - current_time).total_seconds()
        if future_diff > self._future_tolerance_seconds:
            diag = self._secret_filter.redact(
                f"Message timestamp {envelope.timestamp_utc} is {future_diff:.3f}s in future, "
                f"exceeding tolerance of {self._future_tolerance_seconds}s"
            )
            self._dlq.record(
                envelope=envelope,
                reason=DeadLetterReason.FUTURE_TIMESTAMP_EXCEEDED,
                diagnostic=diag,
                now_utc=current_time,
            )
            raise TimestampValidationError(diag)

        # Max age check: age > max_age_seconds => MESSAGE_EXPIRED
        age = (current_time - msg_dt).total_seconds()
        if age > self._max_age_seconds:
            diag = self._secret_filter.redact(
                f"Message timestamp {envelope.timestamp_utc} is {age:.3f}s old, "
                f"exceeding max age of {self._max_age_seconds}s"
            )
            self._dlq.record(
                envelope=envelope,
                reason=DeadLetterReason.MESSAGE_EXPIRED,
                diagnostic=diag,
                now_utc=current_time,
            )
            raise TimestampValidationError(diag)

    def _check_cycle(self, envelope: MessageEnvelope, current_time: datetime) -> None:
        # 1. Direct re-entry: message_id already in active in-flight stack
        for in_flight_env in self._in_flight:
            if in_flight_env.message_id == envelope.message_id:
                diag = self._secret_filter.redact(
                    f"Direct re-entry cycle detected: message_id '{envelope.message_id}' already in-flight"
                )
                self._dlq.record(
                    envelope=envelope,
                    reason=DeadLetterReason.CYCLE_DETECTED,
                    diagnostic=diag,
                    now_utc=current_time,
                )
                raise CycleDetectedError(diag)

        # 2. Direct self-causation
        if envelope.causation_id is not None and envelope.causation_id == envelope.message_id:
            diag = self._secret_filter.redact(
                f"Direct self-causation cycle detected: message_id '{envelope.message_id}' causes itself"
            )
            self._dlq.record(
                envelope=envelope,
                reason=DeadLetterReason.CYCLE_DETECTED,
                diagnostic=diag,
                now_utc=current_time,
            )
            raise CycleDetectedError(diag)

        # 3. Causal ancestry cycle
        if envelope.causation_id is not None:
            curr = envelope.causation_id
            seen = {envelope.message_id}
            while curr in self._causal_map:
                if curr in seen:
                    diag = self._secret_filter.redact(
                        f"Causal ancestry cycle detected: message_id '{envelope.message_id}' loops via '{curr}'"
                    )
                    self._dlq.record(
                        envelope=envelope,
                        reason=DeadLetterReason.CYCLE_DETECTED,
                        diagnostic=diag,
                        now_utc=current_time,
                    )
                    raise CycleDetectedError(diag)
                seen.add(curr)
                curr = self._causal_map[curr]

    def _validate_handler_postconditions(
        self,
        request: MessageEnvelope,
        result: Any,
        current_time: datetime,
    ) -> None:
        """Enforce strict postconditions on command/query handler return value."""
        violation: Optional[str] = None

        if not isinstance(result, MessageEnvelope):
            violation = f"Handler returned invalid type: {type(result).__name__}; expected MessageEnvelope"
        elif result.message_type not in (MessageType.RESPONSE, MessageType.ERROR):
            violation = f"Handler response message_type must be RESPONSE or ERROR, got {result.message_type}"
        elif result.correlation_id != request.correlation_id:
            violation = (
                f"Handler response correlation_id '{result.correlation_id}' does not match "
                f"request correlation_id '{request.correlation_id}'"
            )
        elif result.causation_id != request.message_id:
            violation = (
                f"Handler response causation_id '{result.causation_id}' does not match "
                f"request message_id '{request.message_id}'"
            )
        elif result.target != request.source:
            violation = (
                f"Handler response target '{result.target}' does not match request source '{request.source}'"
            )
        else:
            try:
                validate_envelope(result)
            except Exception as ve:
                violation = f"Handler response failed envelope validation: {ve}"

        if violation is not None:
            redacted_violation = self._secret_filter.redact(violation)
            self._dlq.record(
                envelope=result if isinstance(result, MessageEnvelope) else request,
                reason=DeadLetterReason.INVALID_HANDLER_RESPONSE,
                diagnostic=redacted_violation,
                now_utc=current_time,
            )
            raise InvalidHandlerResponseError(redacted_violation)

    def _handle_handler_exception(
        self,
        request: MessageEnvelope,
        original_exc: Exception,
        current_time: datetime,
    ) -> MessageEnvelope:
        """Boundary handler exception handling: redact, log, DLQ, synthesize ERROR envelope."""
        err_str = self._secret_filter.redact(str(original_exc))
        err_type = type(original_exc).__name__
        diag = f"Handler execution raised {err_type}: {err_str}"

        # Write to DLQ
        self._dlq.record(
            envelope=request,
            reason=DeadLetterReason.HANDLER_EXCEPTION,
            diagnostic=diag,
            now_utc=current_time,
        )

        # Log
        self._logger.error(
            "Handler execution exception",
            extra={"error_type": err_type, "message": err_str, "topic": request.message_name},
            exc_info=True,
        )

        # Synthesize compliant ERROR envelope
        error_envelope = create_error_response(
            request=request,
            responder_source=self.SERVICE_NAME,
            error_code="ERR_HANDLER_EXECUTION",
            error_message=err_str or f"Handler raised {err_type}",
            details={"exception_type": err_type, "original_message": err_str},
            recoverable=False,
            message_name=f"{request.message_name}.error",
        )
        return error_envelope
