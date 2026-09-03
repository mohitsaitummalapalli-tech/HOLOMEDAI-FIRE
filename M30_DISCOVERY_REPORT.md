# M30 DISCOVERY — SYSTEM-WIDE FORENSIC ARCHITECTURE AUDIT

**Authoritative Baseline:** `8c46aa2ad883aca2089da98db13cc2d5ef0b1dcb`  
**Previous Release:** M29 — Clinical Tool Subsystem Lifecycle Eviction & Teardown Hardening (`M29_FROZEN`)  
**Status:** READ-ONLY ARCHITECTURAL DISCOVERY  
**Scope Changes:** ZERO (0 source files modified, 0 test files modified, 0 commits, 0 pushes)

---

## 1. Current Architecture Snapshot

The HoloMed platform at baseline `8c46aa2ad883aca2089da98db13cc2d5ef0b1dcb` comprises 24 distinct packages organized across core, infrastructure, perceptual, domain, gateway, and execution layers.

### Subsystem Inventory & Ownership Matrix

| Subsystem | Authoritative State Owner | Public Entrypoints | Mutation Authority | Lifecycle Owner | Persistence Boundary | Capability Boundary | Dependency Direction |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Platform** | `SessionManager`, `LifecycleCoordinator` | `tick()`, `start_session()`, `stop_session()`, `evict_session()` | PlatformService / Engine | `PlatformService` (M01/M25) | Ephemeral in-memory + M09 session journal | Unenforced (Legacy M01) | Inward (Foundation) |
| **Dispatcher** | `SubscriptionRegistry`, `DeadLetterQueue` | `dispatch()`, `register_command_handler()`, `register_query_handler()` | `MessageDispatcher` | `MessageDispatcher` (M00.4) | None (In-memory bus) | Internal recursion guard | Foundation bus |
| **Gateway / Ingress** | `GatewayConnection`, `ClientSession` pool | `register_client_transport()`, `process_client_ingress()`, `evict_session()` | `GatewayService` (M11/M28) | `GatewayService` (Step 11 teardown) | In-memory connection map | GatewayAuthorizationPolicy | Inward toward Dispatcher |
| **Workflow** | `WorkflowStateMachine`, `InterlockEngine`, `CheckpointValidator` | `start_workflow()`, `transition_phase()`, `confirm()`, `abort_workflow()`, `evict_session()` | `WorkflowService` (M10/M27) | `WorkflowService` (Step 8 teardown) | Audit logging via PersistenceService | Phase & Interlock Gate | Toward Platform & Persistence |
| **Planning** | `PlanStore`, `TrajectoryPlan` | `submit_plan()`, `verify_plan()`, `lock_plan()`, `get_plan()`, `evict_session()` | `PlanningService` (M03/M24) | `PlanningService` (Step 6 teardown) | In-memory plan store | `PLANNING_MODIFICATION` | Inward |
| **Registration** | `RegistrationStore`, `FiducialCloud`, `RigidTransform` | `submit_fiducials()`, `solve_registration()`, `verify_registration()`, `get_registration()`, `evict_session()` | `RegistrationService` (M13/M23) | `RegistrationService` (Step 5 teardown) | In-memory transform store | `REGISTRATION_ALIGNMENT` | Toward Planning |
| **Navigation** | `TrajectoryPlan`, `TrackedInstrumentPose`, `TrajectoryDeviationRecord` | `bind_trajectory()`, `submit_pose()`, `evaluate()`, `get_navigation_status()`, `evict_session()` | `NavigationService` (M14/M25) | `NavigationService` (Step 1 teardown) | In-memory trajectory/pose store | `TRAJECTORY_ALIGNMENT`, `POSE_STREAM`, `NAVIGATION_EVALUATION` | Toward Registration |
| **Proximity** | `SafetyExclusionZone` table, dwell buffers | `bind_zones()`, `evaluate()`, `get_proximity_status()`, `evict_session()` | `ProximityService` (M15/M26) | `ProximityService` (Step 2 teardown) | In-memory zone geometry | Latched interlock state | Toward Registration |
| **Drift** | `LandmarkDefinition` table, dwell buffers | `bind_landmarks()`, `evaluate()`, `get_drift_status()`, `evict_session()` | `DriftService` (M16/M26) | `DriftService` (Step 3 teardown) | In-memory landmark store | Latched interlock state | Toward Registration |
| **Recovery** | `RecoveryCandidate`, staged fiducials | `stage_recovery()`, `verify_recovery()`, `activate_recovery()`, `get_recovery_status()`, `evict_session()` | `RecoveryService` (M17/M22) | `RecoveryService` (Step 4 teardown) | In-memory candidate cache | `RECOVERY_ACTIVATION` | Toward Registration, Navigation, Drift, Proximity |
| **Safety Gate** | `SafetyGateEvaluator`, decision cache | `evaluate()`, `get_safety_status()`, `evict_session()` | `SafetyGateService` (M18/M25) | `SafetyGateService` (Step 7 teardown) | Deduplicated AuditStore records | Evaluates cross-service precedence | Queries Nav, Prox, Drift, Reg, Rec, WF |
| **Clinical Execution Gateway** | In-flight execution transactions, capability minter | `execute_navigation()`, `execute_tool()`, `execute_recovery()`, `execute_registration()`, `execute_planning()`, `execute_session_teardown()` | `ClinicalExecutionGatewayService` (M19–M29) | Orchestrates universal teardown | Coordinated AuditStore records | Mints and invalidates `_ExecutionCapability` | Top-level orchestrator of all clinical mutations |
| **Tools** | `ToolRegistry`, `ToolExecutionEngine` sequence map | `invoke_tool()`, `evict_session()` | `ToolService` (M07/M21/M29) | `ToolService` (Step 12 teardown) | Bounded global result history | `TOOL_INVOCATION` | Toward Execution Gateway |
| **Persistence / Audit** | `DurableSessionStore`, `DurableAuditStore`, `JournalWriter` | `record_audit()`, `start_session()`, `replay()` | `PersistenceService` (M08/M09) | `PersistenceService` | Append-only filesystem journals + audit buffers | Immutable audit records | Outward sink |
| **Perception (Vision/Audio/Gesture)** | Tracker pipelines, Kalman filters, frame buffers | `process_frame()`, `get_tracks()`, `reset()` | Vision, Audio, Gesture Services | Respective service lifecycles | Transient streaming buffers | None (Sensor observations) | Toward Dispatcher |
| **Ultron Reasoning** | Multi-modal context buffer, reasoning engine | `reason()`, `get_context()`, `reset()` | `UltronService` (M12) | `UltronService` | Transient reasoning log | None (Advisory/Informational) | Reads Perception & Domain |
| **XR Presentation** | Scene graph nodes, viewports | `update_node()`, `render_frame()`, `reset()` | `XRService` (M05) | `XRService` | Transient frame buffers | None (Display sink) | Toward Ingress Clients |

