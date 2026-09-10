# -*- coding: utf-8 -*-
"""Milestone 33 Unit and Integration Tests: Checkpoint Ownership, Lifecycle & Capacity Hardening.

Verifies:
1. Plan-owned checkpoint gets correct session owner.
2. Every registered checkpoint appears in both storage indexes (_checkpoints and _session_checkpoints).
3. Missing ownership fails closed (WorkflowValidationError).
4. Same-owner duplicate behaves deterministically (updates definition in-place without capacity increase).
5. Cross-owner duplicate cannot reassign ownership (WorkflowValidationError fail-closed).
6. Session A cannot evaluate Session B checkpoint (CHECKPOINT_MISSING fail-closed).
7. Evaluation does not mutate ownership indexes (no side-effects during query).
8. Session A eviction removes all Session A checkpoints.
9. Session A eviction leaves Session B checkpoints intact.
10. Repeated eviction is safe and idempotent.
11. Capacity is reclaimed immediately after eviction.
12. 32 -> teardown -> 33rd checkpoint succeeds (hostile bug reproduction and resolution).
13. Session ID reuse has zero stale checkpoints.
14. Full plan-lock -> checkpoint-registration path works.
15. Storage divergence protection: failure leaves dual stores consistent.
16. Atomic plan-lock rollback: failure on partial batch rolls back checkpoints and preserves unlocked plan state.
"""

from __future__ import annotations

import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.execution._capability import _create_execution_capability
from holomed.planning.checkpoints import derive_checkpoints_from_plan
from holomed.planning.models import (
    PatientCaseContext,
    SafetyExclusionZone,
    SurgicalLaterality,
    SurgicalPlanDefinition,
    TrajectoryPlan,
)
from holomed.planning.service import PlanningService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.workflow.checkpoints import AnatomicalCheckpointValidator
from holomed.workflow.exceptions import (
    WorkflowCapacityError,
    WorkflowValidationError,
)
from holomed.workflow.models import (
    MAX_REGISTERED_CHECKPOINTS,
    AnatomicalCheckpoint,
    InterlockSeverity,
)
from holomed.workflow.service import WorkflowService


def _create_checkpoint(
    cid: str,
    structure: str = "acetabular_fossa",
    tolerance_mm: float = 2.0,
    min_confidence: float = 0.85,
    max_uncertainty: float = 0.15,
) -> AnatomicalCheckpoint:
    """Helper to create valid AnatomicalCheckpoint."""
    return AnatomicalCheckpoint(
        checkpoint_id=cid,
        entity_id=structure,
        expected_relation="trajectory_aligned",
        max_tolerance_mm=tolerance_mm,
        min_confidence=min_confidence,
        max_uncertainty=max_uncertainty,
    )


def _create_plan(plan_id: str, case_id: str = "case_m33") -> SurgicalPlanDefinition:
    """Helper to create a valid surgical plan with 1 trajectory and 1 exclusion zone (2 checkpoints)."""
    case_context = PatientCaseContext(
        case_id=case_id,
        patient_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        procedure_code="THA_POSTERIOR_APPROACH",
        laterality=SurgicalLaterality.RIGHT,
        primary_surgeon_id="surgeon_m33_01",
        scheduled_date_utc="2026-09-15T08:00:00Z",
    )
    traj = TrajectoryPlan(
        trajectory_id=f"traj_{plan_id}",
        target_structure="acetabular_fossa",
        entry_point_mm=(10.0, 20.0, 30.0),
        target_point_mm=(10.0, 20.0, 80.0),
        max_lateral_deviation_mm=2.0,
        max_angular_deviation_deg=3.0,
        min_confidence=0.85,
        max_uncertainty=0.15,
    )
    zone = SafetyExclusionZone(
        zone_id=f"zone_{plan_id}",
        anatomical_structure="sciatic_nerve_trunk",
        center_point_mm=(35.0, 40.0, 75.0),
        bounding_radius_mm=10.0,
        min_clearance_mm=5.0,
        severity_if_breached=InterlockSeverity.BLOCKING,
    )
    return SurgicalPlanDefinition(
        plan_id=plan_id,
        version="1.0",
        case_context=case_context,
        trajectories=(traj,),
        exclusion_zones=(zone,),
    )


