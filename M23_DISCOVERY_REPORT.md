# M23 Architecture Discovery Report

**Authoritative Baseline:** `0c6da00d40132fd0215d6c17db91c84f9377f207` (M22 Release)  
**Mode:** READ-ONLY Architectural Audit  
**Date:** September 2026  

---

## 1. Executive Summary

Following the successful release of **M22 (Universal Spatial Recovery Actuation & Trajectory Binding Capability Hardening)**, an adversarial architectural audit was conducted across the entire HoloMed codebase.

The audit proves that runtime execution pathways (`execute_navigation`, `execute_recovery_reorientation`, `execute_trajectory_binding`, `execute_tool`, `execute_workflow_resumption`) are now strictly dual-gated and hardened with unforgeable `_ExecutionCapability` tokens.

However, a critical architectural gap remains in the **Pre-Procedure Initial Registration & Planning Life-Cycle Boundary (M13/M12/M10)** and the **Cross-Service Epoch Migration & Session Eviction Synchronization (M09/Platform)**:
1. **Primary Critical Gap**: **M13 Initial Registration & M12 Planning Dispatcher Mutation Bypass**. While M17 recovery re-registration is hardened, `RegistrationService` (M13) still exposes raw mutating command handlers on `MessageDispatcher` (`registration.submit`, `registration.solve`, `registration.verify`). Any uncoordinated client or rogue message can mutate, solve, or invalidate the authoritative 3D patient-to-plan transform during live surgical navigation (`NAVIGATION`/`INTERVENTION` phases) without M10 workflow authorization or M18 safety gate oversight.
2. **Secondary Gap**: **Platform Epoch Migration Topological Disconnect (M09 vs M10-M22)**. `PlatformService.migrate_epoch()` (M09) only coordinates epoch transitions for M01-M07. Post-M09 services (M10, M12, M13, M14, M15, M16, M17, M18, M19/M21) maintain un-migrated local `_epoch_id` values, leading to silent epoch desynchronization.
3. **Tertiary Gap**: **Session Lifecycle Teardown & State Eviction Omission**. Terminating a clinical session in `PlatformService` or `WorkflowService` fails to evict session memory across spatial/safety subsystems, leading to capacity saturation.

---

## 2. System Authority Map

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           CENTRAL SUPERVISOR & GATEWAY                          │
│                                                                                 │
│   ┌───────────────────────────────┐     ┌───────────────────────────────────┐   │
│   │ PlatformService (M09)         │     │ ClinicalExecutionGateway (M19/21) │   │
│   │ - Session Lifecycle           │     │ - Dual-Gated Execution            │   │
│   │ - Epoch Migration (GAP!)      │     │ - _ExecutionCapability Minting   │   │
│   └──────────────┬────────────────┘     └─────────────────┬─────────────────┘   │
└──────────────────┼────────────────────────────────────────┼─────────────────────┘
                   │                                        │
      ┌────────────┴────────────┐              ┌────────────┴────────────┐
      ▼                         ▼              ▼                         ▼
┌──────────────┐         ┌──────────────┐┌──────────────┐         ┌──────────────┐
│ M10 Workflow │         │ M18 Safety   ││ M14 Nav      │         │ M17 Recovery │
│ Phase Auth   │         │ Dual Gate    ││ (Hardened)   │         │ (Hardened)   │
└──────┬───────┘         └──────┬───────┘└──────────────┘         └──────────────┘
       │                        │
       └───────────┬────────────┘
                   ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    UNPROTECTED PRE-PROCEDURE SPATIAL PREROGATIVES               │
│                                                                                 │
│   ┌────────────────────────────────┐    ┌───────────────────────────────────┐   │
│   │ RegistrationService (M13)      │    │ PlanningService (M12)             │   │
│   │ - registration.submit (RAW)    │    │ - planning.submit (RAW)           │   │
│   │ - registration.solve (RAW)     │    │ - planning.lock (RAW)             │   │
│   │ - registration.verify (RAW) ◄──┼────┼── CRITICAL BYPASS (NO GATE / WF!) │   │
│   └────────────────────────────────┘    └───────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. State Ownership Matrix