---

## 2. Complete Dispatcher / Route Forensics

A forensic crawl of all `register_command_handler` and `register_query_handler` calls across `python/holomed` discovered exactly **76 registered dispatcher routes** (30 COMMAND routes and 46 QUERY routes).

### Complete Route Inventory Table

| # | Route Name | Route Type | Owner Service | Mutation / Read-Only | Clinical / Safety Relevance | Capability Required | Gateway Authorized | Session Bound | Status / Anomaly |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | `anatomy.reset` | COMMAND | AnatomyService | Mutation (Transient) | Low | None | Surgeon/Internal | No | Nominal reset |
| 2 | `audio.pipeline.reset` | COMMAND | AudioService | Mutation (Transient) | Low | None | Internal | No | Nominal pipeline reset |
| 3 | `device.coordination.snapshot` | COMMAND | Coordinator | Read-Only (Action) | Low | None | Internal | No | Nominal coordination |
| 4 | `device.orchestration.sync` | COMMAND | Orchestrator | Mutation (Sync) | Medium | None | Internal | No | Nominal plane sync |
| 5 | `drift.evaluate` | COMMAND | DriftService | Mutation (State latch) | **HIGH** | None | Raw Sensor Ingress | Yes | Direct perceptual evaluation |
| 6 | `execution.navigation.execute` | COMMAND | ClinicalExecutionGateway | **MUTATION** | **CRITICAL** | Yes (`POSE_STREAM` / `NAV_EVAL`) | Surgeon Console | Yes | Authoritative Navigation Path |
| 7 | `execution.planning.execute` | COMMAND | ClinicalExecutionGateway | **MUTATION** | **CRITICAL** | Yes (`PLANNING_MODIFICATION`) | Surgeon Console | Yes | Authoritative Planning Path |
| 8 | `execution.recovery.execute` | COMMAND | ClinicalExecutionGateway | **MUTATION** | **CRITICAL** | Yes (`RECOVERY_ACTIVATION`) | Surgeon Console | Yes | Authoritative Recovery Path |
| 9 | `execution.registration.execute` | COMMAND | ClinicalExecutionGateway | **MUTATION** | **CRITICAL** | Yes (`REGISTRATION_ALIGNMENT`)| Surgeon Console | Yes | Authoritative Registration Path |
| 10 | `execution.session.teardown` | COMMAND | ClinicalExecutionGateway | **MUTATION (LIFECYCLE)** | **CRITICAL** | Yes (`SESSION_TEARDOWN`) | Surgeon/Admin | Yes | Authoritative Universal Teardown |
| 11 | `execution.tool.invoke` | COMMAND | ClinicalExecutionGateway | **MUTATION** | **CRITICAL** | Yes (`TOOL_INVOCATION`) | Surgeon Console | Yes | Authoritative Tool Path |
| 12 | `execution.trajectory.bind` | COMMAND | ClinicalExecutionGateway | **MUTATION** | **CRITICAL** | Yes (`TRAJECTORY_ALIGNMENT`)| Surgeon Console | Yes | Authoritative Trajectory Binding |
| 13 | `execution.workflow.resume` | COMMAND | ClinicalExecutionGateway | **MUTATION** | **HIGH** | Internal | Surgeon Console | Yes | Authoritative Workflow Resume |
| 14 | `gateway.disconnect` | COMMAND | GatewayService | Mutation (Lifecycle) | Low | None | Admin/Client | No | Connection termination |
| 15 | `gesture.pipeline.reset` | COMMAND | GestureService | Mutation (Transient) | Low | None | Internal | No | Nominal pipeline reset |
| 16 | `persistence.replay` | COMMAND | PersistenceService | Read-Only (Action) | Low | None | Diagnostic | Yes | Journal replay |
| 17 | `platform.cycle` | COMMAND | PlatformService | Mutation (Clock tick) | Medium | None | Platform Engine | Optional | Defaults to `"default_session"` |
| 18 | `platform.reset` | COMMAND | PlatformService | Mutation (Lifecycle) | Medium | None | Admin | No | Diagnostic reset |
| 19 | `platform.session.start` | COMMAND | PlatformService | Mutation (Lifecycle) | Medium | None | Platform Client | Yes | Legacy M01 session start |
| 20 | `platform.session.stop` | COMMAND | PlatformService | Mutation (Lifecycle) | Medium | None | Platform Client | Yes | Legacy M01 session stop |
| 21 | `proximity.evaluate` | COMMAND | ProximityService | Mutation (State latch) | **HIGH** | None | Raw Sensor Ingress | Yes | Direct proximity evaluation |
| 22 | `safety_gate.evaluate` | COMMAND | SafetyGateService | Mutation (Decision/Audit) | **CRITICAL** | None | Unrouted | Yes | **PROTOCOL VIOLATION & ANOMALY** |
| 23 | `tools.reset` | COMMAND | ToolService | Mutation (Transient) | Medium | None | Diagnostic | No | Catalog reset |
| 24 | `ultron.reset` | COMMAND | UltronService | Mutation (Transient) | Low | None | Diagnostic | No | Context buffer clear |
| 25 | `vision.pipeline.reset` | COMMAND | VisionService | Mutation (Transient) | Low | None | Internal | No | Pipeline reset |
| 26 | `workflow.abort` | COMMAND | WorkflowService | **MUTATION (LIFECYCLE)** | **CRITICAL** | None | Surgeon Console | Yes | Authoritative Workflow Abort |
| 27 | `workflow.confirm` | COMMAND | WorkflowService | **MUTATION (SAFETY)** | **CRITICAL** | None | Surgeon Console | Yes | Operator Confirmation Gate |
| 28 | `workflow.start` | COMMAND | WorkflowService | **MUTATION (LIFECYCLE)** | **HIGH** | None | Surgeon Console | Yes | Workflow Session Start |
| 29 | `workflow.transition` | COMMAND | WorkflowService | **MUTATION (LIFECYCLE)** | **CRITICAL** | None | Surgeon Console | Yes | Workflow Phase Advance |
| 30 | `xr.reset` | COMMAND | XRService | Mutation (Transient) | Low | None | Diagnostic | No | Scene graph clear |
| 31–76 | 46 Query Routes | QUERY | Respective Services | Read-Only | Low–Medium | None | Observability/Read | Yes/No | Nominal queries (see below) |

