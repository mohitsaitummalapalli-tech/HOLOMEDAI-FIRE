# M27 DISCOVERY REPORT — SYSTEM-WIDE ARCHITECTURAL FORENSIC AUDIT

**Authoritative Baseline**: `0885622984bf3ba3304586685c53956be4cc6e6a`  
**Previous Release**: M26 (`feat(M26): harden perceptual monitoring lifecycle`)  
**Audit Mode**: READ-ONLY FORENSIC AUDIT  
**Status**: ZERO SOURCE CHANGES, ZERO TEST CHANGES, ZERO COMMITS, ZERO PUSHES  

---

## EXECUTIVE SUMMARY

Following the completion and freeze of M25 (Coordinated Clinical Session Teardown) and M26 (Perceptual Monitoring Lifecycle Hardening), this hostile system-wide forensic audit examined all 26 packages, the public dispatcher, authorization layers, capability lifecycles, and cross-subsystem consistency invariants.

The forensic audit has uncovered **three genuine architectural and safety gaps**:
1. **CRITICAL — Workflow Interlock Cross-Session Pollution & Teardown Leak (M10)**:
   `SafetyInterlockEngine._interlocks` lacks session-scoped partitioning. Methods `has_critical_interlock()` and `has_blocking_interlock()` evaluate globally across all sessions without filtering by `session_id`. Consequently, an interlock tripped in Session A immediately aborts Session B's surgical workflow. Furthermore, `WorkflowService.evict_session()` fails to evict `_interlock_engine` or `_checkpoint_validator`, leaving stale tripped interlocks in memory permanently, which immediately aborts reused sessions.
2. **HIGH — Client Gateway Ingress Lifecycle Leak & Payload Session Spoofing (M11)**:
   `GatewayService` enforces `MAX_CONNECTIONS_PER_SESSION = 8` but is omitted from the teardown chain. Disconnected or torn-down sessions leave dangling connections in `self._connections`, causing session reuse to hit `GatewayCapacityError` and permanently deny service. Furthermore, `GatewayAuthorizationPolicy.authorize_message()` fails to enforce that `envelope.payload["session_id"] == session.session_id`, permitting authenticated clients to inject commands into other sessions.
3. **HIGH — Durable Persistence Session Capacity Exhaustion (M09 Persistence)**:
   `DurableSessionStore` enforces `MAX_DURABLE_SESSIONS = 16`. When sessions are closed or torn down, they remain in `self._sessions` indefinitely without an eviction or pruning mechanism, causing the 17th clinical session to raise `PersistenceCapacityError`.

**Discovery Classification**: `M27_JUSTIFIED`  
**Selected Candidate for M27**: **Workflow Safety Interlock Scoping & Lifecycle Eviction Hardening (M10)**.

---

## PHASE 1 — CURRENT ARCHITECTURE SNAPSHOT

The architecture after M26 comprises 26 modular packages:
```
Clients (Web/XR/CLI)
        │ (Framed Envelopes over WebSocket/TCP)
        ▼
   GatewayService (M11)
        │
   MessageDispatcher (M08)
        │
   ClinicalExecutionGatewayService (M19–M26)  <-- Central Clinical Authority
   ├── NavigationService (M14)
   ├── ProximityService (M15)
   ├── DriftService (M16)
   ├── RecoveryService (M17)
   ├── RegistrationService (M13)
   ├── PlanningService (M12)
   ├── SafetyGateService (M18)
   ├── WorkflowService (M10, M20)
   └── PlatformService (M09)
```

Ownership and Authority Principles established through M26:
- Platform lifecycle is owned by `PlatformService` / `SessionManager`.
- Clinical mutation authority is unified in `ClinicalExecutionGatewayService`.
- Privileged operations require single-use `_ExecutionCapability`.
- Teardown coordinates reverse-topological eviction across all clinical subsystems.

---

## PHASE 2 — COMPLETE DISPATCHER FORENSICS

### 1. Route Enumeration
- **Clinical Execution Gateway (`execution.*`)**:
  - `execution.session.teardown` [Command, Capability-Gated, Lifecycle]
  - `execution.registration.verify` [Command, Capability-Gated, Clinical Mutation]
  - `execution.navigation.execute` [Command, Capability-Gated, Clinical Mutation]
  - `execution.recovery.plan` [Command, Capability-Gated, Safety-Critical]
  - `execution.recovery.execute` [Command, Capability-Gated, Safety-Critical]
  - `execution.tool.invoke` [Command, Capability-Gated, Clinical Mutation]
  - `execution.planning.bind` [Command, Capability-Gated, Safety-Critical]
  - `execution.workflow.resume` [Command, Capability-Gated, Safety-Critical]
  - `execution.status` [Query, Read-Only]