# =========================================================================
# 1 & 2: Ownership Registration and Dual-Store Consistency
# =========================================================================

def test_m33_register_checkpoint_dual_store_consistency() -> None:
    """Verify registered checkpoint appears in both _checkpoints and _session_checkpoints."""
    validator = AnatomicalCheckpointValidator()
    chk = _create_checkpoint("chk_01")
    session_id = "SESSION-A"

    validator.register_checkpoint(chk, session_id=session_id)

    assert validator.checkpoint_count == 1
    assert "chk_01" in validator._checkpoints
    assert validator._checkpoints["chk_01"] == chk
    assert session_id in validator._session_checkpoints
    assert "chk_01" in validator._session_checkpoints[session_id]


# =========================================================================
# 3: Missing Ownership Fails Closed
# =========================================================================

@pytest.mark.parametrize("invalid_session", [None, "", "   ", 123, []])
def test_m33_register_checkpoint_missing_ownership_fails_closed(invalid_session) -> None:
    """Verify registration without a valid string session_id raises WorkflowValidationError."""
    validator = AnatomicalCheckpointValidator()
    chk = _create_checkpoint("chk_fail")

    with pytest.raises(WorkflowValidationError, match="session_id must be a non-empty string"):
        validator.register_checkpoint(chk, session_id=invalid_session)  # type: ignore

    # Zero state mutation
    assert validator.checkpoint_count == 0
    assert len(validator._checkpoints) == 0
    assert len(validator._session_checkpoints) == 0


def test_m33_workflow_service_register_checkpoint_missing_ownership_fails_closed() -> None:
    """Verify WorkflowService.register_checkpoint enforces session_id fail-closed."""
    wf = WorkflowService()
    chk = _create_checkpoint("chk_wf_fail")

    with pytest.raises(WorkflowValidationError, match="session_id must be a non-empty string"):
        wf.register_checkpoint(chk, session_id="")  # type: ignore

    with pytest.raises(WorkflowValidationError, match="session_id must be a non-empty string"):
        wf.register_checkpoint(chk, session_id=None)  # type: ignore

    assert wf.checkpoint_validator.checkpoint_count == 0


# =========================================================================
# 4 & 5: Duplicate Semantics (Same-Owner vs Cross-Owner)
# =========================================================================

def test_m33_duplicate_same_owner_deterministic_update() -> None:
    """Verify same-owner duplicate updates checkpoint definition without increasing capacity."""
    validator = AnatomicalCheckpointValidator()
    session_id = "SESSION-A"
    chk1 = _create_checkpoint("chk_dup", tolerance_mm=2.0)
    chk2 = _create_checkpoint("chk_dup", tolerance_mm=3.5)

    validator.register_checkpoint(chk1, session_id=session_id)
    assert validator.checkpoint_count == 1
    assert validator._checkpoints["chk_dup"].max_tolerance_mm == 2.0

    # Re-register under same session
    validator.register_checkpoint(chk2, session_id=session_id)
    assert validator.checkpoint_count == 1
    assert validator._checkpoints["chk_dup"].max_tolerance_mm == 3.5
    assert validator._session_checkpoints[session_id] == {"chk_dup"}


