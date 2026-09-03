# M26 DISCOVERY REPORT — ARCHITECTURAL & LIFECYCLE FORENSIC AUDIT

**Authoritative Baseline**: `16c5121ecaaae714b62ebe8afd763fa36d938de9`  
**Previous Milestone**: M25 — Coordinated Clinical Session Teardown & Lifecycle Invalidation (FROZEN)  
**Audit Mode**: READ-ONLY / STRICT NON-MODIFYING  
**Final Classification**: `M26_JUSTIFIED`  

---

## Executive Summary

Following the completion and release of Milestone M25 (`16c5121ecaaae714b62ebe8afd763fa36d938de9`), a full-system read-only forensic audit was performed across all 26 packages of HoloMed AI.

The audit verified that M25 successfully hardened session teardown across `PlatformService`, `WorkflowService`, `PlanningService`, `RegistrationService`, `NavigationService`, `RecoveryService`, `SafetyGateService`, and `ClinicalExecutionGatewayService`.

However, the forensic audit identified a genuine, reproducible architectural gap that violates whole-system lifecycle completion and safety isolation:

**The Perceptual Safety Monitoring Subsystems (`ProximityService` M15 and `DriftService` M16) were omitted from session lifecycle coordination.**
Specifically:
1. `DriftService` enforces a strict capacity limit of **16 active sessions** (`MAX_ACTIVE_DRIFT_SESSIONS = 16`). It maintains 6 session-keyed mutable tracking structures. It has no `evict_session(session_id)` hook. After 16 sessions with landmark tracking, `DriftService` permanently exhausts capacity with `DriftCapacityError`, locking out all subsequent clinical sessions.
2. `ProximityService` enforces a capacity limit of **32 active sessions** (`MAX_ACTIVE_PROXIMITY_SESSIONS = 32`). It maintains 9 session-keyed mutable tracking structures. It has no `evict_session(session_id)` hook. After 32 sessions, it permanently exhausts capacity with `ProximityCapacityError`.
3. Both services are authoritative primary evidence sources directly polled by `SafetyGateEvaluator.evaluate()` on every single clinical operation. When a session with an exclusion zone breach (`CRITICAL_BREACH`) is torn down via `execution.session.teardown` and subsequently reused, `ProximityService` still retains the stale breach state. The Safety Gate evaluates this stale evidence and denies all clinical actions with `DENIED_CRITICAL`, causing permanent false-positive interlocks on session restart.
4. Furthermore, raw dispatcher mutation routes `proximity.evaluate` and `drift.evaluate` bypass capability checks and execute unmediated state transitions.

Milestone M26 is **JUSTIFIED** to close this perceptual monitoring lifecycle and safety gap.

---

## Phase 1 — Current System Inventory

After Milestone M25, the HoloMed AI architecture consists of:
- **Platform Layer (M02/M09)**: `PlatformService`, `SessionManager`, `CycleCoordinator`. Manages supervisor cycles, platform session context (`SessionContext`), epoch tracking. Max 32 active sessions.
- **Workflow Authority (M10/M20)**: `WorkflowService`, `WorkflowStateMachine`. Phase transitions (`SETUP -> PLANNING -> REGISTRATION -> TARGETING -> NAVIGATION -> RECOVERY_REQUIRED -> ABORTED -> COMPLETED`). Max 32 active sessions.
- **Preoperative Planning (M12/M24)**: `PlanningService`. Plan definitions, session plan bindings, plan verification. M24 removed raw mutation routes; execution is mediated via `ClinicalExecutionGatewayService`.
- **Intraoperative Registration (M13/M22)**: `RegistrationService`. Rigid point-cloud registration, Horn's quaternion method, TRE/FRE validation. M22 removed raw mutation routes; execution is mediated via gateway.
- **Instrument Tracking & Navigation (M14/M19/M25)**: `NavigationService`. Real-time tool pose tracking, trajectory alignment, deviation calculations. M19 unified execution; M25 hardened composite key eviction.
- **Spatial Recovery & Realignment (M17/M23)**: `RecoveryService`. Checkpoint pairing, staged registration candidates, multi-stage recovery authorization. M23 unified execution via gateway.
- **Safety Gate & Cross-Subsystem Interlocks (M18)**: `SafetyGateService`, `SafetyGateEvaluator`. Synchronous dual-gate clearance (`GateDecision`, `GateSeverity`, `GateReasonCode`). Max 32 active sessions.
- **Perceptual Safety Monitoring (M15/M16)**:
  - `ProximityService`: Real-time instrument-to-exclusion-zone distance tracking, static/dynamic margin inflation, rate-of-closure calculation. Max 32 sessions.
  - `DriftService`: Continuous landmark drift monitoring, dwell stability buffers, displacement tracking. Max 16 sessions.
