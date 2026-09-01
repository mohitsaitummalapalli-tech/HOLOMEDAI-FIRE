# -*- coding: utf-8 -*-
"""Action Intent Management and Deduplication Engine for M04 Ultron."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence
import uuid

from holomed.ultron.models import (
    MAX_INTENT_KEYS,
    ActionIntent,
    ActionType,
)
from holomed.ultron.serialization import canonical_json_parameters, serialize_ultron_payload


class IntentManager:
    """Manages creation, parameter canonicalization, and deduplication of action intents."""

    def __init__(self) -> None:
        self._seen_keys: OrderedDict[tuple[int, str, str, str], str] = OrderedDict()

    @property
    def seen_count(self) -> int:
        return len(self._seen_keys)

    def create_intent(
        self,
        action_type: ActionType,
        confidence: float,
        epoch_id: int,
        entity_id: Optional[str] = None,
        parameters: Optional[Mapping[str, Any]] = None,
        explanation: str = "",
        timestamp_utc: Optional[str] = None,
    ) -> Optional[ActionIntent]:
        """Synthesize an ActionIntent and filter duplicates using D215 canonical key."""
        params = parameters or {}
        norm_params = serialize_ultron_payload(params)
        canon_json = canonical_json_parameters(norm_params)

        # D215 hashable key: (epoch_id, entity_id_or_empty, action_type.value, canonical_json_parameters)
        e_id_str = entity_id or ""
        dedup_key = (epoch_id, e_id_str, action_type.value, canon_json)

        if dedup_key in self._seen_keys:
            # Deduplicated: identical intent already emitted
            return None

        # Bounded FIFO cache eviction at 2048 keys
        if len(self._seen_keys) >= MAX_INTENT_KEYS:
            self._seen_keys.popitem(last=False)

        intent_id = str(uuid.uuid4())
        self._seen_keys[dedup_key] = intent_id

        ts = timestamp_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return ActionIntent(
            intent_id=intent_id,
            action_type=action_type,
            confidence=confidence,
            entity_id=entity_id,
            parameters=norm_params,
            explanation=explanation,
            epoch_id=epoch_id,
            timestamp_utc=ts,
        )

    def clear(self) -> None:
        """Clear all deduplication cache keys."""
        self._seen_keys.clear()