def test_m33_duplicate_cross_owner_fails_closed() -> None:
    """Verify cross-owner duplicate raises WorkflowValidationError and cannot reassign ownership."""
    validator = AnatomicalCheckpointValidator()
    chk1 = _create_checkpoint("chk_shared", tolerance_mm=2.0)
    chk2 = _create_checkpoint("chk_shared", tolerance_mm=5.0)

    validator.register_checkpoint(chk1, session_id="SESSION-A")

    # Session B attempts to register same checkpoint ID
    with pytest.raises(WorkflowValidationError, match="already registered under another session"):
        validator.register_checkpoint(chk2, session_id="SESSION-B")

    # Verify Session A retains sole ownership and original content
    assert validator.checkpoint_count == 1
    assert validator._checkpoints["chk_shared"].max_tolerance_mm == 2.0
    assert "SESSION-B" not in validator._session_checkpoints
    assert "chk_shared" in validator._session_checkpoints["SESSION-A"]


# =========================================================================
# 6 & 7: Cross-Session Evaluation Shield & Non-Mutating Query
# =========================================================================

def test_m33_cross_session_evaluation_fails_closed() -> None:
    """Verify Session B cannot evaluate Session A's checkpoint and gets CHECKPOINT_MISSING."""
    validator = AnatomicalCheckpointValidator()
    chk = _create_checkpoint("chk_a", tolerance_mm=2.0, min_confidence=0.80)
    validator.register_checkpoint(chk, session_id="SESSION-A")

    # Session B queries Session A's checkpoint
    interlock = validator.evaluate_checkpoint(
        checkpoint_id="chk_a",
        session_id="SESSION-B",
        measured_confidence=0.90,
        measured_uncertainty=0.10,
        measured_deviation_mm=1.0,
        epoch_id=1,
    )

    assert interlock.status is False
    assert interlock.severity == InterlockSeverity.BLOCKING
    assert interlock.condition_name == "CHECKPOINT_MISSING"
    assert "SESSION-B" in interlock.reason


def test_m33_evaluation_does_not_mutate_session_index() -> None:
    """Verify evaluation does NOT mutate _session_checkpoints (eliminating query-side-effect bug)."""
    validator = AnatomicalCheckpointValidator()
    chk = _create_checkpoint("chk_a")
    validator.register_checkpoint(chk, session_id="SESSION-A")

    assert "SESSION-B" not in validator._session_checkpoints

    # Session B evaluates unknown or foreign checkpoint
    validator.evaluate_checkpoint(
        checkpoint_id="chk_a",
        session_id="SESSION-B",
        measured_confidence=0.90,
        measured_uncertainty=0.10,
        measured_deviation_mm=1.0,
        epoch_id=1,
    )

    validator.evaluate_checkpoint(
        checkpoint_id="chk_nonexistent",
        session_id="SESSION-B",
        measured_confidence=0.90,
        measured_uncertainty=0.10,
        measured_deviation_mm=1.0,
        epoch_id=1,
    )

    # Prove SESSION-B has zero entries in _session_checkpoints
    assert "SESSION-B" not in validator._session_checkpoints


# =========================================================================
# 8, 9 & 10: Eviction Completeness, Isolation and Idempotence
# =========================================================================

def test_m33_session_eviction_completeness_and_isolation() -> None:
    """Verify evicting Session A removes all A checkpoints and leaves Session B checkpoints intact."""
    validator = AnatomicalCheckpointValidator()
    validator.register_checkpoint(_create_checkpoint("chk_a1"), session_id="SESSION-A")
    validator.register_checkpoint(_create_checkpoint("chk_a2"), session_id="SESSION-A")
    validator.register_checkpoint(_create_checkpoint("chk_b1"), session_id="SESSION-B")

    assert validator.checkpoint_count == 3

    # Evict Session A
    evicted = validator.evict_session("SESSION-A")
    assert evicted is True

    # Checkpoints for A removed
    assert validator.checkpoint_count == 1
    assert "chk_a1" not in validator._checkpoints
    assert "chk_a2" not in validator._checkpoints
    assert "SESSION-A" not in validator._session_checkpoints

    # Checkpoints for B completely untouched
    assert "chk_b1" in validator._checkpoints
    assert validator._session_checkpoints["SESSION-B"] == {"chk_b1"}


