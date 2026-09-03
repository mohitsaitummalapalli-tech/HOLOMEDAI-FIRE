# M25_FINAL_FEASIBILITY_REPORT: SESSION LIFECYCLE & COORDINATED TEARDOWN

**Authoritative Baseline**: `8ad002ca58fb1d41c53a052345fb7c23d3e54d13`  
**Audit Mode**: READ-ONLY / PRE-LOCK  
**Status**: FEASIBILITY PROVEN — READY FOR CONTRACT LOCK  

---

## 1. Actual Session Lifecycle Analysis

### Complete Lifecycle Trace
1. **Creation**:
   - `PlatformService.start_session(session_id)` calls `SessionManager.start_session(session_id, epoch_id)`.
   - `WorkflowService.start_workflow(session_id)` creates a `WorkflowStateMachine` in phase `PATIENT_CONTEXT`.
2. **Clinical Preparation**:
   - `execution.planning.execute` creates and locks `SurgicalPlanDefinition` bound to `session_id`.
   - `execution.registration.execute` solves and verifies `RegistrationStatusRecord` bound to `session_id`.
3. **Execution**:
   - `execution.trajectory.bind` binds trajectory to `session_id`.
   - `execution.navigation.execute` streams poses and computes deviations for `session_id`.
   - `execution.recovery.execute` reorients spatial tracking for `session_id`.
   - `execution.tool.invoke` operates surgical tools for `session_id`.
4. **Termination (Current State)**:
   - `workflow.abort(session_id)` marks workflow phase as `ABORTED` in `WorkflowService`.
   - `platform.session.stop(session_id)` marks session status as `STOPPED` in `PlatformService`.
5. **Teardown (Current State)**:
   - **MISSING**: No cross-service teardown occurs. Downstream clinical services are never notified and never evict session state.

### Authority Mapping
- **Session Creator**: Client / Supervisor via `platform.session.start` and `workflow.start`.
- **Session Owner**: Fractured. M09 owns `SessionContext`; M10 owns `WorkflowStateMachine`; M12 owns plan bindings; M13 owns registration; M14 owns tracking; M17 owns recovery; M18 owns gate decisions; M19 owns execution sequence.
- **Session Terminator**: Fractured. `PlatformService.stop_session` terminates M09 only. `WorkflowService.abort_workflow` terminates M10 state machine only.
- **Authoritative Answer**: **Currently, NO SERVICE has authoritative ownership or ability to execute complete session teardown across the platform.**

---

## 2. Session State Inventory

| Subsystem | State Structure | Stored Entity | Invalidation Mechanism | Capacity Cap | Stale-State Risk |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **M09 Platform** | `_sessions` | `SessionContext` | `reset(epoch_id)` / `clear()` | `MAX_ACTIVE_PLATFORM_SESSIONS = 32` | Stored as `STOPPED`; capacity never reclaimed |
| **M10 Workflow** | `_workflows`, `_confirmations` | `WorkflowStateMachine`, `ConfirmationManager` | `clear()` only | `MAX_ACTIVE_WORKFLOWS = 32` | Stored as `ABORTED` / `COMPLETION`; capacity never reclaimed |
| **M12 Planning** | `_session_plan_bindings` | `session_id -> plan_id` | `clear()` only | `MAX_ACTIVE_PLANS = 32` | Binding persists; plan reused if `session_id` repeated |
| **M13 Registration** | `_registrations`, `_fiducial_clouds` | `RegistrationStatusRecord`, `FiducialPointPairsCloud` | `clear()` only | `MAX_ACTIVE_REGISTRATIONS = 32` | Registration remains `VERIFIED`; leaks to repeated `session_id` |
| **M14 Navigation** | `_session_states`, `_bound_trajectories`, `_latest_poses`, `_latest_sequences`, `_latest_deviations` | Spatial tracking records | `clear()` only | `MAX_ACTIVE_NAVIGATION_SESSIONS = 32` | Poses and trajectories persist; leaks to repeated `session_id` |
| **M15 Proximity** | `_evaluations` | Spatial evaluation cache | `clear()` only | Dynamic | Cached breaches persist |
| **M16 Drift** | `_tracking_sessions` | Landmark tracking sessions | `clear()` only | Dynamic | Landmark baselines persist |
| **M17 Recovery** | `_session_states` | `RecoverySessionState` | `clear()` only | `MAX_ACTIVE_RECOVERY_SESSIONS = 32` | Recovery states persist |
| **M18 Safety Gate** | `_latest_decisions`, `_persisted_states` | `GateStatusRecord` | `clear()` only | `MAX_ACTIVE_GATE_SESSIONS = 32` | Gate decisions persist; `get_gate_status` returns stale decision |
| **M19 Execution** | `_latest_results`, `_persisted_states`, sequence counters | `NavigationExecutionResult` | `clear()` only | Dynamic | Prior execution results and sequences persist |

