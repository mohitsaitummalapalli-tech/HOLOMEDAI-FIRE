# M36 Hostile Audit Report: Cross-Session Surgical Plan Ownership & Trajectory Integrity Verification

**Status**: Verified & Audited
**Milestone**: M36
**Subsystems**: `holomed.planning`, `holomed.registration`, `holomed.navigation`, `holomed.recovery`, `holomed.execution`
**Verdict**: **M36_HOSTILE_AUDIT_PASS**

---

## 1. Exact Original Defect

Prior to M36, clinical workflows across `RegistrationService`, `NavigationService`, `RecoveryService`, and `ClinicalExecutionGatewayService` accepted caller-supplied `plan_id` and trajectory parameter dictionaries without enforcing cryptographic/authoritative cross-session binding:
1. **Unchecked Caller-Supplied Plan IDs**: Callers could provide an arbitrary `plan_id`. If `PlanningService.get_plan(plan_id)` succeeded, the subsystem accepted it even if the plan belonged to an entirely different clinical session (`plan.session_id != request.session_id`) or was still in an editable/unlocked state (`plan.is_locked == False`).
2. **Oracle Probing Vulnerability**: Subsystems checked caller-supplied `plan_id` first before evaluating session context, allowing hostile actors to probe for the existence of confidential patient surgical plans across sessions.
3. **Trajectory Geometry Tampering**: Navigation trajectory binding (`bind_trajectory`) accepted caller-supplied trajectory dictionaries or modified trajectory attributes without verifying geometric and mathematical integrity against the locked, authoritative trajectory defined within the plan.
4. **Subsystem Desynchronization**: Individual clinical subsystems could be instantiated with differing `PlanningService` references or `None`, allowing unverified bypasses of clinical safety checks.
5. **Incomplete Recovery Checkpoints**: Recovery procedures could stage candidates or execute reorientations without proving that the active surgical plan and trajectory remained locked and unmutated between initial staging and actual execution.

---

## 2. Threat Model & Security Posture

### Threats Addressed
1. **Cross-Session Surgical Plan Confusion / Usurpation**:
   - *Threat*: A rogue or concurrent session `SESSION-B` references `plan_id_A` belonging to `SESSION-A`. The patient in session B would be treated using anatomical landmarks and trajectory geometry designed for patient A.
   - *Defense*: Canonical ownership verification: `request.session_id` → `PlanningService._session_plan_bindings[session_id]` → authoritative `plan_id` → `plan lookup` → `plan.is_locked`. Any caller-supplied `plan_id` that differs from the authoritative bound plan is rejected immediately.
2. **Side-Channel Oracle Probing**:
   - *Threat*: An attacker supplies random or sequential `plan_id` strings to discover valid plan IDs. If the service checked `get_plan(candidate_id)` first, error codes would reveal whether a plan exists in the hospital database.
   - *Defense*: Anti-oracle lookup order: Subsystems query `get_plan_for_session(session_id)` *first*. If no plan is bound to the requesting session, rejection occurs immediately with a generic plan mismatch exception, never touching or confirming caller-supplied IDs.
3. **Trajectory Geometric Tampering & Parameter Manipulation**:
   - *Threat*: A malicious or malfunctioning caller binds a trajectory with altered entry points, target coordinates, angular tolerances, or target margins, potentially directing surgical instruments into critical neurovascular structures.
   - *Defense*: Pure mathematical integrity validation `validate_trajectory_integrity(candidate, authoritative)`. Evaluates 5 separate physical dimensions with unit-specific tolerances. In addition, `NavigationService` binds the *authoritative plan trajectory instance* rather than caller copies.
4. **Recovery Time-of-Check to Time-of-Use (TOCTOU) Exploits**:
   - *Threat*: A surgical plan is valid when a recovery maneuver is staged, but is subsequently unlocked, modified, or rebound before `activate_recovery` executes.
   - *Defense*: Dual-checkpoint verification: Checkpoint 1 at `stage_candidate()`, Checkpoint 2 at `activate_recovery()`. Re-verifies session lock and trajectory integrity against the authoritative plan prior to arming robotic motion.
