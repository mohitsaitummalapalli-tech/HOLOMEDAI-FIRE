# -*- coding: utf-8 -*-
"""Unit tests for Action Intent Deduplication (D215)."""

from __future__ import annotations

import uuid
import pytest

from holomed.ultron.intents import IntentManager
from holomed.ultron.models import (
    MAX_INTENT_KEYS,
    ActionType,
)


def test_d215_intent_deduplication_unhashable_mapping_handled() -> None:
    """Verify D215 canonical parameter hashing safely handles nested unhashable dicts."""
    manager = IntentManager()

    # Create intent with nested unhashable dictionary
    nested_params = {
        "options": {"scale": 1.5, "mode": "snap"},
        "tags": ["urgent", "spatial"],
    }

    intent_1 = manager.create_intent(
        action_type=ActionType.SELECT,
        confidence=0.95,
        epoch_id=1,
        entity_id="entity_1",
        parameters=nested_params,
        explanation="First selection",
    )
    assert intent_1 is not None

    # Exact duplicate intent within same epoch/entity/action/parameters
    intent_duplicate = manager.create_intent(
        action_type=ActionType.SELECT,
        confidence=0.95,
        epoch_id=1,
        entity_id="entity_1",
        parameters=nested_params,
        explanation="Duplicate selection",
    )
    assert intent_duplicate is None  # Filtered by deduplication


def test_intent_manager_cache_capacity() -> None:
    """Verify MAX_INTENT_KEYS (2048) bounded FIFO eviction."""
    manager = IntentManager()

    for i in range(2100):
        intent = manager.create_intent(
            action_type=ActionType.HIGHLIGHT,
            confidence=0.8,
            epoch_id=1,
            entity_id=f"entity_{i}",
            parameters={"index": i},
        )
        assert intent is not None

    assert manager.seen_count == MAX_INTENT_KEYS  # 2048
