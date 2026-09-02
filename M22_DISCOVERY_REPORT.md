# M22 ARCHITECTURAL DISCOVERY REPORT

**Authoritative Baseline:** `a3308f742f21d01f4c99e92f8288f5d6c9c4f8d1` (M21 Release)  
**Timestamp:** 2026-09-02T18:57:00Z  
**Mode:** READ-ONLY ARCHITECTURAL AUDIT  

---

## 1. Executive Summary

Milestone M21 established the `ClinicalExecutionGatewayService` as the sole authoritative execution gateway for tracked navigation (`execution.navigation.execute`), clinical tools (`execution.tool.invoke`), and workflow recovery re-entry (`execution.workflow.resume`). It eliminated direct dispatcher execution on M07 (`tools.invoke`) and M14 (`navigation.pose.submit`, `navigation.evaluate`), enforcing single-use object capabilities (`_ExecutionCapability`) across synchronous execution cycles.

A comprehensive post-M21 architectural audit of all 21 milestones reveals the next major architectural vulnerability and boundary gap:
1. **Primary Critical Gap**: **Spatial Lifecycle & Recovery Actuation Dispatcher Bypass (M17/M14/M13)**. While M21 gated execution, M17 `RecoveryService` still registers raw mutating command handlers on `MessageDispatcher` (`recovery.stage`, `recovery.verify`, `recovery.activate`), allowing uncoordinated registration overwrites, landmark re-seeding, and safety exclusion zone mutations without capability or safety-gate oversight. Furthermore, M14 `NavigationService.bind_trajectory()` lacks capability gating.
2. **Secondary High Gap**: **Universal Session Lifecycle & Multi-Service State Teardown (M06/M10/M14/M17/M21)**. 15+ microservices maintain per-session memory state without a unified session teardown or revocation coordinator when procedures complete or abort.

---

## 2. System Authority Map

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            MessageDispatcher (M00.4)                        │
└──────┬───────────────────────┬────────────────────────────┬──────────────────┘
       │                       │                            │
       ▼                       ▼                            ▼
┌───────────────────┐   ┌───────────────────────────┐  ┌───────────────────────┐
│ Workflow (M10/M20)│   │ Execution Gateway (M21)   │  │ Recovery (M17 - OPEN) │
│ - workflow.start  │   │ - exec.nav.execute        │  │ - recovery.stage      │
│ - workflow.transit│   │ - exec.tool.invoke        │  │ - recovery.verify     │
│ - workflow.abort  │   │ - exec.workflow.resume    │  │ - recovery.activate ◄─┼── UNGATED BYPASS!
└───────────────────┘   │ - exec.recovery.execute   │  └───────────────────────┘
                        │ - exec.trajectory.bind    │
                        │ - exec.status.get         │
                        └─────────────┬─────────────┘
                                      │ (_ExecutionCapability)
                                      ▼
                        ┌───────────────────────────┐
                        │ Navigation (M14) & Tools  │
                        │ - submit_pose (Gated)     │
                        │ - evaluate (Gated)        │
                        │ - invoke_tool (Gated)     │
                        │ - bind_trajectory (OPEN!) ◄──── UNGATED BYPASS!
                        └───────────────────────────┘
