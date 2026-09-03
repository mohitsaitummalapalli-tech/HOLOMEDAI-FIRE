# M24 DISCOVERY REPORT: Post-M23 Architecture Audit & Perimeter Closure

```yaml
audit_date: 2026-09-02
authoritative_baseline: 9ed062de4444e92d2d99b3e7094bb08f45b7aebb
mode: READ_ONLY_HOSTILE_DISCOVERY
system_regression_status: 1501 passed
target_objective: Identify the next genuine architectural gap following M23 registration hardening
```

---

## 1. Authoritative Baseline

The active repository HEAD matches the M23 release commit:
```text
9ed062de4444e92d2d99b3e7094bb08f45b7aebb (HEAD -> main, origin/main)
feat(M23): harden initial registration execution
```
The working tree is completely clean. All 1501 unit and integration tests are passing.

---

## 2. System Authority Map

Following M21, M22, and M23, the system execution and coordination authority is structured as follows:

```text
                               ┌──────────────────────────────────────────────┐
                               │             MessageDispatcher                │
                               └──────────────────────┬───────────────────────┘
                                                      │
                       ┌──────────────────────────────┴──────────────────────────────┐
                       │                                                            │
                       ▼                                                            ▼
         [ execution.* Routes (COMMAND) ]                             [ Raw Query Routes (QUERY) ]
         - execution.navigation.execute                               - planning.get
         - execution.recovery.execute                                 - registration.get
         - execution.trajectory.bind                                  - navigation.status.get
         - execution.tool.invoke                                      - drift.status.get / drift.landmarks.get
         - execution.workflow.resume                                  - proximity.status.get / proximity.zones.get
         - execution.registration.execute                             - recovery.status.get
                       │                                              - workflow.status
                       ▼                                              - platform.status / platform.audit
       ┌───────────────────────────────┐                              - persistence.status / audit / get
       │ ClinicalExecutionGateway      │
       │ (Canonical Execution Auth)    │
       └───────┬───────────────┬───────┘
               │               │
       Step 1  ▼               ▼ Step 2
       ┌───────────────┐ ┌───────────────┐
       │ M18 SafetyGate│ │ M10 Workflow  │
       │ (Dual-Gate)   │ │ (Phase Auth)  │
       └───────┬───────┘ └───────┬───────┘
               │ Clear           │ Permitted
               └───────┬─────────┘
                       ▼ Step 4: Mint Single-Use Capability
        ┌─────────────────────────────────────────────────────────────┐
        │  _ExecutionCapability (Service-Bound, Ephemeral, Validated)  │
        └──────┬──────────────┬──────────────┬──────────────┬─────────┘
               ▼              ▼              ▼              ▼
           M14 Nav       M17 Recovery   M13 Reg        M07 Tools
           (Locked)       (Locked)       (Locked)       (Locked)
```

**Notice**: M14 Tracked Navigation, M17 Recovery Reorientation, M13 Spatial Registration, and M07 Clinical Tools are strictly mediated by the gateway and reject direct calls without active capabilities.
However, **M12 Preoperative Planning (`PlanningService`)** remains entirely outside this coordination boundary, exposing raw mutating commands on `MessageDispatcher`.

---

## 3. Privileged Action Inventory

