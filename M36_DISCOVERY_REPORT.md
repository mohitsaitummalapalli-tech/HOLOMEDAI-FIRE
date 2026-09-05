# M36 Discovery Report: Cross-Session Surgical Plan Ownership & Trajectory Integrity Disconnect

**Status**: Discovery & Architecture Pass Complete — Normative Contract Fully Corrected
**Milestone**: M36
**Subsystems**: `holomed.planning`, `holomed.registration`, `holomed.navigation`, `holomed.execution`, `holomed.recovery`
**Classification**: `M36_JUSTIFIED`

---

## 1. Git State & Working Tree Audit

A forensic inspection of the Git working tree confirms:

```
$ git status --short
 M python/holomed/gateway/service.py
 M python/holomed/planning/service.py
 M python/holomed/workflow/checkpoints.py
 M python/holomed/workflow/service.py
 M tests/unit/gateway/test_gateway_adversarial_matrix.py
 M tests/unit/workflow/test_workflow_checkpoints.py
?? M33_CONTRACT_SPEC.md
?? M33_DISCOVERY_REPORT.md
?? M33_HOSTILE_AUDIT_REPORT.md
?? M33_IMPLEMENTATION_REPORT.md
?? M34_CONTRACT_SPEC.md
?? M34_HOSTILE_AUDIT_REPORT.md
?? M36_CONTRACT_SPEC.md
?? M36_DISCOVERY_REPORT.md
?? tests/unit/gateway/test_m34_gateway_lifecycle.py
?? tests/unit/workflow/test_m33_checkpoint_lifecycle.py
```

### Distinction of Repository States:
1. **M35 Committed State**:
   - Commit `119d342804fd29e521cfe8680f7faae51cc89a66` (`security: add durable persistence session eviction`).
   - All M35 production code, tests (16/16), contract spec, and audit report are committed, passed, and officially **FROZEN**.
2. **Pre-Existing Frozen M33 / M34 Uncommitted State**:
   - **M33**: `python/holomed/workflow/checkpoints.py`, `python/holomed/workflow/service.py`, `python/holomed/planning/service.py`, `tests/unit/workflow/test_workflow_checkpoints.py`, `tests/unit/workflow/test_m33_checkpoint_lifecycle.py`, and M33 documentation. (19/19 tests passing; frozen).
   - **M34**: `python/holomed/gateway/service.py`, `tests/unit/gateway/test_gateway_adversarial_matrix.py`, `tests/unit/gateway/test_m34_gateway_lifecycle.py`, and M34 documentation. (13/13 tests passing; frozen).
3. **No Unwanted Debris**:
   - Zero unexpected files or untracked debris. No production or test code has been modified for M36.

---

## 2. Process Note: `implementation_plan.md`

Per the HoloMed milestone process:
- Discovery produces `M<N>_DISCOVERY_REPORT.md`.
- Contract correction and normative specification produces `M<N>_CONTRACT_SPEC.md`.
- `implementation_plan.md` is created ONLY AFTER user approval of the contract specification.
- Therefore, `implementation_plan.md` is **not** created during this discovery and contract correction pass.

---

## 3. Forensic Investigation of the Complete Ownership Chain

We trace every object and transition through:
$$\text{request.session\_id} \longrightarrow \text{authoritative bound plan} \longrightarrow \text{locked plan definition} \longrightarrow \text{trajectory membership} \longrightarrow \text{heterogeneous geometric integrity} \longrightarrow \text{navigation execution} \longrightarrow \text{recovery transitions}$$

### 3.1 Authoritative Plan Ownership Source
1. **Authoritative Storage**:
   Authoritative session ownership of a surgical plan is stored exclusively in `PlanningService._session_plan_bindings: Dict[str, str]` (`session_id -> plan_id`). The plan definition is stored in `PlanningService._plans: Dict[str, SurgicalPlanDefinition]` (`plan_id -> SurgicalPlanDefinition`).
2. **Chain of Trust Verification**:
   Callers MUST NOT be able to establish ownership merely by supplying a matching-looking `plan_id`. The verification sequence is:
   $$\text{request.session\_id} \longrightarrow \text{authoritative bound plan\_id} \longrightarrow \text{plan lookup in \_plans} \longrightarrow \text{plan.is\_locked verification}$$
   If any link is missing or inconsistent, execution fails closed immediately.
