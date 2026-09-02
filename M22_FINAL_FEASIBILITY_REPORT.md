# M22 FINAL FEASIBILITY REPORT

**Authoritative Baseline:** `a3308f742f21d01f4c99e92f8288f5d6c9c4f8d1` (M21 Release)  
**Timestamp:** 2026-09-02T19:00:00Z  
**Mode:** READ-ONLY ARCHITECTURAL FEASIBILITY AUDIT  
**Candidate:** Universal Spatial Recovery Actuation & Trajectory Binding Capability Hardening  

---

## 1. Recovery Method Semantics

| Method | Reads | Writes | Side Effects | Reversible | Idempotent | Classification |
|---|---|---|---|---|---|---|
| `RecoveryService.stage_candidate()` | `plan_id`, `FiducialCloud`, local state | `_staged_candidates`, `_session_states` | None (Isolated calculation) | Yes | Yes | **PREPARATORY / EVALUATIVE** |
| `RecoveryService.verify_candidate()` | `_staged_candidates`, `RecoveryAuthorization`, checkpoint pairs | `_verifications`, `_authorizations`, `_checkpoint_pairs` | None (Local verification snapshot) | Yes | Yes | **EVALUATIVE / VERIFICATION-COMMITTING** |
| `RecoveryService.activate_recovery()` | `_staged_candidates`, `_verifications`, `_authorizations` | `_session_states`, `_revisions`, `_latest_records` | **Mutates M13** (overwrites registration), **M16** (re-seeds landmarks), **M15** (re-binds safety zones), **M14** (re-binds trajectory) | No (Commit) | No (Advances revision) | **ACTUATING & SPATIAL-COMMITTING** |

---

## 2. Recovery Route Semantics

| Route | Type | Handler | Downstream Impact | Recommended Action |
|---|---|---|---|---|
| `recovery.stage` | COMMAND | `handle_stage_command` | Stages candidate in M17 memory | **ELIMINATE FROM DISPATCHER** (Route via `execution.recovery.execute`) |
| `recovery.verify` | COMMAND | `handle_verify_command` | Commits verification snapshot in M17 | **ELIMINATE FROM DISPATCHER** (Route via `execution.recovery.execute`) |
| `recovery.activate` | COMMAND | `handle_activate_command` | **Actuates spatial transformation across M13, M14, M15, M16** | **ELIMINATE FROM DISPATCHER** (Route strictly via `execution.recovery.execute`) |
| `recovery.status.get` | QUERY | `handle_get_status_query` | Read-only recovery status lookup | **RETAIN AS READ-ONLY QUERY** |

---

## 3. Recovery Action Model

- **Action Binding:**
  - `RECOVERY_REORIENTATION`: Used for `stage_candidate()`, `verify_candidate()`, `activate_recovery()`, and `reset_recovery()`.
  - `TRAJECTORY_ALIGNMENT`: Used for `NavigationService.bind_trajectory()`.
  - `WORKFLOW_RESUMPTION`: Retained for `WorkflowService.resume_from_recovery()`.
- **Capability Granularity:** Every individual gateway transaction generates a unique single-use `_ExecutionCapability` bound to `session_id`, `action`, `sequence_number`, and `service_instance_id`, invalidated immediately upon operation completion.

---

## 4. M18 Compatibility

- M18 `SafetyGateEvaluator` already implements complete, authoritative rules for both `RECOVERY_REORIENTATION` and `TRAJECTORY_ALIGNMENT`:
  - `RECOVERY_REORIENTATION`: Permits under `DRIFT_EXCEEDED` (with caution), `REGISTRATION_UNVERIFIED`, and `RECOVERY_REQUIRED` to allow recovery re-registration. Denies under `CRITICAL_BREACH`, `INTERLOCKED`, and `RUNTIME_EPOCH_MISMATCH`.
  - `TRAJECTORY_ALIGNMENT`: Permits under `REGISTRATION_UNVERIFIED` (with caution) during planning. Denies under `DRIFT_EXCEEDED`, `CRITICAL_BREACH`, `INTERLOCKED`, and `WORKFLOW_PHASE_BLOCKED`.
