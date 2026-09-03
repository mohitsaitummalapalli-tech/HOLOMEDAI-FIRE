# M28 DISCOVERY REPORT: SYSTEM-WIDE FORENSIC AUDIT

**Authoritative Baseline**: `7acac2469a5da864ae926906671f761888715127`  
**Previous Milestone**: M27 — Workflow Safety Interlock Scoping & Lifecycle Eviction Hardening  
**Audit Mode**: STRICT READ-ONLY FORENSIC AUDIT (0 source changes, 0 test changes, 0 commits, 0 pushes)  
**Status**: DISCOVERY COMPLETE  
**Classification**: `M28_JUSTIFIED`  

---

## 1. CURRENT ARCHITECTURE SNAPSHOT

The HoloMed platform at baseline `7acac2469a5da864ae926906671f761888715127` comprises 26 subpackages in `python/holomed/`. Following the coordinated session teardown architecture introduced in M25, perceptual monitoring eviction in M26, and workflow safety interlock scoping in M27, the system maintains strict lifecycle separation:

- **Platform Authority (`holomed/platform`)**: Authoritative owner of platform lifecycle, cycle advancement, sequence monotonicity, emergency stop, and session bounds (`MAX_ACTIVE_PLATFORM_SESSIONS = 16`).
- **Workflow Supervisor (`holomed/workflow`)**: Manages surgical phases, interlocks, human confirmations, and anatomical checkpoints. Partitioned by `session_id` in M27.
- **Safety Gate (`holomed/safety_gate`)**: Pure stateless safety evaluator combining perceptual, spatial, and workflow evidence into fail-closed gating decisions.
- **Perceptual Monitoring (`holomed/proximity`, `holomed/drift`)**: Evaluates boundary proximity and rigid fiducial drift. Fully evicted on teardown in M26.
- **Clinical Execution Gateway (`holomed/execution`)**: Mediates all privileged clinical mutations through atomic transactions, single-use `_ExecutionCapability` tokens, and coordinates session teardown.
- **External Client Gateway (`holomed/gateway`)**: Ingress transport, WebSocket/TCP connection framing, client role authentication, and message dispatching to the internal `MessageDispatcher`.
- **Durable Persistence (`holomed/persistence`)**: Append-only tamper-evident journal storage on disk, audit trail recording, and in-memory session store (`MAX_DURABLE_SESSIONS = 16`).
- **Core Dispatcher (`holomed/core`)**: Central synchronous message routing bus with strict state enforcement (`INITIALIZED` registration, `STARTED` dispatch).

---

## 2. COMPLETE ROUTE INVENTORY

A complete inventory of all 42 registered dispatcher routes across the platform:

| Route Name | Owning Service | Type | Clinical Impact | Capability Required? | Gateway Mediated? |
|---|---|---|---|---|---|
| `execution.navigation.execute` | `ClinicalExecutionGatewayService` | Command | Critical | Yes (`_ExecutionCapability`) | Yes (Authoritative) |
| `execution.recovery.execute` | `ClinicalExecutionGatewayService` | Command | Critical | Yes (`_ExecutionCapability`) | Yes (Authoritative) |
| `execution.trajectory.bind` | `ClinicalExecutionGatewayService` | Command | Critical | Yes (`_ExecutionCapability`) | Yes (Authoritative) |
| `execution.tool.invoke` | `ClinicalExecutionGatewayService` | Command | Critical | Yes (`_ExecutionCapability`) | Yes (Authoritative) |
| `execution.workflow.resume` | `ClinicalExecutionGatewayService` | Command | Critical | Yes (`_ExecutionCapability`) | Yes (Authoritative) |
| `execution.registration.execute` | `ClinicalExecutionGatewayService` | Command | Critical | Yes (`_ExecutionCapability`) | Yes (Authoritative) |
| `execution.planning.execute` | `ClinicalExecutionGatewayService` | Command | Critical | Yes (`_ExecutionCapability`) | Yes (Authoritative) |
| `execution.session.teardown` | `ClinicalExecutionGatewayService` | Command | Critical | Yes (`_ExecutionCapability`) | Yes (Authoritative) |
| `execution.status.get` | `ClinicalExecutionGatewayService` | Query | Informational | No | Yes |
| `workflow.start` | `WorkflowService` | Command | Moderate | No | Direct Service |
| `workflow.transition` | `WorkflowService` | Command | High | No | Direct Service |
| `workflow.confirm` | `WorkflowService` | Command | High | No | Direct Service |
| `workflow.abort` | `WorkflowService` | Command | High | No | Direct Service |
| `workflow.state.get` | `WorkflowService` | Query | Informational | No | Direct Service |
| `safety_gate.evaluate` | `SafetyGateService` | Command | High | No | Direct Service |
| `safety_gate.status.get` | `SafetyGateService` | Query | Informational | No | Direct Service |
| `proximity.evaluate` | `ProximityService` | Command | Moderate | No | Direct Service |
| `proximity.status.get` | `ProximityService` | Query | Informational | No | Direct Service |
| `proximity.zones.get` | `ProximityService` | Query | Informational | No | Direct Service |
| `drift.evaluate` | `DriftService` | Command | Moderate | No | Direct Service |
| `drift.status.get` | `DriftService` | Query | Informational | No | Direct Service |
| `drift.landmarks.get` | `DriftService` | Query | Informational | No | Direct Service |
| `planning.get` | `PlanningService` | Query | Informational | No | Direct Service |
| `registration.get` | `RegistrationService` | Query | Informational | No | Direct Service |
| `navigation.status.get` | `NavigationService` | Query | Informational | No | Direct Service |
| `recovery.status.get` | `RecoveryService` | Query | Informational | No | Direct Service |
| `platform.session.start` | `PlatformService` | Command | High | No | Direct Service |
| `platform.session.stop` | `PlatformService` | Command | High | No | Direct Service |
| `platform.cycle.advance` | `PlatformService` | Command | High | No | Direct Service |
| `platform.emergency.stop` | `PlatformService` | Command | Critical | No | Direct Service |
| `platform.health` | `PlatformService` | Query | Informational | No | Direct Service |
| `platform.sessions.list` | `PlatformService` | Query | Informational | No | Direct Service |
| `gateway.disconnect` | `GatewayService` | Command | Moderate | No | Direct Service |
| `gateway.status` | `GatewayService` | Query | Informational | No | Direct Service |
| `gateway.clients` | `GatewayService` | Query | Informational | No | Direct Service |
| `tools.invoke` | `ToolService` | Command | High | No | Direct Service |
| `tools.catalog` | `ToolService` | Query | Informational | No | Direct Service |
| `tools.history` | `ToolService` | Query | Informational | No | Direct Service |
| `tools.audit` | `ToolService` | Query | Informational | No | Direct Service |
| `xr.render.mode` | `XRService` | Command | Low | No | Direct Service |
| `xr.status` | `XRService` | Query | Informational | No | Direct Service |
| `xr.metrics` | `XRService` | Query | Informational | No | Direct Service |

---

## 3. PUBLIC API AUTHORITY AUDIT

All major services were audited for unmediated mutation methods and exposure of internal mutable dictionaries:
- **`ClinicalExecutionGatewayService`**: Enforces single-use capabilities on all clinical execution commands.
- **`WorkflowService`**: Direct route `workflow.transition` and `workflow.abort` mutate workflow state without capability requirement (protected by interlocks and confirmation gates).
- **`GatewayService`**: Public method `register_client_transport` and ingress dispatch `process_client_ingress` are open to external transports.

---

## 4. CAPABILITY FORENSICS

- **Minting**: `_ExecutionCapability` is strictly minted within `ClinicalExecutionGatewayService._mint_capability` using an internal crypto-random key `_INTERNAL_CAPABILITY_KEY`.
- **Binding**: Tokens are bound to `session_id`, `service_instance_id`, and `action`.
- **Lifetime**: Invalidation occurs in `finally` blocks after single execution.
- **Replay Protection**: Verified and tested across M23–M27. Tokens cannot be reused across sessions or after transaction completion.

---

## 5. TRANSACTION & REENTRANCY FORENSICS

- All major services (`PlatformService`, `WorkflowService`, `PersistenceService`, `GatewayService`, `ClinicalExecutionGatewayService`) implement `self._in_transaction` boolean guards.
- Nested and reentrant invocations raise service-specific lifecycle errors (`WorkflowLifecycleError`, `ExecutionLifecycleError`, `GatewayLifecycleError`).
- Invalidation and cleanup are strictly encapsulated in `finally` clauses.

---

## 6. SESSION ISOLATION FORENSICS