- **Platform (`platform.*`)**:
  - `platform.status`, `platform.audit` [Query, Read-Only]
  - `platform.cycle`, `platform.session.start`, `platform.session.stop` [Command, Platform Lifecycle]
  - `platform.reset` [Command, Dangerous Global Mutation — Bypasses Gateway]
- **Workflow (`workflow.*`)**:
  - `workflow.status` [Query, Read-Only]
  - `workflow.start`, `workflow.transition`, `workflow.confirm`, `workflow.abort` [Command, Public Mutation — Legacy Dispatcher Routes Bypassing Gateway]
- **Perceptual & Safety (`proximity.*`, `drift.*`, `safety_gate.*`)**:
  - `proximity.status.get`, `proximity.zones.get`, `drift.status.get`, `drift.landmarks.get`, `safety_gate.status.get` [Query, Read-Only]
  - `proximity.evaluate`, `drift.evaluate`, `safety_gate.evaluate` [Command, Direct Evaluation Mutation — Bypasses Gateway Execution]
- **Tools & Devices (`tools.*`, `ultron.*`, `xr.*`, `audio.*`, `gesture.*`, `anatomy.*`, `vision.*`)**:
  - Status queries [Query, Read-Only]
  - Direct resets (`tools.reset`, `ultron.reset`, `xr.reset`, `audio.pipeline.reset`, `gesture.reset`, `anatomy.reset`, `vision.reset`) [Command, Unauthenticated Global Wipes]

### 2. Forensic Findings
- **Legacy Route Survival**: `workflow.start`, `workflow.transition`, `workflow.abort` remain public commands on the dispatcher despite M20 declaring `ClinicalExecutionGatewayService` the sole clinical workflow authority.
- **Dangerous Reset Commands**: `tools.reset` and `platform.reset` allow arbitrary clients with an `epoch_id` to wipe all execution results and active sessions mid-procedure.

---

## PHASE 3 — PUBLIC API / AUTHORITY FORENSICS

| Service | Method | Exposure | Risk | Finding |
| :--- | :--- | :--- | :--- | :--- |
| `WorkflowService` | `transition_phase()` | Public | **CRITICAL** | Checks `_interlock_engine.has_critical_interlock()` globally without session filter |
| `SafetyInterlockEngine` | `has_critical_interlock()` | Public | **CRITICAL** | Iterates `_interlocks.values()` without checking `session_id` |
| `SafetyInterlockEngine` | `has_blocking_interlock()` | Public | **CRITICAL** | Iterates `_interlocks.values()` without checking `session_id` |
| `WorkflowService` | `evict_session()` | Public | **CRITICAL** | Does not evict `_interlock_engine` or `_checkpoint_validator` |
| `GatewayService` | `_connections` | Internal | **HIGH** | Lingering connections survive teardown; `MAX_CONNECTIONS_PER_SESSION` lock |
| `GatewayAuthorizationPolicy`| `authorize_message()`| Public | **HIGH** | No validation that `envelope.payload["session_id"] == session.session_id` |
| `ToolService` | `handle_reset_command()` | Public | **MEDIUM** | Calls `clear()` mid-flight if epoch matches |
| `PersistenceService` | `_sessions` | Internal | **HIGH** | No session eviction hook; 16-session hard capacity lockout |

---

## PHASE 4 — CAPABILITY FORENSICS

- `_ExecutionCapability` in `execution/service.py`:
  - Minted solely by `ClinicalExecutionGatewayService` inside private transaction blocks.
  - Action-bound (`SESSION_TEARDOWN`, `NAVIGATION_EXECUTE`, `REGISTRATION_VERIFY`, etc.).
  - Session-bound (`session_id`).
  - Single-use and invalidated immediately upon transaction completion or error.
  - Replay and cross-session misuse are completely prevented.
- `_RecoveryTransactionCapability` in `workflow/_transaction.py`:
  - Minted solely by `WorkflowService.resume_from_recovery()`.
  - Session-bound and sequence-bound.
  - Invalidated immediately upon commit.
  - **Vulnerability**: `stage_recovery_clearance()` calls `self.has_critical_interlock()`, which is global across all sessions. A capability minted for Session A cannot commit if Session B has an active interlock!

---

## PHASE 5 — TRANSACTION / REENTRANCY FORENSICS

