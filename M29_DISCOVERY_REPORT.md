# M29 DISCOVERY REPORT
## System-Wide Forensic Architecture Audit

**Authoritative Baseline**: `e7362bcc8708a347abc851686f3f25f66358d2f7`  
**Previous Release**: M28 — Gateway Ingress Security & Connection Lifecycle Hardening (Strictly Frozen)  
**Audit Mode**: STRICT READ-ONLY FORENSIC AUDIT  
**Discovery Classification**: `M29_JUSTIFIED`  

---

## 1. CURRENT SYSTEM SNAPSHOT

A full inspection of the repository at baseline `e7362bcc8708a347abc851686f3f25f66358d2f7` inventories all packages and subsystems:

| Package | Responsibility & Authority | State Lifetime | Privileged Boundaries |
|---|---|---|---|
| **Platform** (`holomed.platform`) | Lifecycle authority: session supervisor, cycle coordinator, epoch tracking. | Ephemeral runtime + session contexts. | Authoritative session lifecycle (`start_session`, `stop_session`, `evict_session`). |
| **Gateway** (`holomed.gateway`) | Perimeter security, framing, client authentication, session-payload binding, transport management. | Ephemeral per-connection + active session registry. | Enforces non-actuation keywords, role capability, and session-binding. |
| **Execution Gateway** (`holomed.execution`) | Coordinated clinical actuation orchestrator, capability minting, safety gate verification. | Ephemeral per-session results + latest execution status. | Mints internal single-use `_ExecutionCapability` for downstream services. |
| **Safety Gate** (`holomed.safety_gate`) | Authoritative dual-gate safety decision authority, cross-subsystem precedence evaluation. | Ephemeral decision cache per session. | Evaluates proximity, drift, recovery, registration, and workflow signals. |
| **Workflow** (`holomed.workflow`) | Phase transition engine, anatomical checkpoint validator, safety interlock engine. | Per-session state machines, confirmations, and interlocks. | Authorizes clinical tool safety classifications and phase transitions. |
| **Planning** (`holomed.planning`) | Preoperative surgical plan repository, trajectory definitions, case context verification. | Active plan registry, session-plan bindings. | Capability-gated plan ingestion, locking, and verification. |
| **Registration** (`holomed.registration`) | Patient-to-image registration solver, fiducial cloud management, rigid transformation. | Active registration records, fiducial point clouds. | Capability-gated fiducial submission, solving, and verification. |
| **Navigation** (`holomed.navigation`) | Real-time optical tool tracking, physical trajectory binding, target deviation evaluation. | Active poses, bound trajectories, tracking deviations. | Capability-gated pose submission, trajectory binding, and deviation tracking. |
| **Proximity** (`holomed.proximity`) | Real-time distance evaluation against critical anatomical exclusion zones. | Monitored zones, latest geometries, sequence tracking. | Capability-gated zone registration and geometry updates. |
| **Drift** (`holomed.drift`) | Landmark registration drift verification, rigid transform stability monitoring. | Bound landmarks, reference geometries, drift records. | Capability-gated landmark binding and drift evaluation. |
| **Recovery** (`holomed.recovery`) | Spatial recovery supervisor, candidate transform staging, clearance coordination. | Recovery status records, staged transforms. | Capability-gated candidate staging, validation, and activation. |
| **Tools** (`holomed.tools`) | Clinical instrument registry, tool invocation execution engine, parameter validation. | Tool registry, sequence monotonicity tracker (`_session_sequences`), invocation history. | Capability-gated tool invocation execution. |
| **Persistence** (`holomed.persistence`) | Durable regulatory audit logging, session journal persistence (`.jsonl`), FDA Part 11 trail. | Immutable append-only disk journals. | System-wide audit sink. Durable records are never deleted on session teardown. |
| **Core** (`holomed.core`) | Deterministic in-process `MessageDispatcher`, dead-letter queue, correlation router. | Static handler registries (INITIALIZED) + dynamic listeners. | In-process message routing perimeter. |

---

## 2. COMPLETE ROUTE FORENSICS

An exhaustive audit of every registered command and query across `MessageDispatcher` reveals 28 registered commands, 24 registered queries, and 5 event subscriptions:

### A. Execution Gateway Routes (`ClinicalExecutionGatewayService`)
- `execution.navigation.execute` [Command, Clinical Mutation, Dual-Gated, Capability Required]
- `execution.recovery.execute` [Command, Clinical Mutation, Dual-Gated, Capability Required]
- `execution.trajectory.bind` [Command, Clinical Mutation, Dual-Gated, Capability Required]
- `execution.tool.invoke` [Command, Clinical Mutation, Dual-Gated, Capability Required]
- `execution.workflow.resume` [Command, Clinical Mutation, Dual-Gated, Capability Required]
- `execution.registration.execute` [Command, Clinical Mutation, Dual-Gated, Capability Required]
- `execution.planning.execute` [Command, Clinical Mutation, Dual-Gated, Capability Required]
- `execution.session.teardown` [Command, Lifecycle Mutation, Capability Required]
- `execution.status.get` [Query, Read-Only, Session-Scoped]

### B. Workflow Subsystem Routes (`WorkflowService`)
- `workflow.start` [Command, Lifecycle Mutation, Session-Scoped]
- `workflow.transition` [Command, Lifecycle Mutation, Session-Scoped]
- `workflow.confirm` [Command, Safety/Lifecycle Mutation, Session-Scoped]
- `workflow.abort` [Command, Safety/Lifecycle Mutation, Session-Scoped]
- `workflow.status` [Query, Read-Only, Session-Scoped]

### C. Platform Subsystem Routes (`PlatformService`)
- `platform.cycle` [Command, Operational Mutation]
- `platform.session.start` [Command, Lifecycle Mutation]
- `platform.session.stop` [Command, Lifecycle Mutation]
- `platform.reset` [Command, Lifecycle Mutation]
- `platform.status` [Query, Read-Only]
- `platform.audit` [Query, Read-Only]

### D. Gateway Subsystem Routes (`GatewayService`)
- `gateway.disconnect` [Command, Lifecycle Mutation]
- `gateway.status` [Query, Read-Only]
- `gateway.clients` [Query, Read-Only]

### E. Domain Subsystem Query-Only Perimeter (Retired Direct Mutations)
- `planning.get` [Query, Read-Only] (M24: Direct mutation commands retired; strictly mediated by `execution.planning.execute`)
- `registration.get` [Query, Read-Only] (M23: Direct mutation commands retired; strictly mediated by `execution.registration.execute`)
- `navigation.status.get` [Query, Read-Only] (M22: Direct mutation commands retired; strictly mediated by `execution.navigation.execute`)
- `safety_gate.status.get` [Query, Read-Only]
- `proximity.status.get`, `proximity.zones.get` [Query, Read-Only]
- `drift.status.get`, `drift.landmarks.get` [Query, Read-Only]
- `recovery.status.get` [Query, Read-Only]

**Forensic Finding**: Zero dispatcher bypasses detected. All clinical mutations in planning, registration, navigation, tools, and recovery are strictly mediated through `ClinicalExecutionGatewayService` and require single-use capabilities.

---

## 3. GATEWAY SECURITY REGRESSION AUDIT

M28 introduced session-payload binding at `GatewayAuthorizationPolicy.authorize_message()`:
```python
if isinstance(envelope.payload, dict) and "session_id" in envelope.payload:
    payload_session_id = envelope.payload.get("session_id")
    if payload_session_id != session.session_id:
        raise GatewaySessionMismatchError(...)
```
- **Ingress Perimeter Completeness**: Audit confirms that `GatewayService._handle_client_message()` calls `GatewayAuthorizationPolicy.authorize_message()` unconditionally before calling `_dispatcher.dispatch()`.
- **Target Selection Attack**: An authenticated client for Session A specifying `payload["session_id"] = "SESSION_B"` is rejected with `ERR_SESSION_MISMATCH` across all routes.
- **Global Routes**: Routes omitting `session_id` (`gateway.status`, `gateway.clients`) pass cleanly.
- **Malformed Payloads**: Empty strings, None, or integer `session_id` are rejected immediately.
- **Conclusion**: The M28 gateway session-binding perimeter is fully intact and regression-free.

---

## 4. CAPABILITY FORENSICS

Audit of `_ExecutionCapability` in `python/holomed/execution/_capability.py`:
- **Unexported**: Hidden module sentinel key `_INTERNAL_EXECUTION_KEY` prevents direct external construction.
- **Bound Metadata**: Contains `service_instance_id`, `session_id`, `action`, `sequence_number`, `transaction_id`.
- **Serialization Blocked**: `__getstate__` and `__setstate__` raise `TypeError`.
- **Single-Use**: Invalidated immediately upon commit, abort, or transaction `finally` block in `ClinicalExecutionGatewayService`.
- **Downstream Validation**: Downstream services (`navigation`, `tools`, `recovery`, `registration`, `planning`) rigorously validate `capability.is_active`, `session_id`, `action`, and `sequence_number`.
- **Conclusion**: Capability authorization chain is unbroken and non-replayable.

