# PHASE 27 CONTRACT: WORKFLOW SAFETY INTERLOCK SCOPING & LIFECYCLE EVICTION HARDENING

**Authoritative Baseline**: `0885622984bf3ba3304586685c53956be4cc6e6a`  
**Milestone**: M27 — Workflow Safety Interlock Scoping & Lifecycle Eviction Hardening  
**Status**: DRAFT CONTRACT (Awaiting Lock Authorization)  
**Predecessor**: M26 (Frozen)  

---

## 1. OBJECTIVE

Eliminate cross-session safety interlock contamination and anatomical checkpoint lifecycle leakage inside the workflow subsystem.

Specifically, M27 establishes and guarantees:
1. **Cross-Session Isolation**: Interlocks tripped in Session A cannot abort, block, or influence Session B.
2. **Deterministic Teardown**: Teardown of Session A surgically purges all interlocks and session-bound checkpoints belonging to Session A.
3. **Clean Session Reuse**: Reusing a session ID starts with zero residual interlock or checkpoint state.
4. **Capacity Reclamation**: Anatomical checkpoint capacity (`MAX_REGISTERED_CHECKPOINTS = 32`) is reclaimed upon session teardown, preventing cumulative exhaustion.
5. **Frozen Preservation**: All existing M07–M26 safety precedence, execution contracts, legal workflow state transitions, and gateway coordination remain 100% intact.

---

## 2. AUTHORIZED REOPEN SET

The source code modifications for M27 are strictly confined to the following **3 production files**:
1. `python/holomed/workflow/interlocks.py`
2. `python/holomed/workflow/checkpoints.py`
3. `python/holomed/workflow/service.py`

Authorized test surface:
4. `tests/unit/execution/test_m27_workflow_interlock_lifecycle.py`

Authorized documentation/contract artifacts:
5. `PHASE_27_CONTRACT.md`
6. `M27_IMPLEMENTATION_REPORT.md`
7. `M27_HOSTILE_AUDIT_REPORT.md`
8. `M27_FINAL_PRECOMMIT_AUDIT.md`

`ClinicalExecutionGatewayService` (`python/holomed/execution/service.py`) requires **NO architectural or code change** because M25 already invokes `self._workflow_service.evict_session(session_id, cap)` at Step 8 of teardown.

All other 25 packages (Gateway, Platform, Planning, Registration, Navigation, Recovery, Safety Gate, Proximity, Drift, Tools, Devices, etc.) remain **FROZEN**.

---

## 3. INTERLOCK DATA MODEL

In `python/holomed/workflow/interlocks.py`, replace the unsafe flat dictionary:
```python
# OLD (Unsafe):
self._interlocks: dict[str, SafetyInterlock] = {}
```
With the session-partitioned data structure:
```python
# NEW (Session-Partitioned):
self._session_interlocks: dict[str, dict[str, SafetyInterlock]] = {}
```
- **Outer key**: `session_id: str` (derived directly from `interlock.session_id`).
- **Inner key**: `interlock_id: str` (the unique interlock identifier).
- **Semantics**: Preserves all existing `SafetyInterlock` dataclass fields, severity levels, precedence ordering (`SEVERITY_PRECEDENCE`), and status booleans without change.

---

## 4. SESSION-SCOPED EVALUATION CONTRACT

In `python/holomed/workflow/interlocks.py`, update:
```python
def has_critical_interlock(self, session_id: Optional[str] = None) -> bool: ...
def has_blocking_interlock(self, session_id: Optional[str] = None) -> bool: ...
def get_tripped_interlocks(self, session_id: Optional[str] = None) -> tuple[SafetyInterlock, ...]: ...
```

