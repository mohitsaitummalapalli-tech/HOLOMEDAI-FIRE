# M24 FINAL FEASIBILITY REPORT: Preoperative Planning Execution Hardening

```yaml
audit_type: PRE_LOCK_FEASIBILITY_AUDIT
authoritative_baseline: 9ed062de4444e92d2d99b3e7094bb08f45b7aebb
discovery_source: M24_DISCOVERY_REPORT.md
candidate: M12 Preoperative Planning Execution Hardening
mode: READ_ONLY
status: FEASIBLE_AND_READY_FOR_LOCK
```

---

## 1. M12 Semantic Analysis

Inspection of mutating methods in `python/holomed/planning/service.py`:

### A. `submit_plan(plan, session_id)`
- **Inputs**: `plan: SurgicalPlanDefinition`, `session_id: str`
- **State Read**: `self._state` (must be `STARTED`), `self._in_transaction`, `self._plans` (capacity & existing lock checks).
- **State Written**: `self._plans[plan.plan_id] = plan`, `self._session_plan_bindings[session_id] = plan.plan_id`. Emits `planning.plan.submitted`.
- **Downstream Effects**: Binds preoperative plan definition (trajectories, exclusion zones, case context) to session.
- **Reversibility / Idempotence**: Reversible by re-submitting before locking. Idempotent.
- **Classification**: **STATE-COMMITTING**

### B. `lock_plan(plan_id)`
- **Inputs**: `plan_id: str`
- **State Read**: `self._state`, `self._in_transaction`, `self._plans[plan_id]` (checks plan exists and `is_locked`).
- **State Written**: `self._plans[plan_id] = locked_plan` (with `is_locked=True`), `self._total_plans_locked += 1`.
- **Downstream Mutation**: Calls `derive_checkpoints_from_plan(locked_plan)`. Iterates derived checkpoints and calls `self._workflow_service.register_checkpoint(chk)`. Emits `planning.plan.locked`.
- **Downstream Effects**: Irrevocably freezes plan geometry; enables M13 registration solving (`_verify_locked_plan`); injects anatomical checkpoints into M10 `WorkflowService._checkpoint_validator`.
- **Reversibility / Idempotence**: **IRREVERSIBLE**. Idempotent.
- **Classification**: **STATE-COMMITTING & DOWNSTREAM-ACTUATING**

### C. `verify_plan(plan_id, session_id, operator_id, patient_hash, procedure_code, laterality)`
- **Inputs**: `plan_id: str`, `session_id: str`, `operator_id: str`, `patient_hash: str`, `procedure_code: str`, `laterality: SurgicalLaterality`
- **State Read**: `self._state`, `self._plans[plan_id]`. Compares case context against reported parameters.
- **State Written**: `self._verification_records[plan_id] = record`. Emits `planning.plan.verified`.
- **Downstream Effects**: Records formal safety checklist outcome (WHO Surgical Safety Timeout).
- **Reversibility / Idempotence**: Commits immutable verification snapshot. Idempotent.
- **Classification**: **EVALUATIVE & STATE-COMMITTING**

---

## 2. Planning Dispatcher Routes

### Current State:
- `planning.submit` (COMMAND) $\to$ `handle_submit_command`: Unpacks payload, calls `submit_plan()`.
- `planning.lock` (COMMAND) $\to$ `handle_lock_command`: Unpacks payload, calls `lock_plan()`.
- `planning.verify` (COMMAND) $\to$ `handle_verify_command`: Unpacks payload, calls `verify_plan()`.
- `planning.get` (QUERY) $\to$ `handle_get_query`: Reads `_plans`, returns summary.

### Trace & Authorization Boundaries:
- **Zero authorization boundaries** currently exist on `planning.submit`, `planning.lock`, and `planning.verify`.
- Any dispatcher participant can submit, lock, or verify plans at any time without capability, without workflow phase check, and without safety gate evaluation.
- No dynamic registrations, wrappers, or aliases exist.

---

## 3. Workflow Compatibility