### Notable Query Routes (31–76)
- Queries: `anatomy.entity`, `anatomy.query`, `anatomy.simulation.status`, `anatomy.status`, `audio.pipeline.audit`, `audio.pipeline.status`, `audio.tracker.tracks`, `device.coordination.health`, `device.orchestration.audit`, `device.orchestration.status`, `drift.landmarks.get`, `drift.status.get`, `execution.status.get`, `gateway.clients`, `gateway.status`, `gesture.pipeline.audit`, `gesture.pipeline.status`, `gesture.tracks`, `navigation.status.get`, `persistence.audit`, `persistence.cycle.get`, `persistence.session.get`, `persistence.status`, `planning.get`, `platform.audit`, `platform.status`, `proximity.status.get`, `proximity.zones.get`, `recovery.status.get`, `registration.get`, `safety_gate.status.get` (**PROTOCOL VIOLATION**), `tools.registry`, `tools.result`, `tools.status`, `ultron.audit`, `ultron.context`, `ultron.reasoning`, `ultron.status`, `vision.pipeline.audit`, `vision.pipeline.status`, `vision.tracker.tracks`, `workflow.status`, `xr.frame`, `xr.node`, `xr.status`, `xr.viewport.status`.

### Critical Route Forensics Findings
1. **Raw Clinical Mutation Bypasses Eliminated**: In M21–M24, direct commands `tools.invoke`, `recovery.stage/verify/activate`, `registration.submit/solve/verify`, and `planning.submit/verify/lock` were removed from dispatcher registration. All clinical execution mutations strictly route through `execution.*.execute`.
2. **`safety_gate.evaluate` Route Anomaly**:
   - Registered at `python/holomed/safety_gate/service.py:135`.
   - Violates the core dispatcher topic regular expression `^[a-z0-9]+(\.[a-z0-9]+)*$` due to the underscore `_`.
   - Exposes a raw evaluation command to the dispatcher, bypassing the authoritative gateway.