def test_m33_eviction_idempotence() -> None:
    """Verify repeated eviction of the same session returns False cleanly without corruption."""
    validator = AnatomicalCheckpointValidator()
    validator.register_checkpoint(_create_checkpoint("chk_01"), session_id="SESSION-A")

    assert validator.evict_session("SESSION-A") is True
    assert validator.evict_session("SESSION-A") is False
    assert validator.evict_session("SESSION-A") is False

    assert validator.checkpoint_count == 0
    assert len(validator._checkpoints) == 0
    assert len(validator._session_checkpoints) == 0


# =========================================================================
# 11 & 12: Capacity Reclamation and Hostile 32 -> 33 Attack Reproduction
# =========================================================================

def test_m33_capacity_reclamation_after_eviction() -> None:
    """Verify that after evicting MAX_REGISTERED_CHECKPOINTS, all slots are reusable."""
    validator = AnatomicalCheckpointValidator()
    session_a = "SESSION-A"

    # Fill up to limit (32)
    for i in range(MAX_REGISTERED_CHECKPOINTS):
        validator.register_checkpoint(_create_checkpoint(f"chk_cap_{i:02d}"), session_id=session_a)

    assert validator.checkpoint_count == MAX_REGISTERED_CHECKPOINTS

    # 33rd must fail
    with pytest.raises(WorkflowCapacityError, match="Registered checkpoint limit"):
        validator.register_checkpoint(_create_checkpoint("chk_cap_extra"), session_id=session_a)

    # Evict Session A
    assert validator.evict_session(session_a) is True
    assert validator.checkpoint_count == 0

    # Fill again under Session B -> must succeed
    session_b = "SESSION-B"
    for i in range(MAX_REGISTERED_CHECKPOINTS):
        validator.register_checkpoint(_create_checkpoint(f"chk_b_cap_{i:02d}"), session_id=session_b)

    assert validator.checkpoint_count == MAX_REGISTERED_CHECKPOINTS


