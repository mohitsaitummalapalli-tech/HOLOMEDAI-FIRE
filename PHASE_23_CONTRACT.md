# Phase 23 Contract — Initial Spatial Registration Lifecycle Capability Hardening

**Authoritative Baseline:** `0c6da00d40132fd0215d6c17db91c84f9377f207` (M22 Release)  
**Contract Status:** `LOCKED`  
**Date:** September 2026  

---

## 1. Objective

Close the remaining unmediated initial spatial registration execution bypass identified after M22.

M23 must:
1. Remove raw dispatcher mutation routes:
   - `registration.submit`
   - `registration.solve`
   - `registration.verify`
2. Require internal `_ExecutionCapability` on:
   - `RegistrationService.submit_fiducials()`
   - `RegistrationService.solve_registration()`
   - `RegistrationService.verify_registration()`
3. Route registration execution exclusively through the existing `ClinicalExecutionGatewayService`.
4. Preserve exactly ONE execution coordinator (`ClinicalExecutionGatewayService`).
5. Preserve M10/M20 workflow authority without duplicating policy.
6. Preserve M18 safety semantics without modifying frozen files.
7. Keep M09 Platform epoch migration FROZEN (safe fail-closed).

---

## 2. Reopened Milestones

**EXACTLY:**
- `M13 Registration`
- `M17 Recovery` (capability propagation in `activate_recovery()`)
- `M19/M21/M22 Execution Gateway`

Corresponding unit tests may be modified.

---

## 3. Frozen Milestones

- `M01-M08 Subsystems`
- `M09 Platform`
- `M10/M20 Workflow`
- `M11 Gateway`
- `M12 Planning`
- `M14 Navigation`
- `M15 Proximity`
- `M16 Drift`
- `M18 Safety Gate`
- And all other milestones.

**NO OTHER MILESTONE MAY CHANGE.**

---

## 4. M13 Registration Semantics

Preserve the audited internal registration semantics:
- `submit_fiducials()`: **PREPARATORY** (Ingests point cloud, creates DRAFT record).
- `solve_registration()`: **EVALUATIVE & STATE-COMMITTING** (Solves Horn transform, computes FRE, sets SOLVED or FAILED).
- `verify_registration()`: **CLINICAL_ACTUATION** (Evaluates drift at checkpoint, commits VERIFIED or INVALIDATED transform).

Internal semantics are distinct and must not be collapsed.

---

## 5. Exact Routes to Remove

Remove from M13 `RegistrationService` dispatcher registration:
- `registration.submit` (COMMAND) $\to$ **REMOVED** (Raises `UnroutableMessageError`)
- `registration.solve` (COMMAND) $\to$ **REMOVED** (Raises `UnroutableMessageError`)
- `registration.verify` (COMMAND) $\to$ **REMOVED** (Raises `UnroutableMessageError`)

**KEEP:**
- `registration.get` (QUERY) as the sole read-only query route on M13.

---

## 6. Authoritative New Route

Add exactly:
- `execution.registration.execute` (COMMAND)
- **Owner:** `ClinicalExecutionGatewayService`
- **Payload:**
  - `session_id: str`
  - `sequence_number: int`
  - `now_utc: str`
  - `registration_operation: str` (`"SUBMIT"`, `"SOLVE"`, `"VERIFY"`)
  - `plan_id: Optional[str]`
  - `cloud: Optional[FiducialCloud]`
  - `operator_id: Optional[str]`
  - `checkpoint_plan_mm: Optional[Tuple[float, float, float]]`
  - `checkpoint_measured_mm: Optional[Tuple[float, float, float]]`

---

## 7. Execution Authority

- **Sole Coordinator:** `ClinicalExecutionGatewayService`
- **Authorizer:** `WorkflowService`
- **Safety Evaluator:** `SafetyGateService`
- **Executor:** `RegistrationService`
- **Persister:** `PersistenceService`

No second registration gateway. No registration-specific peer coordinator.

---

## 8. Capability Action

- **Capability Action:** `REGISTRATION_ALIGNMENT`
- **Format:** `_ExecutionCapability(internal_key, service_instance_id, session_id, action="REGISTRATION_ALIGNMENT", sequence_number)`

---

## 9. Registration Capability Requirements

The following methods require an active `_ExecutionCapability`:
- `RegistrationService.submit_fiducials()`
- `RegistrationService.solve_registration()`
- `RegistrationService.verify_registration()`

Capability validation at method entry MUST enforce:
1. `capability is not None`
2. `capability.is_active is True`
3. `capability.action == "REGISTRATION_ALIGNMENT"`
4. `capability.session_id == session_id`
5. `capability.sequence_number == sequence_number`
6. `capability.service_instance_id == id(self)`
7. Inactive, expired, or replayed capabilities fail closed.

Validation must occur BEFORE protected state mutation.

---