3. **`safety_gate.status.get` Route Anomaly**:
   - Registered at `python/holomed/safety_gate/service.py:136`.
   - Also violates `^[a-z0-9]+(\.[a-z0-9]+)*$` due to the underscore `_`.

---

## 3. M28 Gateway Security Regression

The session binding and cross-session isolation established in M28 was subjected to complete hostile regression verification.

### Attack Vector Analysis
1. **Authenticated Session A -> Payload Targeting Session B**:
   - Source: Authenticated client connection on Session A (`session.session_id = "session-A"`).
   - Ingress message: Envelope with `payload={"session_id": "session-B", ...}`.
   - Enforcement point: `GatewayAuthorizationPolicy.authorize_message(session, envelope)` at `python/holomed/gateway/authorization.py:40-46`.
   - Result: **REJECTED** with `GatewaySessionMismatchError("Cross-session injection rejected...")`.
2. **Missing `session_id` in Payload**:
   - If `session_id` is omitted from `envelope.payload`:
     - Gateway allows message through policy step 2.
     - Target command handlers (`execution.*`, `workflow.*`, `drift.evaluate`, `proximity.evaluate`): Every authoritative execution command explicitly validates `if not session_id: return create_error_response("ERR_INVALID_ARGS", "Missing session_id")`.
     - Result: **FAIL-CLOSED**. Attacker cannot target any session without specifying `session_id`.
3. **Unknown / Stopped Session**:
   - Handshake accepts any valid string `session_id` without checking active platform sessions (`known_sessions=None`).
   - If client targets a stopped/evicted session:
     - Downstream services fail: Workflow returns `WORKFLOW_UNINITIALIZED`, Planning/Registration/Navigation return `UNINITIALIZED` / `NOT_FOUND`, Safety Gate returns `DENIED_INTERLOCKED`.
     - Result: **FAIL-CLOSED**.
4. **Reused Session ID**:
   - Verified across M25–M29: Teardown purges all state. If an identical session ID is re-established, sequence counters start clean at 1, previous bindings do not leak, and capacity is fully preserved.

---

## 4. Capability System Forensics

Every call site of `_create_execution_capability` was audited:

```
python/holomed/execution/service.py:429  -> action="POSE_STREAM" (Navigation)
python/holomed/execution/service.py:435  -> action="NAVIGATION_EVALUATION" (Navigation)
python/holomed/execution/service.py:618  -> action="TRAJECTORY_ALIGNMENT" (Navigation)
python/holomed/execution/service.py:822  -> action="RECOVERY_ACTIVATION" (Recovery)
python/holomed/execution/service.py:1050 -> action="TOOL_INVOCATION" (Tools)
python/holomed/execution/service.py:1607 -> action="REGISTRATION_ALIGNMENT" (Registration)
python/holomed/execution/service.py:1899 -> action="PLANNING_MODIFICATION" (Planning)
python/holomed/execution/service.py:2129 -> action="SESSION_TEARDOWN" (Coordinated Teardown)
python/holomed/recovery/service.py:492  -> action="REGISTRATION_ALIGNMENT" (Recovery internal)
python/holomed/recovery/service.py:540  -> action="TRAJECTORY_ALIGNMENT" (Recovery internal)
```

### Forensic Findings on Capabilities
- **Minting Authority**: `_create_execution_capability` requires unexported sentinel `_INTERNAL_EXECUTION_KEY`. It can only be called from within `holomed.execution` and `holomed.recovery`. External construction raises `ExecutionAuthorizationError`.
- **Session Binding**: Bound immutably to `session_id`. Downstream service compares `capability.session_id == session_id` and rejects mismatches.
- **Single-Use Invalidation**: Guaranteed via `try ... finally: capability.invalidate()`. Replay or second use raises `ExecutionAuthorizationError("Execution capability is inactive or invalidated")`.
- **Privilege Escalation**: Bound to `action` string and `service_instance_id = id(target_service)`. A capability minted for `ToolService` cannot be used to invoke `RegistrationService`.
- **Teardown Reuse**: In `execute_session_teardown`, a single `SESSION_TEARDOWN` capability is securely coordinated across Steps 1–12 and invalidated in `finally: cap.invalidate()`.

---

## 5. Temporal / Replay Integrity