Inspection of `WorkflowService.authorize_tool()` and `ToolAuthorizationGate`:
- In `WorkflowPhase.PRE_PROCEDURE_PLANNING`: Tools with `tool_id="planning.submit"`, `"planning.lock"`, `"planning.verify"` are permitted under `ToolSafetyClassification.READ_ONLY_INFORMATIVE` or `VISUALIZATION_ADJUSTMENT`.
- In `WorkflowPhase.SAFETY_TIMEOUT`: `planning.verify` is permitted for WHO timeout checklist verification.
- In `WorkflowPhase.NAVIGATION` / `INTERVENTION`: Plan modifications are strictly prohibited.
- In `WorkflowPhase.ABORTED` / `RECOVERY_REQUIRED`: `ToolAuthorizationGate` automatically blocks non-telemetry tools with `BLOCKED_PHASE`.
- Active blocking interlocks automatically return `BLOCKED_INTERLOCK`.

**Result**: Workflow authorization policy is 100% native and supported by existing frozen M10 code.

---

## 4. Checkpoint Authority Analysis

### Audit Findings:
- `PlanningService.lock_plan()` mutates `WorkflowService._checkpoint_validator` by registering derived checkpoints (`chk_traj_*` and `chk_zone_*`).
- Checkpoints derived on plan lock represent ground-truth tolerances for live trajectory navigation and exclusion zone protection.
- Allowing unauthenticated dispatcher calls to `planning.lock` permits unauthorized modification of active safety checkpoints in M10.
- **Proper Architecture**:
  - `PlanningService.lock_plan()` requires an active `_ExecutionCapability` (`PLANNING_COORDINATION`).
  - Calls to lock a plan MUST route through `ClinicalExecutionGatewayService.execute_planning()`.
  - The Gateway validates M18 safety and M10 workflow phase (`PRE_PROCEDURE_PLANNING`) *before* minting the capability.
  - When `lock_plan()` executes with the capability, derived checkpoints are securely registered into `WorkflowService` under full authorization.

---

## 5. M18 Safety Action Compatibility

Evaluating `SafetyGateAction.TRAJECTORY_ALIGNMENT`:
- In `SafetyGateEvaluator.evaluate()`:
  - Session mismatch $\to$ `DENIED_INTERLOCKED`
  - Critical exclusion zone breach $\to$ `DENIED_CRITICAL`
  - Landmark integrity / drift exceeded $\to$ `DENIED_INTERLOCKED`
  - Recovery failure $\to$ `DENIED_INTERLOCKED`
  - Epoch mismatch $\to$ `DENIED_INTERLOCKED`
  - Workflow phase `ABORTED` or `RECOVERY_REQUIRED` $\to$ `DENIED_INTERLOCKED`
  - Registration unverified $\to$ `PERMITTED_WITH_CAUTION` with `REGISTRATION_UNVERIFIED`
- During preoperative planning, registration is not yet verified. `TRAJECTORY_ALIGNMENT` returns `PERMITTED_WITH_CAUTION`, which allows planning operations while strictly interlocking if any critical breach, sensor failure, or aborted workflow occurs.
- M18 Safety Gate is 100% compatible and remains **FROZEN**.

---

## 6. Capability Action Model

- Evaluation of action name: `PLANNING_COORDINATION`.
- **Action Isolation**:
  - `PLANNING_COORDINATION` is accepted ONLY by `PlanningService`.
  - Rejected by `RegistrationService` (`REGISTRATION_ALIGNMENT` required).
  - Rejected by `NavigationService` (`TRAJECTORY_ALIGNMENT` / `TOOL_NAVIGATION` required).
  - Rejected by `RecoveryService` (`RECOVERY_REORIENTATION` required).
  - Rejected by `ToolService` (`TOOL_INVOCATION` required).
  - Rejected by `WorkflowService` (`WORKFLOW_RESUMPTION` required).
- All 3 planning operations (`SUBMIT`, `LOCK`, `VERIFY`) share the single capability action `PLANNING_COORDINATION`.

---

## 7. Direct-Call Security

- An audit of the entire codebase confirms: **zero production callers** of `submit_plan()`, `lock_plan()`, `verify_plan()` outside `PlanningService`'s own dispatcher handlers.
- Adding capability validation to these three methods is completely safe.
- Direct-call validation rules:
  1. `capability is None or not getattr(capability, "is_active", False)` $\to$ `PlanningAuthorizationError`
  2. `getattr(capability, "action", None) != "PLANNING_COORDINATION"` $\to$ `PlanningAuthorizationError`
  3. `getattr(capability, "session_id", None) != session_id` $\to$ `PlanningAuthorizationError`
  4. `sequence_number is not None and getattr(capability, "sequence_number", None) != sequence_number` $\to$ `PlanningAuthorizationError`
  5. `getattr(capability, "service_instance_id", None) != id(self)` $\to$ `PlanningAuthorizationError`
