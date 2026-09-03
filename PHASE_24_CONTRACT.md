# Phase 24 Contract — Preoperative Planning Execution Hardening

**Authoritative Baseline:** `9ed062de4444e92d2d99b3e7094bb08f45b7aebb` (M23 Release)  
**Contract Status:** `LOCKED`  
**Date:** September 2026  

---

## 1. Objective

Close the remaining unmediated clinical mutation boundary in M12 `PlanningService`.

M24 must:
1. Remove raw dispatcher mutation routes:
   - `planning.submit`
   - `planning.lock`
   - `planning.verify`
2. Retain:
   - `planning.get` (read-only query)
3. Require internal `_ExecutionCapability` for:
   - `PlanningService.submit_plan()`
   - `PlanningService.lock_plan()`
   - `PlanningService.verify_plan()`
4. Add authoritative gateway execution route:
   - `execution.planning.execute`
5. Use the existing:
   - `ClinicalExecutionGatewayService`
6. Introduce capability action:
   - `PLANNING_COORDINATION`
7. Preserve M10 workflow authority.
8. Preserve M18 safety-gate authority.
9. Preserve M09 Platform as FROZEN.
10. Maintain exactly ONE execution coordinator across the entire HoloMed platform.

---

## 2. Reopened Milestones

**EXACTLY:**
- `M12 Planning`
- `M19/M21/M22/M23/M24 Execution Gateway`

Corresponding tests for these packages may be modified.

---

## 3. Frozen Milestones

- `M01-M08 Subsystems`
- `M09 Platform`
- `M10/M20 Workflow`
- `M11 Gateway`
- `M13 Registration`
- `M14 Navigation`
- `M15 Proximity`
- `M16 Drift`
- `M17 Recovery`
- `M18 Safety Gate`
- And all other milestones.

**NO FROZEN PRODUCTION PACKAGE MAY BE CHANGED.**

---

## 4. M12 Semantics

Preserve the existing distinct operational semantics:
- `submit_plan()`: **PREPARATORY** (Ingests, parses, validates, and registers/updates unlocked plan in registry).
- `lock_plan()`: **EVALUATIVE / STATE-COMMITTING / IRREVERSIBLE** (Permanently freezes plan, derives anatomical checkpoints, and registers them into M10 `WorkflowService`).
- `verify_plan()`: **VERIFICATION / STATE-COMMITTING** (Evaluates patient case context, hashes, procedure code, and laterality against active session, recording formal verification).

Do not merge or collapse their internal behavior.

---

## 5. Exact Routes to Remove

Remove from M12 `PlanningService` dispatcher registration:
- `planning.submit` (COMMAND) $\to$ **REMOVED** (Must be completely unroutable; raises `UnroutableMessageError`)
- `planning.lock` (COMMAND) $\to$ **REMOVED** (Must be completely unroutable; raises `UnroutableMessageError`)
- `planning.verify` (COMMAND) $\to$ **REMOVED** (Must be completely unroutable; raises `UnroutableMessageError`)

After M24, all direct external dispatcher mutation access to `PlanningService` is terminated.

---

## 6. Retained M12 Query

Retain:
- `planning.get` (QUERY)

as a strictly read-only query route on M12.

Do not remove unrelated read-only planning queries (`get_plan`, `get_plan_for_session`) unless explicitly required by this contract.

---

## 7. New Authoritative Route

Add exactly:
- `execution.planning.execute`
- **Type:** `COMMAND`
- **Owner:** `ClinicalExecutionGatewayService`
- **Operation Field:**
  - `SUBMIT`
  - `LOCK`
  - `VERIFY`

**DO NOT CREATE:**
- `execution.planning.submit`
- `execution.planning.lock`
- `execution.planning.verify`

---

## 8. Execution Authority

- **Sole Coordinator:** `ClinicalExecutionGatewayService`
- **Authorizer:** `WorkflowService`
- **Safety Evaluator:** `SafetyGateService`
- **Executor:** `PlanningService`
- **Persister:** `PersistenceService`

No `PlanningGateway`. No second coordinator.

---

## 9. Capability Action

Use:
- `PLANNING_COORDINATION`

This capability is strictly required for:
- `PlanningService.submit_plan()`
- `PlanningService.lock_plan()`
- `PlanningService.verify_plan()`

Validation at method entry MUST enforce:
1. `capability is not None`
2. `capability.is_active is True`
3. `capability.action == "PLANNING_COORDINATION"`
4. `capability.session_id == session_id`
5. `capability.sequence_number == sequence_number`
6. `capability.service_instance_id == id(self)`
7. Stale capability fails closed.
8. Replayed capability fails closed.
9. Wrong subsystem capability fails closed.

Capability validation MUST occur BEFORE protected state mutation or checkpoint registration.

---

## 10. Planning Gateway Flow

