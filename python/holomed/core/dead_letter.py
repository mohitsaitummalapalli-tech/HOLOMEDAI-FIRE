"""M00.4 Dead-letter queue — bounded FIFO ring with overflow policies.

Policies:
  * ``DROP_OLDEST``  — evicts the oldest record, increments ``evicted_count``.
  * ``REJECT_NEW``   — raises ``DeadLetterCapacityError``; does NOT evict,
                       does NOT mutate queue contents, does NOT increment
                       ``evicted_count``.

Guarantees:
  * Fixed memory (bounded by *capacity*).
  * Deterministic FIFO inspection order.
  * Immutable exported records (tuple snapshot).
  * ``clear()`` resets records **and** ``evicted_count``.
  * Overflow warning MUST NOT recurse through the dispatcher.
"""

from __future__ import annotations

import warnings
from collections import deque
from datetime import datetime, timezone

from holomed.core.exceptions import DeadLetterCapacityError
from holomed.core.models import (
    DEFAULT_DLQ_CAPACITY,
    DeadLetterOverflowPolicy,
    DeadLetterReason,
    DeadLetterRecord,
)
from holomed.protocol.models import MessageEnvelope


class DeadLetterQueue:
    """Bounded FIFO dead-letter queue with configurable overflow policy."""

    def __init__(
        self,
        capacity: int = DEFAULT_DLQ_CAPACITY,
        overflow_policy: DeadLetterOverflowPolicy = DeadLetterOverflowPolicy.DROP_OLDEST,
        *,
        now_utc: datetime | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError(f"Dead-letter queue capacity must be >= 1, got {capacity}")
        self._capacity = capacity
        self._overflow_policy = overflow_policy
        self._records: deque[DeadLetterRecord] = deque()
        self._evicted_count: int = 0
        self._overflow_warned: bool = False

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def overflow_policy(self) -> DeadLetterOverflowPolicy:
        return self._overflow_policy

    @property
    def evicted_count(self) -> int:
        return self._evicted_count

    @property
    def overflow_warned(self) -> bool:
        """Whether an overflow warning has been emitted for the current overflow episode."""
        return self._overflow_warned

    @property
    def count(self) -> int:
        return len(self._records)

    @property
    def records(self) -> tuple[DeadLetterRecord, ...]:
        """Immutable snapshot of all records in FIFO order."""
        return tuple(self._records)

    def record(
        self,
        envelope: MessageEnvelope | None,
        reason: DeadLetterReason,
        diagnostic: str,
        *,
        now_utc: datetime | None = None,
    ) -> DeadLetterRecord:
        """Add a dead-letter record.

        Raises:
            DeadLetterCapacityError: when queue is full under ``REJECT_NEW`` policy.
        """
        if len(self._records) >= self._capacity:
            if self._overflow_policy is DeadLetterOverflowPolicy.REJECT_NEW:
                raise DeadLetterCapacityError(
                    f"Dead-letter queue at capacity ({self._capacity}); "
                    f"REJECT_NEW policy prohibits insertion"
                )
            # DROP_OLDEST
            self._records.popleft()
            self._evicted_count += 1
            # Overflow warning: emitted upon transition into overflow state to provide
            # notification while avoiding unbounded warning storms.
            # Direct warnings.warn, does NOT recurse via dispatcher.
            if not self._overflow_warned:
                self._overflow_warned = True
                warnings.warn(
                    f"Dead-letter queue overflow: evicted oldest record (total evicted: {self._evicted_count})",
                    stacklevel=2,
                )

        ts = now_utc or datetime.now(timezone.utc)
        entry = DeadLetterRecord(
            envelope=envelope,
            reason=reason,
            diagnostic=diagnostic,
            timestamp_utc=ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        )
        self._records.append(entry)
        return entry

    def clear(self) -> None:
        """Reset all records, evicted_count, and overflow warning state."""
        self._records.clear()
        self._evicted_count = 0
        self._overflow_warned = False