5. **Gateway Ingress Bypass**:
   - *Threat*: Ingress calls bypass internal service boundaries by injecting unverified parameters into gateway execution endpoints.
   - *Defense*: `ClinicalExecutionGatewayService` enforces plan ownership and locked status at ingress before granting execution capabilities or delegating to child subsystems.

---

## 3. Unit-Specific Tolerance Invariants

In compliance with the M36 specification, heterogeneous physical quantities are never evaluated with a single scalar tolerance. Trajectory comparison strictly enforces 5 distinct physical dimensions:

| Dimension | Physical Unit | Canonical Constant | Maximum Allowed Deviation |
| :--- | :--- | :--- | :--- |
| **Point Coordinates (Target & Entry)** | Millimeters (mm) | `TRAJECTORY_POINT_TOLERANCE_MM` | $10^{-6}\text{ mm}$ ($1\text{ nm}$) |
| **Lateral Tolerance Deviation** | Millimeters (mm) | `TRAJECTORY_LATERAL_TOLERANCE_MM` | $10^{-6}\text{ mm}$ ($1\text{ nm}$) |
| **Angular Deviation (Pitch & Yaw)** | Degrees (deg) | `TRAJECTORY_ANGULAR_TOLERANCE_DEG` | $10^{-4\circ}$ ($\approx 0.36\text{ arcsec}$) |
| **Confidence Level** | Normalized $[0, 1]$ | `TRAJECTORY_CONFIDENCE_TOLERANCE` | $10^{-6}$ |
| **Uncertainty Margin** | Standard deviations / mm | `TRAJECTORY_UNCERTAINTY_TOLERANCE` | $10^{-6}$ |

Any deviation exceeding these bounds, or any mismatch in trajectory identifiers or structure names, raises `PlanningTrajectoryIntegrityError` without mutating system state.

---

## 4. Implementation Changes

### A. `python/holomed/planning/models.py` & `python/holomed/planning/__init__.py`
- Defined unit-specific tolerance constants:
  - `TRAJECTORY_POINT_TOLERANCE_MM = 1e-6`
  - `TRAJECTORY_LATERAL_TOLERANCE_MM = 1e-6`
  - `TRAJECTORY_ANGULAR_TOLERANCE_DEG = 1e-4`
  - `TRAJECTORY_CONFIDENCE_TOLERANCE = 1e-6`
  - `TRAJECTORY_UNCERTAINTY_TOLERANCE = 1e-6`
- Implemented `validate_trajectory_integrity(candidate, authoritative) -> bool`:
  - Pure, side-effect free mathematical integrity check.
  - Compares `trajectory_id`, `structure_name`, entry point coordinates $(x, y, z)$, target point coordinates $(x, y, z)$, pitch and yaw angles, lateral tolerance, angular tolerance, confidence, and uncertainty.
  - Raises `PlanningTrajectoryIntegrityError` on discrepancy.

### B. `python/holomed/recovery/exceptions.py` & `python/holomed/recovery/__init__.py`
- Added and exported `RecoveryPlanMismatchError(RecoveryError)`.

### C. `python/holomed/registration/service.py`
- Implemented anti-oracle `_verify_locked_plan(plan_id: str, session_id: str) -> SurgicalPlan`:
  - Fails closed if `_planning_service` is `None` with `RegistrationLifecycleError`.
  - Queries `_planning_service.get_plan_for_session(session_id)` *first*.
  - If no plan is bound, raises `RegistrationPlanMismatchError` without evaluating caller-supplied `plan_id`.
  - Verifies `bound_plan.plan_id == plan_id`.
  - Verifies `bound_plan.is_locked is True`.
  - Enforced before state mutation in `submit_fiducials` and `solve_registration`.

### D. `python/holomed/navigation/service.py`
- Added `planning_service` dependency injection.
- Added `_verify_locked_plan(plan_id: str, session_id: str) -> SurgicalPlan`:
  - Fails closed if `_planning_service` is `None` with `NavigationLifecycleError`.
  - Verifies session-bound plan identity and locked status.