- **Clinical Execution Gateway (M19–M25)**: `ClinicalExecutionGatewayService`. Central authoritative coordinator for all clinical mutations (`execution.*` routes). Single-use ephemeral `_ExecutionCapability`.
- **Client Transport Gateway (M05/M11)**: `GatewayService`. External network client authentication (`GatewayAuthenticator`), role-based routing (`GatewayAuthorizationPolicy`), XR frame streaming. Max 32 connections.
- **Durable Persistence & Audit (M04)**: `PersistenceService`, `DurableSessionStore`, `ReplayEngine`. Append-only disk journaling, crash replay verification.
- **Message Dispatcher (M01)**: `MessageDispatcher`. Synchronous in-memory command/query bus with strict lifecycle state validation.

---

## Phase 2 — Route Forensics

A complete audit of all registered command and query routes on the `MessageDispatcher` reveals:

### 1. Clinical Execution Gateway Routes (Hardened M19–M25)
- `execution.navigation.execute` [COMMAND, capability-gated, safety-critical]
- `execution.recovery.execute` [COMMAND, capability-gated, safety-critical]
- `execution.trajectory.bind` [COMMAND, capability-gated, safety-critical]
- `execution.tool.invoke` [COMMAND, capability-gated, safety-critical]
- `execution.workflow.resume` [COMMAND, capability-gated, safety-critical]
- `execution.registration.execute` [COMMAND, capability-gated, safety-critical]
- `execution.planning.execute` [COMMAND, capability-gated, safety-critical]
- `execution.session.teardown` [COMMAND, capability-gated, lifecycle-critical]
- `execution.status.get` [QUERY, read-only]

### 2. Clinical Subsystem Public Query Routes (Read-Only)
- `planning.get` [QUERY, read-only]
- `registration.get` [QUERY, read-only]
- `navigation.status.get` [QUERY, read-only]
- `recovery.status.get` [QUERY, read-only]
- `workflow.status` [QUERY, read-only]
- `safety_gate.status.get` [QUERY, read-only]

### 3. Perceptual Monitoring Routes (Unmediated Mutations)
- `proximity.evaluate` [COMMAND, state-mutation, UNGATED]: Dispatched directly to `ProximityService.handle_evaluate_command`. Mutates `_session_states`, `_latest_geometries`, and triggers interlock events without capability gating.
- `proximity.status.get` [QUERY, read-only]
- `proximity.zones.get` [QUERY, read-only]
- `drift.evaluate` [COMMAND, state-mutation, UNGATED]: Dispatched directly to `DriftService.handle_evaluate_command`. Mutates dwell buffers, landmark displacements, and drift state without capability gating.
- `drift.status.get` [QUERY, read-only]
- `drift.landmarks.get` [QUERY, read-only]

### 4. Raw Safety Gate Route
- `safety_gate.evaluate` [COMMAND, state-mutation, UNGATED]: Mutates `_latest_decisions` and writes persistence audit outside the execution gateway.

### 5. Administrative / Reset Routes
- `platform.session.start` [COMMAND, platform lifecycle]
- `platform.session.stop` [COMMAND, platform lifecycle — bypasses clinical teardown]
- `platform.reset` [COMMAND, uncoordinated global state wipe]
- `tools.reset` [COMMAND, uncoordinated tool engine wipe]
- `ultron.reset` [COMMAND, uncoordinated reasoning rule engine wipe]
- `audio.pipeline.reset` [COMMAND, sensory pipeline reset]

---

## Phase 3 — Capability Forensics

Audit of `_ExecutionCapability` in `python/holomed/execution/_capability.py`:
- **Construction**: Protected by `internal_key is _INTERNAL_EXECUTION_KEY`. Cannot be imported or constructed by external callers.
- **Binding**: Strictly bound to `service_instance_id`, `session_id`, `action`, `sequence_number`.
- **Single-Use Invalidation**: `cap.invalidate()` called in `finally:` block of all gateway methods. Replay fails closed.
- **Finding in M17 Recovery**: `RecoveryService.apply_recovery_authorization` imports `_create_execution_capability` internally to delegate sub-capabilities to registration and navigation during recovery application. This was authorized in M23.
- **Gap Identified**: `ProximityService.bind_zones` and `DriftService.bind_landmarks` take NO execution capability parameter. Any code with a reference to the service instance can reconfigure zones or landmarks at any time without gateway coordination.