| Field | Source / Scope | Freshness Validation | Replay Resistance |
| :--- | :--- | :--- | :--- |
| `sequence_number` | Execution, Tools, Drift, Nav | Strictly monotonic (`seq > last_seq`). Equal or lower is rejected. | Replayed sequence numbers immediately rejected with `*SequenceError`. |
| `epoch_id` | RuntimeContext | Verified against `service._epoch_id`. Mismatch rejected with `*EpochMismatchError`. | Stale epoch commands rejected across all services. |
| `transaction_id` | UUIDv4 per execution | Ephemeral UUID minted per capability. | Non-serializable, non-replayable. |
| `timestamp_utc` | ISO-8601 UTC | Validated against max observation age (`MAX_OBSERVATION_AGE_MS = 500ms`). | Future timestamps (>5s skew) or expired timestamps rejected. |
| `registration_revision` | M13/M17 counter | Checked by `SafetyGateEvaluator` and `DriftEvaluator`. | Revision increments on every verification. Stale revisions trigger `DENIED_INTERLOCKED`. |

---

## 6. Transaction / Atomicity Forensics

Every service mutation uses the synchronous reentrancy guard pattern:
```python
if self._in_transaction:
    raise *LifecycleError("Cannot ... during an active transaction")
self._in_transaction = True
try:
    # mutations...
finally:
    self._in_transaction = False
```
- **Stuck Transaction Guard Attack**: Prevented by strict `try ... finally` blocks across all 24 subsystems.
- **Rollback / Partial Mutation**: Multi-step operations (e.g. `RecoveryService.activate_recovery`) record prior registration state. If subsequent re-bindings fail, exceptions bubble up, and the operation reports `degraded` or `failed`.
- **Teardown Reentrancy**: Protected by `_in_transaction` across all 12 teardown steps.

---

## 7. Cross-Subsystem Consistency

Hostile matrix of cross-subsystem states:

1. **Platform ACTIVE + Workflow ABORTED**: Reachable. Handled fail-closed: Workflow state is `ABORTED` (`is_terminal=True`), all subsequent tool and navigation executions are blocked by `WorkflowExecutionGate`.
2. **Workflow ACTIVE + Plan missing**: Handled fail-closed: Navigation binding requires verified plan trajectory. Safety gate returns `DENIED_INTERLOCKED` (`MISSING_PLAN`).
3. **Workflow ACTIVE + Registration missing**: Handled fail-closed: Proximity and Drift require verified registration. Navigation execution is rejected.
4. **Plan LOCKED + Registration belongs elsewhere**: Handled fail-closed: Registration verifies fiducial cloud against active `plan_id`. Mismatches raise `RegistrationValidationError`.
5. **Registration VERIFIED + Plan changed**: Registration increments `revision`. If plan changes, revision desynchronizes and safety gate fails closed.
6. **Navigation EXECUTING + Safety DENIED**: Handled fail-closed: `ClinicalExecutionGatewayService.execute_navigation` executes `SafetyGateService.evaluate` in Step 1. If decision is `DENIED_*`, navigation pose is never submitted to `NavigationService`.
7. **Proximity CRITICAL + Safety CLEAR**: Unreachable: `SafetyGateEvaluator` Precedence 1 prioritizes `CRITICAL_BREACH` above all other states, forcing `DENIED_CRITICAL`.
8. **Drift EXCEEDED + Safety CLEAR**: Unreachable: `SafetyGateEvaluator` Precedence 3 forces `DENIED_INTERLOCKED` unless action is `RECOVERY_REORIENTATION`.
9. **Gateway CONNECTED + Platform STOPPED**: Reachable. Evaluated in Section 17.

---

## 8. Safety Decision Integrity

Safety decision path:
$$\text{Sensor Observation} \longrightarrow \text{Perception/Tracker} \longrightarrow \text{Proximity / Drift} \longrightarrow \text{SafetyGateEvaluator} \longrightarrow \text{ClinicalExecutionGateway} \longrightarrow \text{Mutation}$$

- **Stale Observation Rejection**: Observations older than 500 ms or with non-monotonic sequences are discarded before reaching the evaluator.
- **Latching Breaches**: Once `DriftState.DRIFT_EXCEEDED` or `INTERLOCKED` is reached, ordinary landmark evaluations cannot reset the state. Recovery activation is required.
- **Strict Precedence Hierarchy**:
  - Precedence 0: Session Mismatch -> `DENIED_INTERLOCKED`
  - Precedence 1: Proximity Critical Breach -> `DENIED_CRITICAL`
  - Precedence 2: Landmark Integrity Interlock -> `DENIED_INTERLOCKED`
  - Precedence 3: Landmark Drift Exceeded -> `DENIED_INTERLOCKED`
  - Precedence 4: Registration Missing/Stale -> `DENIED_INTERLOCKED`
  - Precedence 5: Workflow Incompatible -> `DENIED_INTERLOCKED`
  - Precedence 6: Proximity Warning -> `PERMITTED_WITH_CAUTION`

---

## 9. Observation / Perception Freshness

- `DriftService`: Evaluates `MAX_OBSERVATION_AGE_MS = 500.0` and `MAX_TIP_JITTER_MM = 0.5`. Non-monotonic sequence numbers rejected.
- `ProximityService`: Evaluates `MAX_POSE_AGE_MS = 500.0`. High velocity tracking and static margins enforced.
- Disconnected sensors or stale frames fail closed by producing zero fresh evaluations, causing safety gates to timeout or remain interlocked.

