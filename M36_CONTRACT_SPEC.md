# M36 CONTRACT SPECIFICATION: Cross-Session Surgical Plan Ownership & Trajectory Integrity Verification

## Milestone Metadata
- **Milestone**: M36 — Cross-Session Surgical Plan Ownership & Trajectory Integrity Verification
- **Authoritative Baseline Commit**: `119d342804fd29e521cfe8680f7faae51cc89a66` (M35 Frozen Release)
- **Prior Milestones (M19–M35)**: RELEASED & FROZEN
- **Target Production Modules**:
  1. `python/holomed/planning/models.py`
  2. `python/holomed/registration/service.py`
  3. `python/holomed/registration/exceptions.py`
  4. `python/holomed/recovery/service.py`
  5. `python/holomed/recovery/exceptions.py`
  6. `python/holomed/navigation/service.py`
  7. `python/holomed/execution/service.py`
- **Target Test Surface**: `tests/unit/execution/test_m36_plan_trajectory_ownership.py`

---

## A. Canonical Ownership Invariant

> **The Canonical HoloMed M36 Clinical Ownership & Trajectory Integrity Invariant:**
> *"Any clinical execution, spatial registration, trajectory binding, navigation evaluation, or recovery reorientation operation targeting a clinical session $S$ MUST consume ONLY the authoritative surgical plan $P$ where $\text{PlanningService.\_session\_plan\_bindings}[S] == P.\text{plan\_id}$ and $P.\text{is\_locked} = \text{True}$. Callers MUST NOT be able to establish ownership merely by supplying a matching-looking `plan_id`. All downstream registration records ($\text{RegistrationStatusRecord.plan\_id}$), trajectory identifiers, and trajectory geometries ($\text{TrajectoryPlan}$) MUST be strictly derived from or verified against $P.\text{trajectories}$; caller-supplied identifiers or geometries that conflict with or diverge from $P$ MUST be rejected fail-closed with zero state mutation. In production execution paths, absence of the authoritative ownership source MUST fail closed and MUST NOT bypass ownership validation."*

---

## B. Authoritative Plan Ownership Source

1. **Authoritative Ownership Registry**:
   Authoritative session ownership of a surgical plan is stored exclusively in:
   $$\text{PlanningService.\_session\_plan\_bindings}: \text{Dict}[\text{str}, \text{str}] \quad (\text{session\_id} \longrightarrow \text{plan\_id})$$
2. **Plan Definition Repository**:
   The authoritative surgical plan definition is stored in:
   $$\text{PlanningService.\_plans}: \text{Dict}[\text{str}, \text{SurgicalPlanDefinition}] \quad (\text{plan\_id} \longrightarrow \text{SurgicalPlanDefinition})$$
3. **Chain of Trust Verification**:
   Ownership validation MUST NOT rely on caller assertions. The verification sequence is:
   $$\text{request.session\_id} \longrightarrow \text{authoritative bound plan\_id} \longrightarrow \text{plan lookup in \_plans} \longrightarrow \text{plan.is\_locked verification}$$
   - If `request.session_id` is missing from `_session_plan_bindings`, reject immediately.
   - If caller supplied `request.plan_id` and `request.plan_id != bound_plan_id`, reject immediately.
   - If `bound_plan_id` is not present in `_plans`, reject immediately.
   - If $P.\text{is\_locked}$ is `False`, reject immediately.
   - Any broken or inconsistent link causes deterministic fail-closed rejection.

---

## C. Authoritative Trajectory Source

1. **Authoritative Trajectory Collection**:
   Trajectories are owned exclusively by their enclosing `SurgicalPlanDefinition`:
   $$P_S.\text{trajectories}: \text{Tuple}[\text{TrajectoryPlan}, \dots]$$
2. **The Authoritative Trajectory Rule**:
   1. Resolve the currently locked plan for `request.session_id` via `PlanningService.get_plan_for_session(request.session_id)`.
   2. Resolve `request.trajectory_id` ONLY within that plan's `P_S.trajectories`.
   3. The resolved trajectory $T \in P_S.\text{trajectories}$ is authoritative.
   4. A caller-supplied `request.plan_trajectory` is NEVER the source of truth.
   5. If `request.plan_trajectory` is supplied, treat it strictly as an assertion and compare it against the authoritative trajectory $T$.
   6. On mismatch, reject before any state mutation.
   7. Pass and use the authoritative trajectory object $T$ downstream for coordinate transformation and navigation binding.