## 10. Gateway Registration Execution Flow

`execution.registration.execute` execution lifecycle:
1. Request validation (parameters, syntax, sequence).
2. Sequence, session, and epoch consistency validation.
3. Step 1: M18 Safety Gate evaluation.
4. Step 2: M10 Workflow authorization (`authorize_tool`).
5. Step 3: Ephemeral `_ExecutionCapability` minted bound to `id(self._registration_service)`.
6. Step 4: Invocation of target `RegistrationService` operation (`submit_fiducials`, `solve_registration`, or `verify_registration`).
7. Step 5: Audit recording in `PersistenceService`.
8. `finally:` Unconditional capability invalidation (`cap.invalidate()`).
9. Response returned.

---

## 11. Workflow Compatibility

M10/M20 remains **FROZEN**.
Registration operations obey phase policies:
- `SUBMIT`: `REGISTRATION` phase.
- `SOLVE`: `REGISTRATION` phase.
- `VERIFY`: `SAFETY_TIMEOUT` / `REGISTRATION` phase.
- During `NAVIGATION`, `INTERVENTION`, `RECOVERY_REQUIRED`, `ABORTED`, operations fail closed under M10 authorization.

---

## 12. M18 Safety Compatibility

M18 remains **FROZEN**.
M18 evaluation occurs through existing evaluator interfaces without modifying M18 code.

---

## 13. Registration Consistency

- Transform updates committed to `RegistrationService`.
- Subsequent trajectory binding and navigation retrieve the updated transform.
- Recovery re-registration retains atomic post-activation consistency checks.

---

## 14. M17 Recovery Compatibility

- M17 `activate_recovery()` continues using `RegistrationService`.
- It creates and passes an internal `REGISTRATION_ALIGNMENT` capability bound to `id(self._registration_service)` and invalidates it in a `finally:` block.

---

## 15. Capability Lifecycle

- Ephemeral, single-use, transaction-bound.
- Invalidated unconditionally in `finally:` blocks.
- No capability survives execution or can be replayed.

---

## 16. Failure Modes

All failures fail closed:
- Missing, inactive, mismatched, replayed capability $\to$ `RegistrationAuthorizationError`.
- Unauthorized workflow phase $\to$ `ExecutionStatus.BLOCKED_WORKFLOW`.
- Denied safety gate $\to$ `ExecutionStatus.BLOCKED_SAFETY_GATE`.
- Calculation / drift error $\to$ `RegistrationAccuracyError` / `RegistrationVerificationError`.

---

## 17. Audit / Persistence

- All registration executions, verifications, and gate blocks audited to `PersistenceService`.
- Capability cleanup occurs even if persistence fails.

---

## 18. M09 Epoch Rule

- M09 is **FROZEN**.
- `PlatformService.migrate_epoch()` remains unmodified (safe fail-closed).

---

## 19. Exact M23 Route Inventory

**M13 Registration:**
- `registration.get` (QUERY only)

**M19/M21/M22/M23 Execution Gateway:**
- `execution.navigation.execute` (COMMAND)
- `execution.status.get` (QUERY)
- `execution.recovery.execute` (COMMAND)
- `execution.trajectory.bind` (COMMAND)
- `execution.tool.invoke` (COMMAND)
- `execution.workflow.resume` (COMMAND)
- `execution.registration.execute` (COMMAND)

---

## 20. Test Requirements

M23 must prove:
1. `registration.submit` is unroutable.
2. `registration.solve` is unroutable.
3. `registration.verify` is unroutable.
4. `registration.get` remains functional.
5. `execution.registration.execute` is registered and functional.
6. Submission execution works through gateway.
7. Solve execution works through gateway.
8. Verification execution works through gateway.
9. Direct submit without capability fails closed.
10. Direct solve without capability fails closed.
11. Direct verify without capability fails closed.
12. Wrong session fails closed.
13. Wrong action fails closed.
14. Wrong sequence fails closed.
15. Wrong service binding fails closed.
16. Inactive capability fails closed.
17. Replayed capability fails closed.
18. Capability invalidates in `finally:` on exception.
19. M10 phase authorization remains enforced.
20. M18 safety evaluation remains enforced.
21. Registration semantics remain ordered.
22. Downstream consistency remains intact.
23. M17 recovery registration usage remains functional.
24. All 6 M22 execution routes remain functional.
25. Full repository regression remains green.

---

## 21. Implementation Restrictions

**DO NOT:**
- Modify M09, M10/M20, M18.
- Create a second coordinator or separate registration gateway.
- Bypass M10 or M18.
- Expose `_ExecutionCapability`.
- Restore old registration dispatcher routes.

---

## 22. Completion Gate

M23 is not complete until:
- Implementation complete.
- Hostile audit PASS.
- Full regression PASS.
- Frozen boundary PASS.
- Release commit created and pushed to origin/main.