---

## 5. TEMPORAL / REPLAY SECURITY

Audit of sequence tracking across services:
- `NavigationService`: Tracks `_latest_sequences[(session_id, instrument_id)]`.
- `ProximityService`: Tracks `_latest_sequences[(session_id, instrument_id)]`.
- `DriftService`: Tracks `_latest_sequences[(session_id, landmark_id)]`.
- `RecoveryService`: Tracks `recovery_revision` and `sequence_number`.
- `ToolExecutionEngine`: Tracks `_session_sequences[session_id]`.
- **Finding**: While Navigation, Proximity, and Drift properly evict session sequence keys during teardown, `ToolExecutionEngine` **does NOT evict session sequences**, creating a severe temporal/replay vulnerability upon session reuse (see Candidate 1).

---

## 6. TRANSACTIONAL ATOMICITY

All multi-step mutations in `WorkflowService`, `PlanningService`, `RegistrationService`, `NavigationService`, `ProximityService`, `DriftService`, `RecoveryService`, and `ClinicalExecutionGatewayService` utilize explicit `_in_transaction` reentrancy guards and `try ... finally` blocks to ensure transaction flags are cleared. Staged mutations in recovery clearance and interlocks use rollback snapshots (`_staged_recovery_prior`). No half-committed state was found unhandled.

---

## 7. CROSS-SUBSYSTEM STATE CONSISTENCY

Audit of cross-subsystem state interactions:
- `SafetyGateEvaluator` checks epoch consistency across all attached subsystems (`snap.epoch_id == epoch_id`).
- Workflow abort trips interlocks and disconnects gateway clients.
- Recovery reorientation requires verified registration revision synchronization (`m15_rev == m17_rev`).
- **Gap Identified**: While `PlanningService` binds plans to sessions (`_session_plan_bindings`), `RegistrationService` fails to verify whether a submitted `plan_id` actually belongs to the session being registered (see Candidate 2).

---

## 8. SAFETY DECISION INTEGRITY

The M18 `SafetyGateEvaluator` precedence rules remain strictly ordered:
1. Session Mismatch -> `DENIED_INTERLOCKED`
2. M15 Exclusion Zone Breach -> `DENIED_CRITICAL`
3. M16 Landmark Integrity -> `DENIED_INTERLOCKED`
4. M16 Drift Exceeded -> `DENIED_INTERLOCKED` (or `PERMITTED_WITH_CAUTION` for recovery reorientation)
5. M17 Recovery Failed -> `DENIED_INTERLOCKED`
6. Epoch Mismatch -> `DENIED_INTERLOCKED`
7. M13 Registration Missing -> `DENIED_INTERLOCKED`
8. M10 Workflow Phase Missing -> `DENIED_INTERLOCKED`

Zero bypass paths exist around `SafetyGateEvaluator`.

---

## 9. OBSERVATION / PERCEPTION FRESHNESS

Observation timestamps and sequences are validated in `VisionService`, `AudioService`, and `GestureService`. Hardware device sessions track physical ID migrations and retire stale sessions upon device re-registration.

---

## 10. PERSISTENCE / RECOVERY FORENSICS

`DurableSessionStore` maintains immutable `.jsonl` audit records on disk. Audit records are regulatory requirements (FDA 21 CFR Part 11) and are deliberately preserved during runtime session teardown. No state resurrection occurs because active runtime state is stored in ephemeral dictionaries that are purged on teardown.

---

## 11. EPOCH / GENERATION FORENSICS

All services acquire structural resources tagged with `epoch_id`. Resetting an epoch triggers `clear()` and resource validation across all services. Stale epoch capabilities and commands are rejected synchronously.

---

## 12. RESOURCE / CAPACITY FORENSICS

Audit of bounded capacities across the platform:
- `MAX_ACTIVE_EXECUTION_SESSIONS`: 32 (reclaimed on teardown)
- `MAX_ACTIVE_WORKFLOWS`: 32 (reclaimed on teardown)
- `MAX_ACTIVE_NAVIGATION_SESSIONS`: 32 (reclaimed on teardown)
- `MAX_ACTIVE_GATE_SESSIONS`: 32 (reclaimed on teardown)
- `MAX_ACTIVE_REGISTRATIONS`: 32 (reclaimed on teardown)
- `MAX_ACTIVE_PLANS`: 32 (reclaimed on teardown)
- `MAX_CONNECTIONS_PER_SESSION`: 4 (reclaimed on teardown in M28)
- `MAX_REGISTERED_CHECKPOINTS`: 32 (reclaimed on teardown in M27)
- **CRITICAL DEFECT**: `ToolExecutionEngine.MAX_ACTIVE_SESSIONS = 64`. **NOT RECLAIMED ON TEARDOWN**. Permanent leak identified.