---

## D. Production Behavior When `PlanningService` Is Unavailable

1. **Production Fail-Closed Rule**:
   In production execution paths (`RegistrationService`, `ClinicalExecutionGatewayService`, `RecoveryService`), absence of the authoritative `PlanningService` handle MUST NOT disable or bypass ownership validation.
   - If `PlanningService` is required for a security authorization decision but unavailable (`is None`), execution MUST FAIL CLOSED immediately with the subsystem's established lifecycle exception:
     - `ClinicalExecutionGatewayService`: raises `ExecutionLifecycleError("PlanningService handle is unavailable; cannot verify plan ownership")` (or returns `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` with that message).
     - `RegistrationService`: raises `RegistrationLifecycleError("PlanningService handle is unavailable; cannot verify plan ownership")`.
     - `RecoveryService`: raises `RecoveryLifecycleError("PlanningService handle is unavailable; cannot verify plan ownership")`.
   - Production code MUST NEVER contain a fail-open conditional branch such as:
     ```python
     # FORBIDDEN:
     if planning_service is not None:
         validate()
     else:
         allow()
     ```
2. **Test Isolation Compatibility**:
   - Standalone unit tests requiring test isolation (e.g. testing isolated Horn solver routines without platform wiring) must provide an explicit mock or test-double `PlanningService` (e.g. `mock_planning = MagicMock(spec=PlanningService)`) configured with `get_plan_for_session(session_id)` and `get_locked_plan(plan_id)` to the service constructor.
   - Compatibility is achieved through explicit test fixture configuration, never by weakening production security gates.

---

## E. Exact Trajectory Integrity Comparison Semantics and Units

Do not use a universal scalar across heterogeneous physical and normalized units. Each property of `TrajectoryPlan` is compared according to its physical dimension:

| Field | Type & Units | Comparison Semantics | Canonical Tolerance Constant | Tolerance Value | Justification / Repository Convention |
|---|---|---|---|---|---|
| `trajectory_id` | `str` | Exact string equality | N/A | `==` | Identifier grammar match. |
| `target_structure` | `str` | Exact string equality | N/A | `==` | Anatomical target label match. |
| `entry_point_mm` | `Tuple[float, float, float]` (mm) | Absolute tolerance coordinate-wise | `TRAJECTORY_POINT_TOLERANCE_MM` | `1e-6` mm | Conforms to `COORDINATE_EPSILON = 1e-6` in `anatomy.models` & `gesture.models`. $\max_i \|c_i - a_i\| \le 10^{-6}$. |
| `target_point_mm` | `Tuple[float, float, float]` (mm) | Absolute tolerance coordinate-wise | `TRAJECTORY_POINT_TOLERANCE_MM` | `1e-6` mm | Cartesian spatial coordinate match. $\max_i \|c_i - a_i\| \le 10^{-6}$. |
| `max_lateral_deviation_mm` | `float` (mm) | Absolute tolerance | `TRAJECTORY_LATERAL_TOLERANCE_MM` | `1e-6` mm | Spatial clearance bound in mm. $\|c - a\| \le 10^{-6}$. |
| `max_angular_deviation_deg` | `float` (degrees) | Absolute tolerance | `TRAJECTORY_ANGULAR_TOLERANCE_DEG` | `1e-4` deg | Angular divergence bound (~0.36 arcseconds, well below tracking noise). $\|c - a\| \le 10^{-4}$. |
| `min_confidence` | `float` ($[0.0, 1.0]$) | Absolute tolerance | `TRAJECTORY_CONFIDENCE_TOLERANCE` | `1e-6` | Normalized ratio. Conforms to `NORMALIZED_QUANTIZATION = 1e-6` in `vision.models`. $\|c - a\| \le 10^{-6}$. |
| `max_uncertainty` | `float` ($[0.0, 1.0]$) | Absolute tolerance | `TRAJECTORY_UNCERTAINTY_TOLERANCE` | `1e-6` | Normalized ratio. Conforms to `NORMALIZED_QUANTIZATION = 1e-6` in `vision.models`. $\|c - a\| \le 10^{-6}$. |

All canonical constants are defined in `python/holomed/planning/models.py` and imported across `holomed.execution` and `holomed.navigation`. Scatter of raw float literals is strictly prohibited.

---

## F. Exact Registration Enforcement Point