---

## Phase 4 — Transaction / Concurrency Forensics

- Single-threaded reentrancy guards (`_in_transaction = True`) exist across all major services (`ClinicalExecutionGatewayService`, `PlatformService`, `WorkflowService`, `RegistrationService`, `NavigationService`, `RecoveryService`, `SafetyGateService`, `ProximityService`, `DriftService`).
- If an exception occurs inside a transaction, `finally: self._in_transaction = False` is consistently present across all audited services, preventing stuck locks.
- **Ordering Dependency in M25 Teardown**:
  Topological sequence: Navigation -> Recovery -> Registration -> Planning -> Safety Gate -> Workflow -> Gateway Cache -> Platform Session.
  Because `ProximityService` and `DriftService` are absent from this sequence, their transactions are never invoked during teardown.

---

## Phase 5 — Session Isolation Forensics

### The M15 / M16 Perceptual Leakage Defect

M25 established complete session-scoped eviction for 7 clinical subsystems + platform. However, auditing all session-keyed dicts across the codebase reveals:

#### In `python/holomed/proximity/service.py`:
1. `_monitored_zones`: `session_id -> Tuple[SafetyExclusionZone, ...]`
2. `_registration_errors`: `session_id -> float`
3. `_static_margins`: `session_id -> float`
4. `_latest_geometries`: `Tuple[session_id, instrument_id] -> ToolClearanceGeometry`
5. `_latest_sequences`: `Tuple[session_id, instrument_id] -> int`
6. `_latest_evaluations`: `session_id -> ProximityEvaluationRecord`
7. `_session_states`: `session_id -> ProximityState`
8. `_active_instruments`: `session_id -> str`
9. `_clearance_history`: `Tuple[session_id, zone_id] -> Tuple[float, str]`

`len(_session_states)` is capped at `MAX_ACTIVE_PROXIMITY_SESSIONS = 32`.
When `execution.session.teardown` runs:
`ProximityService` is NOT called. All 9 structures remain populated in memory.

#### In `python/holomed/drift/service.py`:
1. `_landmarks`: `session_id -> Dict[landmark_id, LandmarkDefinition]`
2. `_session_states`: `session_id -> DriftState`
3. `_latest_sequences`: `session_id -> int`
4. `_verified_landmarks`: `session_id -> Set[str]`
5. `_latest_verifications`: `session_id -> LandmarkVerificationRecord`
6. `_dwell_buffers`: `Tuple[session_id, landmark_id] -> List[LandmarkObservation]`

`len(_session_states)` is capped at `MAX_ACTIVE_DRIFT_SESSIONS = 16`.
When `execution.session.teardown` runs:
`DriftService` is NOT called. All 6 structures remain populated in memory.

### Concrete Impact Proof
1. **Capacity Lockout on Session 17**:
   Start 16 consecutive sessions, bind landmarks in `DriftService`, and teardown each session via M25 `execution.session.teardown`. Attempt to start session 17 and bind landmarks:
   `DriftCapacityError: Max active drift sessions (16) exceeded` is raised. The surgical suite is disabled.
2. **Stale Safety Interlock Trip on Session Reuse**:
   In session `SESS-001`, instrument breaches an exclusion zone (`CRITICAL_BREACH`). Session `SESS-001` is torn down.
   Session `SESS-001` is restarted cleanly with a new patient and plan.
   During the first navigation action, `ClinicalExecutionGatewayService` invokes `SafetyGateService.evaluate()`.
   `SafetyGateEvaluator` calls `proximity_service.get_proximity_status("SESS-001")`.
   `ProximityService` returns the residual `CRITICAL_BREACH` from the prior surgical session.
   `SafetyGateEvaluator` trips Precedence 1:
   `decision=GateDecision.DENIED_CRITICAL`, `reason_code=GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH`.
   The new surgical procedure is immediately halted due to ghost evidence from the previous surgery.

---

## Phase 6 — Persistence / Recovery Forensics

- `PersistenceService` stores append-only session journals on disk in `artifacts/persistence/{session_id}/journal.jsonl`.
- Session teardown correctly writes durable audit records (`session_teardown_completed`, `session_teardown_degraded`, `session_teardown_failed`).
- Disk journals survive process restart and are verifiable via `ReplayEngine`.
- No clinical state is resurrected into active memory from disk journals during normal startup; active clinical state is strictly in-memory and created through authenticated commands.

---

## Phase 7 — Workflow / Lifecycle Forensics

