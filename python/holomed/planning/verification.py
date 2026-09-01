# -*- coding: utf-8 -*-
"""Preoperative Plan Verification Engine (WHO Surgical Safety Timeout)."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from holomed.planning.exceptions import PlanningVerificationError
from holomed.planning.models import (
    PlanVerificationRecord,
    SurgicalLaterality,
    SurgicalPlanDefinition,
)


class PlanVerificationEngine:
    """Evaluates cross-verification between clinical session and locked plan."""

    @staticmethod
    def verify_plan(
        plan: SurgicalPlanDefinition,
        session_id: str,
        epoch_id: int,
        verifier_operator_id: str,
        reported_patient_hash: str,
        reported_procedure_code: str,
        reported_laterality: SurgicalLaterality,
    ) -> PlanVerificationRecord:
        """Execute rigorous preoperative checklist matching (D295, D305)."""
        if not plan.is_locked:
            raise PlanningVerificationError(f"Plan {plan.plan_id!r} must be locked before verification")

        patient_match = plan.case_context.patient_hash.lower() == reported_patient_hash.lower()
        proc_match = plan.case_context.procedure_code.strip() == reported_procedure_code.strip()
        laterality_match = plan.case_context.laterality == reported_laterality

        all_matched = bool(patient_match and proc_match and laterality_match)
        now_utc = datetime.now(timezone.utc).isoformat()
        vid = f"verif_{uuid.uuid4().hex[:12]}"

        details = {
            "expected_patient_hash": plan.case_context.patient_hash,
            "reported_patient_hash": reported_patient_hash,
            "expected_procedure": plan.case_context.procedure_code,
            "reported_procedure": reported_procedure_code,
            "expected_laterality": plan.case_context.laterality.value,
            "reported_laterality": reported_laterality.value,
        }

        if not all_matched:
            mismatches = []
            if not patient_match:
                mismatches.append("patient_hash")
            if not proc_match:
                mismatches.append("procedure_code")
            if not laterality_match:
                mismatches.append("laterality")
            raise PlanningVerificationError(
                f"Plan verification failed on mismatched fields: {', '.join(mismatches)}",
                details=details,
            )

        return PlanVerificationRecord(
            verification_id=vid,
            plan_id=plan.plan_id,
            session_id=session_id,
            epoch_id=epoch_id,
            verified=True,
            patient_identity_matched=patient_match,
            procedure_matched=proc_match,
            laterality_matched=laterality_match,
            verifier_operator_id=verifier_operator_id,
            timestamp_utc=now_utc,
            details=details,
        )