1. **`RegistrationService._verify_locked_plan(plan_id: str, session_id: str) -> None`**:
   - Requires both `plan_id` and `session_id`.
   - Step 1: Verify `self._planning_service is not None` (raises `RegistrationLifecycleError` if None).
   - Step 2: Query $P_S = \text{self.\_planning\_service.get\_plan\_for\_session}(session\_id)$. If $P_S$ is None, raise `RegistrationPlanMismatchError(f"No surgical plan bound to session {session_id!r}")`.
   - Step 3: Verify $P_S.\text{plan\_id} == plan\_id$. If mismatch, raise `RegistrationPlanMismatchError(f"Plan {plan_id!r} is not bound to session {session_id!r} (bound plan is {P_S.plan_id!r})")`.
   - Step 4: Verify $P_S.\text{is\_locked}$. If False, raise `RegistrationPlanMismatchError(f"Registration requires plan {plan_id!r} to be locked (D315)")`.
2. **Zero-Mutation Call Ordering**:
   In `submit_fiducials` and `solve_registration`, the call sequence is:
   $$\text{Session Ownership Check} \longrightarrow \text{Plan Locked Check} \longrightarrow \text{Capability Check} \longrightarrow \text{Only Then Fiducial/Registration Mutation}$$
3. **Gateway Ingress (`ClinicalExecutionGatewayService.execute_registration`)**:
   In Step 4, before capability minting or calling `RegistrationService`, the gateway validates that `_planning_service` is available and that `request.plan_id` matches the session-bound locked plan. If invalid, returns `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` with audit record.

---

## G. Exact Navigation Enforcement Point

1. **Gateway Enforcement (`ClinicalExecutionGatewayService.execute_trajectory_binding`)**:
   - Resolves $P_S = \text{self.\_planning\_service.get\_plan\_for\_session}(request.session\_id)$.
   - Rejects if $P_S$ is None or $P_S.\text{is\_locked}$ is False (`FAILED_NAVIGATION_GEOMETRY`).
   - Resolves target trajectory $T \in P_S.\text{trajectories}$ where $T.\text{trajectory\_id} == request.trajectory\_id$. Rejects if not found.
   - If caller supplied `request.plan_trajectory`: compares assertion against authoritative $T$ using heterogeneous tolerances. Rejects if divergent.
   - Passes authoritative trajectory $T$ to `NavigationService.bind_trajectory()`.
2. **Navigation Service Ingress (`NavigationService.bind_trajectory`)**:
   - Validates execution capability (`session_id`, `action == "TRAJECTORY_ALIGNMENT"`, `service_instance_id == id(self)`, `is_active`).
   - Verifies active registration exists and has non-empty `plan_id`.
   - Transforms authoritative trajectory $T$ into patient tracker frame and mutates `self._bound_trajectories[session_id]`. Zero mutation occurs on failure.

---

## H. Exact Recovery Enforcement Points

Recovery enforces plan ownership at two distinct, non-bypassable checkpoints:

1. **Enforcement Point 1 (`RecoveryService.stage_candidate`)**:
   - Pre-condition: `self._planning_service is not None` (raises `RecoveryLifecycleError` if None).
   - Validates capability (`action == "RECOVERY_REORIENTATION"`).
   - Queries $P_S = \text{self.\_planning\_service.get\_plan\_for\_session}(session\_id)$.
   - Verifies $P_S$ is not None, is locked, and $P_S.\text{plan\_id} == plan\_id$. Raises `RecoveryPlanMismatchError` if mismatch.
   - Execution occurs BEFORE calling Horn solver and BEFORE mutating `_staged_candidates[session_id]`.
2. **Enforcement Point 2 (`RecoveryService.activate_recovery`)**:
   - Retrieves `candidate = self._staged_candidates[session_id]`.
   - Re-queries $P_S = \text{self.\_planning\_service.get\_plan\_for\_session}(session\_id)$ to verify the session plan has not been unseated.
   - Verifies $P_S.\text{plan\_id} == candidate.\text{plan\_id}$ and $P_S.\text{is\_locked}$.
   - If `plan_trajectory` is provided: resolves trajectory within $P_S.\text{trajectories}$ and verifies geometric integrity against the canonical trajectory.
   - Only upon passing both checks may recovery invoke M13 re-registration and M14 trajectory rebinding.
3. **Gateway Recovery Ingress (`execute_recovery_reorientation`)**:
   - Validates `request.plan_id` on STAGE and `request.plan_trajectory` on ACTIVATE before minting capability.