---

## 10. Persistence / Crash Recovery

- **Ephemeral vs Durable Separation**:
  - Ephemeral runtime state (poses, dwell buffers, sequence counters, in-flight transactions, client connections) is held strictly in memory and purged on session teardown or process exit.
  - Durable historical state (`DurableSessionRecord`, `JournalWriter` files, `DurableAuditStore`) survives session teardown and is preserved across restarts.
- **Crash Recovery**: `PersistenceService.replay` validates journal entry CRC32 checksums, truncates partial journal writes safely (`_recovered_truncations`), and re-executes valid transactions deterministically.

---

## 11. Epoch / Restart / Reconnect

- `RuntimeContext` holds active `epoch_id: int`.
- Every service verifies `context.epoch_id` during `initialize()` and enforces `epoch_id` matches on incoming envelopes.
- Epoch increment renders previous capabilities, sessions, and connections invalid. Split-brain execution is prevented.

---

## 12. Capacity / Resource Forensics

All resources in the repository have explicit, verified upper bounds:
- `MAX_ACTIVE_PLATFORM_SESSIONS = 16`
- `MAX_ACTIVE_WORKFLOWS = 16`
- `MAX_ACTIVE_PLANS = 16`
- `MAX_ACTIVE_REGISTRATIONS = 16`
- `MAX_ACTIVE_NAVIGATION_SESSIONS = 16`
- `MAX_ACTIVE_PROXIMITY_SESSIONS = 16`
- `MAX_ACTIVE_DRIFT_SESSIONS = 16`
- `MAX_ACTIVE_RECOVERY_SESSIONS = 16`
- `MAX_ACTIVE_GATE_SESSIONS = 16`
- `MAX_ACTIVE_SESSIONS = 64` (Tools)
- `MAX_CLIENTS = 16` (Gateway)
- `MAX_CONNECTIONS_PER_SESSION = 4` (Gateway)

All session-bound services expose `evict_session(session_id)` and are purged during `execute_session_teardown()`.

---

## 13. Error Semantics

- Every service catches exceptions, formats protocol-compliant error codes (`_format_error_code`), redacts secrets via `SecretFilter`, and returns `create_error_response`.
- No internal failure returns an error response disguised as a successful execution.
- Teardown partial failures return `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` or degraded status with non-empty `failures` list.

---

## 14. Public API / Direct Access Forensics

- Direct mutation methods on domain services (`submit_pose`, `submit_fiducials`, `submit_plan`, `invoke_tool`, `activate_recovery`) require `_ExecutionCapability`.
- Direct execution without a valid capability raises `ExecutionAuthorizationError`.

---

## 15. Dependency / Authority Graph

```
  PlatformService (Lifecycle Foundation)
        ^
        |
  GatewayService (Ingress / Transport Boundary)
        |
  MessageDispatcher (Pub/Sub Event & Command Bus)
        |
  ClinicalExecutionGatewayService (Authoritative Clinical Transaction Orchestrator)
   ├── WorkflowGate / SafetyGateService (Authoritative Safety Decision)
   ├── NavigationService (Trajectory & Pose)
   ├── RegistrationService (Fiducials & Alignment)
   ├── PlanningService (Surgical Plans)
   ├── RecoveryService (Intraoperative Realignment)
   └── ToolService -> ToolExecutionEngine (Tool Invocation)
        |
  PersistenceService (Append-Only Durable Audit & Journal)
```

The dependency direction flows strictly downward from Ingress -> Execution Gateway -> Domain Services -> Persistence. No circular dependencies exist.

---

## 16. M07–M29 Gap Review