- **Result:** **ZERO changes required to M18**. M18 remains strictly frozen.

---

## 5. M14 Trajectory Binding

- `NavigationService.bind_trajectory(session_id, trajectory_id, plan_trajectory, capability: _ExecutionCapability)`:
  - Validates `capability is not None`, `capability.is_active is True`, `capability.session_id == session_id`, `capability.action == "TRAJECTORY_ALIGNMENT"`, `capability.sequence_number == sequence_number`.
  - Validation occurs **BEFORE** coordinate transformation, capacity verification, state mutation, or event emission.
  - Fails closed with `NavigationAuthorizationError`.

---

## 6. Gateway Compatibility

- In `ClinicalExecutionGatewayService`:
  - `execute_recovery_reorientation()` generates `_create_execution_capability(id(self._recovery_service), session_id, "RECOVERY_REORIENTATION", seq)` and passes to `stage_candidate`, `verify_candidate`, or `activate_recovery`. Capability is invalidated in `finally:`.
  - `execute_trajectory_binding()` generates `_create_execution_capability(id(self._navigation_service), session_id, "TRAJECTORY_ALIGNMENT", seq)` and passes to `bind_trajectory`. Capability is invalidated in `finally:`.
- **Result:** Preserves 100% backward compatibility with all M21 public contracts.

---

## 7. Capability Lifecycle & Boundaries

```
Gateway Request (execute_recovery_reorientation / execute_trajectory_binding)
  │
  ├─► Step 1: M18 Safety Gate Evaluation (Inline)
  │     └─► Denied? -> Return BLOCKED_SAFETY_GATE (subsystem not called)
  │
  ├─► Step 2: M10 Workflow Authorization Gate
  │     └─► Denied? -> Return BLOCKED_WORKFLOW (subsystem not called)
  │
  ├─► Step 3: Capability Minting
  │     └─► cap = _create_execution_capability(...)
  │
  ├─► Step 4: Subsystem Execution (M17 stage/verify/activate OR M14 bind_trajectory)
  │     └─► try: subsystem.operation(..., capability=cap)
  │         finally: cap.invalidate()
  │
  └─► Step 5: Audit Persistence & Result Return
```

---

## 8. Sequence / Revision Freshness

- Monotonic sequence numbers enforced on every request.
- M17 advances `registration_revision` strictly inside `activate_recovery()`.
- Expired or replayed capabilities are instantly rejected by `is_active` check.

---

## 9. Direct-Call Attack Scenarios

| Attack Case | Direct Target | Expected Failure | Result |
|---|---|---|---|
| A. Direct call without capability | `RecoveryService.activate_recovery()` | `RecoveryAuthorizationError("missing execution capability")` | **FAIL-CLOSED** |
| B. Direct call without capability | `NavigationService.bind_trajectory()` | `NavigationAuthorizationError("missing execution capability")` | **FAIL-CLOSED** |
| C. Wrong capability action | `bind_trajectory(capability.action="TOOL_NAVIGATION")` | `NavigationAuthorizationError("action mismatch")` | **FAIL-CLOSED** |
| D. Replayed capability | `activate_recovery()` after gateway transaction | `RecoveryAuthorizationError("capability inactive")` | **FAIL-CLOSED** |
| E. Mismatched session | `stage_candidate()` with other session capability | `RecoveryAuthorizationError("session mismatch")` | **FAIL-CLOSED** |

---

## 10. Dispatcher Attack Scenarios

- `recovery.stage`, `recovery.verify`, `recovery.activate` are removed from dispatcher registration.
- Any message sent to these topics returns `UnroutableMessageError`.
- `execution.recovery.execute` and `execution.trajectory.bind` remain the only routable command endpoints.

---

## 11. State Ownership & Single Writer

- Spatial re-registration, landmark re-seeding, and safety exclusion zone updates occur **ONLY** when `RecoveryService.activate_recovery()` is called via `ClinicalExecutionGatewayService` under `_ExecutionCapability`.
- Direct uncoordinated writers are completely eliminated.

---

## 12. Audit / Persistence

