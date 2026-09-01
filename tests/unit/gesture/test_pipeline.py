# -*- coding: utf-8 -*-
"""Unit tests for synchronous GesturePipeline orchestration, budgets, and quality."""

from __future__ import annotations

import time
import pytest

from holomed.gesture.models import (
    GestureQuality,
    GestureType,
    MAX_GESTURE_HISTORY,
)
from holomed.gesture.pipeline import GesturePipeline
from holomed.vision.models import SpatialLandmark
from tests.unit.gesture.conftest import make_observation


def test_pipeline_synchronous_execution(pointing_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify clean synchronous execution and result generation."""
    pipeline = GesturePipeline()
    obs = make_observation(pointing_landmarks, sequence_number=1)

    res = pipeline.process(obs)
    assert res.quality in (GestureQuality.HEALTHY, GestureQuality.DEGRADED)
    assert res.execution_time_ms >= 0.0
    assert len(res.tracks) == 1
    assert len(res.gestures) == 1
    assert res.gestures[0].gesture_type == GestureType.POINT


def test_pipeline_history_bounded_256(pointing_landmarks: tuple[SpatialLandmark, ...]) -> None:
    """Verify pipeline history is capped at MAX_GESTURE_HISTORY (256)."""
    pipeline = GesturePipeline()

    for i in range(270):
        obs = make_observation(pointing_landmarks, sequence_number=i + 1)
        pipeline.process(obs)

    assert len(pipeline.history) == MAX_GESTURE_HISTORY


def test_pipeline_degraded_quality_on_unprojection_failure(
    open_hand_landmarks: tuple[SpatialLandmark, ...],
) -> None:
    """Verify invalid or missing depth results in DEGRADED quality snapshot."""
    # Corrupt landmark depth
    corrupted = []
    for lm in open_hand_landmarks:
        corrupted.append(
            SpatialLandmark(lm.landmark_id, lm.name, lm.u, lm.v, depth_m=None, confidence=lm.confidence)
        )

    pipeline = GesturePipeline()
    obs = make_observation(tuple(corrupted), is_partial=True)

    res = pipeline.process(obs)
    assert res.quality == GestureQuality.DEGRADED
    assert res.error_message is not None
    assert "invalid or missing depth_m" in res.error_message


def test_pipeline_budget_exceeded_handling(
    pointing_landmarks: tuple[SpatialLandmark, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify budget_exceeded flag set when execution time exceeds 10.0 ms."""
    pipeline = GesturePipeline()
    obs = make_observation(pointing_landmarks)

    # Mock time.perf_counter to simulate 15ms duration
    times = [100.0, 100.015]  # 15 ms
    time_iter = iter(times)
    monkeypatch.setattr(time, "perf_counter", lambda: next(time_iter))

    res = pipeline.process(obs)
    assert res.budget_exceeded is True
    assert res.quality == GestureQuality.DEGRADED