- All major clinical services enforce `if self._in_transaction: raise LifecycleError(...)` with `try...finally: self._in_transaction = False`.
- In `WorkflowService.transition_phase`:
  - `sm.abort(sequence_number)` is called *before* raising `WorkflowSafetyInterlockError`.
  - If a critical interlock is detected (even from another session), the workflow state machine is transitioned to `ABORTED`. This is an irreversible clinical state mutation caused by a foreign session's interlock!

---

## PHASE 6 — SESSION / IDENTITY ISOLATION FORENSICS

### 1. Detailed Attack: Session A Interlock -> Session B Workflow Abort
- **Vulnerability Vector**: `SafetyInterlockEngine._interlocks` in `python/holomed/workflow/interlocks.py`.
- **Source Code**:
  ```python
  # python/holomed/workflow/interlocks.py:46-58
  def has_blocking_interlock(self) -> bool:
      return any(
          not it.status and it.severity in (InterlockSeverity.BLOCKING, InterlockSeverity.CRITICAL)
          for it in self._interlocks.values()  # <--- NO SESSION FILTER!
      )

  def has_critical_interlock(self) -> bool:
      return any(
          not it.status and it.severity == InterlockSeverity.CRITICAL
          for it in self._interlocks.values()  # <--- NO SESSION FILTER!
      )
  ```
- **Execution Path**:
  1. Session `SESS-A` runs an anatomical checkpoint evaluation via `workflow_service.evaluate_checkpoint(checkpoint_id="chk-01", session_id="SESS-A", ...)` which fails confidence thresholds.
  2. `SafetyInterlockEngine.register_interlock()` registers the failed interlock `it` (`status=False`, `severity=CRITICAL`, `session_id="SESS-A"`).
  3. Session `SESS-B` attempts to transition from `PATIENT_CONTEXT` to `ANATOMICAL_MAPPING`:
     `workflow_service.transition_phase(session_id="SESS-B", target_phase=WorkflowPhase.ANATOMICAL_MAPPING, sequence_number=1)`.
  4. Step 1 of `transition_phase()` evaluates `self._interlock_engine.has_critical_interlock()`.
  5. `has_critical_interlock()` scans `self._interlocks.values()`, encounters `SESS-A`'s tripped interlock, and returns `True`!
  6. `WorkflowService` invokes `sm.abort(sequence_number, reason="Critical safety interlock tripped")` on `SESS-B`'s state machine and raises `WorkflowSafetyInterlockError`.
  7. **Impact**: Session B's clinical workflow is irreversibly aborted due to an interlock in Session A.

### 2. Detailed Attack: Teardown Leak -> Reused Session ID Abort
- **Vulnerability Vector**: `WorkflowService.evict_session()` in `python/holomed/workflow/service.py:674-685`.
- **Source Code**:
  ```python
  def evict_session(self, session_id: str, capability: Optional[Any] = None) -> bool:
      if self._in_transaction:
          raise WorkflowLifecycleError("Reentrant call to evict_session rejected")
      evicted = False
      if session_id in self._workflows:
          del self._workflows[session_id]
          evicted = True
      if session_id in self._confirmations:
          del self._confirmations[session_id]
          evicted = True
      return evicted
  ```
- **Execution Path**:
  1. Session `SESS-001` registers a tripped interlock in `_interlock_engine._interlocks`.
  2. Surgery finishes; gateway teardown executes `execution.session.teardown(session_id="SESS-001")`.
  3. Gateway invokes `workflow_service.evict_session("SESS-001")`.
  4. `evict_session()` purges `_workflows["SESS-001"]` and `_confirmations["SESS-001"]`, but **does not touch `self._interlock_engine`**.
  5. The tripped interlock from `SESS-001` remains in `_interlock_engine._interlocks` indefinitely.
  6. A subsequent surgery starts on `SESS-001` (or any new session `SESS-002`).
  7. First phase transition calls `has_critical_interlock()`, which returns `True`.
  8. The new surgery is immediately aborted.

---

## PHASE 7 — TEMPORAL / REPLAY FORENSICS

- All gateway execution commands enforce monotonically increasing `sequence_number > self._latest_sequences[session_id]`.
- M25/M26 teardown correctly resets `_latest_sequences` across Navigation, Recovery, Registration, Planning, Safety Gate, Proximity, and Drift.
- However, `AnatomicalCheckpointValidator` has no sequence tracker and no timestamp expiry.

---

## PHASE 8 — SAFETY DECISION INTEGRITY