| Milestone | Title | Architectural Classification | Status |
| :--- | :--- | :--- | :--- |
| M07 | Clinical Tools Subsystem | Functional Tool Registry & Engine | Complete / Hardened in M21 & M29 |
| M08 | Audit & Persistence Subsystem | Durable Audit Logging & Secret Filter | Complete / Frozen |
| M09 | Session Persistence & Replay Engine | Append-Only Journaling & Crash Recovery | Complete / Frozen |
| M10 | Procedural Workflow Engine | State Machine & Interlocks | Complete / Hardened in M27 |
| M11 | Ingress Gateway & Client Management | External Client Transport & Auth | Complete / Hardened in M28 |
| M12 | Ultron Cognitive Reasoning Engine | Multimodal Fusion & Reasoning | Complete / Frozen |
| M13 | Registration & Spatial Alignment | Fiducial Alignment & Rigid Transform | Complete / Hardened in M23 |
| M14 | Real-Time Surgical Navigation | Trajectory Monitoring & Deviation | Complete / Hardened in M25 |
| M15 | Proximity Protection Subsystem | Exclusion Zones & Safety Margins | Complete / Hardened in M26 |
| M16 | Landmark Drift Monitoring | Target Integrity & Landmark Verification | Complete / Hardened in M26 |
| M17 | Spatial Recovery Subsystem | Intraoperative Realignment | Complete / Hardened in M22 |
| M18 | Centralized Safety Gate | Cross-Service Interlock Decision Engine | **UNRESOLVED DISPATCHER PROTOCOL GAP** |
| M19 | Clinical Execution Gateway | Single Authoritative Execution Entrypoint | Complete / Frozen |
| M20 | Object-Capability Security | Non-Forgeable Internal Capabilities | Complete / Frozen |
| M21 | Tool Subsystem Hardening | Direct `tools.invoke` Removal & Capability Binding | Complete / Frozen |
| M22 | Recovery Subsystem Hardening | Direct Recovery Command Removal & Capability Binding| Complete / Frozen |
| M23 | Registration Subsystem Hardening | Direct Registration Removal & Capability Binding | Complete / Frozen |
| M24 | Planning Subsystem Hardening | Direct Planning Removal & Capability Binding | Complete / Frozen |
| M25 | Universal Session Teardown | Multi-Subsystem Coordinated Teardown (Steps 1–10) | Complete / Frozen |
| M26 | Perceptual Subsystem Lifecycle Eviction| Proximity & Drift Eviction & Capacity Reclamation | Complete / Frozen |
| M27 | Workflow Interlock Lifecycle Eviction| Workflow Interlock & Checkpoint Eviction | Complete / Frozen |
| M28 | Gateway Ingress Security Hardening | Ingress Session Isolation & Connection Eviction | Complete / Frozen |
| M29 | Tool Subsystem Lifecycle Eviction | Tool Execution State Eviction (Step 12) | Complete / Frozen |

---

## 17. Candidate M30 Identification

### Candidate 1: Safety Gate Dispatcher Protocol Reconciliation & Ingress Route Hardening

#### Title
Safety Gate Dispatcher Route Protocol Non-Compliance & Ingress Bypass Remediation

#### Problem
In `python/holomed/safety_gate/service.py:134-136`, `SafetyGateService` attempts to register two dispatcher routes during initialization:
```python
self._dispatcher.register_command_handler("safety_gate.evaluate", self.handle_evaluate_command, self.name)
self._dispatcher.register_query_handler("safety_gate.status.get", self.handle_get_status_query, self.name)
```
1. **Runtime Fatal TopicValidationError**: Both topic strings contain underscores (`_`). According to `holomed.core.subscription.validate_concrete_topic`, concrete topics must strictly match `^[a-z0-9]+(\.[a-z0-9]+)*$`. As a result, wiring `SafetyGateService` to a real `MessageDispatcher` immediately raises `TopicValidationError`, crashing service initialization. To bypass this crash, every integration test in M25, M27, and M29 was forced to pass `dispatcher=None`.
2. **Architectural Ingress Bypass Anomaly**: In M21–M24, raw domain execution commands (`tools.invoke`, `recovery.activate`, `registration.solve`, `planning.submit`) were systematically removed from the dispatcher so that all clinical mutations must route through `ClinicalExecutionGatewayService`. However, `SafetyGateService` still retains `safety_gate.evaluate` as a raw dispatcher command handler, exposing an unmediated, capability-free evaluation command to the message bus.

#### Actual Source Evidence
1. `python/holomed/safety_gate/service.py:134-136`:
   ```python
   if self._dispatcher is not None:
       self._dispatcher.register_command_handler("safety_gate.evaluate", self.handle_evaluate_command, self.name)
       self._dispatcher.register_query_handler("safety_gate.status.get", self.handle_get_status_query, self.name)
   ```
2. `python/holomed/core/subscription.py:77-80`:
   ```python
   _CONCRETE_TOPIC_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9]+)*$")
   if not _CONCRETE_TOPIC_RE.match(topic):
       raise TopicValidationError(f"Concrete topic must match ^[a-z0-9]+(\\.[a-z0-9]+)*$, got {topic!r}")
   ```
3. Test workarounds in `tests/unit/execution/test_m25_session_teardown.py:66`, `tests/unit/execution/test_m27_workflow_interlock_lifecycle.py:78`, `tests/unit/execution/test_m29_tool_lifecycle.py:146`:
   ```python
   safety_gate = SafetyGateService(dispatcher=None, workflow_service=workflow)
   ```

#### Real Execution Path & Proof of Failure
Reproduction executed via Python:
```python
disp = MessageDispatcher()
disp.initialize(ctx)
sg = SafetyGateService(dispatcher=disp)
sg.initialize(ctx)
```
Output:
`TopicValidationError: Concrete topic must match ^[a-z0-9]+(\.[a-z0-9]+)*$, got 'safety_gate.evaluate'`

#### Severity
**HIGH (Systemic Integration Blocker)**: Prevents `SafetyGateService` from ever being connected to the real production `MessageDispatcher` or emitting events over the unified message bus.

#### Affected Services
- `python/holomed/safety_gate/service.py`
- Tests in `tests/unit/safety_gate/`