3. **Dependency Topology & Production Fail-Closed Behavior**:
   - In production topology, `PlanningService` is an essential subsystem instantiated at platform initialization and wired into `ClinicalExecutionGatewayService`, `RegistrationService`, and `RecoveryService`.
   - The condition `planning_service=None` is NOT a valid production topology; it represents exclusively a legacy isolated unit-test fixture.
   - **Production Rule**: In production execution paths, absence of the authoritative `PlanningService` handle MUST NOT disable or bypass ownership validation. If `PlanningService` is unavailable when required, execution MUST FAIL CLOSED with the repository's existing lifecycle exception (`ExecutionLifecycleError` in Gateway, `RegistrationLifecycleError` in Registration, `RecoveryLifecycleError` in Recovery).
   - **Test Isolation Compatibility**: Standalone unit tests testing isolated algorithms (e.g. Horn solver in `test_registration_service.py`) must provide an explicit mock or test-double `PlanningService` wired to the test session. Production code MUST NEVER contain fail-open bypass branches (`if planning_service is not None: ... else: allow`).

### 3.2 Registration Ownership & Zero-Mutation Ordering
1. **Retained State**:
   - `self._registrations: Dict[str, RegistrationStatusRecord]`: Keyed by `session_id`.
   - `self._fiducial_clouds: Dict[str, FiducialCloud]`: Keyed by `session_id`.
2. **Strict Zero-Mutation Call Order**:
   In `submit_fiducials` and `solve_registration`, the order of execution MUST be restructured as:
   $$\text{Ownership Validation} \longrightarrow \text{Plan Locked Validation} \longrightarrow \text{Capability Validation} \longrightarrow \text{State Mutation}$$
   State dictionaries (`_fiducial_clouds`, `_registrations`) are untouched if any validation fails.

### 3.3 Trajectory Ownership & Heterogeneous Tolerance Semantics
1. **Authoritative Trajectory Source**:
   Trajectories are owned exclusively by `SurgicalPlanDefinition.trajectories: Tuple[TrajectoryPlan, ...]`. Trajectory ownership is strictly plan-scoped and session-bound.
2. **Authoritative Trajectory Rule**:
   - Step 1: Resolve the currently locked plan for `request.session_id`.
   - Step 2: Resolve `request.trajectory_id` ONLY within that plan.
   - Step 3: The resolved trajectory from the locked plan is authoritative.
   - Step 4: A caller-supplied `request.plan_trajectory` is NEVER the source of truth; it is treated strictly as an assertion.
   - Step 5: Compare the caller-supplied assertion against the authoritative trajectory using field-specific tolerances.
   - Step 6: On mismatch, reject before any state mutation.
   - Step 7: Pass and use the authoritative trajectory object downstream.
3. **Heterogeneous Tolerance Contract**:
   Do not use one universal scalar across heterogeneous physical and normalized units. Tolerances must be distinguished by physical dimension:
   - **Point coordinates (`entry_point_mm`, `target_point_mm`)**: Cartesian spatial coordinates in millimeters. Compared with absolute tolerance `TRAJECTORY_POINT_TOLERANCE_MM: float = 1e-6` mm (conforms to `COORDINATE_EPSILON = 1e-6` in `anatomy.models` and `gesture.models`).
   - **Lateral deviation (`max_lateral_deviation_mm`)**: Clearance bound in millimeters. Compared with absolute tolerance `TRAJECTORY_LATERAL_TOLERANCE_MM: float = 1e-6` mm.
   - **Angular deviation (`max_angular_deviation_deg`)**: Shaft angle bound in degrees. Compared with absolute tolerance `TRAJECTORY_ANGULAR_TOLERANCE_DEG: float = 1e-4` deg (~0.36 arcseconds, well below optical tracking noise).
   - **Confidence (`min_confidence`)**: Normalized score in $[0.0, 1.0]$. Compared with absolute tolerance `TRAJECTORY_CONFIDENCE_TOLERANCE: float = 1e-6` (conforms to `NORMALIZED_QUANTIZATION = 1e-6` in `vision.models`).
   - **Uncertainty (`max_uncertainty`)**: Normalized score in $[0.0, 1.0]$. Compared with absolute tolerance `TRAJECTORY_UNCERTAINTY_TOLERANCE: float = 1e-6`.
   - **Identifiers (`trajectory_id`, `target_structure`)**: Exact string equality (`==`).
   Canonical constants live in `python/holomed/planning/models.py` and are imported across services to prevent scattered literals.

### 3.4 Navigation Ownership & Execution
1. **Binding Flow**:
   Gateway validates session plan ownership, trajectory membership, and trajectory integrity assertions BEFORE capability minting and BEFORE calling `NavigationService.bind_trajectory()`.