- `SafetyGateEvaluator` operates correctly based on subsystem status snapshots.
- However, `WorkflowService.transition_phase()` has its own safety check: `if self._interlock_engine.has_critical_interlock(): abort()`.
- Because this check is un-scoped by session, a false-positive safety abort is triggered across session boundaries.

---

## PHASE 9 — CROSS-SUBSYSTEM CONSISTENCY

| Subsystem State | Reality | Invariant Violation |
| :--- | :--- | :--- |
| `WorkflowService` | Interlock Tripped globally | Blocks/Aborts workflows of nominal sessions |
| `SafetyGateService` | Evaluates Session B as `PERMITTED_CLEAR` | `WorkflowService` aborts Session B regardless |
| `GatewayService` | 8 connections stale from Session A | Refuses Session A reconnects even though teardown completed |
| `PersistenceService` | 16 sessions registered in memory | Blocks Session 17 start even though all 16 are closed/torn down |

---

## PHASE 10 — PERSISTENCE / CRASH CONSISTENCY

- Journal files are append-only with SHA-256 integrity hashing and fail-stop recovery.
- Stale durable session records survive in `DurableSessionStore._sessions` up to `MAX_DURABLE_SESSIONS = 16`.
- There is no session pruning/eviction API in `PersistenceService`.

---

## PHASE 11 — FAILURE ATOMICITY

- Gateway teardown uses best-effort failure aggregation (`failures.append(...)`), ensuring that if one subsystem throws an exception, all other subsystems still receive eviction calls.
- However, within `WorkflowService.resume_from_recovery`:
  If `stage_recovery_clearance()` fails due to an external session's interlock, rollback occurs cleanly via `abort_recovery_clearance()`.

---

## PHASE 12 — CAPACITY / RESOURCE FORENSICS

| Component | Limit | Eviction Status | Risk |
| :--- | :--- | :--- | :--- |
| `SafetyInterlockEngine` | Unbounded | **NEVER EVICTED** | Memory leak + Cross-session aborts |
| `AnatomicalCheckpointValidator` | `MAX_REGISTERED_CHECKPOINTS = 64` | **NEVER EVICTED** | Cumulative lockout at 64 checkpoints |
| `GatewayService` | `MAX_CONNECTIONS_PER_SESSION = 8` | **NEVER EVICTED** | Reconnect lockout on reused session |
| `DurableSessionStore` | `MAX_DURABLE_SESSIONS = 16` | **NEVER EVICTED** | Total system crash on 17th session |

---

## PHASE 13 — ERROR SEMANTICS

- All exceptions in M10, M11, M09 are strongly typed subclasses of domain errors (`WorkflowSafetyInterlockError`, `GatewayCapacityError`, `PersistenceCapacityError`).
- In `WorkflowService.transition_phase`, raising `WorkflowSafetyInterlockError` correctly halts the transaction, but doing so for the *wrong session* is a critical semantic failure.

---

## PHASE 14 — DEPENDENCY / AUTHORITY GRAPH

- In M25 and M26, `ClinicalExecutionGatewayService` acts as the coordinator for session teardown.
- `WorkflowService` is already integrated in Step 8 of `execute_session_teardown()`.
- The problem is NOT dependency inversion; the problem is that `WorkflowService.evict_session()` is incomplete because `SafetyInterlockEngine` and `AnatomicalCheckpointValidator` are un-scoped.

---

## PHASE 15 — FROZEN MILESTONE GAP ANALYSIS

- **M10 Workflow**: Incomplete session isolation in `SafetyInterlockEngine` and `AnatomicalCheckpointValidator`.
- **M11 Gateway**: Incomplete session lifecycle tracking; missing payload session validation.
- **M09 Persistence**: Missing session eviction in `DurableSessionStore`.
- **M25 / M26**: Teardown coordinator works properly, but M10's internal eviction was only partially implemented.

---

## PHASE 16 — CANDIDATE M27 IDENTIFICATION

### CANDIDATE 1: Workflow Safety Interlock Scoping & Lifecycle Eviction Hardening (M10)
- **Problem**: `SafetyInterlockEngine` lacks session isolation. Tripped interlocks in Session A abort Session B. `WorkflowService.evict_session()` does not purge interlocks or checkpoints, permanently contaminating reused sessions.
- **Source Evidence**:
  - `python/holomed/workflow/interlocks.py:46-70`
  - `python/holomed/workflow/checkpoints.py:27-34`
  - `python/holomed/workflow/service.py:352-364, 674-685`
