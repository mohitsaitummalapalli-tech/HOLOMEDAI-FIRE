# PHASE_25_CONTRACT: Coordinated Clinical Session Teardown & Lifecycle Invalidation

**Status**: LOCKED  
**Authoritative Baseline**: `8ad002ca58fb1d41c53a052345fb7c23d3e54d13`  
**Milestone**: M25  

---

## 1. Objective
Eliminate the permanent 32-session capacity exhaustion failure, cross-session state leakage, and absence of cross-subsystem session teardown by establishing an authoritative, synchronous, session-scoped teardown protocol coordinated by `ClinicalExecutionGatewayService`.

---

## 2. Architecture & Design Decisions
1. **Coordination Authority**: `ClinicalExecutionGatewayService` coordinates the ordered teardown protocol.
2. **Teardown Command Route**: `execution.session.teardown`.
3. **Execution Capability**: Mints single-use `_ExecutionCapability(action="SESSION_TEARDOWN", session_id=session_id, ...)`.
4. **Hook Semantics**:
   - Every participating subsystem implements `evict_session(session_id: str) -> bool`.
   - Eviction is surgical and session-scoped: it purges ONLY entries matching `session_id`.
   - Global `clear()` is strictly forbidden for runtime teardown.
   - Idempotent: Evicting a non-existent or already evicted session returns `False` without error.
   - Reentrancy safe: Protected by `_in_transaction` guards.
5. **Execution Order**:
   1. `NavigationService.evict_session(session_id)`
   2. `RecoveryService.evict_session(session_id)`
   3. `RegistrationService.evict_session(session_id)`
   4. `PlanningService.evict_session(session_id)`
   5. `SafetyGateService.evict_session(session_id)`
   6. `WorkflowService.evict_session(session_id)`
   7. Gateway internal cache eviction (`_latest_results`, `_persisted_states`, sequence counters)
   8. `PlatformService.evict_session(session_id)` (if connected)
6. **Failure Policy**:
   - Best-effort with failure aggregation (D160 pattern).
   - If one subsystem fails during eviction, remaining subsystems are still evicted.
   - Failures are aggregated and audited.
   - If any failure occurred: `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` with audit `session_teardown_degraded` / `session_teardown_failed`.
   - If all succeeded: `ExecutionStatus.EXECUTED_CLEAR` with audit `session_teardown_completed`.
7. **Durable Persistence**:
   - Audit recorded via `PersistenceService.record_audit`.

---

## 3. Reopened Surface
- `python/holomed/execution/models.py`
- `python/holomed/execution/service.py`
- `python/holomed/execution/__init__.py`
- Additive `evict_session(session_id)` methods in:
  - `python/holomed/platform/session.py` & `python/holomed/platform/service.py` (M09)
  - `python/holomed/workflow/service.py` (M10)
  - `python/holomed/planning/service.py` (M12)
  - `python/holomed/registration/service.py` (M13)
  - `python/holomed/navigation/service.py` (M14)
  - `python/holomed/recovery/service.py` (M17)
  - `python/holomed/safety_gate/service.py` (M18)

---

## 4. Frozen Boundary Integrity
- Core mathematical algorithms, rigid transforms, deviation calculations, state machine transition tables, and safety precedences remain strictly untouched.
- All existing 7 execution routes and 1 status query remain completely unmodified in signature and behavior.