- `WorkflowService.start_workflow(session_id)` initializes workflow state.
- `WorkflowStateMachine` governs transitions.
- When `workflow.abort` is dispatched, it sets the phase to `ABORTED`.
- However, `workflow.abort` does NOT trigger session eviction or stop navigation.
- If a session is aborted, its navigation poses and proximity zones remain resident until `execution.session.teardown` is explicitly called.

---

## Phase 8 — Safety Forensics

- `SafetyGateEvaluator` enforces a strict precedence hierarchy (Precedence 0: Session Mismatch -> Precedence 1: Exclusion Zone Breach -> Precedence 2: Recovery Required -> Precedence 3: Unverified Registration -> Precedence 4: Landmark Drift -> Precedence 5: Workflow State -> Precedence 6: Trajectory Deviation -> Precedence 7: Warning Margin).
- **Vulnerability**: Because Precedence 1 (Exclusion Zone Breach) and Precedence 4 (Landmark Drift) rely directly on `ProximityService` and `DriftService`, unpurged session state in these two services directly corrupts Safety Gate decisions.

---

## Phase 9 — Data / Temporal Consistency

- Sequence numbers are validated monotonically within `NavigationService` (`_latest_sequences`), `PlatformService` (`validate_and_advance_sequence`), `ProximityService` (`_latest_sequences`), and `DriftService` (`_latest_sequences`).
- In `ProximityService`, `_latest_sequences` uses composite key `(session_id, instrument_id)`.
- In `DriftService`, `_latest_sequences` uses key `session_id`.
- Without eviction, reusing a `session_id` can reject valid low sequence numbers if sequence numbers are not reset.

---

## Phase 10 — Error / Failure Forensics

- M25 implemented best-effort failure aggregation during teardown: if one subsystem fails, eviction continues for remaining subsystems, and the gateway records failures in `SessionTeardownExecutionResult.failures`.
- Extending this to `ProximityService` and `DriftService` ensures that perceptual monitoring failures are similarly aggregated without halting platform teardown.

---

## Phase 11 — API / Contract Forensics

Classification of findings across public API surfaces:

| Component | Finding | Severity |
| :--- | :--- | :--- |
| `DriftService` | Missing `evict_session(session_id)` — 16-session capacity exhaustion | **CRITICAL** |
| `ProximityService` | Missing `evict_session(session_id)` — 32-session capacity exhaustion & stale breach leakage | **CRITICAL** |
| `GatewayService` | `GatewayAuthorizationPolicy` lacks `payload["session_id"] == session.session_id` check | **HIGH** |
| `PlatformService` | `platform.session.stop` command bypasses clinical session teardown | **MEDIUM** |
| `SafetyGateService` | `safety_gate.evaluate` raw command exposed on dispatcher | **MEDIUM** |
| `ProximityService` | `proximity.evaluate` raw command exposed on dispatcher without capability check | **MEDIUM** |
| `DriftService` | `drift.evaluate` raw command exposed on dispatcher without capability check | **MEDIUM** |
| `PlatformService` | `platform.reset` uncoordinated destructive command on dispatcher | **LOW** |

---

## Phase 12 — Architectural Dependency Forensics

- No circular imports exist between services.
- `SafetyGateEvaluator` depends on public status query methods:
  - `proximity_service.get_proximity_status(session_id)`
  - `drift_service.get_drift_status(session_id)`
- `RecoveryService` depends on:
  - `proximity_service.bind_zones()`
  - `drift_service.bind_landmarks()`
- `ClinicalExecutionGatewayService` currently lacks handles to `proximity_service` and `drift_service`. Injecting optional handles maintains unidirectional dependency flow from Gateway down to subsystems.

---

## Phase 13 — Milestone Gap Analysis (M07–M25)

1. **M07/M11 (Tools)**: Complete. M21 unified tool invocation through gateway.
2. **M12/M24 (Planning)**: Complete. M24 removed dispatcher mutation routes.
3. **M13/M22 (Registration)**: Complete. M22 unified execution through gateway.
4. **M14/M19/M25 (Navigation)**: Complete. M25 fixed composite-key eviction.
5. **M17/M23 (Recovery)**: Complete. M23 unified recovery execution.
6. **M18 (Safety Gate)**: Complete. Inline dual-gate evaluation.
7. **M25 (Session Teardown)**: Substantially complete for core execution, but **omitted M15 Proximity and M16 Drift**.
8. **Remaining Flaw**: The exclusion of `ProximityService` and `DriftService` from M25 teardown leaves a reproducible 16-session capacity wall in `DriftService` and safety gate state contamination upon session reuse.