---

## 3. Actual Teardown Failure Proof
Executing the sequence:
1. `platform.session.start("SESS-001")`
2. `workflow.start("SESS-001")`
3. Execute planning, registration, navigation for `"SESS-001"`
4. `workflow.abort("SESS-001")`
5. `platform.session.stop("SESS-001")`

### Concrete Inspection Results:
- In `WorkflowService`: `"SESS-001"` remains in `_workflows["SESS-001"]` (marked `ABORTED`).
- In `PlanningService`: `"SESS-001"` remains in `_session_plan_bindings["SESS-001"]`.
- In `RegistrationService`: `"SESS-001"` remains in `_registrations["SESS-001"]` (`state=VERIFIED`).
- In `NavigationService`: `"SESS-001"` remains in `_session_states["SESS-001"]` and `_bound_trajectories["SESS-001"]`.
- In `RecoveryService`: `"SESS-001"` remains in `_session_states["SESS-001"]`.
- In `SafetyGateService`: `"SESS-001"` remains in `_latest_decisions["SESS-001"]`.
- In `PlatformService`: `"SESS-001"` remains in `_session_manager._sessions["SESS-001"]` (`status=STOPPED`).

**Conclusion**: Stop/abort operations update local status flags, but perform **ZERO eviction** of memory structures.

---

## 4. 32-Session Capacity Attack
- **Hypothesis**: Starting and stopping 32 consecutive sessions exhausts all capacity limits, causing session 33 to fail permanently.
- **Source Verification**:
  - `PlatformService`: `SessionManager.start_session` checks `len(self._sessions) >= MAX_ACTIVE_PLATFORM_SESSIONS (32)`. Stopped sessions are not deleted. **Fails at Session 33 with `PlatformCapacityError`**.
  - `WorkflowService`: `start_workflow` checks `len(self._workflows) >= MAX_ACTIVE_WORKFLOWS (32)`. Aborted/completed workflows are not deleted. **Fails at Session 33 with `WorkflowCapacityError`**.
  - `RegistrationService`: `submit_fiducials` checks `len(self._registrations) >= MAX_ACTIVE_REGISTRATIONS (32)`. **Fails at Session 33 with `RegistrationCapacityError`**.
  - `NavigationService`: `submit_pose` checks `len(self._session_states) >= MAX_ACTIVE_NAVIGATION_SESSIONS (32)`. **Fails at Session 33 with `NavigationCapacityError`**.
  - `RecoveryService`: `stage_recovery` checks `len(self._session_states) >= MAX_ACTIVE_RECOVERY_SESSIONS (32)`. **Fails at Session 33 with `RecoveryCapacityError`**.
  - `SafetyGateService`: `evaluate` checks `len(self._latest_decisions) >= MAX_ACTIVE_GATE_SESSIONS (32)`. **Fails at Session 33 with `SafetyGateCapacityError`**.
- **Classification**: **CONFIRMED** (Reproducible from source semantics).

---

## 5. Session ID Reuse Attack
Attempting to reuse `session_id = "CASE-001"` after termination:
1. `PlatformService.start_session("CASE-001")`: Raises `PlatformValidationError("Session 'CASE-001' already exists in STOPPED state")`.
2. In other services (if PlatformService is bypassed):
   - `PlanningService`: Reuses old plan binding!
   - `RegistrationService`: `is_registered("CASE-001")` returns `True` before any fiducials are submitted!
   - `NavigationService`: Reuses previously bound trajectory!
   - `SafetyGateService`: `get_gate_status("CASE-001")` returns previous decision!
- **Classification**: **CRITICAL ISOLATION FLAW**.

---

## 6. Capability Invalidation on Teardown
- Current implementation: `_ExecutionCapability` is invalidated strictly in `finally:` blocks of synchronous gateway calls.
- Teardown requirement: When a session is torn down, any in-flight or replayed capability bearing `capability.session_id == session_id` must fail closed.
- Because capabilities already bind `session_id`, evicting the session from the services causes all subsequent capability checks to fail closed with `*SessionError` or `*AuthorizationError`.

---

## 7. Sequence & Execution State on Teardown
- `ClinicalExecutionGatewayService` tracks `_latest_results` and `_persisted_states`.
- `NavigationService` tracks `_latest_sequences`.
- `WorkflowStateMachine` tracks `_last_sequence`.
- Upon teardown, all sequence counters for the session must be purged so that if a session ID is legitimately re-initialized, its sequence resets cleanly to 0 or 1 without monotonic collision.

---