- In `bind_trajectory(session_id, plan_id, trajectory, capability)`:
  - Validates registration-plan coherence: `_active_registration.plan_id == plan.plan_id`.
  - Validates trajectory membership in `plan.trajectories`.
  - Executes `validate_trajectory_integrity(candidate=trajectory, authoritative=auth_traj)`.
  - Binds authoritative trajectory instance `auth_traj` to `_active_trajectory`.
  - On failure, leaves `_active_trajectory` untouched.

### E. `python/holomed/recovery/service.py`
- Added `_verify_locked_plan(plan_id: str, session_id: str) -> SurgicalPlan`:
  - Fails closed if `_planning_service` is `None` with `RecoveryLifecycleError`.
  - Verifies session binding and locked status.
- Enforced Dual-Checkpoint Verification:
  - **Checkpoint 1 (`stage_candidate`)**: Validates session-bound plan is locked. On failure, preserves candidate state.
  - **Checkpoint 2 (`activate_recovery`)**: Re-verifies session-bound plan is still locked. If an active trajectory is bound or candidate asserts a trajectory, validates trajectory existence and integrity against the authoritative plan.

### F. `python/holomed/execution/service.py`
- Added `_safe_get_planning_service(srv)` helper to avoid autovivification in mock-based testing environments.
- In `initialize()`: Enforced `PlanningService` instance identity check across `registration_service`, `navigation_service`, and `recovery_service`.
- Ingress Checks: Added locked session-bound plan validation at gateway ingress in `execute_registration`, `execute_trajectory_binding`, and `execute_recovery_reorientation`.
- Teardown: Gateway teardown unbinds session plans in `PlanningService`, ensuring clean session lifecycle isolation.

---

## 5. Hostile Audit Matrix (26 Attack Vectors)

All 26 hostile attack vectors were executed via `tests/unit/execution/test_m36_plan_trajectory_ownership.py`.