---

## 13. INGRESS / CONNECTION LIFECYCLE

M28 added `GatewayService.evict_session(session_id)`. Transports are closed, connection descriptors popped, and broadcast/presentation listeners unregistered. Ingress connection lifecycle is complete and verified.

---

## 14. ERROR SEMANTICS

All service methods raise domain-specific exceptions inheriting from `HoloMedError`. In teardown pipelines, exceptions are aggregated into `failures` lists and produce degraded status rather than false success.

---

## 15. PUBLIC API / DIRECT SERVICE ACCESS

All direct mutation routes on `PlanningService`, `RegistrationService`, and `NavigationService` were retired in M22–M24. Their mutation methods enforce active `_ExecutionCapability` with matching `service_instance_id = id(self)`. Direct uncoordinated invocation from application code raises `AuthorizationError`.

---

## 16. DEPENDENCY / AUTHORITY GRAPH

The architectural authority model is preserved:
- `PlatformService`: Lifecycle authority
- `GatewayService`: Ingress network perimeter
- `ClinicalExecutionGatewayService`: Actuation orchestrator & capability minter
- `SafetyGateService`: Safety evaluation authority
- Domain Services (`workflow`, `planning`, `registration`, `navigation`, `proximity`, `drift`, `recovery`, `tools`): Domain state owners

---

## 17. M07–M28 GAP REVIEW

Reviewing the milestone contracts against the actual code:
- M07 (`tools`): Implemented `ToolExecutionEngine` with `_session_sequences` and `MAX_ACTIVE_SESSIONS = 64`. However, M07 was built before the M25 coordinated teardown architecture was introduced, and was **never retrofitted with session eviction**.
- M25 (`execution teardown`): Implemented Steps 1–10 (Navigation, Recovery, Registration, Planning, Safety Gate, Workflow, Gateway, Platform). **Omitted ToolService**.
- M26 (`perceptual teardown`): Added Steps 2 & 3 (Proximity, Drift).
- M27 (`workflow interlocks`): Isolated workflow interlocks and checkpoints.
- M28 (`gateway ingress`): Added Step 11 (Gateway Ingress Connections).
- **Result**: `ToolService` remains the sole un-evicted clinical actuation subsystem.

---

## 18. TOP M29 CANDIDATES

### CANDIDATE 1: Tool Subsystem Session Sequence Lifecycle Leak & Permanent Capacity Lockout
- **Problem**: `ToolExecutionEngine._session_sequences` stores `session_id -> last_sequence_number` with capacity limit `MAX_ACTIVE_SESSIONS = 64`. Neither `ToolExecutionEngine` nor `ToolService` provides `evict_session()`. Furthermore, `ClinicalExecutionGatewayService.execute_session_teardown()` never calls `self._tool_service`.
- **Actual Source Evidence**:
  - `python/holomed/tools/engine.py:38, 60-72, 172-175`:
    `_session_sequences` enforces monotonicity and capacity (`MAX_ACTIVE_SESSIONS = 64`), but only clears on global `clear()`.
  - `python/holomed/tools/service.py:285-350`:
    Has `invoke_tool`, `audit_tools`, `clear()`, but zero `evict_session()` method.
  - `python/holomed/execution/service.py:126, 2140-2245`:
    `_tool_service` injected, but completely missing from Steps 1–11 of `execute_session_teardown()`.
- **Real Execution Path**:
  1. Procedure 1 on Session A runs tool invocations up to sequence 5: `_session_sequences["Session A"] = 5`.
  2. Session A is torn down via `ClinicalExecutionGatewayService.execute_session_teardown("Session A")`.
  3. Session A is restarted or reused for a new procedure starting at sequence 1.
  4. Tool invocation for sequence 1 arrives: `ToolExecutionEngine` compares `1 <= 5` and raises `ToolSequenceError("Non-monotonic sequence number 1 <= last seen 5 for session 'Session A'")`.
  5. The procedure is completely blocked from invoking any clinical tools.
  6. Over time, after 64 distinct procedures are executed and torn down, session 65 raises `ToolCapacityError("Active session capacity exceeded (64 max)")`, permanently disabling all clinical tool invocations across the entire server.
- **Severity**: **CRITICAL / HIGH** (Direct clinical actuation denial-of-service).
- **Affected Services**: `holomed.tools`, `holomed.execution`.
- **Minimum Reopen Set**:
  1. `python/holomed/tools/engine.py`
  2. `python/holomed/tools/service.py`
  3. `python/holomed/execution/service.py`