M25 (Platform, Navigation, Planning, Registration, Recovery, SafetyGate), M26 (Proximity, Drift), and M27 (Workflow Interlocks & Checkpoints) established session eviction across 8 subsystems.

### Remaining Session State Discovered:
1. **`GatewayService._connections`**:
   - Key: `client_id: str`
   - Stored value: `GatewayConnection` containing `ClientSession(session_id: str, ...)`
   - Problem: `GatewayService` has **no `evict_session(session_id)` method**. Connections belonging to a terminated session are never closed or evicted upon session teardown.
2. **`DurableSessionStore._sessions`**:
   - Key: `session_id: str`
   - Problem: Closed sessions remain in `_sessions` dictionary in memory. `MAX_DURABLE_SESSIONS = 16`. After 16 sessions, system experiences permanent lockout.
3. **`ToolExecutionEngine._session_sequences`**:
   - Key: `session_id: str`
   - Problem: Sequence counters are retained up to `MAX_ACTIVE_SESSIONS = 64`.

---

## 7. TEMPORAL & REPLAY SECURITY

- Monotonic sequence numbers are strictly validated in `SessionManager.validate_and_advance_sequence` and `JournalWriter`.
- Stale capability replay is mathematically rejected across all capability actions.
- Stale interlocks and checkpoints are purged upon session teardown in M27.

---

## 8. SAFETY DECISION INTEGRITY

- `SafetyGateEvaluator` is a pure mathematical evaluator with deterministic precedence (`EMERGENCY_STOP` > `MANUAL_OVERRIDE` > `CRITICAL_BREACH` > `SYSTEM_ERROR` > `TRACKING_DRIFT` > `INTERLOCK_TRIPPED` > `UNCONFIRMED_PHASE`).
- M26 eliminated perceptual evidence leakage.
- M27 eliminated interlock cross-session leakage.

---

## 9. CROSS-SUBSYSTEM INVARIANTS

- All clinical execution paths pass through `SafetyGateService.evaluate()`.
- If safety decision is not `PERMITTED_CLEAR`, execution gateway fails closed and records gate status.

---

## 10. PERSISTENCE & CRASH RECOVERY

- `JournalWriter` writes append-only JSONL files with SHA-256 rolling entry hashes.
- `JournalReader` validates hashes and detects corrupted or truncated entries.
- Audit records are synchronously flushed to disk.

---

## 11. FAILURE ATOMICITY

- Multi-step teardown in `ClinicalExecutionGatewayService.execute_session_teardown()` aggregates failures across 10 steps.
- Best-effort execution guarantees that a failure in one subsystem does not prevent cleanup in remaining subsystems.

---

## 12. RESOURCE & CAPACITY FORENSICS

| Constant | Value | Subsystem | Teardown Eviction Status | Finding |
|---|---|---|---|---|
| `MAX_ACTIVE_PLATFORM_SESSIONS` | 16 | Platform | Evicted (M25) | Clean |
| `MAX_ACTIVE_PROXIMITY_SESSIONS` | 32 | Proximity | Evicted (M26) | Clean |
| `MAX_ACTIVE_DRIFT_SESSIONS` | 16 | Drift | Evicted (M26) | Clean |
| `MAX_REGISTERED_CHECKPOINTS` | 32 | Workflow | Evicted (M27) | Clean |
| `MAX_CONNECTIONS_PER_SESSION` | 4 | Gateway | **NEVER EVICTED** | **LEAK**: Permanent lockout on reuse |
| `MAX_CLIENTS` | 16 | Gateway | **NEVER EVICTED** | **LEAK**: Pool exhausted if clients don't disconnect |
| `MAX_DURABLE_SESSIONS` | 16 | Persistence | **NEVER EVICTED** | **LEAK**: 16-session cumulative limit |
| `MAX_ACTIVE_SESSIONS` | 64 | Tools | **NEVER EVICTED** | **LEAK**: 64-session cumulative limit |

---

## 13. ERROR SEMANTICS

- Exceptions in message dispatching produce standard protocol `ERROR` envelopes (`ERR_VALIDATION`, `ERR_AUTHORIZATION`, etc.).
- Errors are never swallowed in execution gateway; degraded states are explicitly logged in persistence audit.

---

## 14. INGRESS & GATEWAY SECURITY AUDIT

A critical vulnerability was proven in `python/holomed/gateway/authorization.py` and `python/holomed/gateway/service.py`:

### The Cross-Session Payload Injection Vulnerability
1. **Source Evidence**: In `GatewayAuthorizationPolicy.authorize_message(session: ClientSession, envelope: MessageEnvelope)`:
   ```python
   # 1. Prevent Source Spoofing (D284)
   if envelope.source != session.client_id:
       raise GatewayValidationError(
           f"Source spoofing detected: envelope declared source={envelope.source!r}, authenticated={session.client_id!r}"
       )
   ```
2. **Missing Invariant**: The policy verifies that `envelope.source == session.client_id`. **However, it completely ignores `envelope.payload.get("session_id")`!**
3. **Exploit Path**:
   - Client authenticates handshake with `client_id="surgeon_console_1"` for `session_id="SESS-PATIENT-A"`.
   - Client crafts an envelope with `source="surgeon_console_1"`, `message_name="workflow.transition"`, and payload:
     ```json
     {"session_id": "SESS-PATIENT-B", "target_phase": "ABORTED", "sequence_number": 5}
     ```
   - `GatewayAuthorizationPolicy.authorize_message` checks:
     - `envelope.source == session.client_id` -> **PASSES** (`surgeon_console_1 == surgeon_console_1`).
     - No actuation keywords in message name -> **PASSES**.
     - Role is `SURGEON_CONSOLE` -> **PASSES**.
   - `GatewayService._handle_client_message` forwards envelope to `self._dispatcher.dispatch(envelope)`.
   - Dispatcher invokes `WorkflowService.handle_transition_command()`, which reads `payload["session_id"]` (`SESS-PATIENT-B`) and **aborts Patient B's surgery!**
4. **Clinical Severity**: **CRITICAL**. An authenticated client for one operating room/session can maliciously or accidentally manipulate, abort, or execute commands against a different patient's session across the entire hospital system.

### The Connection Teardown Leak
1. **Source Evidence**: `GatewayService` has no `evict_session(session_id)` method.
2. When `execution.session.teardown` executes for `session_id="SESS-A"`, `GatewayService` is not notified.
3. `GatewayConnection` objects remain active in `self._connections`.
4. When `session_id="SESS-A"` is reused:
   `_handle_handshake` evaluates:
   ```python
   session_conns = sum(1 for c in self._connections.values() if c.session and c.session.session_id == session.session_id)
   if session_conns >= MAX_CONNECTIONS_PER_SESSION:
       raise GatewayCapacityError(...)
   ```
   After 4 connections, subsequent connection attempts are permanently rejected with `GatewayCapacityError`.
5. Stale connections continue to receive live presentation frames (`handle_presentation_event`) and workflow broadcasts (`broadcast_envelope`).

---

## 15. OBSERVATION & PERCEPTION PIPELINE

- Observations are validated against `epoch_id` and timestamp thresholds.
- M26 verified that proximity and drift perceptual evidence is purged upon session eviction.

---

## 16. EPOCH, STARTUP & RECONNECT

- All services implement `initialize(context)` and verify `context.epoch_id`.
- Reconnecting clients must negotiate `gateway.handshake` with matching `epoch_id`.

---

## 17. AUTHORITY & DEPENDENCY GRAPH

- Hierarchy: `PlatformService` (Lifecycle) -> `ClinicalExecutionGatewayService` (Execution) -> `SafetyGateService` (Safety) -> `Subsystems`.
- Ingress: `GatewayService` acts as the external network boundary, converting framed messages into protocol `MessageEnvelope` objects and passing them to `MessageDispatcher`.
- Authority Inversion Risk: Currently, `GatewayService` allows untrusted external payloads to dictate internal `session_id` routing without verifying session identity against the authenticated TLS/transport connection.

---

## 18. M07–M27 GAP ANALYSIS

- M07–M24: Complete and frozen.
- M25: Coordinated clinical teardown implemented across core services. Complete and frozen.
- M26: Perceptual monitoring lifecycle eviction implemented. Complete and frozen.
- M27: Workflow safety interlock scoping and checkpoint eviction implemented. Complete and frozen.
- **Unresolved Gap**: `holomed/gateway` was not included in M25–M27 teardown and lacks session-binding authorization in ingress validation.

---

## 19. CANDIDATE M28 IDENTIFICATION