## 8. Spatial Services Teardown (M13, M14, M17)
- Spatial state includes rigid transforms, fiducial clouds, tool poses, deviations, and recovery states.
- Leaving spatial state resident in memory after procedure completion is dangerous.
- Explicit teardown must purge:
  - `RegistrationService`: `self._registrations.pop(session_id, None)`, `self._fiducial_clouds.pop(session_id, None)`.
  - `NavigationService`: `self._session_states.pop(session_id, None)`, `self._bound_trajectories.pop(session_id, None)`, `self._latest_poses.pop(session_id, None)`, `self._latest_deviations.pop(session_id, None)`, `self._latest_sequences.pop(session_id, None)`.
  - `RecoveryService`: `self._session_states.pop(session_id, None)`.

---

## 9. Workflow Teardown (M10)
- `WorkflowStateMachine.abort()` and `complete()` transition the phase to a terminal state (`ABORTED` or `COMPLETION`).
- This makes the state machine terminal logically, but leaves the object in `WorkflowService._workflows[session_id]`.
- M10 must provide `evict_session(session_id)` to release the workflow and confirmation manager from memory.

---

## 10. Safety Gate Teardown (M18)
- `SafetyGateService._latest_decisions` and `_persisted_states` cache the last decision.
- `SafetyGateService` must provide `evict_session(session_id)` to remove the session from cache, guaranteeing that subsequent queries for that session return `None`.

---

## 11. Planning & Checkpoint Teardown (M12)
- `PlanningService._session_plan_bindings` binds `session_id -> plan_id`.
- Teardown must remove the binding: `self._session_plan_bindings.pop(session_id, None)`.
- Note: The `SurgicalPlanDefinition` itself is a case artifact and may remain in `_plans[plan_id]` for historical reference, but must be unbound from the active session.
- Checkpoints derived and registered in M10: When M10 workflow is evicted, its session-associated interlocks are cleared.

---

## 12. Persistence & Durable Audit
- Teardown is a critical clinical boundary.
- When session teardown is executed, `PersistenceService.record_audit()` must record:
  - Event: `session_teardown_completed`
  - Payload: `session_id`, `epoch_id`, `subsystems_purged`, timestamp.
- If teardown encounters partial failures:
  - Event: `session_teardown_failed` / `session_teardown_degraded`.

---

## 13. Teardown Architecture Comparison

| Dimension | Option A: Gateway Owns Route | Option B: Platform Owns Teardown | Option C: Event Broadcast |
| :--- | :--- | :--- | :--- |
| **Dispatcher Route** | `execution.session.teardown` | `platform.session.teardown` | `platform.session.stopped` event |
| **Authority** | Clinical Execution Gateway | Platform Supervisor | Uncoordinated |
| **Dependency Direction** | Gateway -> Subsystems (Clean) | Platform -> Gateway -> Subsystems | Event bus only |
| **Execution Gating** | Enforced via Capability | Supervisor bypass | None |
| **Determinism** | Fully Synchronous | Fully Synchronous | Asynchronous (Unreliable) |
| **Reopened Milestones** | M19 + Hooks in M10,12,13,14,17,18 | M09 + M19 + Subsystems | M09 + M10 + Subsystems |
| **Audit Point** | Gateway Durable Audit | Platform Audit | Fragmented |

**Selection**: **Option A with Platform Coordination**:
- Primary clinical execution teardown is commanded via `execution.session.teardown` on `ClinicalExecutionGatewayService`.
- `PlatformService.stop_session` also provides an evict flag or calls gateway teardown if wired.
- Gateway coordinates ordered synchronous eviction across all clinical subsystems.

---

## 14. Platform / Gateway Boundary & Circularity
- `ClinicalExecutionGatewayService` holds references to domain services: M10, M12, M13, M14, M17, M18, M07, M08.
- `ClinicalExecutionGatewayService` does **NOT** import or depend on `PlatformService`.
- `PlatformService` holds a dictionary of services: `self._services: dict[str, IService]`.
- Therefore:
  - `Gateway -> Subsystems` is completely acyclic and direct.
  - If `PlatformService` needs to trigger gateway teardown, it accesses `self._services.get("execution_gateway")` without any circular import.

---

## 15. Minimum Reopen Set
- **Reopened for Implementation**:
  - `M19-M25 Clinical Execution Gateway` (`python/holomed/execution/*`): Implements `execute_session_teardown`, route `execution.session.teardown`, and capability gating.
  - Subsystem Eviction Hooks:
    - `python/holomed/planning/service.py` (M12): `evict_session(session_id)`
    - `python/holomed/registration/service.py` (M13): `evict_session(session_id)`
    - `python/holomed/navigation/service.py` (M14): `evict_session(session_id)`
    - `python/holomed/recovery/service.py` (M17): `evict_session(session_id)`
    - `python/holomed/safety_gate/service.py` (M18): `evict_session(session_id)`
    - `python/holomed/workflow/service.py` (M10): `evict_session(session_id)`
    - `python/holomed/platform/session.py` (M09): `evict_session(session_id)`