| Vector | Category | Attack Scenario | Expected Rejection | Result |
| :---: | :--- | :--- | :--- | :---: |
| **01** | Registration Ownership | Hostile caller submits fiducials using plan belonging to foreign session | `RegistrationPlanMismatchError` | **PASS** |
| **02** | Registration Ownership | Caller attempts registration with an unlocked/draft plan | `RegistrationPlanMismatchError` | **PASS** |
| **03** | Registration Fail-Closed | Registration attempted when `PlanningService` is unconfigured/missing | `RegistrationLifecycleError` | **PASS** |
| **04** | Anti-Oracle Verification | Hostile caller probes secret plan ID when session has no plan bound | `RegistrationPlanMismatchError` (probe suppressed) | **PASS** |
| **05** | Zero Mutation Guarantee | Registration plan mismatch leaves existing fiducial points untouched | `len(points) == 0` | **PASS** |
| **06** | Zero Mutation Guarantee | `solve_registration` with plan mismatch leaves transform `None` | `transform is None` | **PASS** |
| **07** | Navigation Ownership | Trajectory binding attempted with foreign session plan | `NavigationRegistrationMismatchError` | **PASS** |
| **08** | Navigation Ownership | Trajectory binding attempted with unlocked plan | `NavigationRegistrationMismatchError` | **PASS** |
| **09** | Navigation Coherence | Trajectory binding with plan differing from active registration | `NavigationRegistrationMismatchError` | **PASS** |
| **10** | Trajectory Membership | Trajectory binding with trajectory ID not present in locked plan | `NavigationRegistrationMismatchError` | **PASS** |
| **11** | Geometry Tampering | Trajectory target coordinates $(x, y, z)$ modified by $1.1\times 10^{-6}\text{ mm}$ | `PlanningTrajectoryIntegrityError` | **PASS** |
| **12** | Geometry Tampering | Trajectory entry coordinates $(x, y, z)$ modified by $1.1\times 10^{-6}\text{ mm}$ | `PlanningTrajectoryIntegrityError` | **PASS** |
| **13** | Geometry Tampering | Trajectory pitch angle modified by $1.1\times 10^{-4\circ}$ | `PlanningTrajectoryIntegrityError` | **PASS** |
| **14** | Geometry Tampering | Trajectory lateral tolerance modified by $1.1\times 10^{-6}\text{ mm}$ | `PlanningTrajectoryIntegrityError` | **PASS** |
| **15** | Instance Immutability | Service binds authoritative plan trajectory, discarding caller copy | `active is auth_traj` | **PASS** |
| **16** | Zero Mutation Guarantee | Navigation failure leaves active trajectory strictly `None` | `active_trajectory is None` | **PASS** |
| **17** | Recovery Checkpoint 1 | Recovery candidate staging attempted with foreign session plan | `RecoveryPlanMismatchError` | **PASS** |
| **18** | Recovery Checkpoint 1 | Recovery candidate staging attempted with unlocked plan | `RecoveryPlanMismatchError` | **PASS** |
| **19** | Zero Mutation Guarantee | Recovery stage failure leaves candidate slot strictly `None` | `candidate is None` | **PASS** |
| **20** | Recovery Checkpoint 2 | Plan unlocked or unbound between candidate stage and activation | `RecoveryPlanMismatchError` | **PASS** |
| **21** | Recovery Trajectory Audit| Recovery activation validates trajectory assertion against plan | `RecoveryPlanMismatchError` | **PASS** |
| **22** | Gateway Ingress | Gateway registration ingress rejects foreign/unlocked plan | `FAILED_NAVIGATION_GEOMETRY` | **PASS** |
| **23** | Gateway Ingress | Gateway trajectory binding ingress rejects foreign/unlocked plan | `FAILED_NAVIGATION_GEOMETRY` | **PASS** |
| **24** | Gateway Ingress | Gateway recovery reorientation ingress rejects foreign/unlocked plan | `FAILED_NAVIGATION_GEOMETRY` | **PASS** |
| **25** | Subsystem Wiring | Gateway initialization fails closed if child services share differing planning service | `GatewayLifecycleError` | **PASS** |
| **26** | Lifecycle Teardown | Session teardown unbinds session plan, permitting clean session reuse | Unbound & reusable | **PASS** |

---

## 6. Regression Verification Across Full Repository

All previous milestones and existing test suites were verified:
- **M36 Implementation**: PASS
- **M36 Hostile Audit Suite**: 26/26 PASS in 0.08s (`test_m36_plan_trajectory_ownership.py`).
- **M35 Persistence Lifecycle**: 22 passed in 0.28s (`test_m35_persistence_lifecycle.py`).
- **M34 Gateway Lifecycle**: 22 passed in 0.35s (`test_m34_gateway_lifecycle.py`).
- **M33 Checkpoint Lifecycle**: 20 passed in 0.28s (`test_m33_checkpoint_lifecycle.py`).
- **Full Repository Test Suite**: **1,725/1,725 PASS in 20.59s** (0 failures, 0 errors, 0 warnings).
- **Targeted M36 Pyright**: **0 errors, 0 warnings, 0 informations** on M36 dedicated surfaces (`tests/unit/execution/test_m36_plan_trajectory_ownership.py`).
- **Repository-Wide Pyright**: Pre-existing 414 errors / 1 warning in legacy test fixtures across repository, therefore NOT claimed clean.
- **Git Diff Check**: `git diff --check` is completely clean with 0 whitespace or syntax warnings.
- **Milestone Isolation**: Pre-existing M33/M34 working-tree changes intentionally remain uncommitted and were NOT included in the M36 commit.

---

## 7. Final Audit Verdict

The cross-session surgical plan ownership and trajectory integrity verification architecture strictly satisfies all requirements of `M36_CONTRACT_SPEC.md`.

```
================================================================================
FINAL VERDICT: M36_HOSTILE_AUDIT_PASS
================================================================================
```