---

## I. Capability / Session Coherence

1. **Capability Model**:
   `_ExecutionCapability` in `python/holomed/execution/_capability.py` encapsulates:
   - `service_instance_id: int` (`id(target_service)`)
   - `session_id: str`
   - `action: str` (`REGISTRATION_ALIGNMENT`, `TRAJECTORY_ALIGNMENT`, `TOOL_NAVIGATION`, `RECOVERY_REORIENTATION`, etc.)
   - `sequence_number: int`
   - `transaction_id: str`
   - `is_active: bool`
2. **Session Coherence Invariant**:
   `capability.session_id == request.session_id` is strictly enforced at ingress of every consuming service. Mismatch raises `*AuthorizationError`.
3. **No Invented Fields**:
   `_ExecutionCapability` does NOT encode `plan_id` or `trajectory_id`. Capability validates caller authority and coordinator transaction context; resource ownership is validated against authoritative service state in `PlanningService`.

---

## J. Zero-Mutation Ordering

For every clinical operation, validation MUST occur strictly before state mutation:

### Registration Operation:
$$\text{Session Ownership Validation} \longrightarrow \text{Plan Locked Validation} \longrightarrow \text{Capability Validation} \longrightarrow \text{Only Then Fiducial/Registration Mutation}$$

### Trajectory Binding Operation:
$$\text{Session Ownership Validation} \longrightarrow \text{Locked-Plan Lookup} \longrightarrow \text{Trajectory Membership} \longrightarrow \text{Trajectory Integrity Assertion} \longrightarrow \text{Capability Coherence} \longrightarrow \text{Only Then Navigation Mutation}$$

### Recovery Reorientation Operation:
$$\text{Session Ownership Validation} \longrightarrow \text{Locked-Plan Lookup} \longrightarrow \text{Candidate Plan/Trajectory Validation} \longrightarrow \text{Capability Coherence} \longrightarrow \text{Only Then Staging/Activation Mutation}$$

If the existing implementation order differs, the implementation must reorder validation to execute first rather than merely adding a later check.

---

## K. Error Semantics

| Subsystem | Condition | Exact Exception / Error Code | HTTP / Protocol Result |
|---|---|---|---|
| `RegistrationService` | `PlanningService` is None in production | `RegistrationLifecycleError` | `ERR_REGISTRATIONLIFECYCLE` |
| `RegistrationService` | Plan does not exist or not locked | `RegistrationPlanMismatchError` | `ERR_REGISTRATIONPLANMISMATCH` |
| `RegistrationService` | Plan not bound to session | `RegistrationPlanMismatchError` | `ERR_REGISTRATIONPLANMISMATCH` |
| `ClinicalExecutionGatewayService` | `PlanningService` is None in registration | `ExecutionLifecycleError` | `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` |
| `ClinicalExecutionGatewayService` | Plan mismatch in registration | `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` | Result error message + audit log |
| `ClinicalExecutionGatewayService` | Trajectory not in plan | `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` | Result error message + audit log |
| `ClinicalExecutionGatewayService` | Trajectory geometry deviation $> \text{tolerance}$ | `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` | Result error message + audit log |
| `NavigationService` | Capability mismatch | `NavigationAuthorizationError` | `ERR_NAVIGATIONAUTHORIZATION` |
| `NavigationService` | Unverified registration | `NavigationRegistrationMismatchError` | `ERR_NAVIGATIONREGISTRATIONMISMATCH` |
| `RecoveryService` | `PlanningService` is None | `RecoveryLifecycleError` | `ERR_RECOVERYLIFECYCLE` |
| `RecoveryService` | Plan mismatch on `stage_candidate` | `RecoveryPlanMismatchError` | `ERR_RECOVERYPLANMISMATCH` |
| `RecoveryService` | Plan/trajectory mismatch on `activate_recovery` | `RecoveryActivationError` / `RecoveryPlanMismatchError` | `ERR_RECOVERYACTIVATION` |

---

## L. Backward Compatibility Rules

1. **Preservation of Frozen Milestones**:
   - M33 (`holomed.workflow`): 100% frozen.
   - M34 (`holomed.gateway`): 100% frozen.
   - M35 (`holomed.persistence`): 100% frozen.
2. **Standalone Test Isolation**:
   - Existing unit tests for isolated subsystems (e.g. `tests/unit/registration/test_registration_service.py`) that test Horn math without platform wiring must supply a mock `PlanningService` fixture to the service constructor.
   - Production code maintains zero fail-open branches.