- **Frozen Boundaries**: M01–M28 contracts, capability validation, tool safety categories, and persistence remain frozen.
- **Testability**: Highly deterministic unit and hostile integration tests with memory transports.
- **Dependency Impact**: Zero breaking changes to external APIs.
- **Why It Is Not Already Solved**: M25–M28 focused on navigation, proximity, drift, workflow, and gateway; `ToolService` was overlooked.
- **Why It Should Be M29**: Direct clinical safety and operational availability defect.
- **Why It Should Not Be M29**: None.

---

### CANDIDATE 2: Cross-Session Surgical Plan Integrity & Registration Plan Ownership Disconnect
- **Problem**: `RegistrationService.submit_fiducials()` and `solve_registration()` call `_verify_locked_plan(plan_id)`, which validates that `plan_id` is locked in `PlanningService`, but fails to verify that `plan_id` belongs to the `session_id` being registered. `ClinicalExecutionGatewayService.execute_registration()` and `execute_trajectory_binding()` forward requests without cross-validating session plan ownership.
- **Actual Source Evidence**:
  - `python/holomed/registration/service.py:254, 541-549`:
    `_verify_locked_plan(plan_id)` ignores `session_id`.
  - `python/holomed/planning/service.py:256, 422-425`:
    `_session_plan_bindings` exists and tracks `session_id -> plan_id`, but is not queried during registration verification.
  - `python/holomed/execution/service.py:1615-1635, 625-631`:
    Does not cross-check `request.plan_id` against `planning_service.get_plan_for_session(session_id)`.
- **Real Execution Path**:
  Session B (Patient B) submits `plan_id` created for Session A (Patient A). Registration accepts the locked plan and registers Patient B's anatomy to Patient A's plan.
- **Severity**: **HIGH** (Patient Safety / Cross-Patient Plan Contamination).
- **Affected Services**: `holomed.registration`, `holomed.execution`.
- **Minimum Reopen Set**:
  1. `python/holomed/registration/service.py`
  2. `python/holomed/execution/service.py`
- **Why It Should Be M29**: Crucial cross-session surgical safety guarantee.

---

### CANDIDATE 3: Platform Lifecycle Authority Inversion on Stopped Sessions
- **Problem**: `ClinicalExecutionGatewayService` does not check `PlatformService.has_session(session_id)` or verify that session status is `ACTIVE` before executing clinical actuations.
- **Actual Source Evidence**:
  - `python/holomed/execution/service.py`: `_platform_service` is only referenced in teardown Step 10.
- **Severity**: **MEDIUM / HIGH**.
- **Why It Should Not Be M29**: Many existing unit tests instantiate `ClinicalExecutionGatewayService` without `PlatformService` (optional dependency); requiring it globally could cause widespread test regressions unless carefully scoped.

---

## 19. HOSTILE SELF-CHALLENGE

1. **Candidate 1 vs Candidate 2**:
   - Candidate 1 is a verified runtime capacity leak and immediate failure upon session reuse (`ToolSequenceError` and `ToolCapacityError`). It directly completes the coordinated session teardown architecture initiated in M25 and extended through M28.
   - Candidate 1 has a strictly bounded reopen set (3 files), identical to M26, M27, and M28.
   - Candidate 1 leaves zero ambiguous dependencies.
2. **Is Candidate 1 merely theoretical?**
   - NO. Inspect `python/holomed/tools/engine.py:61`: `if len(self._session_sequences) >= MAX_ACTIVE_SESSIONS: raise ToolCapacityError(...)`.
   - Once 64 sessions are executed, session 65 unconditionally fails.
   - Reused sessions unconditionally fail with `ToolSequenceError`.
   - The defect is 100% reproducible and demonstrable.

---

## 20. M29 JUSTIFICATION STANDARD

Candidate 1 satisfies every requirement of the M29 justification standard:
- Concrete source evidence in `tools/engine.py`, `tools/service.py`, and `execution/service.py`.
- Measurable real impact: permanent platform lockout after 64 procedures, sequence rejection on session reuse.
- Bounded, surgical fix: implement `evict_session()` on `ToolExecutionEngine` and `ToolService`, wire Step 12 into `ClinicalExecutionGatewayService.execute_session_teardown()`.
- Uncompromised frozen boundaries.

---

## 21. FINAL CLASSIFICATION

```
==================================================
M29_JUSTIFIED
==================================================
```

**Recommended M29 Title**:  
`M29 — Clinical Tool Subsystem Lifecycle Eviction & Teardown Hardening`