| Privileged Action / Route | Subsystem | Authoritative Owner | Entrypoint | Gating Mechanism | Capability Required? | Persistent Audit? | Status |
|---|---|---|---|---|---|---|---|
| `execution.navigation.execute` | M14 Navigation | `ClinicalExecutionGatewayService` | Gateway Dispatcher Handler | M18 `TOOL_NAVIGATION` + M10 | Yes (`TOOL_NAVIGATION`) | Yes | **PROTECTED** |
| `execution.recovery.execute` | M17 Recovery | `ClinicalExecutionGatewayService` | Gateway Dispatcher Handler | M18 `RECOVERY_REORIENTATION` + M10 | Yes (`RECOVERY_REORIENTATION`) | Yes | **PROTECTED** |
| `execution.trajectory.bind` | M14 Navigation | `ClinicalExecutionGatewayService` | Gateway Dispatcher Handler | M18 `TRAJECTORY_ALIGNMENT` + M10 | Yes (`TRAJECTORY_ALIGNMENT`) | Yes | **PROTECTED** |
| `execution.tool.invoke` | M07 Tools | `ClinicalExecutionGatewayService` | Gateway Dispatcher Handler | M18 `TOOL_INVOCATION` + M10 | Yes (`TOOL_INVOCATION`) | Yes | **PROTECTED** |
| `execution.workflow.resume` | M10 Workflow | `ClinicalExecutionGatewayService` | Gateway Dispatcher Handler | M18 `WORKFLOW_RESUMPTION` + M10 | Yes (`WORKFLOW_RESUMPTION`) | Yes | **PROTECTED** |
| `execution.registration.execute` | M13 Registration | `ClinicalExecutionGatewayService` | Gateway Dispatcher Handler | M18 `TRAJECTORY_ALIGNMENT` + M10 | Yes (`REGISTRATION_ALIGNMENT`) | Yes | **PROTECTED** |
| `workflow.start / transition / confirm / abort` | M10 Workflow | `WorkflowService` | Workflow Dispatcher Handler | Procedure transitions + Interlocks | No | State Machine Only | **CONDITIONALLY PROTECTED** |
| `platform.cycle / session.start / stop / reset` | M09 Platform | `PlatformService` | Platform Dispatcher Handler | Transaction lock + Epoch validation | No | Event Sink Only | **CONDITIONALLY PROTECTED** |
| `planning.submit` | M12 Planning | `PlanningService` | Planning Dispatcher Handler | **NONE** | **NONE** | **NONE** | **BYPASS** |
| `planning.lock` | M12 Planning | `PlanningService` | Planning Dispatcher Handler | **NONE** | **NONE** | **NONE** | **BYPASS** |
| `planning.verify` | M12 Planning | `PlanningService` | Planning Dispatcher Handler | **NONE** | **NONE** | **NONE** | **BYPASS** |

---

## 4. Dispatcher Audit

A complete sweep across all registered command handlers in `python/holomed/` reveals:
1. `execution.*` (6 routes): Universal execution gateway endpoints. Fully mediated.
2. `workflow.*` (4 routes): Workflow engine state machine controls. Enforce procedure constraints.
3. `platform.*` (4 routes): Platform supervisor controls. Single-transaction locked.
4. `safety_gate.evaluate` (1 route): Pure read-only status evaluation.
5. `proximity.evaluate` / `drift.evaluate` (2 routes): Pure spatial geometry evaluations.
6. `gateway.disconnect` (1 route): Transport session termination.
7. `persistence.replay` (1 route): Audit log replay into memory.
8. `*.reset` (Audio, Vision, Gesture, XR, Anatomy, Tools, Ultron): Perceptual and reasoning buffer clears.
9. `device.*` (2 routes): Telemetry sync and snapshot handlers.
10. `planning.*` (**3 COMMAND routes**):
    - `planning.submit` (COMMAND) $\to$ `handle_submit_command`
    - `planning.lock` (COMMAND) $\to$ `handle_lock_command`
    - `planning.verify` (COMMAND) $\to$ `handle_verify_command`

**Finding**: `planning.submit`, `planning.lock`, and `planning.verify` are the **only mutating domain command routes** remaining on `MessageDispatcher` outside `execution.*`.

---

## 5. Public API Audit

Inspection of public service APIs confirms:
- `RegistrationService` (`submit_fiducials`, `solve_registration`, `verify_registration`): Enforces active `_ExecutionCapability` (`REGISTRATION_ALIGNMENT`).
- `NavigationService` (`bind_trajectory`, `submit_tracking_pose`): Enforces active `_ExecutionCapability` (`TRAJECTORY_ALIGNMENT`, `TOOL_NAVIGATION`).
- `RecoveryService` (`stage_candidate`, `verify_candidate`, `activate_recovery`): Enforces active `_ExecutionCapability` (`RECOVERY_REORIENTATION`).
- `ToolService` (`invoke_tool`): Enforces active `_ExecutionCapability` (`TOOL_INVOCATION`).
- `WorkflowService` (`resume_from_recovery`): Enforces active `_ExecutionCapability` (`WORKFLOW_RESUMPTION`).
- **`PlanningService` (`submit_plan`, `lock_plan`, `verify_plan`)**:
  - Accept direct programmatic invocations with **zero capability validation**.
  - `submit_plan(plan, session_id)` modifies internal dictionaries directly.
  - `lock_plan(plan_id)` directly calls `self._workflow_service.register_checkpoint(chk)`.
  - `verify_plan(...)` updates verification records without gate clearance.

