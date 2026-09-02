# M23 Final Feasibility Audit Report

**Authoritative Baseline:** `M22 = 0c6da00d40132fd0215d6c17db91c84f9377f207`  
**Mode:** READ-ONLY / Pre-Lock Feasibility Audit  
**Date:** September 2026  

---

## 1. M13 Registration Semantic Analysis

Source inspection of `RegistrationService` in [python/holomed/registration/service.py](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/registration/service.py):

| Method | Inputs | State Read | State Mutated | Side Effects | Clinical Classification |
|---|---|---|---|---|---|
| `submit_fiducials()` | `session_id`, `plan_id`, `cloud: FiducialCloud` | `self._registrations`, `self._plan_service` | `_fiducial_clouds[session_id]`, `_registrations[session_id]` (state: `DRAFT`, transform: `None`) | Emits `registration.fiducials.submitted` | **PREPARATORY** |
| `solve_registration()` | `session_id`, `plan_id` | `_fiducial_clouds[session_id]`, `self._plan_service` | Solves transform via Horn solver. If FRE $\le$ 1.5mm: state `SOLVED`, `transform = T`. If FRE > 1.5mm: state `FAILED`, `transform = None` | Emits `registration.solved` or `registration.failed` | **EVALUATIVE & STATE_COMMITTING** |
| `verify_registration()` | `session_id`, `operator_id`, `checkpoint_plan_mm`, `checkpoint_measured_mm` | `_registrations[session_id]` (requires `SOLVED`/`VERIFIED` & `transform`) | If drift $\le$ 1.5mm: state `VERIFIED`, `locked = True`. If drift > 1.5mm: state `INVALIDATED`, `transform = None`, `locked = False` | Emits `registration.verified` or `registration.invalidated` | **CLINICAL_ACTUATION** (Establishes/Destroys Coordinate Frame) |

---

## 2. M13 Dispatcher Route Analysis