| Subsystem | Managed State | Authoritative Owner | Mutation Paths | Gating / Authority Status |
|---|---|---|---|---|
| **M19/M21 Gateway** | Execution history, capabilities | `ClinicalExecutionGatewayService` | Gateway execution APIs | **Authoritative & Dual-Gated** |
| **M18 Safety Gate** | Multi-service gate decisions | `SafetyGateService` | `evaluate()` | **Authoritative & Pure** |
| **M17 Recovery** | Re-registration candidates, recovery status | `RecoveryService` | `execute_recovery_reorientation` | **Hardened (Capability-bound)** |
| **M14 Navigation** | Poses, deviations, trajectory alignment | `NavigationService` | `execute_navigation`, `execute_trajectory_binding` | **Hardened (Capability-bound)** |
| **M10/M20 Workflow** | Clinical phases, interlocks, confirmations | `WorkflowService` | `workflow.transition`, `workflow.confirm`, `workflow.abort` | **Authoritative (Dispatcher-managed)** |
| **M13 Registration** | Fiducials, 3D Rigid Transform, FRE metrics | `RegistrationService` | `registration.submit`, `registration.solve`, `registration.verify` | **UNPROTECTED DISPATCHER BYPASS** |
| **M12 Planning** | Trajectory plans, Safety exclusion zones | `PlanningService` | `planning.submit`, `planning.lock`, `planning.verify` | **UNPROTECTED DISPATCHER MUTATION** |
| **M15 Proximity** | Safety exclusion zones, distance margins | `ProximityService` | `bind_zones`, `proximity.evaluate` | **Service-bound / Query-routed** |
| **M16 Drift** | Landmark definitions, physical measurements | `DriftService` | `bind_landmarks`, `drift.evaluate` | **Service-bound / Query-routed** |
| **M09 Platform** | Active session registry, cycles, health | `PlatformService` | `platform.session.start/stop`, `migrate_epoch` | **Supervisor-level** |

---

## 4. Dispatcher Route Inventory

| Subsystem | Route Name | Type | Handler | Gating / Risk Level |
|---|---|---|---|---|
| **M19/M21 Gateway** | `execution.navigation.execute` | COMMAND | `handle_execute_navigation_command` | Dual-gated (M18 + M10) |
| **M19/M21 Gateway** | `execution.recovery.execute` | COMMAND | `handle_execute_recovery_command` | Dual-gated (M18 + M10) |
| **M19/M21 Gateway** | `execution.trajectory.bind` | COMMAND | `handle_execute_trajectory_command` | Dual-gated (M18 + M10) |
| **M19/M21 Gateway** | `execution.tool.invoke` | COMMAND | `handle_execute_tool_command` | Dual-gated (M18 + M10) |
| **M19/M21 Gateway** | `execution.workflow.resume` | COMMAND | `handle_execute_workflow_command` | Dual-gated (M18 + M10) |
| **M19/M21 Gateway** | `execution.status.get` | QUERY | `handle_get_status_query` | Read-only |
| **M17 Recovery** | `recovery.status.get` | QUERY | `handle_get_status_query` | Read-only |
| **M14 Navigation** | `navigation.status.get` | QUERY | `handle_get_status_query` | Read-only |
| **M13 Registration** | `registration.submit` | COMMAND | `handle_submit_command` | **HIGH RISK: Raw Dispatcher Access** |
| **M13 Registration** | `registration.solve` | COMMAND | `handle_solve_command` | **HIGH RISK: Raw Dispatcher Access** |
| **M13 Registration** | `registration.verify` | COMMAND | `handle_verify_command` | **CRITICAL RISK: Direct Transform Mutation** |
| **M13 Registration** | `registration.get` | QUERY | `handle_get_query` | Read-only |
| **M12 Planning** | `planning.submit` | COMMAND | `handle_submit_command` | **MEDIUM RISK: Uncoordinated Plan Injection** |
| **M12 Planning** | `planning.lock` | COMMAND | `handle_lock_command` | **MEDIUM RISK: Uncoordinated Plan Lock** |
| **M12 Planning** | `planning.verify` | COMMAND | `handle_verify_command` | **MEDIUM RISK: Uncoordinated Plan Verify** |
| **M12 Planning** | `planning.get` / `zones.get` / `trajectory.get` | QUERY | Query Handlers | Read-only |
| **M10 Workflow** | `workflow.start` / `transition` / `confirm` / `abort` | COMMAND | Command Handlers | Workflow-governed |
| **M10 Workflow** | `workflow.status` | QUERY | `handle_status_query` | Read-only |
| **M18 Safety Gate** | `safety_gate.evaluate` | COMMAND | `handle_evaluate_command` | Authoritative Evaluator |
| **M09 Platform** | `platform.session.start` / `stop` / `cycle` / `reset` | COMMAND | Command Handlers | Supervisor-level |