---

## 6. State Ownership Matrix

| State Component | Authoritative Owner | Authorized Writers | Readers | Invalidation / Reset |
|---|---|---|---|---|
| Surgical Plans & Trajectories | `PlanningService` (M12) | `PlanningService` (unprotected) | Registration, Navigation, Gateway | `PlanningService.clear()` |
| Anatomical Checkpoints | `WorkflowService` (M10) | `PlanningService.lock_plan()` (Inverted writer!) | CheckpointValidator, WHO Timeout | Workflow reset |
| Patient-to-Plan Transform | `RegistrationService` (M13) | `RegistrationService` (via Capability) | Navigation, SafetyGate, Recovery | `invalidate()`, `clear()` |
| Active Navigation Trajectory | `NavigationService` (M14) | `NavigationService` (via Capability) | SafetyGate, XR, Proximity | `clear()` |
| Exclusion Zones | `ProximityService` (M15) | `ProximityService` (via Recovery/Setup) | SafetyGate, Navigation | `clear()` |
| Anatomical Landmarks | `DriftService` (M16) | `DriftService` (via Recovery/Setup) | SafetyGate, Recovery | `clear()` |
| Spatial Recovery State | `RecoveryService` (M17) | `RecoveryService` (via Capability) | SafetyGate, Gateway | `clear()` |
| Workflow State & Phase | `WorkflowService` (M10) | `WorkflowService` (via Gateway/Console) | SafetyGate, Gateway, Tools | Workflow reset |
| Cross-Service Safety Decision | `SafetyGateService` (M18) | `SafetyGateEvaluator` | Gateway, Workflow | Stateless evaluation |

**Critical State Ownership Anomaly**: `WorkflowService` anatomical checkpoints are mutated by `PlanningService.lock_plan()`! When `PlanningService` locks a plan, it derives checkpoints and pushes them into `WorkflowService._checkpoint_validator`. Because `planning.lock` is an unauthenticated dispatcher route, an external caller can inject unverified anatomical checkpoints directly into M10 Workflow!

---

## 7. Session Lifecycle

Tracing session lifecycle (`start_session` $\to$ `operate` $\to$ `abort` $\to$ `stop_session`):
- All services maintain per-session isolation caches.
- Execution Gateway deduplicates and tracks results per `session_id`.
- Capabilities minted by Gateway bind strictly to a single `session_id` and single `service_instance_id`.
- Session teardown releases structural resources cleanly across all services.
- **Risk**: In `PlanningService`, `_session_plan_bindings` is indexed by `session_id`, but `submit_plan` does not verify whether `session_id` is an active session registered with `PlatformService` or `WorkflowService`. A rogue caller can bind arbitrary plans to non-existent or conflicting sessions.

---

## 8. Epoch / Revision / Freshness

- `epoch_id`: Bound at service initialization via `RuntimeContext.epoch_id`.
- M18 Safety Gate enforces `RUNTIME_EPOCH_MISMATCH` interlocks if any attached service reports an epoch different from the evaluation context.
- In `PlanningService`: `verify_plan` records `epoch_id`, but `submit_plan` and `lock_plan` do not evaluate or record `epoch_id`.
- Stale plan risk: If an intraoperative epoch migration occurs, planning definitions remain in memory without epoch invalidation.

---

## 9. Safety Gate Coverage

Examining `SafetyGateAction`:
- `TOOL_NAVIGATION` $\to$ M14 tracking poses
- `TRAJECTORY_ALIGNMENT` $\to$ M14 trajectory binding & M13 registration solving/verification
- `RECOVERY_REORIENTATION` $\to$ M17 candidate staging/verification/activation
- `WORKFLOW_RESUMPTION` $\to$ M10 resumption to navigation
- `TOOL_INVOCATION` $\to$ M07 tool execution