- **Reproducible Impact**: Session A trips a critical checkpoint interlock; Session B calls `transition_phase()` and is immediately aborted. Reusing Session A causes immediate abort.
- **Severity**: **CRITICAL** (Patient safety & false procedural abort).
- **Affected Services**: `WorkflowService`, `SafetyInterlockEngine`, `AnatomicalCheckpointValidator`.
- **Minimum Reopen Set**:
  1. `python/holomed/workflow/interlocks.py`
  2. `python/holomed/workflow/checkpoints.py`
  3. `python/holomed/workflow/service.py`
- **Frozen Boundaries**: Gateway, Navigation, Proximity, Drift, Registration, Planning, Platform, Safety Gate remain strictly frozen.
- **Why Not Already Solved**: M25 added `evict_session` to `WorkflowService`, but only evicted `_workflows` and `_confirmations`, leaving `_interlocks` and `_checkpoints` untouched and globally scoped.

### CANDIDATE 2: Client Gateway Ingress Lifecycle & Session Envelope Isolation (M11)
- **Problem**: `GatewayService` retains client connections after session teardown, hitting `MAX_CONNECTIONS_PER_SESSION = 8` on reuse. `GatewayAuthorizationPolicy` does not bind `payload["session_id"]` to authenticated `session.session_id`.
- **Severity**: **HIGH** (Availability & Transport spoofing).
- **Minimum Reopen Set**: `python/holomed/gateway/service.py`, `python/holomed/gateway/authorization.py`.

### CANDIDATE 3: Durable Persistence Session Capacity Eviction & Teardown Coordination (M09)
- **Problem**: `DurableSessionStore` has `MAX_DURABLE_SESSIONS = 16`. Completed/evicted sessions are never removed from `self._sessions`, crashing the 17th session.
- **Severity**: **HIGH** (Availability).
- **Minimum Reopen Set**: `python/holomed/persistence/sessions.py`, `python/holomed/persistence/service.py`, `python/holomed/execution/service.py`.

---

## PHASE 17 — HOSTILE SELF-CHALLENGE

### Challenge on Candidate 1 (Workflow Interlocks):
- *Is this merely theoretical?*
  **No**. `SafetyInterlockEngine.has_critical_interlock()` literally iterates `self._interlocks.values()` without accepting or filtering by `session_id`. Any unit test instantiating two sessions where Session 1 registers an interlock will reliably abort Session 2.
- *Is it already prevented elsewhere?*
  **No**. `SafetyGateEvaluator` evaluates per-session snapshots, but `WorkflowService.transition_phase()` has its own independent pre-check at line 352 that calls `self._interlock_engine.has_critical_interlock()`.
- *Does fixing it require reopening M25/M26?*
  **No**. `ClinicalExecutionGatewayService` already calls `workflow_service.evict_session(session_id)`. M27 only requires hardening M10 internally so that `evict_session` purges the interlocks and checkpoints of that session, and scoping `has_critical_interlock(session_id)` to the active session.

### Challenge on Candidates 2 & 3:
- Candidate 2 (Gateway) and Candidate 3 (Persistence) are real capacity issues, but Candidate 1 is a **direct clinical correctness and patient safety defect** (false procedural abort across surgical cases). Candidate 1 must take absolute precedence.

---

## PHASE 18 — M27 JUSTIFICATION TEST

1. **Concrete Source Evidence**: Verified in `python/holomed/workflow/interlocks.py:46-70` and `python/holomed/workflow/service.py:352-364, 674-685`.
2. **Meaningful Impact**: Prevents cross-session surgical workflow aborts and session reuse aborts.
3. **Clearly Bounded Fix**: Strictly confined to `python/holomed/workflow/interlocks.py`, `python/holomed/workflow/checkpoints.py`, and `python/holomed/workflow/service.py`.
4. **Testable Acceptance Criteria**:
   - Interlocks registered for Session A cannot cause `has_critical_interlock(session_B)` to return True.
   - Teardown of Session A purges all interlocks and checkpoints belonging to Session A.
   - Reused Session A begins with clean interlock state.
   - All 1555 platform tests pass without regressions.
5. **Acceptable Dependency Scope**: Preserves M25/M26 gateway coordination; 0 modifications to other packages.

---

## FINAL CLASSIFICATION

```
==================================================
M27_JUSTIFIED
==================================================
```

**Recommended Milestone M27**:  
**Workflow Safety Interlock Scoping & Lifecycle Eviction Hardening**

*Strict Mode Preserved: ZERO source changes, ZERO test changes, ZERO commits, ZERO pushes.*