The authoritative execution pipeline for `execution.planning.execute` is:

```
execution.planning.execute
  │
  ├─► 1. Request Validation (syntax, schema, operation in SUBMIT/LOCK/VERIFY)
  ├─► 2. Session / Sequence / Epoch Validation
  ├─► 3. M18 SafetyGateService Evaluation (action=TRAJECTORY_ALIGNMENT)
  ├─► 4. M10 WorkflowService Authorization (tool_id=planning.{operation})
  ├─► 5. Ephemeral Capability Creation (action=PLANNING_COORDINATION)
  ├─► 6. PlanningService Operation (try / finally)
  ├─► 7. Downstream Consistency Enforcement
  ├─► 8. Durable Audit / Persistence Recording
  ├─► 9. Finally Capability Invalidation (cap.invalidate())
  └─► 10. Response Returned
```

- M18 MUST execute and clear before capability creation.
- M10 MUST execute and clear before capability creation.
- `PlanningService` MUST NOT execute if either gate denies.

---

## 11. M18 Safety Gate

M18 remains **FROZEN**.

Reuse existing:
- `SafetyGateAction.TRAJECTORY_ALIGNMENT`

Existing M18 semantics are authoritative:
- During preoperative planning prior to registration, `m13_state != "VERIFIED"` correctly returns `PERMITTED_WITH_CAUTION` (reason code `REGISTRATION_UNVERIFIED`), allowing planning operations to proceed under warning.
- Critical exclusion zone breaches, landmark drift/integrity violations, epoch mismatches, session mismatches, or aborted workflows correctly evaluate to `DENIED_INTERLOCKED` or `DENIED_CRITICAL`.

No new M18 action may be created. If any implementation conflict arises, STOP and report a CONTRACT CHANGE REQUEST.

---

## 12. M10 Workflow Authority

M10/M20 remains **FROZEN**.

Use:
- `WorkflowService.authorize_tool()`

with operation-specific tool IDs:
- `planning.submit`
- `planning.lock`
- `planning.verify`

Planning execution MUST be blocked when M10 denies. Do not duplicate workflow phase policy in M12.

---

## 13. Workflow Phase Policy

Preserve the audited M10 policy. Planning mutation is authorized only during its legitimate planning phase:
- `PRE_PROCEDURE_PLANNING`: Planning operations may be permitted subject to M18 clearance.
- `NAVIGATION`: Blocked.
- `INTERVENTION`: Blocked.
- `RECOVERY_REQUIRED`: Blocked.
- `ABORTED`: Blocked.

Do not create an independent competing phase matrix in M12.

---

## 14. Checkpoint Ownership

**IMPORTANT:**
`PlanningService.lock_plan()` currently derives checkpoints (`derive_checkpoints_from_plan()`) and registers them with `WorkflowService`.

Preserve this behavior for M24.

M24 MUST ensure checkpoint mutation can occur ONLY after:
- M18 authorization
- AND M10 authorization
- AND valid `PLANNING_COORDINATION` capability.

Do not modify M10. Do not introduce a second checkpoint owner. `PlanningService` remains the existing source of plan-derived checkpoint creation, but access to that mutation is now authorized through the execution gateway.

---

## 15. Capability Lifecycle

Gateway-created capability:
- Internal
- Single-use
- Transaction-bound
- Session-bound
- Action-bound (`PLANNING_COORDINATION`)
- Sequence-bound
- Service-instance-bound (`id(self._planning_service)`)
- Non-serializable

Lifecycle:
```
authorize → create → execute → finally invalidate
```

- No capability may survive the transaction.
- No capability reuse across `SUBMIT`, `LOCK`, or `VERIFY` unless a separate valid capability is minted for that specific operation.

---

## 16. State Consistency

Preserve the established unidirectional clinical dependency chain:
```
Planning → Registration → Navigation → Proximity → Drift → Recovery → Safety Gate → Execution
```

- Locked plans remain **immutable**.
- M24 MUST NOT create any path that allows mutation of an already locked plan.
- Do not introduce a redundant planning revision system.

---

## 17. Epoch / Freshness

M09 remains **FROZEN**.

Use existing epoch, session, and sequence mechanisms:
- Synchronize with `RuntimeContext.epoch_id`.
- Do not modify `PlatformService.migrate_epoch()` (safe fail-closed).
- No redundant epoch mechanisms.

---

## 18. Persistence / Audit

Through the existing `PersistenceService`, record durable audits for at least:
- `planning_blocked_safety_gate`
- `planning_blocked_workflow`
- `planning_executed`
- `planning_execution_failed`

Audit all three operations:
- `SUBMIT`
- `LOCK`
- `VERIFY`

Capability invalidation MUST still occur even if persistence fails. Audit records must never grant execution authority.

---

## 19. Failure Modes

