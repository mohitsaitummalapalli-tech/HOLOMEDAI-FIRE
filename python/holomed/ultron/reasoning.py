# -*- coding: utf-8 -*-
"""Deterministic Rule-Based Reasoning Engine for M04 Ultron."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Optional, Sequence
import uuid

from holomed.ultron.exceptions import UltronReasoningDepthError, UltronReasoningError
from holomed.ultron.intents import IntentManager
from holomed.ultron.models import (
    MAX_REASONING_BUDGET_MS,
    MAX_REASONING_DEPTH,
    MAX_REASONING_TRACE_STEPS,
    ActionIntent,
    ActionType,
    ConflictSeverity,
    MultimodalContext,
    ReasoningTrace,
)

RuleCondition = Callable[[MultimodalContext], bool]
RuleAction = Callable[[MultimodalContext], tuple[ActionIntent, ...]]


@dataclass(frozen=True)
class ReasoningRule:
    """Deterministic, side-effect-free reasoning rule."""

    rule_id: str
    priority: int
    condition: RuleCondition
    action: RuleAction
    description: str = ""


# Standard Built-in Deterministic Rules
def _condition_pinch_select(ctx: MultimodalContext) -> bool:
    return "PINCH" in ctx.active_gestures


def _action_pinch_select(ctx: MultimodalContext) -> tuple[ActionIntent, ...]:
    # Select targeted entity or emit general select
    target_id = ctx.entities[0].entity_id if ctx.entities else None
    now_utc = ctx.timestamp_utc
    return (
        ActionIntent(
            intent_id=str(uuid.uuid4()),
            action_type=ActionType.SELECT,
            confidence=0.90,
            entity_id=target_id,
            parameters={"gesture": "PINCH"},
            explanation="Pinch gesture detected: trigger selection intent.",
            epoch_id=ctx.epoch_id,
            timestamp_utc=now_utc,
        ),
    )


def _condition_point_highlight(ctx: MultimodalContext) -> bool:
    return "POINT" in ctx.active_gestures


def _action_point_highlight(ctx: MultimodalContext) -> tuple[ActionIntent, ...]:
    target_id = ctx.entities[0].entity_id if ctx.entities else None
    now_utc = ctx.timestamp_utc
    return (
        ActionIntent(
            intent_id=str(uuid.uuid4()),
            action_type=ActionType.HIGHLIGHT,
            confidence=0.85,
            entity_id=target_id,
            parameters={"gesture": "POINT"},
            explanation="Pointing gesture detected: trigger highlight intent.",
            epoch_id=ctx.epoch_id,
            timestamp_utc=now_utc,
        ),
    )


def _condition_critical_conflict_confirmation(ctx: MultimodalContext) -> bool:
    return any(c.severity == ConflictSeverity.CRITICAL for c in ctx.conflicts)


def _action_critical_conflict_confirmation(ctx: MultimodalContext) -> tuple[ActionIntent, ...]:
    now_utc = ctx.timestamp_utc
    return (
        ActionIntent(
            intent_id=str(uuid.uuid4()),
            action_type=ActionType.REQUEST_CONFIRMATION,
            confidence=1.0,
            entity_id=None,
            parameters={"reason": "CRITICAL_MODALITY_CONFLICT"},
            explanation="Critical spatial/semantic modality discrepancy detected: request user confirmation.",
            epoch_id=ctx.epoch_id,
            timestamp_utc=now_utc,
        ),
    )


DEFAULT_REASONING_RULES: tuple[ReasoningRule, ...] = (
    ReasoningRule(
        rule_id="RULE_001_CRITICAL_CONFIRMATION",
        priority=10,
        condition=_condition_critical_conflict_confirmation,
        action=_action_critical_conflict_confirmation,
        description="Trigger confirmation request when critical modality conflict is detected",
    ),
    ReasoningRule(
        rule_id="RULE_010_PINCH_SELECT",
        priority=20,
        condition=_condition_pinch_select,
        action=_action_pinch_select,
        description="Trigger SELECT intent when PINCH gesture is active",
    ),
    ReasoningRule(
        rule_id="RULE_020_POINT_HIGHLIGHT",
        priority=30,
        condition=_condition_point_highlight,
        action=_action_point_highlight,
        description="Trigger HIGHLIGHT intent when POINT gesture is active",
    ),
)


class DeterministicRuleEngine:
    """Evaluates statically registered reasoning rules in strict (priority ASC, rule_id ASC) order."""

    def __init__(self, rules: Sequence[ReasoningRule] = DEFAULT_REASONING_RULES) -> None:
        self._rules: list[ReasoningRule] = list(rules)
        self._intent_manager = IntentManager()
        self._traces: list[ReasoningTrace] = []

    @property
    def rules(self) -> tuple[ReasoningRule, ...]:
        return tuple(self._rules)

    @property
    def traces(self) -> tuple[ReasoningTrace, ...]:
        return tuple(self._traces)

    def register_rule(self, rule: ReasoningRule) -> None:
        """Register a new static reasoning rule before service start."""
        if any(r.rule_id == rule.rule_id for r in self._rules):
            raise UltronReasoningError(f"Duplicate rule_id {rule.rule_id!r}")
        self._rules.append(rule)

    def evaluate(
        self,
        context: MultimodalContext,
        depth: int = 0,
    ) -> tuple[tuple[ActionIntent, ...], ReasoningTrace]:
        """Evaluate registered rules against context with depth guarding and budget tracking."""
        if depth >= MAX_REASONING_DEPTH:
            raise UltronReasoningDepthError(
                f"Reasoning evaluation depth ({depth}) exceeded maximum allowed ({MAX_REASONING_DEPTH})"
            )

        start_time = time.perf_counter()
        trace_id = str(uuid.uuid4())
        fired_rules: list[str] = []
        intents: list[ActionIntent] = []

        # Sort rules strictly by (priority ASC, rule_id ASC)
        sorted_rules = sorted(self._rules, key=lambda r: (r.priority, r.rule_id))

        for rule in sorted_rules:
            try:
                if rule.condition(context):
                    fired_rules.append(rule.rule_id)
                    rule_intents = rule.action(context)
                    for raw_intent in rule_intents:
                        # Deduplicate via IntentManager using D215 canonical key
                        deduped = self._intent_manager.create_intent(
                            action_type=raw_intent.action_type,
                            confidence=raw_intent.confidence,
                            epoch_id=context.epoch_id,
                            entity_id=raw_intent.entity_id,
                            parameters=raw_intent.parameters,
                            explanation=raw_intent.explanation,
                            timestamp_utc=context.timestamp_utc,
                        )
                        if deduped is not None:
                            intents.append(deduped)
            except Exception as e:
                raise UltronReasoningError(f"Rule {rule.rule_id} evaluation raised an unhandled error: {e}") from e

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        degraded = elapsed_ms > MAX_REASONING_BUDGET_MS

        obs_ids = tuple(o.observation_id for o in context.observations)
        conflict_ids = tuple(c.conflict_id for c in context.conflicts)
        intent_ids = tuple(i.intent_id for i in intents)

        trace = ReasoningTrace(
            trace_id=trace_id,
            epoch_id=context.epoch_id,
            input_observation_ids=obs_ids[:MAX_REASONING_TRACE_STEPS],
            fired_rule_ids=tuple(fired_rules[:MAX_REASONING_TRACE_STEPS]),
            conflict_ids=conflict_ids[:MAX_REASONING_TRACE_STEPS],
            intent_ids=intent_ids[:MAX_REASONING_TRACE_STEPS],
            processing_time_ms=round(elapsed_ms, 3),
            degraded=degraded,
        )
        self._traces.append(trace)
        if len(self._traces) > 128:
            self._traces.pop(0)

        return tuple(intents), trace

    def clear(self) -> None:
        """Clear all generated intents and reasoning traces."""
        self._intent_manager.clear()
        self._traces.clear()
