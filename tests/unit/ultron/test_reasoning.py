# -*- coding: utf-8 -*-
"""Unit tests for Deterministic Reasoning Engine (D214)."""

from __future__ import annotations

from types import MappingProxyType
import uuid
import pytest

from holomed.ultron.exceptions import UltronReasoningDepthError
from holomed.ultron.models import (
    ActionIntent,
    ActionType,
    ConflictSeverity,
    ConflictType,
    EntityType,
    Modality,
    MultimodalContext,
    MultimodalEntity,
    ObservationConflict,
)
from holomed.ultron.reasoning import (
    DeterministicRuleEngine,
    ReasoningRule,
)


def test_d214_rule_priority_and_id_ordering() -> None:
    """Verify rules execute strictly in (priority ASC, rule_id ASC) order."""
    execution_order = []

    def make_rule(r_id: str, prio: int) -> ReasoningRule:
        def cond(ctx: MultimodalContext) -> bool:
            return True
        def act(ctx: MultimodalContext) -> tuple[ActionIntent, ...]:
            execution_order.append(r_id)
            return ()
        return ReasoningRule(rule_id=r_id, priority=prio, condition=cond, action=act)

    engine = DeterministicRuleEngine(
        rules=[
            make_rule("RULE_Z", 20),
            make_rule("RULE_B", 10),
            make_rule("RULE_A", 10),
            make_rule("RULE_C", 5),
        ]
    )

    ctx = MultimodalContext(
        epoch_id=1,
        timestamp_utc="2026-09-01T12:00:00.000000Z",
        observations=(),
        entities=(),
        conflicts=(),
        active_gestures=(),
        vision_features=MappingProxyType({}),
        audio_features=MappingProxyType({}),
    )

    engine.evaluate(ctx)
    # Expected order: RULE_C (5), RULE_A (10), RULE_B (10), RULE_Z (20)
    assert execution_order == ["RULE_C", "RULE_A", "RULE_B", "RULE_Z"]


def test_reasoning_depth_guard() -> None:
    """Verify MAX_REASONING_DEPTH (8) triggers UltronReasoningDepthError."""
    engine = DeterministicRuleEngine()
    ctx = MultimodalContext(
        epoch_id=1,
        timestamp_utc="2026-09-01T12:00:00.000000Z",
        observations=(),
        entities=(),
        conflicts=(),
        active_gestures=(),
        vision_features=MappingProxyType({}),
        audio_features=MappingProxyType({}),
    )

    with pytest.raises(UltronReasoningDepthError):
        engine.evaluate(ctx, depth=8)


def test_builtin_rules_pinch_select_and_point_highlight() -> None:
    """Verify standard default rules trigger expected ActionIntents."""
    engine = DeterministicRuleEngine()

    dummy_entity = MultimodalEntity(
        entity_id="entity_target",
        entity_type=EntityType.HAND,
        observations=(),
        confidence=0.9,
        position=(0.0, 0.0, 1.0),
        velocity=None,
        active_gestures=("PINCH",),
        acoustic_direction=None,
        source_modalities=frozenset({Modality.GESTURE}),
        epoch_id=1,
    )

    ctx = MultimodalContext(
        epoch_id=1,
        timestamp_utc="2026-09-01T12:00:00.000000Z",
        observations=(),
        entities=(dummy_entity,),
        conflicts=(),
        active_gestures=("PINCH",),
        vision_features=MappingProxyType({}),
        audio_features=MappingProxyType({}),
    )

    intents, trace = engine.evaluate(ctx)
    assert len(intents) == 1
    assert intents[0].action_type == ActionType.SELECT
    assert intents[0].entity_id == "entity_target"
    assert "RULE_010_PINCH_SELECT" in trace.fired_rule_ids