---

## 5. Privileged Action Safety Matrix

| Clinical Operation | Target Subsystem | Intended Workflow Phase | Current Protection | Bypass Feasibility |
|---|---|---|---|---|
| **Tool Navigation Pose Submit** | M14 Navigation | `NAVIGATION` / `INTERVENTION` | M18 Safety Gate + M10 Auth + Capability | **IMPOSSIBLE** (Hardened in M21) |
| **Trajectory Plan Alignment** | M14 Navigation | `REGISTRATION` $\to$ `NAVIGATION` | M18 Safety Gate + M10 Auth + Capability | **IMPOSSIBLE** (Hardened in M22) |
| **Spatial Recovery Actuation** | M17 Recovery | `RECOVERY_REQUIRED` | M18 Safety Gate + M10 Auth + Capability | **IMPOSSIBLE** (Hardened in M22) |
| **Initial Registration Solving** | M13 Registration | `REGISTRATION` | **None** (`registration.solve` is public) | **FEASIBLE** (Raw command bypass) |
| **Initial Registration Verification** | M13 Registration | `SAFETY_TIMEOUT` | **None** (`registration.verify` is public) | **FEASIBLE** (Can invalidate transform mid-surgery) |
| **Surgical Plan Lock & Verification** | M12 Planning | `PRE_PROCEDURE_PLANNING` | **None** (`planning.lock` is public) | **FEASIBLE** (Can lock plans uncoordinated) |

---

## 6. Revision & Freshness Matrix

| Entity | Revision Field | Increment Trigger | Validated By | Desynchronization Risks |
|---|---|---|---|---|
| **Registration Transform** | `epoch_id`, `verified_at_utc` | Solved/Verified | M18 Safety Gate (`evaluate()`), Recovery Evaluator | Direct `registration.verify` can overwrite transform without updating downstream bound zones/landmarks |
| **Landmarks** | `epoch_id`, `landmark_count` | `bind_landmarks()` | M16 DriftService, M18 Safety Gate | Downstream binds can lag behind initial registration changes |
| **Safety Zones** | `epoch_id`, `zone_count` | `bind_zones()` | M15 ProximityService, M18 Safety Gate | Planning updates can desynchronize from active proximity zones |
| **Execution Gateway** | `sequence_number`, `epoch_id` | Every transaction | Dual-gate check & capability | Synchronized |
| **Platform Supervisor** | `epoch_id` | `migrate_epoch()` | `SessionManager`, `CycleCoordinator` | **Desynchronized from M10-M22** |

---

## 7. Remaining Bypasses & Architectural Gaps