---

## Phase 14 — Top Candidate Milestones for M26

### Candidate 1: Perceptual Safety Monitoring Lifecycle Hardening (Proximity & Drift)
- **Title**: M26 — Perceptual Monitoring Lifecycle & Session Eviction Hardening
- **Problem**: `ProximityService` (M15) and `DriftService` (M16) accumulate session-scoped state without eviction, causing capacity exhaustion at 16 sessions (`DriftCapacityError`) and 32 sessions (`ProximityCapacityError`), as well as stale safety breach contamination across reused session IDs.
- **Evidence**: `MAX_ACTIVE_DRIFT_SESSIONS = 16`, `MAX_ACTIVE_PROXIMITY_SESSIONS = 32`, zero `evict_session()` methods in `proximity/service.py` and `drift/service.py`, direct queries by `SafetyGateEvaluator`.
- **Severity**: **CRITICAL**.
- **Affected Services**: `ProximityService`, `DriftService`, `ClinicalExecutionGatewayService`.
- **Minimum Reopen Set**:
  - `python/holomed/proximity/service.py`: Add `evict_session(session_id: str, capability: Optional[Any] = None) -> bool`.
  - `python/holomed/drift/service.py`: Add `evict_session(session_id: str, capability: Optional[Any] = None) -> bool`.
  - `python/holomed/execution/service.py`: Accept optional `proximity_service` and `drift_service`, include them in `execute_session_teardown()` topological eviction order.
- **Frozen Areas Kept Frozen**: Core algorithms (Horn's registration, deviation math, ray-cast proximity, dwell-buffer drift math, safety gate precedence logic).
- **Testability**: Highly deterministic; reproducible with sequential 16/32 session tests and cross-session stale state reuse tests.
- **Recommendation**: **SELECTED AS M26**.

### Candidate 2: External Client Gateway Session Binding & Role Hardening
- **Title**: Client Gateway Session Isolation & Command Authorization Policy
- **Problem**: `GatewayAuthorizationPolicy` does not verify `payload["session_id"] == session.session_id`, allowing authenticated clients on session A to issue commands targeting session B.
- **Severity**: HIGH.
- **Why Not Selected Over Candidate 1**: Candidate 1 addresses an active crash/lockout bug in the clinical core (16-session failure), whereas Candidate 2 targets external network ingress multi-tenancy.

### Candidate 3: Administrative Dispatcher Route Deprecation & Guarding
- **Title**: Dispatcher Administrative Route Sanitization
- **Problem**: `platform.session.stop`, `platform.reset`, and `tools.reset` remain on the dispatcher as uncoordinated bypasses.
- **Severity**: MEDIUM.
- **Why Not Selected Over Candidate 1**: Does not cause runtime memory or capacity exhaustion during normal workflow; secondary to perceptual monitoring eviction.

---

## Phase 15 — Hostile Self-Challenge

### Attack 1: "Is this already solved in M25?"
- **Rebuttal**: No. Inspected `ClinicalExecutionGatewayService.execute_session_teardown()` in `python/holomed/execution/service.py`. It explicitly calls:
  `navigation`, `recovery`, `registration`, `planning`, `safety_gate`, `workflow`, gateway cache, `platform`.
  Neither `proximity_service` nor `drift_service` is even referenced in `ClinicalExecutionGatewayService`.
  Inspected `ProximityService` and `DriftService` source code: `evict_session` does not exist in either file.

### Attack 2: "Is 16 sessions an actual problem, or just theoretical?"
- **Rebuttal**: In an operating hospital environment or long-running robotic surgical daemon, 16 procedures/cycles easily occur within days. Reaching session 17 raises an unhandled `DriftCapacityError` that crashes or blocks the entire drift tracking subsystem until the process is restarted. This is an active production reliability defect.

### Attack 3: "Does fixing this require reopening frozen M25 or earlier algorithms?"
- **Rebuttal**: No. It requires purely additive changes:
  1. Add `evict_session(session_id)` to `ProximityService` (purging its 9 session dicts).
  2. Add `evict_session(session_id)` to `DriftService` (purging its 6 session dicts).
  3. Wire `proximity_service` and `drift_service` into `ClinicalExecutionGatewayService`'s existing teardown loop.
  Zero mathematical algorithms, geometric evaluators, or state machines need modification.

---

## Final Classification

```
==================================================
M26_JUSTIFIED
==================================================
```

**Proposed Milestone**:  
**M26 — Perceptual Monitoring Lifecycle & Session Eviction Hardening**
