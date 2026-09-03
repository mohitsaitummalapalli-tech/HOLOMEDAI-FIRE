# M27 IMPLEMENTATION REPORT

**Authoritative Baseline**: `0885622984bf3ba3304586685c53956be4cc6e6a`  
**Milestone**: M27 — Workflow Safety Interlock Scoping & Lifecycle Eviction Hardening  
**Status**: IMPLEMENTATION COMPLETE  
**Predecessor**: M26 (Frozen)  

---

## 1. EXECUTIVE SUMMARY

M27 eliminates cross-session safety interlock contamination and anatomical checkpoint lifecycle leakage within the workflow subsystem (`holomed/workflow`). Prior to M27, `SafetyInterlockEngine._interlocks` and `AnatomicalCheckpointValidator._checkpoints` were un-partitioned global dictionaries. A critical interlock tripped in Session A caused Session B's workflow to immediately abort upon phase transition. Additionally, tripped interlocks and checkpoints survived session teardown, resulting in failure upon session reuse and capacity exhaustion.

M27 resolves these issues by:
1. Partitioning `SafetyInterlockEngine` storage by `session_id: str` (`_session_interlocks`).
2. Scoping `has_critical_interlock(session_id)` and `has_blocking_interlock(session_id)` and explicitly passing `session_id` from `WorkflowService.transition_phase()` and `authorize_tool()`.
3. Establishing session-scoped checkpoint ownership in `AnatomicalCheckpointValidator` via `_session_checkpoints: dict[str, set[str]]`.
4. Implementing surgical `evict_session(session_id)` on both `SafetyInterlockEngine` and `AnatomicalCheckpointValidator`.
5. Extending `WorkflowService.evict_session(session_id, capability)` so that coordinated M25 teardown clears workflows, confirmations, interlocks, and checkpoints in one atomic operation.

---

## 2. MODIFIED PRODUCTION SURFACE

Modifications were strictly confined to **3 files** in the authorized reopen set:

| File | Changes | Classification |
|---|---|---|
| `python/holomed/workflow/interlocks.py` | Replaced flat `_interlocks` with `_session_interlocks: dict[str, dict[str, SafetyInterlock]]`; added `session_id` parameter to `has_blocking_interlock`, `has_critical_interlock`, `get_tripped_interlocks`; added session-scoped `stage_recovery_clearance`; added `evict_session(session_id)`; provided backward-compatible `_interlocks` property. | A (Authorized) |
| `python/holomed/workflow/checkpoints.py` | Added `_session_checkpoints: dict[str, set[str]]`; accepted `session_id` in `register_checkpoint` and `evaluate_checkpoint`; added `evict_session(session_id)` to purge checkpoints and reclaim capacity towards `MAX_REGISTERED_CHECKPOINTS = 32`. | A (Authorized) |
| `python/holomed/workflow/service.py` | Explicitly passed `session_id` into `has_critical_interlock(session_id)` and `has_blocking_interlock(session_id)` in `transition_phase()` and `authorize_tool()`; extended `evict_session` to invoke `_interlock_engine.evict_session()` and `_checkpoint_validator.evict_session()`. | B (Wiring) |

**Gateway Orchestration**: `ClinicalExecutionGatewayService` (`holomed/execution/service.py`) was **NOT modified** because M25 already invokes `self._workflow_service.evict_session(session_id, cap)` at Step 8 of teardown.

---

## 3. COMPREHENSIVE TEST SUITE

A new dedicated hostile test suite was created:
`tests/unit/execution/test_m27_workflow_interlock_lifecycle.py`

### Tests Implemented (13 Hostile Scenarios):
1. `test_m27_session_a_critical_interlock_cannot_abort_session_b`: Session A critical interlock does not abort Session B during real `transition_phase()`.
2. `test_m27_session_a_blocking_interlock_cannot_block_session_b`: Session A blocking interlock does not block Session B during real `transition_phase()`.
3. `test_m27_interlock_eviction_removes_only_target_session`: Session A eviction removes only Session A interlocks, leaving Session B intact.
4. `test_m27_checkpoint_isolation_and_eviction`: Session A eviction removes only Session A checkpoints, leaving Session B checkpoints intact.
5. `test_m27_gateway_teardown_cleanses_all_workflow_structures`: Gateway `execution.session.teardown` purges workflow state, confirmations, interlocks, and checkpoints.
6. `test_m27_session_id_reuse_has_zero_stale_interlocks`: Reused session ID has zero stale interlocks/checkpoints and transitions cleanly.
7. `test_m27_checkpoint_capacity_reclaimed_after_teardown`: Checkpoint limit (32) reached, teardown executed, capacity successfully reclaimed.
8. `test_m27_recovery_resumption_unaffected_by_foreign_session_interlock`: Session A recovery resumption proceeds without interference from Session B critical interlock.
9. `test_m27_workflow_evict_reentrancy_fails_closed`: Reentrant eviction during active transaction raises `WorkflowLifecycleError`.
10. `test_m27_partial_workflow_failure_aggregates_and_continues`: Injected workflow eviction failure continues teardown across other subsystems and aggregates failure.
11. `test_m27_tool_authorization_isolated_between_sessions`: Tool authorization for Session B succeeds while Session A is blocked by its own interlock.
12. `test_m27_cross_session_recovery_capability_fails_closed`: Recovery transaction capability for Session A cannot clear Session B interlocks.
13. `test_m27_stale_capability_replay_rejected`: Invalidated capability passed to `stage_recovery_clearance` is rejected with `WorkflowAuthorizationError`.

---

## 4. VERIFICATION EVIDENCE

- `python -m pytest tests/unit/execution/test_m27_workflow_interlock_lifecycle.py -q -ra`: **13 passed in 0.08s**
- `python -m pytest tests/unit/execution/test_m25_session_teardown.py -q -ra`: **12 passed in 0.06s**
- `python -m pytest tests/unit/workflow -q -ra`: **68 passed in 0.39s**
- Full platform regression: `python -m pytest -q -ra`: **1568 passed in 5.63s** (0 failures, 0 warnings)
- `git diff --check`: Clean (0 whitespace/formatting warnings)

---

## 5. SOURCE-OF-TRUTH RESOLUTION: CHECKPOINT CAPACITY

### Investigation Findings
1. **Authoritative Constant**: `python/holomed/workflow/models.py:22` defines:
   ```python
   MAX_REGISTERED_CHECKPOINTS: int = 32
   ```
2. **Git History Verification**: `git log -S "MAX_REGISTERED_CHECKPOINTS"` confirms that `MAX_REGISTERED_CHECKPOINTS = 32` was introduced in commit `1a8eec86` (M10 Workflow introduction) and has **never** been modified.
3. **Discrepancy Root Cause**: During M27 discovery, the report erroneously recorded 64 (derived from an unverified assumption or confusion with `PROCEDURE_ID_REGEX = r"^[a-zA-Z0-9_-]{1,64}$"`).
4. **Test & Implementation Conformance**:
   - `python/holomed/workflow/checkpoints.py` imports and strictly enforces `MAX_REGISTERED_CHECKPOINTS` (32).
   - `test_m27_checkpoint_capacity_reclaimed_after_teardown` in `tests/unit/execution/test_m27_workflow_interlock_lifecycle.py` imports `MAX_REGISTERED_CHECKPOINTS`, fills all 32 slots, verifies the 33rd registration raises `WorkflowCapacityError("Registered checkpoint limit (32) exceeded")`, executes session teardown, proves 0 checkpoints remain, and successfully registers a new checkpoint.
   - All documentation has been aligned with the authoritative source of truth: **32 checkpoints**.