---

## M. Hostile Attack Matrix

| # | Attack Vector | Attacker Payload / Action | Expected Fail-Closed Defense | Enforcement Point | Mutation Check |
|---|---|---|---|---|---|
| **1** | Cross-Session Plan Hijacking in Registration | Session B calls `submit_fiducials` with Session A's `plan_id`. | Rejects with `RegistrationPlanMismatchError`. | `RegistrationService._verify_locked_plan` | `_fiducial_clouds` and `_registrations` empty. |
| **2** | Cross-Session Plan Solve in Registration | Session B calls `solve_registration` with Session A's `plan_id`. | Rejects with `RegistrationPlanMismatchError`. | `RegistrationService._verify_locked_plan` | No transform computed or stored. |
| **3** | Unbound Session Registration Attempt | Session B (no plan in `PlanningService`) calls `submit_fiducials`. | Rejects with `RegistrationPlanMismatchError`. | `RegistrationService._verify_locked_plan` | Zero mutation. |
| **4** | Missing PlanningService in Production Gateway | Gateway invoked with `_planning_service = None`. | Rejects with `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY`. | Gateway `execute_registration` | Zero mutation. |
| **5** | Unlocked Plan Registration Attempt | Caller references plan where `is_locked = False`. | Rejects with `RegistrationPlanMismatchError`. | Gateway & `RegistrationService` | Zero mutation. |
| **6** | Foreign Trajectory ID Binding | Session B requests binding with Session A's `trajectory_id`. | Rejects: trajectory not found in Session B plan. | Gateway `execute_trajectory_binding` | `_bound_trajectories` unchanged. |
| **7** | Tampered Entry Point Coordinate | Caller alters `entry_point_mm` by $10^{-4}$ mm ($> 10^{-6}$ mm). | Rejects: geometry mismatch. | Gateway `execute_trajectory_binding` | `_bound_trajectories` unchanged. |
| **8** | Tampered Target Point Coordinate | Caller alters `target_point_mm` by $0.05$ mm ($> 10^{-6}$ mm). | Rejects: geometry mismatch. | Gateway `execute_trajectory_binding` | `_bound_trajectories` unchanged. |
| **9** | Tampered Lateral Deviation | Caller alters `max_lateral_deviation_mm` by $0.1$ mm ($> 10^{-6}$ mm). | Rejects: geometry mismatch. | Gateway `execute_trajectory_binding` | `_bound_trajectories` unchanged. |
| **10** | Tampered Angular Deviation | Caller alters `max_angular_deviation_deg` by $0.01$ deg ($> 10^{-4}$ deg). | Rejects: geometry mismatch. | Gateway `execute_trajectory_binding` | `_bound_trajectories` unchanged. |
| **11** | Tampered Confidence / Uncertainty | Caller alters `min_confidence` by $0.05$ ($> 10^{-6}$). | Rejects: confidence mismatch. | Gateway `execute_trajectory_binding` | `_bound_trajectories` unchanged. |
| **12** | Staged Foreign Recovery Candidate | Caller invokes `stage_candidate` with foreign `plan_id`. | Rejects with `RecoveryPlanMismatchError`. | `RecoveryService.stage_candidate` (Point 1) | `_staged_candidates` empty. |
| **13** | Foreign Plan Recovery Activation | Staged candidate's plan unseated before `activate_recovery`. | Re-validation fails; activation rejected. | `RecoveryService.activate_recovery` (Point 2) | M13/M14 untouched. |
| **14** | Tampered Trajectory in Recovery Activation | Caller passes tampered `plan_trajectory` to `activate_recovery`. | Rejects: geometry mismatch against locked plan. | `RecoveryService.activate_recovery` (Point 2) | M14 navigation untouched. |
| **15** | Session-Capability Mismatch | Session B request presented with Session A capability. | Rejects with `*AuthorizationError`. | Consuming service capability check | Zero mutation. |
| **16** | Replayed / Expired Capability | Caller replays invalidated capability. | Rejects with `*AuthorizationError` (`is_active` is False). | Consuming service capability check | Zero mutation. |
| **17** | Whitespace / Malformed Session or Plan ID | Caller passes whitespace or malformed ID string. | Fails closed with `ExecutionValidationError`. | Gateway ingress validation | Zero mutation. |