### Architectural Gap 1 (Severity: CRITICAL)
**Unmediated Initial Spatial Registration & Verification Bypass (M13)**
- **Evidence**: [python/holomed/registration/service.py:L130-L134](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/registration/service.py#L130-L134) registers `registration.submit`, `registration.solve`, `registration.verify` directly on `MessageDispatcher`.
- **Attack Vector**: During an active resection in `WorkflowPhase.INTERVENTION`, a client message `registration.verify` containing high checkpoint drift is dispatched. `RegistrationService` drops `transform` to `None` and sets `state = RegistrationState.INVALIDATED`. In parallel, live navigation in M14 continues until the next gateway cycle, causing an immediate catastrophic gate lock.
- **Root Cause**: While M17 recovery re-registration was hardened in M22, primary pre-procedure registration in M13 remains exposed to uncoordinated raw dispatcher invocations.

### Architectural Gap 2 (Severity: HIGH)
**Supervisor Epoch Migration Disconnection from Post-M09 Services (M09)**
- **Evidence**: [python/holomed/platform/service.py:L345-L365](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/platform/service.py#L345-L365) only resets `("tool_service", "xr_service", "anatomy_service", "ultron_service")`.
- **Consequence**: `migrate_epoch()` leaves `WorkflowService` (M10), `PlanningService` (M12), `RegistrationService` (M13), `NavigationService` (M14), `ProximityService` (M15), `DriftService` (M16), `RecoveryService` (M17), `SafetyGateService` (M18), and `ClinicalExecutionGatewayService` (M19/M21) at stale `_epoch_id = 1`. Subsequent safety gate evaluations fail with `RUNTIME_EPOCH_MISMATCH`.

### Architectural Gap 3 (Severity: MEDIUM)
**Absence of Universal Session Lifecycle Teardown & Eviction Protocol**
- **Evidence**: No unified `session.teardown` event or cascade exists across M12, M13, M14, M15, M16, M17, M18, M19. Terminating a session in `PlatformService` or `WorkflowService` leaves stale session records in memory dictionaries.

---

## 8. Severity Ranking

1. **CRITICAL**: **M13 Initial Spatial Registration & Verification Dispatcher Bypass**.
2. **HIGH**: **M09 Platform Epoch Migration Topology Desynchronization**.
3. **MEDIUM**: **Universal Session Teardown & Cross-Service Cache Eviction**.
4. **LOW**: **Planning (M12) Dispatcher Lock Formalization**.

---

## 9. Highest-Priority M23 Candidate

### Candidate Title
**M23 — Initial Spatial Registration & Verification Lifecycle Capability Hardening & Epoch Synchronization**

### Objectives
1. Remove raw dispatcher mutation commands (`registration.submit`, `registration.solve`, `registration.verify`) from `RegistrationService` (M13), retaining only read-only `registration.get`.
2. Enforce internal `_ExecutionCapability` on `RegistrationService.submit_fiducials()`, `solve_registration()`, and `verify_registration()`.
3. Route initial spatial registration solving and verification through `ClinicalExecutionGatewayService` or M10/M18 coordinated workflow lifecycle.
4. Expand `PlatformService.migrate_epoch()` to coordinate epoch advancement across all active spatial, safety, workflow, and execution services.

---

## 10. Minimum Reopen Set

- **M13 Registration**: `python/holomed/registration/service.py`
- **M09 Platform**: `python/holomed/platform/service.py`
- **M19/M21/M22 Execution Gateway**: `python/holomed/execution/service.py`
- **Tests**: `tests/unit/registration/`, `tests/unit/platform/`, `tests/unit/execution/`

---

## 11. Architectural Approaches

### Approach 1: Universal Clinical Execution Gateway Extension (Recommended)
- Extend `ClinicalExecutionGatewayService` with `execute_registration_submission()`, `execute_registration_solve()`, and `execute_registration_verification()`.
- Add `SafetyGateAction.REGISTRATION_SOLVE` and `SafetyGateAction.REGISTRATION_VERIFICATION` (or reuse existing action semantics under workflow phase validation).
- Remove `registration.submit/solve/verify` from dispatcher.
- Update `PlatformService.migrate_epoch()` to iterate over all registered services implementing an `IEpochAware` interface or reset contract.

### Approach 2: Direct Workflow-Mediated Pre-Procedure Registration Protocol
- Keep registration execution inside `WorkflowService` during `REGISTRATION` and `SAFETY_TIMEOUT` phases.
- Require `_ExecutionCapability` minted by `WorkflowService`.
- *Drawback*: Violates single-gateway principle established in M21/M22 where `ClinicalExecutionGatewayService` is the sole capability-minting authority.

---

## 12. Selected Approach & Feasibility

**Selected Approach:** **Approach 1 (Universal Gateway Extension & Epoch Supervisor Coordination)**.
- Maintains architectural integrity: `ClinicalExecutionGatewayService` remains the sole capability minting authority.
- Completely seals the last remaining raw spatial registration bypass in M13.
- Repairs `PlatformService.migrate_epoch()` to guarantee whole-system epoch alignment.

---

## 13. Final Classification

**`M23_JUSTIFIED_AND_FEASIBLE`**