Inspection of [python/holomed/registration/service.py:L130-L134](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/registration/service.py#L130-L134):
- `registration.submit` (COMMAND) $\to$ `handle_submit_command`
- `registration.solve` (COMMAND) $\to$ `handle_solve_command`
- `registration.verify` (COMMAND) $\to$ `handle_verify_command`
- `registration.get` (QUERY) $\to$ `handle_get_query`

**Privileged Routes:** `registration.submit`, `registration.solve`, and `registration.verify` are mutating, privileged commands exposed directly on `MessageDispatcher` without workflow authorization or capability gating.

---

## 3. M13 Workflow Compatibility

Inspection of M10/M20 `WorkflowService`:
- `submit_fiducials` and `solve_registration` are legitimately permitted strictly during `WorkflowPhase.REGISTRATION` (or `WorkflowPhase.RECOVERY_REQUIRED` under M17).
- `verify_registration` is legitimately permitted strictly during `WorkflowPhase.SAFETY_TIMEOUT` and `WorkflowPhase.REGISTRATION`.
- Calling registration solving or verification during `WorkflowPhase.NAVIGATION` or `WorkflowPhase.INTERVENTION` represents a dangerous surgical safety violation and must be blocked by M10 workflow authorization (`BLOCKED_PHASE`).

---

## 4. M18 Safety Action Compatibility

- M18 `SafetyGateService` evaluates cross-service interlocks for high-level clinical actions (`TOOL_NAVIGATION`, `TRAJECTORY_ALIGNMENT`, `RECOVERY_REORIENTATION`, `WORKFLOW_RESUMPTION`, `TOOL_INVOCATION`).
- Initial registration operations (`submit`, `solve`, `verify`) take place during pre-procedure setup prior to live instrument navigation.
- In M18 evaluator, initial registration is evaluated via M10 workflow authorization gate (`authorize_tool`) and registration status queries.
- Existing action semantics (`TRAJECTORY_ALIGNMENT` / `WORKFLOW_RESUMPTION` / tool classification) or workflow phase enforcement in `ClinicalExecutionGatewayService` provide complete authorization without modifying frozen M18.

---

## 5. Capability Action Model

To preserve the invariant of single-use, unforgeable capability validation:
- Action name: `"REGISTRATION_ALIGNMENT"` (or `"REGISTRATION_MANAGEMENT"` / `"TRAJECTORY_ALIGNMENT"`).
- Bound parameters: `service_instance_id = id(self._registration_service)`, `session_id`, `action`, `sequence_number`.
- Fails closed on: missing capability, inactive capability, mismatched action, mismatched session, mismatched sequence, mismatched service instance.

---

## 6. M13 Direct-Call Security

Production caller audit for `submit_fiducials()`, `solve_registration()`, `verify_registration()`:
1. `RegistrationService` command handlers (to be removed from dispatcher).
2. `RecoveryService.activate_recovery()`: Already operates inside a hardened capability context; will mint and pass internal capability to `RegistrationService` before downstream rebinds.
3. `ClinicalExecutionGatewayService`: New coordinated execution entrypoints.

Direct calls without valid capability will raise `RegistrationAuthorizationError`.

---

## 7. Downstream Registration Consistency

When registration is verified through the execution gateway:
- Transform is committed to `RegistrationService`.
- Subsequent trajectory binding (`execute_trajectory_binding`) and navigation (`execute_navigation`) read the verified transform.
- Recovery re-registration retains existing atomic post-activation consistency verification.

---

## 8. Gateway Design

Minimal and cohesive gateway additions on `ClinicalExecutionGatewayService`:
- Add execution method: `execute_registration()` (handling sub-operations `SUBMIT`, `SOLVE`, `VERIFY`) OR distinct methods:
  - `execute_registration_submission()`
  - `execute_registration_solve()`
  - `execute_registration_verification()`
- Add dispatcher command: `execution.registration.execute` (COMMAND).
- Remove raw `registration.submit`, `registration.solve`, `registration.verify` from `MessageDispatcher`.
- Retain read-only query `registration.get` on `MessageDispatcher`.

---

## 9. M09 Epoch Semantics & Architecture Analysis

Source inspection of [python/holomed/platform/service.py:L330-L375](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/platform/service.py#L330-L375):
- `migrate_epoch(target_epoch_id)` is a platform supervisor mechanism designed in Phase 9.
- It is only invoked via `platform.reset` command.
- When an epoch mismatch occurs downstream, safety evaluators reject requests with `RUNTIME_EPOCH_MISMATCH` (Safe Fail-Closed).
- Post-M09 services do not dynamically mutate epoch during clinical execution; epoch is immutable per runtime process instance (`context.epoch_id`).

---

## 10. Epoch Migration Behavior Classification

- Current behavior when `migrate_epoch()` is called: **SAFE FAIL-CLOSED**.
- Downstream safety checks reject mismatched epochs immediately, preventing stale state acceptance.

---

## 11. Milestone Scope Decision: Candidate A vs Candidate B

| Criterion | Option 1: M13 Registration Hardening Only | Option 2: M13 + M09 Epoch Coordination | Option 3: M13 in M23, M09 in M24 |
|---|---|---|---|
| **Reopened Milestones** | **M13, M19/M21/M22** (Minimal, Cohesive) | M09, M10, M12, M13, M14, M15, M16, M17, M18, M19 (Massive Reopen) | M13 in M23, M09 deferred |
| **Security Impact** | **Closes Critical Raw Dispatcher Bypass** | Closes bypass + modifies supervisor | Closes critical bypass first |
| **Regression Surface** | Low / Localized | Extreme (Cross-subsystem coupling) | Low / Localized |
| **Architectural Cohesion** | **100% Focused on Spatial Authority** | Diluted across supervisor and spatial | High |

**Decision:** **OPTION 1 (M13 Registration Hardening Only)**.  
M09 supervisor epoch migration is safe fail-closed and should not force reopening 10 frozen milestones.

---

## 12. Minimum Reopen Set

- **M13 Registration**: `python/holomed/registration/service.py`, `python/holomed/registration/exceptions.py`
- **M19/M21/M22 Execution Gateway**: `python/holomed/execution/service.py`, `python/holomed/execution/models.py`
- **M17 Recovery**: `python/holomed/recovery/service.py` (pass capability when calling registration in `activate_recovery`)
- **Tests**: `tests/unit/registration/`, `tests/unit/execution/`, `tests/unit/recovery/`

---

## 13. M22 Compatibility

All 6 M22 execution gateway routes remain unchanged:
1. `execution.navigation.execute`
2. `execution.status.get`
3. `execution.recovery.execute`
4. `execution.trajectory.bind`
5. `execution.tool.invoke`
6. `execution.workflow.resume`

---

## 14. M23 CONTRACT_DRAFT_PRELOCK

```markdown
# Phase 23 Contract — Initial Spatial Registration Lifecycle Capability Hardening

## Reopened Milestones:
- M13 Registration
- M17 Recovery (capability propagation in activate_recovery)
- M19/M21/M22 Clinical Execution Gateway

## Frozen Milestones:
- M01-M12, M14-M16, M18, M20

## Removed Routes:
- `registration.submit` (COMMAND)
- `registration.solve` (COMMAND)
- `registration.verify` (COMMAND)

## Retained Routes:
- `registration.get` (QUERY)

## Added Routes:
- `execution.registration.execute` (COMMAND)

## Capability Requirements:
- `RegistrationService.submit_fiducials()` requires valid `_ExecutionCapability`
- `RegistrationService.solve_registration()` requires valid `_ExecutionCapability`
- `RegistrationService.verify_registration()` requires valid `_ExecutionCapability`
- Capability action: `REGISTRATION_ALIGNMENT`
- Bound to: `service_instance_id = id(self._registration_service)`, `session_id`, `sequence_number`
- Invalidation: Unconditional `finally: cap.invalidate()` in gateway and recovery callers
```

---

## 15. Final Classification

**`READY_FOR_LOCK`**