2. **Downstream Object**:
   Gateway passes the authoritative plan trajectory from `P_S.trajectories` into `NavigationService.bind_trajectory()`.
3. **Execution in Navigation**:
   `NavigationService.bind_trajectory()` transforms the authoritative trajectory into patient tracker frame and mutates `_bound_trajectories[session_id]` only after registration verification and capability checks pass.

### 3.5 Recovery Ownership & Dual Enforcement Points
1. **Dual Enforcement Architecture**:
   - **Enforcement Point 1 (`stage_candidate`)**: Validates `plan_id` against `_planning_service.get_plan_for_session(session_id)` BEFORE calling the Horn solver and BEFORE mutating `_staged_candidates[session_id]`.
   - **Enforcement Point 2 (`activate_recovery`)**: Re-verifies that `candidate.plan_id` STILL matches the authoritative locked plan for `session_id`, and verifies that `plan_trajectory` (if provided) is a member of the locked plan with valid canonical geometry before modifying M13 Registration or M14 Navigation.
2. **Zero Mutation in Recovery**:
   Capacity checks and Horn solving occur only after plan ownership is confirmed. Latching to `FAILED`/`BLOCKED` on unauthorized plan injection is eliminated; foreign plan injection fails closed with zero mutation.

### 3.6 Capability Consistency
1. **Model**: `_ExecutionCapability` in `python/holomed/execution/_capability.py`.
2. **Bound Fields**: `service_instance_id`, `session_id`, `action`, `sequence_number`, `transaction_id`, `is_active`.
3. **Session Coherence**: `capability.session_id == request.session_id` is strictly enforced at ingress of every consuming service.
4. **No Invented Fields**: `_ExecutionCapability` does NOT encode resource IDs (`plan_id`, `trajectory_id`). Resource ownership is validated against authoritative service state in `PlanningService`.

---

## 4. Exact M36 Objective

**Objective**:
**Enforce Fail-Closed Cross-Session Surgical Plan Ownership and Trajectory Geometry Verification Across Registration, Navigation, Recovery, and Clinical Execution.**

---

## 5. Primary Defect & Clinical Safety Threat

Cross-patient plan contamination (registering Patient B's anatomy to Patient A's plan, or navigating Patient B's instruments along Patient A's trajectory or arbitrary unverified geometry) constitutes an IEC 62304 / ISO 14971 Class C critical safety hazard, risking catastrophic neurological or vascular damage.

---

## 6. Affected Production Files, Functions, and Classes

| File | Class | Function / Method | Required Remediation |
|---|---|---|---|
| `python/holomed/planning/models.py` | N/A | Canonical Constants | Define `TRAJECTORY_POINT_TOLERANCE_MM`, `TRAJECTORY_LATERAL_TOLERANCE_MM`, `TRAJECTORY_ANGULAR_TOLERANCE_DEG`, `TRAJECTORY_CONFIDENCE_TOLERANCE`, `TRAJECTORY_UNCERTAINTY_TOLERANCE`. |
| `python/holomed/registration/service.py` | `RegistrationService` | `_verify_locked_plan(plan_id, session_id)` | Validate session ownership against `_planning_service.get_plan_for_session()`; fail closed if `_planning_service` is None. |
| `python/holomed/registration/service.py` | `RegistrationService` | `submit_fiducials(...)`, `solve_registration(...)` | Restructure call order to validate plan ownership before capacity checks or state mutation. |
| `python/holomed/execution/service.py` | `ClinicalExecutionGatewayService` | `execute_registration(...)` | Validate `request.plan_id` matches session's bound plan before capability minting; fail closed if `_planning_service` is None. |
| `python/holomed/execution/service.py` | `ClinicalExecutionGatewayService` | `execute_trajectory_binding(...)` | Resolve locked plan, resolve trajectory within plan, verify caller assertion against authoritative trajectory, and pass authoritative trajectory downstream. Fail closed if `_planning_service` is None. |
| `python/holomed/navigation/service.py` | `NavigationService` | `bind_trajectory(...)` | Verify registration plan coherence before transforming authoritative trajectory. |
| `python/holomed/recovery/service.py` | `RecoveryService` | `stage_candidate(...)` | Enforcement Point 1: Validate `plan_id` against session's bound locked plan before Horn solving or candidate staging. |
| `python/holomed/recovery/service.py` | `RecoveryService` | `activate_recovery(...)` | Enforcement Point 2: Re-validate candidate plan ownership and trajectory geometry before applying re-registration or rebinding navigation. |

---

## 7. Canonical M36 Invariant