### Invariants:
1. **Explicit Clinical Scoping**: `WorkflowService.transition_phase()` and `WorkflowService.resume_from_recovery()` MUST explicitly pass `session_id` to these methods.
2. **Isolation Guarantee**: When `session_id` is provided, evaluation queries *only* `self._session_interlocks.get(session_id, {})`. Foreign session interlocks are invisible and have zero effect.
3. **Backward Compatibility**: If `session_id is None` (legacy/testing caller), the method falls back to inspecting all sessions across `self._session_interlocks.values()`. Clinical transition logic is prohibited from relying on `session_id=None`.
4. **Recovery Clearance Scoping**: `stage_recovery_clearance()` line 118 evaluates:
   `if self.has_critical_interlock(session_id) or self.has_blocking_interlock(session_id):`
   guaranteeing that Session B's interlocks cannot block Session A's recovery resumption.

---

## 5. INTERLOCK LIFECYCLE EVICTION

Add to `SafetyInterlockEngine`:
```python
def evict_session(self, session_id: str) -> bool:
    """Evict all session-scoped safety interlocks, releasing state (M27)."""
    if not isinstance(session_id, str) or not session_id.strip():
        return False
    return self._session_interlocks.pop(session_id, None) is not None
```
- **Surgical**: Removes only `self._session_interlocks[session_id]`.
- **Isolation**: Every other session's interlock dictionary remains 100% untouched.
- **Idempotent**: Returns `True` if interlocks existed for `session_id`, `False` otherwise.
- **Zero Global Clear**: Never invokes `self.clear()`.

---

## 6. CHECKPOINT LIFECYCLE EVICTION & CAPACITY RECLAMATION

In `python/holomed/workflow/checkpoints.py`:
Add session-tracking structure to `AnatomicalCheckpointValidator`:
```python
self._checkpoints: dict[str, AnatomicalCheckpoint] = {}
self._session_checkpoints: dict[str, set[str]] = {}
```

### Methods:
1. `register_checkpoint(checkpoint: AnatomicalCheckpoint, session_id: Optional[str] = None) -> None`:
   - Registers checkpoint in `self._checkpoints`.
   - Enforces `MAX_REGISTERED_CHECKPOINTS = 32`.
   - If `session_id` is provided, records ownership in `self._session_checkpoints[session_id].add(checkpoint.checkpoint_id)`.
2. `evaluate_checkpoint(checkpoint_id, ..., session_id) -> SafetyInterlock`:
   - Binds `checkpoint_id` to `session_id` in `self._session_checkpoints` if not already bound.
   - Evaluates against `self._checkpoints[checkpoint_id]` and returns `SafetyInterlock(..., session_id=session_id)`.
3. `evict_session(session_id: str) -> bool`:
   - Retrieves `chk_ids = self._session_checkpoints.pop(session_id, set())`.
   - For each `cid` in `chk_ids`: removes `self._checkpoints.pop(cid, None)`.
   - Reclaims capacity towards `MAX_REGISTERED_CHECKPOINTS = 32`.
   - Returns `True` if any checkpoint was evicted, `False` otherwise.

---

## 7. WORKFLOW TEARDOWN INTEGRATION

In `python/holomed/workflow/service.py`, extend `evict_session()`:
```python
def evict_session(self, session_id: str, capability: Optional[Any] = None) -> bool:
    """Evict session-scoped workflow, confirmations, interlocks, and checkpoints (M27)."""
    if self._in_transaction:
        raise WorkflowLifecycleError("Reentrant call to evict_session rejected")
    evicted = False
    if session_id in self._workflows:
        del self._workflows[session_id]
        evicted = True
    if session_id in self._confirmations:
        del self._confirmations[session_id]
        evicted = True
    if self._interlock_engine.evict_session(session_id):
        evicted = True
    if self._checkpoint_validator.evict_session(session_id):
        evicted = True
    return evicted
```

### Teardown Coordination:
- Triggered exclusively by `execution.session.teardown` via `ClinicalExecutionGatewayService.execute_session_teardown()`.
- Executes at **Step 8** of gateway teardown.
- Best-effort failure aggregation is preserved: any failure in workflow eviction is recorded in `failures` and causes degraded status without blocking platform teardown.

---

## 8. CAPABILITY & AUTHORIZATION SECURITY

