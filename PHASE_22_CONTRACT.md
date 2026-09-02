# PHASE 22 CONTRACT
# Universal Spatial Recovery Actuation & Trajectory Binding Capability Hardening

**Authoritative Baseline:** `a3308f742f21d01f4c99e92f8288f5d6c9c4f8d1` (M21 Release)  
**Contract Status:** **LOCKED**  
**Document Type:** Normative Architectural Specification  

---

## 1. Objective

This contract establishes the final spatial execution boundary hardening by closing all remaining uncoordinated spatial actuation pathways across M17 Recovery and M14 Navigation.

Specifically, M22:
1. Removes raw unmediated spatial recovery execution routes from `RecoveryService` (`recovery.stage`, `recovery.verify`, `recovery.activate`).
2. Gates `RecoveryService.stage_candidate()`, `verify_candidate()`, and `activate_recovery()` with `_ExecutionCapability(action="RECOVERY_REORIENTATION")`.
3. Gates `NavigationService.bind_trajectory()` with `_ExecutionCapability(action="TRAJECTORY_ALIGNMENT")`.
4. Extends `ClinicalExecutionGatewayService` in `holomed.execution` as the sole coordinator for spatial recovery and trajectory alignment.
5. Preserves strictly **ONE** execution coordinator across the system with zero duplicate spatial gateways.
6. Preserves all frozen M18 cross-service safety decision semantics and M10/M20 clinical workflow recovery invariants.

---

## 2. Authoritative Architecture & System Roles

- **Sole Execution Coordinator:** `ClinicalExecutionGatewayService` in `holomed.execution`.
- **Clinical Workflow Authorizer:** `WorkflowService` in `holomed.workflow` (FROZEN).
- **Cross-Service Safety Evaluator:** `SafetyGateService` in `holomed.safety_gate` (FROZEN).
- **Spatial Actuation Executors:**
  - `RecoveryService` in `holomed.recovery` (Reopened).
  - `NavigationService` in `holomed.navigation` (Reopened).
- **Audit Persister:** `PersistenceService` in `holomed.persistence` (FROZEN).

---

## 3. Strict Boundary Control

### Reopened Milestones (Authorized Scope):
- **M14 — Navigation** (`python/holomed/navigation/`, `tests/unit/navigation/`)
- **M17 — Recovery** (`python/holomed/recovery/`, `tests/unit/recovery/`)
- **M19/M21 — Execution Gateway** (`python/holomed/execution/`, `tests/unit/execution/`)

### Frozen Milestones (Strictly Prohibited from Modification):
- **M00–M13** (Core, Devices, Perception, Vision, Tools, Platform, Anatomy, XR, Ultron, Workflow M10, Gateway M11, Planning M12, Registration M13)
- **M15** (Proximity Protection)
- **M16** (Landmark Drift Detection)
- **M18** (Safety Decision Foundation)
- **M20** (Workflow Recovery Re-entry)

---

## 4. Subsystem Semantics & Capability Gating

### A. M17 Recovery Method Semantics
1. **`RecoveryService.stage_candidate(session_id, plan_id, cloud, ..., capability)`**
   - *Classification:* `PREPARATORY / EVALUATIVE`
   - *Behavior:* Solves candidate transformation in isolated memory without mutating M13/M14/M15/M16.
   - *Capability:* Requires active `_ExecutionCapability` with `action == "RECOVERY_REORIENTATION"`.
2. **`RecoveryService.verify_candidate(session_id, authorization, checkpoint_plan_mm, checkpoint_measured_mm, ..., capability)`**
   - *Classification:* `EVALUATIVE / VERIFICATION-COMMITTING`
   - *Behavior:* Verifies operator authorization and checkpoint drift error in isolated memory.
   - *Capability:* Requires active `_ExecutionCapability` with `action == "RECOVERY_REORIENTATION"`.
3. **`RecoveryService.activate_recovery(session_id, ..., capability)`**
   - *Classification:* `ACTUATING / SPATIAL-COMMITTING`
   - *Behavior:* Actuates and overwrites M13 registration, re-seeds M16 drift landmarks, re-binds M15 proximity safety zones, and re-binds M14 trajectory. Advances `registration_revision`.
   - *Capability:* Requires active `_ExecutionCapability` with `action == "RECOVERY_REORIENTATION"`.

### B. M14 Trajectory Binding Semantics
1. **`NavigationService.bind_trajectory(session_id, trajectory_id, plan_trajectory, ..., capability)`**
   - *Classification:* `ACTUATING / GEOMETRY-BINDING`
   - *Behavior:* Transforms plan trajectory into patient tracker frame and registers active bound trajectory.
   - *Capability:* Requires active `_ExecutionCapability` with `action == "TRAJECTORY_ALIGNMENT"`.
   - *Invariant:* Capability validation occurs strictly before coordinate transformation, capacity validation, or state mutation.

---

## 5. Dispatcher Route Registry (M22 Locked State)

### Removed / Unroutable Routes:
- `recovery.stage` $\to$ **REMOVED** (Raises `UnroutableMessageError`)
- `recovery.verify` $\to$ **REMOVED** (Raises `UnroutableMessageError`)
- `recovery.activate` $\to$ **REMOVED** (Raises `UnroutableMessageError`)

### Retained Read-Only Recovery Route:
- `recovery.status.get` (QUERY) $\to$ `RecoveryService.handle_get_status_query`