- Validation occurs before any transaction guard acquisition or state mutation.

---

## 8. Gateway Route Design

- **Selected Route**: `execution.planning.execute` (COMMAND) on `MessageDispatcher`.
- Handled by `ClinicalExecutionGatewayService.handle_planning_execute_command`.
- Request model: `PlanningExecutionRequest`
  - `session_id: str`
  - `sequence_number: int`
  - `now_utc: str`
  - `operation: str` (`"SUBMIT"`, `"LOCK"`, `"VERIFY"`)
  - `action: SafetyGateAction = SafetyGateAction.TRAJECTORY_ALIGNMENT`
  - `plan: Optional[SurgicalPlanDefinition]` (for `SUBMIT`)
  - `plan_id: Optional[str]` (for `SUBMIT`, `LOCK`, `VERIFY`)
  - `operator_id: Optional[str]` (for `VERIFY`)
  - `patient_hash: Optional[str]` (for `VERIFY`)
  - `procedure_code: Optional[str]` (for `VERIFY`)
  - `laterality: Optional[SurgicalLaterality]` (for `VERIFY`)
- Result model: `PlanningExecutionResult`
  - Standard gateway result fields + `plan: Optional[SurgicalPlanDefinition]`, `verification_record: Optional[PlanVerificationRecord]`.

---

## 9. Authorization Ordering

Execution order in `execute_planning()`:
1. Reentrancy & lifecycle checks (`ServiceState.STARTED`, `_in_transaction = True`).
2. Request parameter validation.
3. **Step 1: M18 Safety Gate Evaluation** (`TRAJECTORY_ALIGNMENT`).
   - If denied: audits and returns `ExecutionStatus.BLOCKED_SAFETY_GATE`.
4. **Step 2: M10 Workflow Authorization** (`tool_name = f"planning.{operation.lower()}"`).
   - If blocked: audits and returns `ExecutionStatus.BLOCKED_WORKFLOW`.
5. **Step 3: Service Availability Check** (`_planning_service is not None`).
6. **Step 4: Ephemeral Capability Minting & Execution**:
   - `cap_plan = _create_execution_capability(...)`
   - `try:` invokes `submit_plan`, `lock_plan`, or `verify_plan`
   - `finally: cap_plan.invalidate()`
7. **Step 5: Persistent Audit Recording** (`PersistenceService.record_audit()`).
8. **Step 6: Protocol Event Emission & Result Return**.
9. `finally: self._in_transaction = False`.

---

## 10. Downstream Consistency

- Plan locking derives checkpoints and enables M13 registration solving (`_verify_locked_plan`).
- Mediating planning operations prevents out-of-phase plan replacement during live surgical navigation (`NAVIGATION` / `INTERVENTION`).
- Stale downstream state is prevented by M10 phase gating and M18 safety gating.

---

## 11. Epoch / Freshness

- `PlanningExecutionRequest` binds `session_id`, `sequence_number`, and `now_utc`.
- `PlanningService` binds `_epoch_id` to `runtime_context.epoch_id`.
- M18 Safety Gate enforces `RUNTIME_EPOCH_MISMATCH` interlocks on epoch divergence.

---

## 12. Audit / Persistence

Audit events written to `PersistenceService.record_audit()`:
- `planning_blocked_safety_gate`
- `planning_blocked_workflow`
- `planning_execution_failed`
- `planning_executed` (with operation: `SUBMIT`, `LOCK`, or `VERIFY`)

All audits include `session_id`, `sequence_number`, `epoch_id`, and `plan_id`.

---

## 13. Failure Modes

- Gate denial $\to$ Returns `BLOCKED_SAFETY_GATE`, capability never minted.
- Workflow block $\to$ Returns `BLOCKED_WORKFLOW`, capability never minted.
- Calculation / validation exception $\to$ Redacted, returns `FAILED_NAVIGATION_GEOMETRY`, capability invalidated in `finally:`.
- Persistence failure $\to$ Capability already invalidated before persistence call.
- No partial state commit on failure.

---

## 14. Concurrency

- `PlanningService._in_transaction` enforces non-reentrancy on direct calls.
- `ClinicalExecutionGatewayService._in_transaction` serializes planning executions alongside all other clinical execution routes.