The following MUST fail closed:
- Missing capability
- Inactive capability
- Wrong session
- Wrong action
- Wrong sequence
- Wrong service instance
- Stale capability
- Replayed capability
- M18 denial (`BLOCKED_SAFETY_GATE`)
- M10 denial (`BLOCKED_WORKFLOW`)
- Stale epoch (`RUNTIME_EPOCH_MISMATCH`)
- Invalid plan definition (`PlanningValidationError`)
- Invalid plan lock (`PlanningValidationError` / `PlanningLockError`)
- Verification failure (`verified=False`)
- Checkpoint registration failure
- Downstream exception
- Persistence exception

No unsafe fallback execution under any circumstances.

---

## 20. Concurrency / Transaction

- Preserve the existing synchronous deterministic execution architecture.
- Use existing transaction/reentrancy guards (`self._in_transaction`).
- Do not claim OS-level thread safety without external synchronization.
- Ensure `submit` $\to$ `lock` $\to$ `verify` cannot interleave in a way that violates existing planning invariants.

---

## 21. API Boundary

- Public read-only APIs remain permitted:
  - `get_plan()`
  - `get_plan_for_session()`
  - Existing read-only query behavior.
- Privileged mutation methods (`submit_plan`, `lock_plan`, `verify_plan`) remain callable only with a valid internal `_ExecutionCapability`.
- No public capability factory.
- No bypass helper.

---

## 22. M23 Compatibility

M24 MUST NOT weaken existing execution routes:
- `execution.navigation.execute`
- `execution.recovery.execute`
- `execution.trajectory.bind`
- `execution.tool.invoke`
- `execution.workflow.resume`
- `execution.registration.execute`
- `execution.status.get`

Existing M13/M14/M17/M07 hardening remains completely intact.

---

## 23. Exact Route Inventory After M24

**M12 Planning:**
- `planning.get` (QUERY)
- (All existing explicitly read-only queries only)

**Removed from M12:**
- `planning.submit`
- `planning.lock`
- `planning.verify`

**Execution Gateway:**
- `execution.navigation.execute` (COMMAND)
- `execution.recovery.execute` (COMMAND)
- `execution.trajectory.bind` (COMMAND)
- `execution.tool.invoke` (COMMAND)
- `execution.workflow.resume` (COMMAND)
- `execution.registration.execute` (COMMAND)
- `execution.planning.execute` (COMMAND)
- `execution.status.get` (QUERY)

No additional clinical execution command routes.

---

## 24. Test Requirements

M24 unit and integration test suites MUST verify:
1. `planning.submit` is unroutable.
2. `planning.lock` is unroutable.
3. `planning.verify` is unroutable.
4. `planning.get` remains functional.
5. `execution.planning.execute` is registered on gateway.
6. `SUBMIT` operation works through gateway.
7. `LOCK` operation works through gateway.
8. `VERIFY` operation works through gateway.
9. Direct `submit_plan()` call without capability fails closed.
10. Direct `lock_plan()` call without capability fails closed.
11. Direct `verify_plan()` call without capability fails closed.
12. Wrong session capability fails closed.
13. Wrong action capability fails closed.
14. Wrong sequence capability fails closed.
15. Wrong service binding capability fails closed.
16. Inactive capability fails closed.
17. Replayed capability fails closed.
18. M18 safety gate denial blocks before M12 mutation.
19. M10 workflow denial blocks before M12 mutation.
20. Locked plan remains permanently immutable.
21. Checkpoint registration occurs only through authorized lock execution.
22. Checkpoint registration failure fails safely without leaking capability.
23. Planning audit is persisted to durable store.
24. Capability invalidates in `finally:` block on exceptions.
25. All M23/M22 routes remain fully functional.
26. Full repository regression remains green.

---

## 25. Implementation Restrictions

**DO NOT:**
- Modify M09 Platform.
- Modify M10/M20 Workflow.
- Modify M18 Safety Gate.
- Modify M11 Gateway.
- Create a second gateway or planning-specific gateway.
- Create multiple planning execution routes.
- Bypass M18.
- Bypass M10.
- Expose `_ExecutionCapability` construction outside internal modules.
- Alter locked-plan immutability.
- Redesign checkpoint ownership.

Any requirement outside this contract is a **CONTRACT CHANGE REQUEST**. Implementation must STOP immediately if one occurs.

---

## 26. Completion Gate

M24 is complete only when:
1. Implementation complete in reopened packages.
2. Hostile security audit PASS.
3. Full test regression PASS.
4. Frozen milestone boundaries PASS (zero modifications to frozen files).
5. Exactly one release commit created.
6. Commit SHA recorded.
7. `origin/main` updated.
8. Local `HEAD` == `origin/main`.
9. Working tree clean.

---

## 27. Contract Status

**M24 CONTRACT LOCKED**