`SafetyGateAction.TRAJECTORY_ALIGNMENT` evaluates trajectory and alignment safety across all attached subsystems:
- Denies on session mismatch.
- Denies on exclusion zone breach.
- Denies on landmark integrity failure.
- Denies on landmark drift exceeded.
- Denies on recovery failure or epoch mismatch.
- Permitted with caution when registration is unverified (exactly the condition during preoperative planning).
- Denies when workflow is blocked or aborted (`RECOVERY_REQUIRED`, `ABORTED`).

**Notice**: `TRAJECTORY_ALIGNMENT` is semantically and operationally compatible with preoperative planning verification, locking, and submission! M18 can remain **100% FROZEN**.

---

## 10. Workflow Authority

- In M10 `WorkflowService`, phases strictly govern clinical progression:
  - `PATIENT_CONTEXT` $\to$ Patient identity matching
  - `PRE_PROCEDURE_PLANNING` $\to$ Preoperative plan submission, locking, and verification
  - `REGISTRATION` $\to$ Fiducial alignment
  - `SAFETY_TIMEOUT` $\to$ WHO surgical checklist & checkpoint verification
  - `NAVIGATION` / `INTERVENTION` $\to$ Active surgical guidance
- Currently, `PlanningService` allows `planning.submit` and `planning.lock` to execute during ANY phase (including `NAVIGATION` and `INTERVENTION`).
- Gating planning through the Execution Gateway and evaluating M10 `WorkflowService.authorize_tool("planning.submit")` will strictly prevent plan mutations outside `PRE_PROCEDURE_PLANNING`.

---

## 11. Planning Audit (Detailed M12 Analysis)

### Code Inspection: `python/holomed/planning/service.py`
1. **Lines 128–131**:
   ```python
   self._dispatcher.register_query_handler("planning.get", self.handle_get_query, self.name)
   self._dispatcher.register_command_handler("planning.submit", self.handle_submit_command, self.name)
   self._dispatcher.register_command_handler("planning.lock", self.handle_lock_command, self.name)
   self._dispatcher.register_command_handler("planning.verify", self.handle_verify_command, self.name)
   ```
   Exposes mutating commands directly to all dispatcher participants.
2. **Lines 215–245 (`submit_plan`)**:
   - Only checks `plan.is_locked` if already present.
   - Does not check capability.
   - Does not check workflow phase.
   - Does not write persistent audit.
3. **Lines 247–292 (`lock_plan`)**:
   - Mutates plan state to `is_locked = True`.
   - **Inversion of Control**: Unilaterally derives checkpoints and registers them in M10 `WorkflowService`.
   - Does not check capability.
   - Does not write persistent audit.
4. **Lines 293–330 (`verify_plan`)**:
   - Evaluates patient hash, procedure code, laterality.
   - Does not check capability.
   - Does not write persistent audit.

---

## 12. Platform / Lifecycle Audit (M09 Evaluation)

As requested, M09 Platform was re-evaluated:
- `PlatformService` commands on dispatcher:
  - `platform.cycle`
  - `platform.session.start`
  - `platform.session.stop`
  - `platform.reset`
- `migrate_epoch()` is NOT exposed on the dispatcher (internal programmatic API only).
- When `migrate_epoch()` runs:
  - If an epoch mismatch occurs between services, M18 Safety Gate **fails closed** (`GateReasonCode.RUNTIME_EPOCH_MISMATCH`).
- Platform lifecycle operations are already single-threaded and guarded by `self._in_transaction`.
- **Verdict**: M09 Platform is already fail-closed. No bypass exists that would allow uncoordinated clinical actuation. M09 MUST remain **FROZEN**.

---

## 13. Persistence & Audit Consistency

- `ClinicalExecutionGatewayService` logs all execution attempts, gate denials, workflow blocks, calculation failures, and completed executions to `PersistenceService`.
- In contrast, `PlanningService` emits transient dispatcher events (`planning.plan.submitted`, `planning.plan.locked`, `planning.plan.verified`), but has NO persistent audit trail.
- If an unauthorized entity submits an invalid plan or locks an unverified plan, no durable audit record is created in `DurableAuditStore`.

---

## 14. Fail-Closed & Exception Behavior