- **Kept Strictly FROZEN**:
  - Mathematical & algorithmic cores: `geometry.py`, `evaluator.py`, `rigid_body.py`, `deviation.py`, `interlocks.py`.
  - All existing execution routes and capability verifications.

---

## 16. Teardown Hook Semantics: `clear()` vs `evict_session()`
- `clear()`: Destructive, global reset. Wipes all sessions. Only valid when service is STOPPED or during test fixture setup.
- `evict_session(session_id)`: Granular, surgical eviction. Removes ONLY entries keyed by `session_id`. Preserves all other active sessions and service state.
- Rule: **M25 MUST introduce `evict_session(session_id)` on each service, rather than misusing `clear()`.**

---

## 17. Public `clear()` Attack
- Analysis: Public `clear()` exists for test fixtures and `stop()` routines.
- Defense: M25 ensures that normal clinical workflows use `evict_session(session_id)`. `clear()` remains restricted to teardown/test contexts and can be hardened to reject calls during active transactions.

---

## 18. Session Termination Races
- Single-threaded dispatcher execution guarantees that an execution command and a teardown command never execute concurrently on the same thread.
- If teardown is called reentrantly during an active transaction:
  `if self._in_transaction: raise ExecutionLifecycleError("Cannot teardown session during active transaction")`.
- Transaction guards prevent mid-execution eviction.

---

## 19. Partial Teardown Failure Policy
- Policy: **Best-Effort with Aggregated Failure Reporting (D160 Pattern)**:
  - Gateway attempts eviction across all subsystems sequentially.
  - If a subsystem raises an exception during eviction, the exception is caught and recorded in a failure list, and eviction continues for the remaining subsystems.
  - Durable audit records the complete outcome.
  - If any subsystem failed: Gateway returns `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` with sanitized details.
  - If all succeeded: Gateway returns `ExecutionStatus.EXECUTED_CLEAR`.

---

## 20. Restart / Reconnect Behavior
- When a client reconnects after teardown:
  - The session has been evicted from memory.
  - Starting a new session with the same or new ID begins from a clean slate.
  - Zero state leakage from prior runs.

---

## 21. M24 Compatibility
- Zero impact on M24 planning execution (`execution.planning.execute`).
- Zero impact on M23 registration execution (`execution.registration.execute`).
- Zero impact on M22 recovery / trajectory execution (`execution.recovery.execute`, `execution.trajectory.bind`).
- Zero impact on M21 navigation / tool execution (`execution.navigation.execute`, `execution.tool.invoke`).
- Full backward compatibility maintained.

---

## 22. Frozen Milestone Analysis
- M09, M10, M12, M13, M14, M17, M18 each require a single, isolated, additive method: `evict_session(session_id: str) -> bool`.
- This is a non-breaking, surgical change that does not alter any existing algorithm, state machine transition rule, or gate precedence.

---

## 23. M25 Scope Decision
**Scope Decision**: **Targeted Clinical Lifecycle Reopening (Architecture A)**.
- Reopen `M19 Execution Gateway` to own the teardown coordination and dispatcher endpoint.
- Add `evict_session(session_id)` to M09, M10, M12, M13, M14, M17, M18.
- No redesign of the core state machines or safety evaluator.

---

## 24. Contract Blockers
- **None Identified**. The architecture is clean, acyclic, deterministic, and fully compatible with the existing repository design.

---

# M25_CONTRACT_DRAFT_PRELOCK

### 1. Title
**M25 — Coordinated Clinical Session Teardown & Lifecycle Invalidation**

### 2. Objectives
1. Implement `execution.session.teardown` on `ClinicalExecutionGatewayService`.
2. Add granular `evict_session(session_id)` methods to `PlatformService` (M09), `WorkflowService` (M10), `PlanningService` (M12), `RegistrationService` (M13), `NavigationService` (M14), `RecoveryService` (M17), and `SafetyGateService` (M18).
3. Coordinate synchronous, ordered teardown:
   - Step 1: Navigation eviction
   - Step 2: Recovery eviction
   - Step 3: Registration eviction
   - Step 4: Planning eviction
   - Step 5: Safety Gate eviction
   - Step 6: Workflow eviction
   - Step 7: Gateway cache eviction
   - Step 8: Platform Session eviction
4. Record durable persistence audit: `session_teardown_completed`.
5. Recover capacity across all subsystems, allowing unbounded sequential clinical sessions.
6. Eliminate cross-session state leakage.

### 3. Dispatcher Route
- Command: `execution.session.teardown`
- Payload: `{"session_id": str, "sequence_number": int, "now_utc": str}`
- Response: `{"session_id": str, "execution_status": str, "subsystems_purged": list[str]}`

---

## FINAL CLASSIFICATION

```
==================================================
READY_FOR_LOCK
==================================================
```
