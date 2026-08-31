"""HoloMed AI - Bounded FIFO idempotency tracker."""

from __future__ import annotations

import collections
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple

from holomed.devices.control.exceptions import IdempotencyConflictError
from holomed.devices.control.models import IdempotencyRecord, MAX_IDEMPOTENCY_KEYS


def compute_payload_hash(payload: Mapping[str, Any]) -> str:
    """Compute deterministic SHA-256 hash of a normalized JSON payload."""
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class IdempotencyTracker:
    """Bounded FIFO cache for command execution deduplication.

    Guarantees:
    - Bounded to exactly MAX_IDEMPOTENCY_KEYS (4096).
    - Eviction is strict FIFO ring buffer.
    - Matching message_id + matching payload hash => returns cached response.
    - Matching message_id + conflicting payload hash => raises IdempotencyConflictError.
    """

    def __init__(self, max_keys: int = MAX_IDEMPOTENCY_KEYS) -> None:
        self._max_keys = max_keys
        self._records: dict[str, IdempotencyRecord] = {}
        self._order: collections.deque[str] = collections.deque()

    def get(self, message_id: str, current_payload: Mapping[str, Any]) -> Optional[IdempotencyRecord]:
        """Lookup cached idempotency record and verify payload identity."""
        rec = self._records.get(message_id)
        if rec is None:
            return None

        current_hash = compute_payload_hash(current_payload)
        if rec.payload_hash != current_hash:
            raise IdempotencyConflictError(
                f"Command with message_id '{message_id}' already executed with a conflicting payload"
            )

        return rec

    def record(
        self,
        message_id: str,
        current_payload: Mapping[str, Any],
        response_payload: Mapping[str, Any],
        response_metadata: Mapping[str, Any] = MappingProxyType({}),
    ) -> None:
        """Store command execution outcome into FIFO ring buffer."""
        if message_id in self._records:
            return

        # FIFO eviction if at capacity
        while len(self._order) >= self._max_keys:
            oldest_id = self._order.popleft()
            self._records.pop(oldest_id, None)

        p_hash = compute_payload_hash(current_payload)
        rec = IdempotencyRecord(
            message_id=message_id,
            payload_hash=p_hash,
            response_payload=MappingProxyType(dict(response_payload)),
            response_metadata=MappingProxyType(dict(response_metadata)),
        )
        self._records[message_id] = rec
        self._order.append(message_id)

    def clear(self) -> None:
        """Clear tracker on service stop."""
        self._records.clear()
        self._order.clear()

    def __len__(self) -> int:
        return len(self._records)