- In `PlanningService`, dispatcher handlers catch `Exception` and return `ERR_...` error responses.
- However, direct programmatic calls do not fail closed with authorization errors because no authorization check exists.
- In `ClinicalExecutionGatewayService`, all execution paths catch exceptions, redact sensitive tokens, invalidate capabilities in `finally:`, and return `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` with audit records.

---

## 15. Concurrency & Reentrancy

- `PlanningService` uses `self._in_transaction: bool` to prevent reentrancy during `submit_plan`, `lock_plan`, and `stop`.
- `ClinicalExecutionGatewayService` uses `self._in_transaction: bool` to serialize all clinical executions.
- By mediating planning operations through the Execution Gateway, planning mutations will be serialized alongside trajectory binding, registration, recovery, and tool execution.

---

## 16. Test Coverage Gaps

Existing tests:
- `tests/unit/planning/test_planning_service.py` currently relies on raw dispatcher commands `planning.submit`, `planning.lock`, and `planning.verify`.
- There are no capability enforcement tests for `PlanningService`.
- There are no tests verifying that `planning.submit` is blocked during `INTERVENTION` or `RECOVERY_REQUIRED` phases.
- There are no tests verifying that M18 safety gate blocks prevent plan locking.

---

## 17. Frozen-Boundary Integrity

All frozen packages must remain strictly untouched:
- M09 Platform: FROZEN
- M10/M20 Workflow: FROZEN
- M11 Transport Gateway: FROZEN
- M13 Registration: FROZEN (Completed in M23)
- M14 Navigation: FROZEN
- M15 Proximity: FROZEN
- M16 Drift: FROZEN
- M17 Recovery: FROZEN
- M18 Safety Gate: FROZEN

Can M24 achieve complete perimeter hardening without reopening any frozen milestones?
**YES**:
`ClinicalExecutionGatewayService` already interfaces with M18 Safety Gate (using existing `SafetyGateAction.TRAJECTORY_ALIGNMENT`) and M10 Workflow (using existing `WorkflowService.authorize_tool()`).
Neither M18 nor M10 needs to be modified.

---

## 18. Remaining Genuine Gaps

The audit identifies exactly **ONE** remaining genuine architectural gap:
- **Title**: M12 Preoperative Planning Dispatcher Mutation Bypass & Unmediated Checkpoint Injection
- **Severity**: **CRITICAL**
- **Evidence**:
  - `python/holomed/planning/service.py:129-131` registers raw mutating commands `planning.submit`, `planning.lock`, `planning.verify`.
  - `PlanningService` methods lack capability verification.
  - `PlanningService.lock_plan` mutates M10 `WorkflowService` without authorization.
  - No M18 safety gating or M10 phase checking on plan mutations.
  - No persistent audit records for planning operations.

---

## 19. Candidate Ranking

| Candidate | Description | Severity | Impact | Feasibility | Recommendation |
|---|---|---|---|---|---|
| **Candidate A: M12 Preoperative Planning Execution Hardening** | Remove raw dispatcher mutation routes; require `_ExecutionCapability` (`PLANNING_COORDINATION`) on `submit_plan`, `lock_plan`, `verify_plan`; route via `execution.planning.execute` on `ClinicalExecutionGatewayService` | **CRITICAL** | Closes final clinical mutation bypass in the entire HoloMed platform | **HIGH** | **PROCEED TO M24** |
| **Candidate B: M09 Platform Epoch Migration Expansion** | Update `migrate_epoch` to iterate M13-M18 services | **LOW** | Minor optimization; already fails closed via M18 `RUNTIME_EPOCH_MISMATCH` interlocks | **LOW** | **DO NOT REOPEN M09** |

---

## 20. Highest-Priority M24 Candidate

### M12 Preoperative Planning Execution Hardening
The preoperative surgical plan is the foundation for all spatial safety:
1. Trajectories bound to tracked navigation derive from `plan.trajectories`.
2. Proximity exclusion zones derive from `plan.exclusion_zones`.
3. Anatomical checkpoints derive from the locked plan.
4. Rigid registration requires a locked plan (`_verify_locked_plan`).

