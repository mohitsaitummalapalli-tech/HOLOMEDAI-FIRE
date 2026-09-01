"""Unit and adversarial tests for VisionPipeline 7-stage processing."""

from __future__ import annotations

import time
import pytest

from holomed.runtime.logging import SecretFilter
from holomed.vision.models import VisionQuality
from holomed.vision.pipeline import VisionPipeline
from holomed.vision.store import VisionFrameStore
from tests.unit.vision.conftest import make_test_frame


def test_pipeline_execution_produces_valid_result() -> None:
    """Verifies standard pipeline run produces landmarks, pose, and OPTIMAL quality."""
    store = VisionFrameStore()
    pipeline = VisionPipeline()

    desc, data = make_test_frame(width=160, height=120)
    handle = store.allocate_slot(desc, data)
    view = store.get_readonly_view(handle)

    result = pipeline.process(desc, view)
    assert result.frame_id == desc.frame_id
    assert result.quality == VisionQuality.OPTIMAL
    assert len(result.landmarks) > 0
    assert result.budget_exceeded is False
    assert result.execution_time_ms >= 0.0


def test_observational_budget_overage_marking(monkeypatch) -> None:
    """Simulates artificial delay exceeding 25ms; asserts QUALITY_DEGRADED without crash."""
    store = VisionFrameStore()
    pipeline = VisionPipeline()

    desc, data = make_test_frame(width=80, height=60)
    handle = store.allocate_slot(desc, data)
    view = store.get_readonly_view(handle)

    # Monkeypatch stage 3 to simulate 30ms processing time
    orig_extract = pipeline._extract_features
    def slow_extract(*args, **kwargs):
        time.sleep(0.030)  # 30ms > 25ms budget
        return orig_extract(*args, **kwargs)

    monkeypatch.setattr(pipeline, "_extract_features", slow_extract)

    result = pipeline.process(desc, view)
    assert result.budget_exceeded is True
    assert result.quality == VisionQuality.DEGRADED
    assert result.execution_time_ms >= 25.0


def test_pipeline_handles_corrupt_view_gracefully() -> None:
    """Buffer length mismatch fails validation and sandbox returns FAILED result without crash."""
    pipeline = VisionPipeline()
    desc, data = make_test_frame(width=80, height=60)

    # Pass truncated view
    short_view = memoryview(data[:10])
    result = pipeline.process(desc, short_view)

    assert result.quality == VisionQuality.FAILED
    assert result.error_message is not None
    assert "VisionFrameValidationError" in result.error_message