- All recovery operations (`STAGE`, `VERIFY`, `ACTIVATE`, `RESET`) and trajectory bindings are audited in `PersistenceService` with sequence numbers, epoch IDs, and gate decisions.

---

## 13. Minimum Reopen Set

Strictly:
1. `python/holomed/recovery/` (M17)
2. `python/holomed/navigation/` (M14)
3. `python/holomed/execution/` (M19/M21)
4. Corresponding unit tests in `tests/unit/recovery/`, `tests/unit/navigation/`, `tests/unit/execution/`.

**FROZEN:** M00–M13, M15–M16, M18, M20.

---

## 14. M21 Compatibility

- All 6 M21 execution routes remain intact with identical signatures:
  1. `execution.navigation.execute`
  2. `execution.recovery.execute`
  3. `execution.trajectory.bind`
  4. `execution.tool.invoke`
  5. `execution.workflow.resume`
  6. `execution.status.get`

---

## 15. Failure Modes

- Missing capability: Fails closed (`RecoveryAuthorizationError` / `NavigationAuthorizationError`).
- M18 denial: Fails closed before capability creation (`BLOCKED_SAFETY_GATE`).
- M10 denial: Fails closed before capability creation (`BLOCKED_WORKFLOW`).
- Exception during activation: Fails closed (`FAILED_NAVIGATION_GEOMETRY`), capability invalidated in `finally:`.

---

## 16. Architectural Comparison

| Attribute | Option A: Gateway-Only Capability Delegation (Selected) | Option B: Standalone Spatial Mediator |
|---|---|---|
| **Execution Authorities** | Exactly 1 (`ClinicalExecutionGatewayService`) | 2 (Gateway + Spatial Mediator) |
| **Reopened Milestones** | M14, M17, M19 | M14, M17, M19 + New Package |
| **Safety Strength** | Unified Dual-Gate Enforcement | Fragmented Multi-Gateway |
| **Code Duplication** | Minimal (Reuses `_ExecutionCapability`) | High (Duplicate routing layers) |
| **Complexity** | Low | High |

---

## 17. Global Guarantee Matrix

| Path | Current State | Post-M22 State | M18 | M10 | Capability | Freshness | Dispatcher | Guarantee |
|---|---|---|---|---|---|---|---|---|
| `recovery.stage` | Open | Removed | N/A | N/A | N/A | N/A | Unroutable | **GUARANTEED** |
| `recovery.verify` | Open | Removed | N/A | N/A | N/A | N/A | Unroutable | **GUARANTEED** |
| `recovery.activate` | Open | Removed | N/A | N/A | N/A | N/A | Unroutable | **GUARANTEED** |
| `execution.recovery.execute` | Open to M17 | Capability-Gated to M17 | Yes | Yes | Yes | Yes | Routable | **GUARANTEED** |
| `NavigationService.bind_trajectory` | Open API | Capability-Gated | Yes | Yes | Yes | Yes | In-Process | **GUARANTEED** |
| `execution.trajectory.bind` | Open to M14 | Capability-Gated to M14 | Yes | Yes | Yes | Yes | Routable | **GUARANTEED** |

---

## 18. Contract Blockers

- **NONE.** All prerequisites, evaluator behaviors, capability primitives, and route structures have been verified.

---

## 19. M22 Contract Preview

1. **`holomed.recovery`**:
   - Add `RecoveryAuthorizationError` to `exceptions.py` and export in `__init__.py`.
   - Remove `recovery.stage`, `recovery.verify`, `recovery.activate` from `initialize()` dispatcher registration.
   - Require `_ExecutionCapability(action="RECOVERY_REORIENTATION")` on `stage_candidate()`, `verify_candidate()`, and `activate_recovery()`.
2. **`holomed.navigation`**:
   - Require `_ExecutionCapability(action="TRAJECTORY_ALIGNMENT")` on `bind_trajectory()`.
3. **`holomed.execution`**:
   - Update `execute_recovery_reorientation()` and `execute_trajectory_binding()` to create and pass `_ExecutionCapability`.

---

## FINAL CLASSIFICATION:

**READY_FOR_LOCK**