```

---

## 3. Execution Bypass & Direct-Call Sweep

| Subsystem | Method / Endpoint | Dispatcher Route | Current Gating | Vulnerability Classification |
|---|---|---|---|---|
| **M17 Recovery** | `RecoveryService.activate_recovery()` | `recovery.activate` | None (Direct command) | **BYPASS** (Mutates M13, M15, M16 without gateway) |
| **M17 Recovery** | `RecoveryService.verify_candidate()` | `recovery.verify` | None (Direct command) | **BYPASS** (Mutates recovery state without gateway) |
| **M17 Recovery** | `RecoveryService.stage_candidate()` | `recovery.stage` | None (Direct command) | **BYPASS** (Stages recovery without gateway) |
| **M14 Navigation** | `NavigationService.bind_trajectory()` | None (M21 route added) | None (Unguarded Python method) | **BYPASS** (Direct call lacks capability check) |
| **M13 Registration** | `RegistrationService.verify_registration()` | `registration.verify` | None (Direct command) | **INTERNAL_MUTATOR** (Direct dispatcher route) |
| **M12 Planning** | `PlanningService.lock_plan()` | `planning.lock` | None (Direct command) | **INTERNAL_MUTATOR** (Direct dispatcher route) |
| **M14 Navigation** | `NavigationService.submit_pose()` | REMOVED in M21 | `_ExecutionCapability` | **CAPABILITY_GATED** |
| **M14 Navigation** | `NavigationService.evaluate()` | REMOVED in M21 | `_ExecutionCapability` | **CAPABILITY_GATED** |
| **M07 Tools** | `ToolService.invoke_tool()` | REMOVED in M21 | `_ExecutionCapability` | **CAPABILITY_GATED** |

---

## 4. State Ownership Matrix

| State Component | Primary Owner | Direct Writers | Gateway Mediated | Revision Tracked | Persistence Audit | Risk Level |
|---|---|---|---|---|---|---|
| **Workflow State** | `WorkflowService` | `WorkflowService` | Yes (M20/M21) | Yes (`sequence_number`) | Yes (`PersistenceService`) | LOW |
| **Navigation Pose** | `NavigationService` | `NavigationService` | Yes (`_ExecutionCapability`) | Yes (`sequence_number`) | Yes (Deduplicated) | LOW |
| **Navigation Trajectory** | `NavigationService` | `ClinicalExecutionGatewayService`, `NavigationService.bind_trajectory` | Partial (Direct API open) | No | Partial | **HIGH** |
| **Spatial Recovery State** | `RecoveryService` | `ClinicalExecutionGatewayService`, `RecoveryService.activate_recovery` | No (Raw dispatcher route open) | Yes (`registration_revision`) | Partial | **CRITICAL** |
| **Spatial Registration** | `RegistrationService` | `RegistrationService`, `RecoveryService` | No | Yes (`registration_revision`) | Partial | **MEDIUM** |
| **Safety Interlocks** | `SafetyGateService` | Evaluator (Read-only synthesis) | Yes (Dual-Gate) | Yes (`sequence_number`) | Yes | LOW |

---

## 5. Candidate Ranking & Selection

### Candidate A: Spatial Recovery Actuation & Trajectory Binding Hardening (M22 Candidate 1 - RECOMMENDED)
- **Title**: Universal Spatial Recovery Actuation & Trajectory Binding Capability Hardening
- **Severity**: **CRITICAL**
- **Affected Milestones**: M14 Navigation, M17 Recovery, M19/M21 Execution Gateway
- **Why Needed**: Eliminates the raw dispatcher routes `recovery.stage`, `recovery.verify`, `recovery.activate` from `RecoveryService` and binds all spatial recovery mutations strictly through `execution.recovery.execute` under `_ExecutionCapability`. Hardens `NavigationService.bind_trajectory()` to require `_ExecutionCapability` with `action == "TRAJECTORY_ALIGNMENT"`.
- **System & Security Impact**: Guarantees that spatial re-registration, landmark re-seeding, safety zone updates, and trajectory physical binding can never occur outside authoritative gateway dual-gate evaluation.

### Candidate B: Multi-Service Clinical Session Teardown & Lifecycle Orchestration (M23 Candidate)
- **Title**: Unified Clinical Session Lifecycle & Multi-Service Teardown Orchestrator
- **Severity**: **HIGH**
- **Affected Milestones**: M06 Platform, M10 Workflow, M18 Safety Gate, M21 Execution Gateway
- **Why Needed**: Provides atomic session teardown and stale state purging across all 15 services on workflow completion or abort.

---

## 6. Selected Architecture for M22

### Core Architectural Invariants:
1. **M17 Dispatcher Hardening**: Remove `recovery.stage`, `recovery.verify`, `recovery.activate` from `RecoveryService` dispatcher registration.
2. **M17 Capability Gating**: Require `_ExecutionCapability` with `action == "RECOVERY_REORIENTATION"` on `RecoveryService.stage_candidate()`, `verify_candidate()`, and `activate_recovery()`.
3. **M14 Trajectory Capability Gating**: Require `_ExecutionCapability` with `action == "TRAJECTORY_ALIGNMENT"` on `NavigationService.bind_trajectory()`.
4. **M19/M21 Gateway Integration**: `ClinicalExecutionGatewayService.execute_recovery_reorientation()` and `execute_trajectory_binding()` generate and pass single-use capabilities to M17 and M14, invalidating them immediately in `finally:` blocks.

### Minimum Reopen Set:
- `M14 navigation`
- `M17 recovery`
- `M19 execution`

---

## 7. Final Classification

**M22_JUSTIFIED_AND_FEASIBLE**