> **The Canonical HoloMed M36 Clinical Ownership & Trajectory Integrity Invariant:**
> *"Any clinical execution, spatial registration, trajectory binding, navigation evaluation, or recovery reorientation operation targeting a clinical session $S$ MUST consume ONLY the authoritative surgical plan $P$ where $\text{PlanningService.\_session\_plan\_bindings}[S] == P.\text{plan\_id}$ and $P.\text{is\_locked} = \text{True}$. All downstream registration records ($\text{RegistrationStatusRecord.plan\_id}$), trajectory identifiers, and trajectory geometries ($\text{TrajectoryPlan}$) MUST be strictly derived from or verified against $P.\text{trajectories}$; caller-supplied identifiers or geometries that conflict with or diverge from $P$ MUST be rejected fail-closed with zero state mutation. In production execution paths, absence of the authoritative ownership source MUST fail closed and MUST NOT bypass ownership validation."*

---

## 8. Comprehensive Hostile Threat Matrix

| Threat Scenario | Current Behavior | Expected Fail-Closed Behavior | Exact Error / Exception | Enforcement Layer | State Mutation |
|---|---|---|---|---|---|
| **1. Cross-Session Plan Hijacking in Registration** | Solves registration against foreign plan. | Deterministically rejects registration; no fiducials or transform stored. | `RegistrationPlanMismatchError` / `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` | `RegistrationService` & Gateway `execute_registration` | Zero mutation. |
| **2. Foreign Trajectory ID Binding** | Binds foreign trajectory if ID matches caller. | Rejects trajectory binding; trajectory not found in session plan. | `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` | Gateway `execute_trajectory_binding` | Zero mutation. |
| **3. Tampered Entry Point Coordinate** | Binds caller coordinates without checking plan. | Rejects: $|entry_{req} - entry_{auth}| > 10^{-6}$ mm. | `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` | Gateway `execute_trajectory_binding` | Zero mutation. |
| **4. Tampered Target Point Coordinate** | Binds caller coordinates without checking plan. | Rejects: $|target_{req} - target_{auth}| > 10^{-6}$ mm. | `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` | Gateway `execute_trajectory_binding` | Zero mutation. |
| **5. Tampered Lateral Deviation** | Binds caller coordinates without checking plan. | Rejects: $|lat_{req} - lat_{auth}| > 10^{-6}$ mm. | `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` | Gateway `execute_trajectory_binding` | Zero mutation. |
| **6. Tampered Angular Deviation** | Binds caller coordinates without checking plan. | Rejects: $|ang_{req} - ang_{auth}| > 10^{-4}$ deg. | `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` | Gateway `execute_trajectory_binding` | Zero mutation. |
| **7. Tampered Confidence / Uncertainty** | Binds caller coordinates without checking plan. | Rejects: deviation $> 10^{-6}$ domain units. | `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` | Gateway `execute_trajectory_binding` | Zero mutation. |
| **8. PlanningService Unavailable in Production** | Historically bypassed validation. | Fails closed immediately. | `ExecutionLifecycleError` / `RegistrationLifecycleError` / `RecoveryLifecycleError` | Gateway, Registration, Recovery Ingress | Zero mutation. |
| **9. Foreign Plan in Recovery Staging** | Stages candidate with foreign plan. | Rejects staging candidate (Point 1). | `RecoveryPlanMismatchError` | `RecoveryService.stage_candidate` | Zero mutation. |
| **10. Foreign Plan in Recovery Activation** | Activates unseated foreign plan into M13. | Re-validation fails; activation rejected (Point 2). | `RecoveryActivationError` / `RecoveryPlanMismatchError` | `RecoveryService.activate_recovery` | Zero mutation. |
| **11. Tampered Trajectory in Recovery Activation** | Rebinds M14 with caller trajectory. | Rejects: geometry mismatch against locked plan (Point 2). | `RecoveryActivationError` / `RecoveryPlanMismatchError` | `RecoveryService.activate_recovery` | Zero mutation. |
| **12. Capability-Session Mismatch** | Mismatched capability presentation. | Rejects with `*AuthorizationError`. | Consuming service capability check | Target service ingress | Zero mutation. |

---

## 9. Backward-Compatibility Strategy

1. **Frozen Milestones**: M33 (`holomed.workflow`), M34 (`holomed.gateway`), and M35 (`holomed.persistence`) remain 100% frozen.
2. **Isolated Unit Test Harnesses**: Standalone tests (e.g. testing Horn math in `test_registration_service.py`) supply a mock `PlanningService` test double wired to the test session. Production code maintains zero fail-open branches.

---

## 10. Final Classification

```
M36_JUSTIFIED
```