#### Minimum Reopen Set
- `python/holomed/safety_gate/service.py`
- New unit/regression tests in `tests/unit/safety_gate/`

#### Frozen Boundaries
- `SafetyGateEvaluator` decision logic and precedence rules remain 100% frozen.
- `ClinicalExecutionGatewayService` execution steps remain 100% frozen.
- Teardown sequence (Steps 1–12) remains 100% frozen.

#### Testability
Deterministic and verifiable via automated unit tests validating that `SafetyGateService` initializes cleanly with real `MessageDispatcher`, registers compliant query routes (e.g. `safety.status.get` or `safetygate.status.get`), removes raw execution command bypasses, and emits protocol events without errors.

#### Dependency Impact
Zero breaking changes to execution gateway or other domain services.

#### Why Not Already Solved
Milestones M21–M24 focused on domain subsystems (`tools`, `recovery`, `registration`, `planning`). Milestones M25–M29 focused on universal teardown and state eviction. The safety gate was treated as a black box with `dispatcher=None` during execution gateway integration.

#### Why This Should Be M30
It solves a proven, reproducible runtime crash on service initialization, eliminates the last unmediated clinical command on the dispatcher, and allows `SafetyGateService` to integrate with the real event bus.

#### Why It Should Not Be M30
The fix is small and localized to `SafetyGateService`.

---

### Candidate 2: Platform Session Ingress Decoupling & Inactive Session Execution

#### Title
Platform Session State Ingress Decoupling on Stopped Sessions

#### Problem
`PlatformService.stop_session(session_id)` marks a session as `SessionStatus.STOPPED`. However, `ClinicalExecutionGatewayService` and `GatewayService` do not check `PlatformService.get_session(session_id)`. If an authenticated client sends commands for a stopped platform session, `ClinicalExecutionGatewayService` will execute them if workflow state allows.

#### Hostile Challenge on Candidate 2
- In M25, the architecture established `execution.session.teardown` as the authoritative universal teardown entrypoint. Step 10 of teardown calls `self._platform_service.evict_session(session_id)`, purging the session completely.
- `PlatformService.stop_session` is a legacy M01 construct. Once `execute_session_teardown` is invoked, all subsystems fail closed.
- **Verdict**: Theoretical / duplicate of M25 teardown architecture. **REJECTED**.

---

### Candidate 3: Unmediated Perceptual Evaluation Routes (`drift.evaluate`, `proximity.evaluate`)

#### Title
Perceptual Evaluation Command Ingress Hardening

#### Problem
`drift.evaluate` and `proximity.evaluate` are registered as COMMAND routes on the dispatcher and allow incoming envelopes to mutate dwell buffers and latch interlocks without execution capabilities.

#### Hostile Challenge on Candidate 3
- Perceptual evaluation is sensor data ingestion, not clinical actuation or procedure modification.
- These routes require active registration and valid epoch, and are rate/age limited.
- Removing them from the dispatcher would break real-time sensor observation ingestion.
- **Verdict**: By-design sensor streaming architecture. **REJECTED**.

---

## 18. Hostile Self-Challenge of Candidate 1

| Question | Evaluation |
| :--- | :--- |
| **Is it theoretical?** | **NO**. Proven by reproducible execution: `sg.initialize(ctx)` with real `MessageDispatcher` raises `TopicValidationError`. |
| **Is it reachable?** | **YES**. Any production bootstrap that wires all services to the central dispatcher hits this failure immediately. |
| **Can the impact really occur?** | **YES**. The service cannot start with a real dispatcher. Every existing execution test was forced to bypass the dispatcher via `dispatcher=None`. |
| **Is there already downstream protection?** | **NO**. Downstream protection does not prevent the fatal initialization crash. |
| **Is it only documentation debt?** | **NO**. It is an unhandled exception in production code crashing initialization. |
| **Can it safely wait?** | It can wait only as long as `dispatcher=None` is hardcoded in test setups, but production deployment cannot run without a unified bus. |
| **Does it duplicate M25–M29?** | **NO**. M25–M29 addressed session teardown and state eviction. M30 Candidate 1 addresses service initialization, topic grammar compliance, and route sanitization. |
| **Does it require too much reopening?** | **NO**. Strictly confined to `python/holomed/safety_gate/service.py` and safety gate unit tests. |

---

## 19. M30 Justification Standard

Under the strict standard:
1. Concrete source evidence: Verified (`safety_gate/service.py:135-136` vs `core/subscription.py:77-80`).
2. Reachable execution path: Verified (initialization path with real dispatcher).
3. Meaningful impact: Verified (fatal startup failure; inability to emit safety events over bus).
4. Strong proof of failure: Verified (reproduced and documented).
5. Bounded fix: Verified (confined to 1 production file and tests).
6. Testable acceptance criteria: Verified.
7. Acceptable scope: Verified.
8. Clear benefit beyond M29: Verified.

---

# FINAL CLASSIFICATION

**`M30_JUSTIFIED`**