1. **Teardown Authorization**: Uses the existing M25 `_ExecutionCapabilityAction.SESSION_TEARDOWN` capability passed from `ClinicalExecutionGatewayService`.
2. **Session Binding**: Teardown capability is verified to match `session_id`.
3. **Replay Rejection**: Capabilities are single-use and invalidated immediately upon transaction conclusion.
4. **Zero Bypass**: No public route or unauthenticated API can trigger eviction.

---

## 9. HARDENED SAFETY INVARIANTS

1. **Cross-Session Isolation**:
   ```
   Session A: critical interlock = True
   Session B: transition_phase(...)
   Invariant: Session B transition succeeds or fails based strictly on Session B state; Session A interlock has ZERO effect.
   ```
2. **Blocking Isolation**:
   ```
   Session A: blocking interlock = True
   Session B: transition_phase(...)
   Invariant: Session B does NOT inherit blocking state.
   ```
3. **Clean Session Reuse**:
   ```
   Session A -> critical interlock -> teardown(Session A) -> start Session A
   Invariant: Reused Session A has zero interlocks, zero checkpoints, and nominal initial state.
   ```
4. **Capacity Recovery**:
   ```
   32 checkpoints registered across sessions -> teardown sessions -> capacity drops below 32 -> new checkpoints register cleanly.
   ```

---

## 10. FROZEN BOUNDARIES

The following are strictly **FROZEN** and must not be altered:
- Legal workflow state transitions (`LEGAL_TRANSITIONS` in `state_machine.py`).
- Terminal phases (`COMPLETION`, `ABORTED`).
- Confirmation request/response semantics.
- Interlock severity rankings (`SEVERITY_PRECEDENCE`).
- `SafetyGateEvaluator` logic and precedence in M18.
- Gateway teardown topological order in M25/M26.
- Mathematical algorithms across all other packages.

---

## 11. TEST ACCEPTANCE REQUIREMENTS

Test suite: `tests/unit/execution/test_m27_workflow_interlock_lifecycle.py`

Required Hostile Verification Tests:
1. `test_m27_session_a_critical_interlock_cannot_abort_session_b`: Real `transition_phase()` on Session B succeeds while Session A has a critical interlock.
2. `test_m27_session_a_blocking_interlock_cannot_block_session_b`: Real `transition_phase()` on Session B proceeds while Session A has a blocking interlock.
3. `test_m27_interlock_engine_session_scoped_lookups`: Verify `has_critical_interlock(session_id)` and `has_blocking_interlock(session_id)` isolate sessions.
4. `test_m27_interlock_eviction_removes_only_target_session`: Evicting Session A purges Session A interlocks and leaves Session B untouched.
5. `test_m27_checkpoint_eviction_removes_only_target_session`: Evicting Session A purges Session A checkpoints and leaves Session B checkpoints untouched.
6. `test_m27_gateway_teardown_cleanses_workflow_interlocks_and_checkpoints`: Full teardown cleans all 4 workflow structures.
7. `test_m27_session_id_reuse_has_zero_stale_interlocks_or_checkpoints`: Stale critical interlock is cleared; reused session transitions cleanly.
8. `test_m27_checkpoint_capacity_reclaimed_after_teardown`: 32 checkpoints registered, session torn down, new checkpoints registered without capacity error.
9. `test_m27_recovery_clearance_unaffected_by_foreign_session_interlock`: Session A recovery resumption succeeds despite active Session B interlock.
10. `test_m27_reentrancy_guard_fails_closed`: Eviction during active transaction raises `WorkflowLifecycleError`.
11. `test_m27_partial_workflow_failure_aggregates_and_continues`: Teardown continues if workflow throws, reporting degraded status.
12. `test_m27_backward_compatible_none_session_lookup`: Calling `has_critical_interlock(None)` inspects across sessions for legacy compatibility.
13. Existing M25 suite (`test_m25_session_teardown.py`) passes 100%.
14. Full system regression (`pytest -q -ra`) passes 100% (1555+ tests).

---

## 12. RELEASE CONDITIONS

- Final classification must be `M27_PRECOMMIT_PASS`.
- ZERO COMMITS and ZERO PUSHES until explicit user release authorization.