### Candidate 1 (RECOMMENDED): Gateway Ingress Isolation & Client Connection Lifecycle Hardening
- **Title**: Gateway Ingress Isolation & Client Connection Lifecycle Hardening
- **Problem**:
  1. `GatewayAuthorizationPolicy` does not validate that `payload["session_id"]` matches `ClientSession.session_id`, allowing cross-session command injection.
  2. `GatewayService` lacks `evict_session(session_id)`, causing connection capacity exhaustion (`MAX_CONNECTIONS_PER_SESSION = 4`) and telemetry/frame leakage to stale clients.
- **Actual Source Evidence**:
  - `python/holomed/gateway/authorization.py:32-37` (only validates `envelope.source == session.client_id`).
  - `python/holomed/gateway/service.py:280-289` (accumulates connections against `MAX_CONNECTIONS_PER_SESSION` with no eviction).
- **Real Execution Path**:
  Client handshakes as `session_A` -> sends `workflow.transition` with `payload={"session_id": "session_B"}` -> policy authorizes -> dispatcher executes transition on `session_B`.
- **Severity**: **CRITICAL** (Cross-session security vulnerability + capacity DoS).
- **Affected Services**: `holomed/gateway/authorization.py`, `holomed/gateway/service.py`, `holomed/execution/service.py`.
- **Minimum Reopen Set**:
  1. `python/holomed/gateway/authorization.py`
  2. `python/holomed/gateway/service.py`
  3. `python/holomed/execution/service.py` (add Step 11 in teardown to invoke `gateway_service.evict_session(session_id, cap)`)
- **Frozen Boundaries**: Protocol codec, message envelope format, client roles, non-actuation keywords, M07–M27 frozen services.
- **Testability**: 100% unit-testable via mock transports and dispatcher messages.
- **Why It Should Be M28**: It is the single remaining external ingress vulnerability allowing cross-session privilege escalation and DoS.

---

### Candidate 2: Durable Persistence In-Memory Session Eviction & Capacity Reclamation
- **Title**: Durable Persistence In-Memory Session Eviction & Capacity Reclamation
- **Problem**: `DurableSessionStore._sessions` accumulates closed sessions in memory up to `MAX_DURABLE_SESSIONS = 16`. Once 16 sessions run, all subsequent `start_session` calls fail with `PersistenceCapacityError`.
- **Source Evidence**: `python/holomed/persistence/sessions.py:76-80`.
- **Severity**: **HIGH** (Capacity DoS after 16 sessions).
- **Why It Should Not Be M28 First**: Persistence capacity exhaustion occurs after 16 distinct surgeries, whereas Candidate 1 is an active cross-session security injection vulnerability at the system perimeter.

---

### Candidate 3: Tool Execution Engine Session Sequence Eviction
- **Title**: Tool Execution Engine Session Sequence Eviction
- **Problem**: `ToolExecutionEngine._session_sequences` retains monotonic sequence state for up to `MAX_ACTIVE_SESSIONS = 64` sessions without eviction on teardown.
- **Source Evidence**: `python/holomed/tools/engine.py:60-64`.
- **Severity**: **MEDIUM**.
- **Why It Should Not Be M28 First**: Large boundary (64 sessions), internal tool subsystem only, no cross-session injection risk.

---

## 20. HOSTILE SELF-CHALLENGE

- **Is Candidate 1 theoretical?**
  No. It is 100% reproducible today: an authenticated client in `GatewayService` can construct any envelope targeting another session, and `GatewayAuthorizationPolicy.authorize_message()` will return cleanly without raising any error.
- **Does Candidate 1 introduce a new security boundary?**
  No. The security boundary already exists (`ClientSession.session_id`); the authorization policy merely failed to enforce it on envelope payload parameters.
- **Does Candidate 1 require excessive reopening?**
  No. It only touches `holomed/gateway/authorization.py`, `holomed/gateway/service.py`, and adds an optional invocation in `holomed/execution/service.py`.

---

## 21. M28 JUSTIFICATION STANDARD

Candidate 1 satisfies all criteria:
- **Concrete Source Evidence**: Verified in `gateway/authorization.py:33` and `gateway/service.py:284`.
- **Real-World Impact**: Eliminates cross-session command injection, stops presentation frame leakage, and prevents connection capacity exhaustion.
- **Bounded Scope**: Strictly confined to Gateway ingress and teardown wiring.
- **Testable**: Easily proved via hostile integration tests.

---

## FINAL CLASSIFICATION

```
==================================================
M28_JUSTIFIED
==================================================
```

**Proposed Milestone**:
**M28 — Gateway Ingress Security & Connection Lifecycle Hardening**
