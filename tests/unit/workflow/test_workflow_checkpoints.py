# -*- coding: utf-8 -*-
"""Unit Tests for AnatomicalCheckpointValidator and Spatial Tolerance Checks."""

from __future__ import annotations

from holomed.workflow.checkpoints import AnatomicalCheckpointValidator
from holomed.workflow.models import AnatomicalCheckpoint, InterlockSeverity


def test_anatomical_checkpoint_evaluation() -> None:
    """Verify checkpoint evaluation against confidence, uncertainty, and tolerance."""
    validator = AnatomicalCheckpointValidator()
    chk = AnatomicalCheckpoint(
        checkpoint_id="chk_femur",
        entity_id="femur_head",
        expected_relation="within_margins",
        max_tolerance_mm=2.0,
        min_confidence=0.80,
        max_uncertainty=0.20,
    )
    validator.register_checkpoint(chk)

    # 1. Valid metrics -> Passed (status=True)
    it_ok = validator.evaluate_checkpoint("chk_femur", 0.90, 0.10, 1.2, 1, "sess")
    assert it_ok.status is True
    assert it_ok.severity == InterlockSeverity.INFO

    # 2. Low confidence -> Tripped BLOCKING
    it_low_conf = validator.evaluate_checkpoint("chk_femur", 0.70, 0.10, 1.2, 1, "sess")
    assert it_low_conf.status is False
    assert it_low_conf.severity == InterlockSeverity.BLOCKING
    assert "INSUFFICIENT_CONFIDENCE" in it_low_conf.condition_name

    # 3. High uncertainty -> Tripped BLOCKING
    it_high_unc = validator.evaluate_checkpoint("chk_femur", 0.90, 0.35, 1.2, 1, "sess")
    assert it_high_unc.status is False
    assert it_high_unc.severity == InterlockSeverity.BLOCKING
    assert "EXCESSIVE_UNCERTAINTY" in it_high_unc.condition_name

    # 4. Tolerance exceeded -> Tripped BLOCKING
    it_tol_exc = validator.evaluate_checkpoint("chk_femur", 0.90, 0.10, 3.5, 1, "sess")
    assert it_tol_exc.status is False
    assert it_tol_exc.severity == InterlockSeverity.BLOCKING
    assert "TOLERANCE_EXCEEDED" in it_tol_exc.condition_name