Leaving `planning.submit`, `planning.lock`, and `planning.verify` exposed on the public dispatcher creates an unmediated vector where rogue messages or out-of-phase clients can alter surgical trajectories, inject false checkpoints, or mutate exclusion zones without safety gate oversight or audit logging.

---

## 21. Alternative Architectures

### Architecture 1: Universal Gateway Integration (Recommended)
- **Authority Count**: 1 (Single Universal Execution Gateway).
- **Reopened Milestones**: M12 Planning, M19/M21/M22/M23 Execution Gateway.
- **New Routes**:
  - Dispatcher: `execution.planning.execute` (COMMAND).
  - Pruned: `planning.submit`, `planning.lock`, `planning.verify` (REMOVED).
  - Retained: `planning.get` (QUERY only).
- **Security Strength**: Universal dual-gate M18/M10 enforcement, single-use capability lifecycle, durable audit.
- **Duplication**: Zero. Reuses existing `ClinicalExecutionGatewayService` pipeline.
- **Regression Surface**: Minimal. Only M12 planning service and gateway tests.

### Architecture 2: Standalone Planning Coordination Gateway
- **Authority Count**: 2 (Separate Planning Coordinator alongside Clinical Execution Gateway).
- **Reopened Milestones**: M12 Planning, new package `holomed/planning/gateway`.
- **New Routes**: `planning.gateway.submit`, `planning.gateway.lock`, etc.
- **Security Strength**: Moderate. Introduces fragmented authorization logic and split audit trails.
- **Duplication**: High. Re-implements capability minting, M18 evaluation, and persistence calls.
- **Regression Surface**: High. Introduces new architectural concepts and dual coordinators.

---

## 22. Selected Architecture

**Architecture 1: Universal Gateway Integration** is selected:
- Extends the established pattern from M21 (Tools, Navigation), M22 (Recovery, Trajectory Binding), and M23 (Registration).
- Maintains `ClinicalExecutionGatewayService` as the **sole authoritative execution coordinator** in the system.
- Leaves M09, M10, M11, M13, M14, M15, M16, M17, and M18 **100% FROZEN**.

---

## 23. Minimum Reopen Set

1. **M12 Planning**:
   - `python/holomed/planning/service.py`
   - `tests/unit/planning/test_planning_service.py`
   - `tests/unit/planning/test_planning_adversarial_matrix.py`
2. **M19/M21/M22/M23/M24 Execution Gateway**:
   - `python/holomed/execution/models.py`
   - `python/holomed/execution/service.py`
   - `tests/unit/execution/test_m24_hardening.py` (new test suite)

All other milestones remain **FROZEN**.

---

## 24. M24 Feasibility

- **Feasibility Assessment**: **READY FOR CONTRACT LOCK**.
- **Complexity**: Low to Moderate. Direct application of established M23 capability pattern to M12.
- **Estimated Test Delta**: ~10–12 new unit tests in execution and updated planning tests.
- **Risk of Regression**: Extremely low. 1501 existing tests establish a rigid regression barrier.

---

## 25. M24 Contract Preview

1. `PlanningService.initialize()` registers ONLY `planning.get` (QUERY).
2. `PlanningService.submit_plan()`, `lock_plan()`, `verify_plan()` require an active `_ExecutionCapability` with `action="PLANNING_COORDINATION"`, `service_instance_id=id(self)`, matching `session_id` and `sequence_number`.
3. `ClinicalExecutionGatewayService` acquires structural handle to `PlanningService`.
4. `ClinicalExecutionGatewayService` registers `execution.planning.execute` (COMMAND).
5. `PlanningExecutionRequest` models `operation: ("SUBMIT", "LOCK", "VERIFY")`.
6. Execution pipeline:
   - Request validation
   - M18 Safety Gate evaluation (`TRAJECTORY_ALIGNMENT`)
   - M10 Workflow Authorization (`planning.submit`, `planning.lock`, `planning.verify`)
   - Ephemeral capability minting (`PLANNING_COORDINATION`)
   - `PlanningService` dispatch inside `try: ... finally: capability.invalidate()`
   - Durable persistence audit
   - Protocol event emission
7. Direct calls without capability fail closed with `PlanningAuthorizationError`.

---

## FINAL CLASSIFICATION

```text
M24_JUSTIFIED_AND_FEASIBLE
```