---

## 15. API / Export

- `holomed/planning/__init__.py` exports only data models, geometry, and service classes.
- `_ExecutionCapability` remains private in `holomed.execution._capability`.
- No bypass helpers or capability factories are exported.

---

## 16. M23 Compatibility

- All 7 existing gateway routes remain intact:
  1. `execution.navigation.execute`
  2. `execution.status.get`
  3. `execution.recovery.execute`
  4. `execution.trajectory.bind`
  5. `execution.tool.invoke`
  6. `execution.workflow.resume`
  7. `execution.registration.execute`
- M24 adds route 8: `execution.planning.execute`. Zero collision.

---

## 17. Frozen Milestone Compatibility

- M09 Platform: **FROZEN** (Unmodified)
- M10/M20 Workflow: **FROZEN** (Unmodified)
- M11 Transport Gateway: **FROZEN** (Unmodified)
- M13 Registration: **FROZEN** (Unmodified)
- M14 Navigation: **FROZEN** (Unmodified)
- M15 Proximity: **FROZEN** (Unmodified)
- M16 Drift: **FROZEN** (Unmodified)
- M17 Recovery: **FROZEN** (Unmodified)
- M18 Safety Gate: **FROZEN** (Unmodified)

---

## 18. Minimum Reopen Set

1. **M12 Planning**:
   - `python/holomed/planning/service.py`
2. **M19/M21/M22/M23/M24 Execution Gateway**:
   - `python/holomed/execution/models.py`
   - `python/holomed/execution/service.py`
3. **Tests**:
   - `tests/unit/planning/test_planning_service.py`
   - `tests/unit/planning/test_planning_adversarial_matrix.py`
   - `tests/unit/execution/test_m24_hardening.py`

---

## 19. Architectural Comparison

| Criterion | Option 1: Universal Gateway Integration | Option 2: Standalone Planning Gateway |
|---|---|---|
| Authority Count | **1 (Single Coordinator)** | 2 (Dual Coordinators) |
| Safety Architecture | Standardized Dual-Gate M18/M10 | Fragmented / Custom |
| Capability Reuse | Full reuse of `_ExecutionCapability` | Duplicated capability system |
| Audit Trail | Unified in `PersistenceService` | Split across two stores |
| Code Duplication | **Zero** | High |
| Regression Risk | **Minimal** | High |

**Selected**: Option 1 (Universal Gateway Integration).

---

## 20. Contract Blockers

- M18 semantics incompatible? **NO**
- M10 policy incompatible? **NO**
- Checkpoint ownership impossible? **NO**
- Capability model insufficient? **NO**
- Frozen-boundary conflict? **NO**
- Hidden production callers? **NO**
- Stale-state architecture impossible to protect? **NO**

**Total Blockers**: **0**

---

## M24_CONTRACT_DRAFT_PRELOCK

### 1. Reopened Milestones
- `M12 Planning`
- `M19/M21/M22/M23/M24 Execution Gateway`
- Corresponding unit tests

### 2. Frozen Milestones
- `M09`, `M10/M20`, `M11`, `M13`, `M14`, `M15`, `M16`, `M17`, `M18`

### 3. Removed Dispatcher Routes
- `planning.submit` (COMMAND) $\to$ **REMOVED** (Raises `UnroutableMessageError`)
- `planning.lock` (COMMAND) $\to$ **REMOVED** (Raises `UnroutableMessageError`)
- `planning.verify` (COMMAND) $\to$ **REMOVED** (Raises `UnroutableMessageError`)

### 4. Retained Dispatcher Routes
- `planning.get` (QUERY) on `PlanningService`

### 5. New Dispatcher Route
- `execution.planning.execute` (COMMAND) on `ClinicalExecutionGatewayService`

### 6. Capability Action
- `"PLANNING_COORDINATION"`

### 7. M18 Safety Action
- `SafetyGateAction.TRAJECTORY_ALIGNMENT`

### 8. M10 Workflow Authorization
- `tool_id = f"planning.{operation.lower()}"` evaluated via `WorkflowService.authorize_tool()`

### 9. Capability Lifecycle
- Single-use, minted before dispatch, invalidated in `finally:` block.

---

## FINAL CLASSIFICATION:

```text
READY_FOR_LOCK
```
