# -*- coding: utf-8 -*-
"""Unit Tests for PlanVerificationEngine and Safety Checklist Matching."""

from __future__ import annotations

from dataclasses import replace
import pytest

from holomed.planning.exceptions import PlanningVerificationError
from holomed.planning.models import (
    PlanVerificationRecord,
    SurgicalLaterality,
    SurgicalPlanDefinition,
)
from holomed.planning.verification import PlanVerificationEngine


def test_plan_verification_engine_success(sample_plan: SurgicalPlanDefinition) -> None:
    """Verify successful verification of a locked plan with exact field matches."""
    locked_plan = replace(sample_plan, is_locked=True)

    record = PlanVerificationEngine.verify_plan(
        plan=locked_plan,
        session_id="session_live_01",
        epoch_id=1,
        verifier_operator_id="surgeon_smith_01",
        reported_patient_hash=locked_plan.case_context.patient_hash,
        reported_procedure_code="THA_POSTERIOR_APPROACH",
        reported_laterality=SurgicalLaterality.RIGHT,
    )

    assert isinstance(record, PlanVerificationRecord)
    assert record.verified is True
    assert record.patient_identity_matched is True
    assert record.procedure_matched is True
    assert record.laterality_matched is True


def test_plan_verification_fails_if_unlocked(sample_plan: SurgicalPlanDefinition) -> None:
    """Verify unlocked plan raises PlanningVerificationError."""
    assert sample_plan.is_locked is False

    with pytest.raises(PlanningVerificationError) as exc_info:
        PlanVerificationEngine.verify_plan(
            plan=sample_plan,
            session_id="session_live_01",
            epoch_id=1,
            verifier_operator_id="surgeon_smith_01",
            reported_patient_hash=sample_plan.case_context.patient_hash,
            reported_procedure_code=sample_plan.case_context.procedure_code,
            reported_laterality=sample_plan.case_context.laterality,
        )
    assert "must be locked before verification" in str(exc_info.value)


def test_plan_verification_fails_on_laterality_mismatch(sample_plan: SurgicalPlanDefinition) -> None:
    """Verify wrong-site surgery protection rejects laterality mismatch (D295, D305)."""
    locked_plan = replace(sample_plan, is_locked=True)

    with pytest.raises(PlanningVerificationError) as exc_info:
        PlanVerificationEngine.verify_plan(
            plan=locked_plan,
            session_id="session_live_01",
            epoch_id=1,
            verifier_operator_id="surgeon_smith_01",
            reported_patient_hash=locked_plan.case_context.patient_hash,
            reported_procedure_code=locked_plan.case_context.procedure_code,
            reported_laterality=SurgicalLaterality.LEFT,  # Expected RIGHT!
        )
    assert "laterality" in str(exc_info.value)


def test_plan_verification_fails_on_procedure_mismatch(sample_plan: SurgicalPlanDefinition) -> None:
    """Verify procedure code mismatch is rejected."""
    locked_plan = replace(sample_plan, is_locked=True)

    with pytest.raises(PlanningVerificationError) as exc_info:
        PlanVerificationEngine.verify_plan(
            plan=locked_plan,
            session_id="session_live_01",
            epoch_id=1,
            verifier_operator_id="surgeon_smith_01",
            reported_patient_hash=locked_plan.case_context.patient_hash,
            reported_procedure_code="KNEE_ARTHROPLASTY",  # Expected THA
            reported_laterality=SurgicalLaterality.RIGHT,
        )
    assert "procedure_code" in str(exc_info.value)


def test_plan_verification_fails_on_patient_hash_mismatch(sample_plan: SurgicalPlanDefinition) -> None:
    """Verify patient hash mismatch is rejected."""
    locked_plan = replace(sample_plan, is_locked=True)

    with pytest.raises(PlanningVerificationError) as exc_info:
        PlanVerificationEngine.verify_plan(
            plan=locked_plan,
            session_id="session_live_01",
            epoch_id=1,
            verifier_operator_id="surgeon_smith_01",
            reported_patient_hash="0" * 64,  # Wrong hash
            reported_procedure_code=locked_plan.case_context.procedure_code,
            reported_laterality=SurgicalLaterality.RIGHT,
        )
    assert "patient_hash" in str(exc_info.value)