def test_m33_hostile_32_to_33_plan_lock_churn(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Hostile Test: Reproduce the exact M33 discovery defect and prove resolution.

    In the discovery report, locking plans across multiple sessions left checkpoints unevicted,
    causing the 33rd checkpoint registration to crash with WorkflowCapacityError.
    With M33, session eviction reclaims slots so the 33rd checkpoint succeeds cleanly.
    """
    wf_srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    wf_srv.initialize(runtime_context)
    wf_srv.start()

    plan_srv = PlanningService(
        dispatcher=message_dispatcher,
        workflow_service=wf_srv,
        secret_filter=secret_filter,
    )
    plan_srv.initialize(runtime_context)
    plan_srv.start()

    # 1. Sequential session churn: 16 sequential clinical sessions lock a plan and tear down
    for i in range(16):
        session_id = f"sess_churn_{i:02d}"
        plan = _create_plan(f"plan_churn_{i:02d}")

        cap_sub = _create_execution_capability(id(plan_srv), session_id, "PLANNING_COORDINATION", 1)
        plan_srv.submit_plan(plan, session_id, capability=cap_sub)

        cap_lock = _create_execution_capability(id(plan_srv), session_id, "PLANNING_COORDINATION", 2)
        plan_srv.lock_plan(plan.plan_id, capability=cap_lock)

        # Verify checkpoints were registered
        assert wf_srv.checkpoint_validator.checkpoint_count == 2

        # Clinical session teardown: evict from planning and workflow
        plan_srv.evict_session(session_id)
        wf_srv.evict_session(session_id)

        # Under M33, checkpoints are immediately reclaimed after every teardown
        assert wf_srv.checkpoint_validator.checkpoint_count == 0

    # 2. Session 17 arrives (totaling 33 & 34 cumulative checkpoints across time)
    # In the unfixed codebase, this locked up with WorkflowCapacityError because all 32 prior checkpoints leaked.
    # Under M33, this succeeds cleanly!
    session_17 = "sess_churn_16"
    plan_17 = _create_plan("plan_churn_16")
    cap_sub_17 = _create_execution_capability(id(plan_srv), session_17, "PLANNING_COORDINATION", 1)
    plan_srv.submit_plan(plan_17, session_17, capability=cap_sub_17)
    cap_lock_17 = _create_execution_capability(id(plan_srv), session_17, "PLANNING_COORDINATION", 2)

    locked_17 = plan_srv.lock_plan(plan_17.plan_id, capability=cap_lock_17)
    assert locked_17.is_locked is True
    assert wf_srv.checkpoint_validator.checkpoint_count == 2

    # 3. Prove concurrent capacity limit and release
    # Pre-fill remaining 30 slots to reach hard cap of 32
    for i in range(30):
        wf_srv.register_checkpoint(_create_checkpoint(f"chk_fill_{i:02d}"), session_id="sess_fill")
    assert wf_srv.checkpoint_validator.checkpoint_count == 32

    # 33rd concurrent checkpoint must fail
    with pytest.raises(WorkflowCapacityError, match="Registered checkpoint limit"):
        wf_srv.register_checkpoint(_create_checkpoint("chk_overflow"), session_id="sess_fill")

    # Evict session_17 -> frees 2 slots
    wf_srv.evict_session(session_17)
    assert wf_srv.checkpoint_validator.checkpoint_count == 30

    # Now 2 slots can be filled
    wf_srv.register_checkpoint(_create_checkpoint("chk_reclaimed_1"), session_id="sess_new")
    wf_srv.register_checkpoint(_create_checkpoint("chk_reclaimed_2"), session_id="sess_new")
    assert wf_srv.checkpoint_validator.checkpoint_count == 32

    plan_srv.stop()
    wf_srv.stop()


# =========================================================================
# 13: Session ID Reuse
# =========================================================================

def test_m33_session_id_reuse_has_zero_stale_checkpoints() -> None:
    """Verify reusing a session_id after eviction starts with zero stale checkpoints."""
    validator = AnatomicalCheckpointValidator()
    session_id = "SESSION-REUSE"

    # Incarnation 1
    validator.register_checkpoint(_create_checkpoint("chk_old_1"), session_id=session_id)
    validator.register_checkpoint(_create_checkpoint("chk_old_2"), session_id=session_id)
    assert validator.checkpoint_count == 2

    # Teardown Incarnation 1
    validator.evict_session(session_id)
    assert validator.checkpoint_count == 0

    # Incarnation 2 with same session_id
    # Old checkpoints must return CHECKPOINT_MISSING
    interlock = validator.evaluate_checkpoint(
        checkpoint_id="chk_old_1",
        session_id=session_id,
        measured_confidence=0.90,
        measured_uncertainty=0.10,
        measured_deviation_mm=1.0,
        epoch_id=1,
    )
    assert interlock.status is False
    assert interlock.condition_name == "CHECKPOINT_MISSING"

    # Register new checkpoint under reincarnated session
    validator.register_checkpoint(_create_checkpoint("chk_new"), session_id=session_id)
    assert validator.checkpoint_count == 1
    assert validator._session_checkpoints[session_id] == {"chk_new"}


# =========================================================================
# 14 & 16: Full Plan-Lock Path & Atomic Rollback
# =========================================================================

def test_m33_full_plan_lock_ownership_propagation(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify PlanningService.lock_plan propagates session ownership to WorkflowService."""
    wf_srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    wf_srv.initialize(runtime_context)
    wf_srv.start()

    plan_srv = PlanningService(
        dispatcher=message_dispatcher,
        workflow_service=wf_srv,
        secret_filter=secret_filter,
    )
    plan_srv.initialize(runtime_context)
    plan_srv.start()

    session_id = "sess_m33_prop"
    plan = _create_plan("plan_m33_prop")

    cap_sub = _create_execution_capability(id(plan_srv), session_id, "PLANNING_COORDINATION", 1)
    plan_srv.submit_plan(plan, session_id, capability=cap_sub)

    cap_lock = _create_execution_capability(id(plan_srv), session_id, "PLANNING_COORDINATION", 2)
    locked = plan_srv.lock_plan(plan.plan_id, capability=cap_lock)
    assert locked.is_locked is True

    # Assert checkpoints are indexed under session_id
    cids = {f"chk_traj_traj_{plan.plan_id}", f"chk_zone_zone_{plan.plan_id}"}
    assert wf_srv.checkpoint_validator._session_checkpoints[session_id] == cids

    # Both checkpoints exist in global store
    for cid in cids:
        assert cid in wf_srv.checkpoint_validator._checkpoints

    # Teardown clears them cleanly
    assert wf_srv.evict_session(session_id) is True
    assert wf_srv.checkpoint_validator.checkpoint_count == 0

    plan_srv.stop()
    wf_srv.stop()


def test_m33_plan_lock_atomic_rollback_on_partial_failure(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify that if registering the 2nd checkpoint of a plan fails due to capacity,

    the 1st checkpoint is rolled back and the plan remains unlocked.
    """
    wf_srv = WorkflowService(dispatcher=message_dispatcher, secret_filter=secret_filter)
    wf_srv.initialize(runtime_context)
    wf_srv.start()

    plan_srv = PlanningService(
        dispatcher=message_dispatcher,
        workflow_service=wf_srv,
        secret_filter=secret_filter,
    )
    plan_srv.initialize(runtime_context)
    plan_srv.start()

    # Pre-fill validator to exactly 31 checkpoints (1 slot remaining)
    for i in range(MAX_REGISTERED_CHECKPOINTS - 1):
        wf_srv.register_checkpoint(_create_checkpoint(f"prefill_{i:02d}"), session_id="sess_prefill")

    assert wf_srv.checkpoint_validator.checkpoint_count == 31

    # Attempt to lock a plan that derives 2 checkpoints (1 will fit, 2nd will fail)
    session_id = "sess_atomic"
    plan = _create_plan("plan_atomic")
    cap_sub = _create_execution_capability(id(plan_srv), session_id, "PLANNING_COORDINATION", 1)
    plan_srv.submit_plan(plan, session_id, capability=cap_sub)

    cap_lock = _create_execution_capability(id(plan_srv), session_id, "PLANNING_COORDINATION", 2)

    with pytest.raises(WorkflowCapacityError):
        plan_srv.lock_plan(plan.plan_id, capability=cap_lock)

    # Prove atomicity:
    # 1. Checkpoint count must still be 31 (the 1st checkpoint was rolled back)
    assert wf_srv.checkpoint_validator.checkpoint_count == 31
    assert session_id not in wf_srv.checkpoint_validator._session_checkpoints

    # 2. Plan in PlanningService remains UNLOCKED
    stored_plan = plan_srv.get_plan(plan.plan_id)
    assert stored_plan.is_locked is False

    plan_srv.stop()
    wf_srv.stop()


# =========================================================================
# 15: Storage Divergence Test
# =========================================================================

def test_m33_storage_divergence_prevention() -> None:
    """Verify that failed registrations never leave orphan entries in either index."""
    validator = AnatomicalCheckpointValidator()

    # Fill to capacity
    for i in range(MAX_REGISTERED_CHECKPOINTS):
        validator.register_checkpoint(_create_checkpoint(f"chk_div_{i}"), session_id="SESS-1")

    # Attempt to register when full
    with pytest.raises(WorkflowCapacityError):
        validator.register_checkpoint(_create_checkpoint("chk_div_overflow"), session_id="SESS-2")

    # Invariant: len(_checkpoints) == sum(len(s) for s in _session_checkpoints.values())
    total_indexed = sum(len(cids) for cids in validator._session_checkpoints.values())
    assert len(validator._checkpoints) == total_indexed == MAX_REGISTERED_CHECKPOINTS
    assert "chk_div_overflow" not in validator._checkpoints
    assert "SESS-2" not in validator._session_checkpoints