### Authoritative Execution Routes on `ClinicalExecutionGatewayService`:
1. `execution.navigation.execute` (COMMAND)
2. `execution.recovery.execute` (COMMAND)
3. `execution.trajectory.bind` (COMMAND)
4. `execution.tool.invoke` (COMMAND)
5. `execution.workflow.resume` (COMMAND)
6. `execution.status.get` (QUERY)

---

## 6. Execution Coordination Lifecycle

### Recovery Execution (`execution.recovery.execute`):
1. Gateway receives request with `recovery_operation: "STAGE" | "VERIFY" | "ACTIVATE" | "RESET" | "STATUS"`.
2. Validates session, request parameters, and sequence monotonicity.
3. Evaluates M18 inline under `SafetyGateAction.RECOVERY_REORIENTATION` (short-circuits on denial).
4. Evaluates M10 workflow authorization (short-circuits on denial).
5. Mints single-use `_ExecutionCapability` bound to `id(self._recovery_service)`, `session_id`, `"RECOVERY_REORIENTATION"`, `sequence_number`.
6. Invokes corresponding `RecoveryService` method in a `try...finally` block.
7. Subsystem validates capability and executes operation.
8. `finally:` permanently revokes capability via `capability.invalidate()`.
9. Records audit payload in `PersistenceService`, emits `execution.recovery.reoriented`, and returns result.

### Trajectory Binding (`execution.trajectory.bind`):
1. Gateway receives request with plan trajectory and trajectory ID.
2. Validates session, request parameters, and sequence monotonicity.
3. Evaluates M18 inline under `SafetyGateAction.TRAJECTORY_ALIGNMENT` (short-circuits on denial).
4. Evaluates M10 workflow authorization (short-circuits on denial).
5. Mints single-use `_ExecutionCapability` bound to `id(self._navigation_service)`, `session_id`, `"TRAJECTORY_ALIGNMENT"`, `sequence_number`.
6. Invokes `NavigationService.bind_trajectory(..., capability=cap)` in a `try...finally` block.
7. `NavigationService` validates capability and binds trajectory.
8. `finally:` permanently revokes capability via `capability.invalidate()`.
9. Records audit payload in `PersistenceService`, emits `execution.trajectory.bound`, and returns result.

---

## 7. Fail-Closed Invariants

Direct invocation without a valid capability MUST fail closed immediately:
- `RecoveryService.stage_candidate()` $\to$ Raises `RecoveryAuthorizationError`
- `RecoveryService.verify_candidate()` $\to$ Raises `RecoveryAuthorizationError`
- `RecoveryService.activate_recovery()` $\to$ Raises `RecoveryAuthorizationError`
- `NavigationService.bind_trajectory()` $\to$ Raises `NavigationAuthorizationError`

The following conditions MUST also raise authorization errors and fail closed:
- `capability is None`
- `capability.is_active is False`
- `capability.session_id != context.session_id`
- `capability.action` mismatch
- `capability.sequence_number != context.sequence_number`
- `capability.service_instance_id != id(service)`
- Replayed, expired, or modified capabilities

---

## 8. Test Requirements

The M22 implementation test suite must verify all of the following scenarios:
1. `recovery.stage` is unroutable over dispatcher.
2. `recovery.verify` is unroutable over dispatcher.
3. `recovery.activate` is unroutable over dispatcher.
4. `recovery.status.get` query remains routable and functional.
5. `execution.recovery.execute` successfully coordinates `STAGE`, `VERIFY`, and `ACTIVATE` under valid capability.
6. `execution.trajectory.bind` successfully coordinates trajectory binding under valid capability.
7. `RecoveryService.stage_candidate()` without capability raises `RecoveryAuthorizationError`.
8. `RecoveryService.verify_candidate()` without capability raises `RecoveryAuthorizationError`.
9. `RecoveryService.activate_recovery()` without capability raises `RecoveryAuthorizationError`.
10. `NavigationService.bind_trajectory()` without capability raises `NavigationAuthorizationError`.
11. Capability with mismatched `session_id` rejected.
12. Capability with mismatched `action` rejected.
13. Capability with mismatched `sequence_number` rejected.
14. Capability with mismatched `service_instance_id` rejected.
15. Replayed capability rejected.
16. Inactive / invalidated capability rejected.
17. Capability invalidation occurs unconditionally on exceptions.
18. M18 `RECOVERY_REORIENTATION` rules enforced across all recovery operations.
19. M18 `TRAJECTORY_ALIGNMENT` rules enforced across trajectory binding.
20. M10 workflow authorization enforced across recovery and trajectory execution.
21. All 60 M21 execution tests pass without regression.
22. Complete repository test suite passes with zero regressions.

---

## 9. Implementation Restrictions

- Do NOT add a second spatial gateway or coordinator.
- Do NOT reopen M00–M13 (except M14), M15, M16, M18, or M20.
- Do NOT introduce new safety actions; reuse `RECOVERY_REORIENTATION` and `TRAJECTORY_ALIGNMENT`.
- Do NOT weaken existing M21 public routes.
- Do NOT expose public capability creation helpers.

---

## 10. Completion Gate

M22 is complete only when:
- All required capability checks and dispatcher removals are implemented.
- Hostile audit passes cleanly.
- All M22 targeted and full regression tests pass.
- Frozen boundary verification confirms zero unintended changes.
- Exactly one commit is created and pushed to `origin/main`.
