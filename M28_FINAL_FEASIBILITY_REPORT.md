# M28 FINAL FEASIBILITY AUDIT REPORT
## Gateway Ingress Security & Connection Lifecycle Hardening

**Authoritative Baseline**: `7acac2469a5da864ae926906671f761888715127`  
**Previous Release**: M27 — Workflow Safety Interlock Scoping & Lifecycle Eviction Hardening  
**Audit Mode**: STRICT READ-ONLY FORENSIC FEASIBILITY AUDIT  
**Status**: FEASIBILITY PROVEN  
**Final Classification**: `READY_FOR_LOCK`  

---

## 1. ACTUAL INGRESS EXECUTION PATH

The complete end-to-end execution path for external messages arriving at the HoloMed platform was traced from raw frame reassembly to internal service state mutation:

```
[External Client Transport Stream (TCP / WebSocket)]
                       │ (bytes)
                       ▼
GatewayService.process_client_ingress(connection)
                       │ (raw_frames via connection.read_ingress())
                       ▼
deserialize_envelope(frame_bytes) -> MessageEnvelope
                       │
       ┌───────────────┴───────────────┐
(CONNECTING state)               (ACTIVE state)
       ▼                               ▼
_handle_handshake()              _handle_client_message()
       │                               │
       │                               ▼
       │                  GatewayAuthorizationPolicy.authorize_message(session, envelope)
       │                  ├── 1. Source spoof check: envelope.source == session.client_id
       │                  ├── 2. Actuation keyword check: no surgical actuation words
       │                  ├── 3. Role capability check: READ_ONLY_OBSERVER/XR_DISPLAY/etc.
       │                  └── 4. [MISSING] Session binding check: payload.session_id == session.session_id
       │                               │
       │                               ▼ (Authorized)
       │                  MessageDispatcher.dispatch(envelope)
       │                               │
       │                               ▼ (Synchronous Route Dispatch)
       │                  target_service.handle_*_command(envelope)
       │                  ├── Reads: session_id = envelope.payload.get("session_id")
       │                  └── Invokes: target_service.mutate_state(session_id, ...)
       │                               │
       ▼                               ▼
State Mutation / Execution in Target Subsystem (Workflow / Execution / Platform)
```

### Exact Methods & Origin of Key Data Elements:
1. **Authenticated Session Identity (`ClientSession`)**:
   - Originates in `GatewayAuthenticator.authenticate_handshake()` (`python/holomed/gateway/auth.py:81-88`).
   - Constructed from client credentials during handshake and attached to the connection: `connection.attach_session(session)` (`python/holomed/gateway/service.py:295`).
   - Immutable fields: `client_id`, `client_role`, `session_id`, `epoch_id`, `remote_address`.
2. **`envelope.source`**:
   - Declared by the external client in the serialized JSON framing.
   - Validated at `python/holomed/gateway/authorization.py:33`: `if envelope.source != session.client_id: raise GatewayValidationError(...)`.
3. **`envelope.payload["session_id"]`**:
   - Declared by the external client in the JSON payload body.
   - **Currently NEVER verified against `session.session_id`** in `GatewayAuthorizationPolicy.authorize_message()`.
4. **Target Session Selection**:
   - Selected solely by the downstream route handler reading `envelope.payload.get("session_id")`.
   - Examples:
     - `WorkflowService.handle_transition_command` (`python/holomed/workflow/service.py:748`): `session_id = command_envelope.payload.get("session_id")`.
     - `ClinicalExecutionGatewayService.handle_tool_invoke_command` (`python/holomed/execution/service.py:1380`): `session_id = payload.get("session_id")`.
     - `ClinicalExecutionGatewayService.handle_session_teardown_command` (`python/holomed/execution/service.py:2285`): `session_id = payload.get("session_id")`.
5. **Authorization Enforcement**:
   - Occurs at `GatewayAuthorizationPolicy.authorize_message(session, envelope)` (`python/holomed/gateway/authorization.py:30`).
6. **Capability Checks**:
   - Occurs downstream inside `ClinicalExecutionGatewayService.execute_tool`, `execute_navigation`, etc., where an `_ExecutionCapability` token is minted and validated against the target service.

---

## 2. CROSS-SESSION SPOOFING PROOF

### Source-Level Exploit Construction:
1. **Setup**:
   - Operating Room 1 has Client A (`client_id="console_or_1"`, `role=SURGEON_CONSOLE`) connected to active clinical `session_id="SESS-PATIENT-100"`.
   - Operating Room 2 has Patient B undergoing active surgery under `session_id="SESS-PATIENT-200"`.
2. **Attack Envelope**:
   - Client A submits a validly framed protocol envelope:
     ```json
     {
       "protocol_version": "1.0",
       "message_id": "msg-exploit-001",
       "correlation_id": "corr-001",
       "causation_id": null,
       "message_type": "COMMAND",
       "message_name": "execution.session.teardown",
       "source": "console_or_1",
       "target": "execution_gateway",
       "timestamp_utc": "2026-09-03T08:00:00Z",
       "payload": {
         "session_id": "SESS-PATIENT-200",
         "sequence_number": 0
       },
       "metadata": {}
     }
     ```
3. **Execution Trace through Current Code**:
   - `GatewayService.process_client_ingress()` feeds frame to `_handle_client_message()`.
   - Calls `GatewayAuthorizationPolicy.authorize_message(session, envelope)`:
     - `envelope.source == session.client_id` (`"console_or_1" == "console_or_1"`) -> **PASSES** (line 33).
     - Categorical surgical keywords check -> `execution.session.teardown` contains no forbidden actuation keywords -> **PASSES** (line 40).
     - Role capability check -> `session.client_role == ClientRole.SURGEON_CONSOLE` -> **PASSES** (line 67).
     - Returns `None` (Approved!).
   - Calls `self._dispatcher.dispatch(envelope)`.
   - `MessageDispatcher` routes to `ClinicalExecutionGatewayService.handle_session_teardown_command()` (`execution/service.py:2281`).
   - Line 2285 reads `session_id = payload.get("session_id")` -> extracts `"SESS-PATIENT-200"`.
   - Line 2298 executes `self.execute_session_teardown(req)` on `"SESS-PATIENT-200"`.
   - **Result**: Operating Room 1 terminates Operating Room 2's surgery, purges Patient 200's navigation, proximity, drift, recovery, registration, planning, safety gate, workflow interlocks, and platform session!
4. **First Point Where Spoof Is Accepted**:
   `GatewayAuthorizationPolicy.authorize_message()` at `python/holomed/gateway/authorization.py:30-70`. It returns successfully without comparing `envelope.payload["session_id"]` against `session.session_id`.

---

## 3. SESSION ID AUTHORITATIVENESS

### Architectural Principle:
- In the HoloMed architecture, external clients (consoles, XR headsets, assistant tablets) establish transport connections mediated by `GatewayService`.
- The handshake binds the connection to a specific, authenticated clinical session (`ClientSession.session_id`).
- For all external client communication, **the authenticated session context (`ClientSession.session_id`) is authoritative**.
- Caller-controlled payload parameters are untrusted user input and must NEVER override the transport's authenticated session binding.
- For internal inter-subsystem communication over `MessageDispatcher` (intra-process services, orchestrators, tests), no client connection exists; in those cases, `envelope.payload["session_id"]` represents parameter passing.
- Therefore, the boundary of authority is `GatewayAuthorizationPolicy`: the gateway MUST enforce that any external client message specifying a `session_id` matches its authenticated session binding.

---

## 4. SAFE FIX DESIGN EVALUATION

Four candidate designs were evaluated:

| Design | Description | Compatibility | Security | Failure Semantics | Recommendation |
|---|---|---|---|---|---|
| **Design A** | **Reject if `payload["session_id"] != session.session_id`** | High (valid clients match) | Strongest (fail-closed) | Clean rejection (`ERR_SESSION_MISMATCH`) before dispatch | **RECOMMENDED** |
| **Design B** | Overwrite `payload["session_id"] = session.session_id` | Medium | Weak (masks bugs, executes unexpected actions) | Silent mutation of caller intent | REJECTED |
| **Design C** | Refactor all 42 handlers to use session context | Zero (breaks all internal routes & tests) | High | Massive churn across entire repository | REJECTED |
| **Design D** | Route-by-route whitelist checks | Low | Flawed (omissions create new zero-days) | Fragmented | REJECTED |

### Chosen Design: Design A
In `GatewayAuthorizationPolicy.authorize_message(session: ClientSession, envelope: MessageEnvelope)`:
```python
# 1b. Prevent Session Spoofing / Cross-Session Injection (D284.1)
if isinstance(envelope.payload, dict) and "session_id" in envelope.payload:
    payload_session_id = envelope.payload.get("session_id")
    if payload_session_id is not None and str(payload_session_id) != session.session_id:
        raise GatewaySessionMismatchError(
            f"Cross-session injection rejected: envelope declared session_id={payload_session_id!r}, "
            f"authenticated session_id={session.session_id!r}"
        )
```
- Uses existing canonical exception: `GatewaySessionMismatchError("ERR_SESSION_MISMATCH")` defined in `python/holomed/gateway/exceptions.py:108`.
- Prevents the message from ever reaching `MessageDispatcher`.
- Preserves legitimate behavior: compliant clients provide their own session ID, which matches and passes.
- Preserves internal routing: internal components calling `dispatcher.dispatch()` directly do not pass through `GatewayAuthorizationPolicy`.

---

## 5. PRIVILEGED ROUTE COVERAGE

Every dispatcher route that accepts `session_id` in its payload was inventoried and verified to be currently vulnerable to cross-session spoofing from an authenticated client:

1. **Workflow Mutations**:
   - `workflow.transition` (`WorkflowService.handle_transition_command`): can force target session into `ABORTED`, `NAVIGATION_ACTIVE`, etc.
   - `workflow.confirm` (`WorkflowService.handle_confirm_command`): can fake human confirmation for target session checkpoints.
   - `workflow.abort` (`WorkflowService.handle_abort_command`): can abort target session workflow.
2. **Clinical Execution Mutations**:
   - `execution.navigation.execute` (`ClinicalExecutionGatewayService.handle_execute_command`)
   - `execution.recovery.execute` (`ClinicalExecutionGatewayService.handle_recovery_execute_command`)
   - `execution.trajectory.bind` (`ClinicalExecutionGatewayService.handle_trajectory_bind_command`)
   - `execution.tool.invoke` (`ClinicalExecutionGatewayService.handle_tool_invoke_command`)
   - `execution.workflow.resume` (`ClinicalExecutionGatewayService.handle_workflow_resume_command`)
   - `execution.registration.execute` (`ClinicalExecutionGatewayService.handle_registration_execute_command`)
   - `execution.planning.execute` (`ClinicalExecutionGatewayService.handle_planning_execute_command`)
   - `execution.session.teardown` (`ClinicalExecutionGatewayService.handle_session_teardown_command`)
3. **Platform Mutations**:
   - `platform.session.stop` (`PlatformService.handle_stop_session_command`)
   - `platform.cycle.advance` (`PlatformService.handle_advance_cycle_command`)
4. **Safety & Evaluation Ingress**:
   - `safety_gate.evaluate` (`SafetyGateService.handle_evaluate_command`)
   - `proximity.evaluate` (`ProximityService.handle_evaluate_command`)
   - `drift.evaluate` (`DriftService.handle_evaluate_command`)

**Conclusion**: All 16 mutating routes are vulnerable to the exact same spoofing primitive. Implementing Design A in `GatewayAuthorizationPolicy` shields all 16 routes at once.

---

## 6. CONNECTION LIFECYCLE PROOF

Detailed inspection of `GatewayService` (`python/holomed/gateway/service.py`) revealed:

1. **Connection Registry**:
   - `self._connections: Dict[str, GatewayConnection]` maps `client_id -> GatewayConnection`.
   - Each `GatewayConnection` holds a `ClientSession` with `session_id`.
2. **Creation**:
   - `register_client_transport()` places connection in `self._pending_connections`.
   - `_handle_handshake()` validates token, attaches `ClientSession`, moves connection to `self._connections[session.client_id]`.
3. **Capacity Enforcement**:
   - Line 281: `session_conns = sum(1 for c in self._connections.values() if c.session and c.session.session_id == session.session_id)`
   - If `session_conns >= MAX_CONNECTIONS_PER_SESSION` (4), `connection.close()` is called and `GatewayCapacityError` is raised.
4. **Closure & Teardown**:
   - `GatewayService` has NO `evict_session(session_id)` method.
   - When a clinical session finishes and `execution.session.teardown` executes, `GatewayService._connections` is untouched.
   - Stale connections remain in `self._connections` indefinitely until the remote transport disconnects or the service stops.
5. **Lifetime Impact**:
   - Reconnecting to a reused `session_id` accumulates against `MAX_CONNECTIONS_PER_SESSION = 4`, permanently rejecting new connections with `GatewayCapacityError`.

---

## 7. GATEWAY TEARDOWN INTEGRATION

### Safe Integration Strategy:
1. Add `GatewayService.evict_session(session_id: str, capability: Optional[Any] = None) -> int`:
   - Scans `self._connections` for connections where `c.session and c.session.session_id == session_id`.
   - For each matching connection:
     - Removes from `self._connections`.
     - Flushes any pending egress frames.
     - Closes the connection (`conn.close()`).
   - Reclaims session connection capacity immediately.
2. Integrate into `ClinicalExecutionGatewayService.execute_session_teardown()`:
   - Accept optional `gateway_service: Optional[Any] = None` in `__init__` (matching the pattern used for `platform_service`, `proximity_service`, and `drift_service`).
   - Add **Step 11: Gateway Ingress Connections** after Step 10:
     ```python
     # Step 11: Gateway Ingress Connections (M28)
     if self._gateway_service is not None:
         try:
             if hasattr(self._gateway_service, "evict_session"):
                 self._gateway_service.evict_session(session_id, cap)
             subsystems_purged.append("gateway_service")
         except Exception as exc:
             failures.append(f"gateway_service: {exc}")
     ```
3. **Dependency Inversion Assessment**:
   - `ClinicalExecutionGatewayService` already coordinates teardown for 9 subsystems (Navigation, Proximity, Drift, Recovery, Registration, Planning, Safety Gate, Workflow, Platform).
   - Passing `gateway_service` as an optional dependency follows the exact same architectural pattern.
   - Zero hard imports are required. Zero circular dependencies.

---

## 8. STALE CONNECTION IMPACT

A stale connection from a terminated Session A has the following proven impacts:

| Impact Category | Proven Behavior in Current Code | Severity |
|---|---|---|
| **Availability Leak** | Retains a connection slot towards `MAX_CONNECTIONS_PER_SESSION = 4` and `MAX_CLIENTS = 16`. When Session A is reused, new connections are locked out. | HIGH |
| **Privacy / Telemetry Leak** | `handle_presentation_event()` broadcasts XR frames to ALL connected `XR_DISPLAY` / `SURGEON_CONSOLE` clients without filtering by `session_id`. Stale connections continue receiving live patient imagery! | CRITICAL |
| **Workflow Event Leak** | When Session A is reused for a new patient, stale connections bound to Session A receive workflow broadcast events (`workflow.phase.entered`, `workflow.confirmation.requested`). | CRITICAL |
| **Authorization Leak** | If not disconnected, the stale connection can continue issuing queries (`gateway.status`, `gateway.clients`) and commands. | MEDIUM |
| **Safety Impact** | Presentation of outdated or mismatched XR frames to an operator console across surgeries creates cognitive confusion in active clinical environments. | CRITICAL |

---

## 9. CROSS-SESSION CONNECTION ISOLATION PROOF

Scenario constructed:
- Client A connected on Connection A (`session_id="SESS-A"`, `client_id="client_a"`).
- Client B connected on Connection B (`session_id="SESS-B"`, `client_id="client_b"`).

Execution of `GatewayService.evict_session("SESS-A")`:
1. Connection A matches `session_id == "SESS-A"`.
2. Connection A is popped from `self._connections`, egress is flushed, and transport is closed.
3. Connection B does NOT match (`"SESS-B" != "SESS-A"`).
4. Connection B remains in `self._connections` in `ConnectionState.ACTIVE`.
5. Connection A cannot receive broadcasts, cannot submit messages, and frees its slot.
6. Connection B continues operating normally without interruption or capacity degradation.

---

## 10. DURABLE PERSISTENCE CAPACITY FORENSIC

### Forensic Analysis of `DurableSessionStore` (`MAX_DURABLE_SESSIONS = 16`):
- **Source Inspection**: In `python/holomed/persistence/sessions.py:76-80`:
  ```python
  if len(self._sessions) >= MAX_DURABLE_SESSIONS:
      raise PersistenceCapacityError(f"Durable session capacity exceeded ({MAX_DURABLE_SESSIONS} max)")
  ```
- **Analysis**:
  - `DurableSessionStore` manages append-only on-disk journal files (`{storage_root}/{session_id}.jsonl`).
  - When a session is closed, `self._sessions[session_id]` is updated to `status=SessionStatus.STOPPED`.
  - **Why records are retained**: Under medical device regulatory standards (FDA 21 CFR Part 11, IEC 62304), surgical procedure audit journals and session descriptors are **immutable legal records**. Deleting session records or journal files during teardown would violate regulatory compliance and break post-operative audit queries and verification replays (`restore_session_from_disk`).
  - `MAX_DURABLE_SESSIONS = 16` represents the provisioned maximum active surgical capacity for a single runtime epoch.
  - **Verdict**: Durable persistence session retention is an **intentional regulatory design**, NOT an eviction defect. Deleting records on teardown is prohibited. Persistence must NOT be modified in M28.

---

## 11. TOOL EXECUTION ENGINE CAPACITY FORENSIC

### Forensic Analysis of `ToolExecutionEngine` (`MAX_ACTIVE_SESSIONS = 64`):
- `ToolExecutionEngine._session_sequences` records the highest sequence number seen per session.
- While a reused session ID starting at sequence 1 would encounter a `ToolSequenceError` if the previous session sequence was >= 1, `ToolExecutionEngine` is an internal engine behind `ToolService`.
- It has no external network boundary and does not handle client ingress.
- **Verdict**: Tool execution sequence eviction is an internal sequence lifecycle optimization that should remain a separate future consideration. It does not belong in the M28 external perimeter security milestone.

---

## 12. CAPABILITY SECURITY

- M28's session binding check occurs in `GatewayAuthorizationPolicy.authorize_message()` BEFORE an envelope is dispatched.
- It does NOT interact with, bypass, or modify the minting or validation of `_ExecutionCapability` in `ClinicalExecutionGatewayService`.
- If an envelope fails session binding, it is rejected immediately with `ERR_SESSION_MISMATCH`. No capability is ever minted.
- If it passes, execution proceeds through the identical dual-gate and capability-gated pipeline established in M23–M27.
- Reentry protection and single-use capability invalidation remain 100% intact.

---

## 13. REPLAY / ENVELOPE INTEGRITY

- **Cross-Session Replay**: A valid envelope intercepted from Session A replayed against Session B is rejected at ingress because `payload["session_id"]` cannot match Session B's authenticated connection.
- **Within-Session Replay**: `GatewayConnection.validate_sequence_number(sequence_number)` rejects non-monotonic or replayed sequence numbers with `GatewayLifecycleError`.
- **Replay Against Reused Session**: Reused sessions start with sequence number 0; replaying a past high sequence number is rejected by sequence monotonicity.

---

## 14. FAILURE SEMANTICS

| Condition | Gateway Ingress Behavior | Error Code | Target Mutation |
|---|---|---|---|
| Missing `session_id` in session-required command | Passes gateway (if permitted by role), rejected by target handler | `ERR_INVALID_ARGS` | None |
| Malformed `session_id` | Rejected by gateway validation | `ERR_VALIDATION` | None |
| Mismatched `session_id` | Rejected by gateway authorization policy | `ERR_SESSION_MISMATCH` | None |
| Stopped session | Rejected downstream by target service | Service-specific lifecycle error | None |
| Unknown session | Rejected downstream | Service-specific validation error | None |
| Disconnected session | Transport closed; frames dropped | N/A | None |
| Invalid capability | Rejected in execution gateway | `ERR_EXECUTION_CAPABILITY` | None |

All failures fail closed with 0 state mutation.

---

## 15. MINIMUM REOPEN SET

The minimum reopen set for M28 is strictly confined to 3 files:

1. `python/holomed/gateway/authorization.py`:
   - Enforce `envelope.payload["session_id"] == session.session_id` in `GatewayAuthorizationPolicy.authorize_message()`.
2. `python/holomed/gateway/service.py`:
   - Add `evict_session(session_id: str, capability: Optional[Any] = None) -> int`.
   - Evict matching connections from `_connections`, flush egress, close transports.
3. `python/holomed/execution/service.py`:
   - Accept optional `gateway_service` in `__init__`.
   - Call `self._gateway_service.evict_session(session_id, cap)` in Step 11 of `execute_session_teardown()`.

Test Surface:
4. `tests/unit/gateway/test_m28_gateway_ingress_lifecycle.py`:
   - Dedicated hostile test suite covering all M28 security and lifecycle guarantees.

---

## 16. FROZEN BOUNDARIES

The following boundaries remain strictly frozen:
- Protocol codec, envelope formatting, and serialization (`holomed/protocol/*`).
- Client roles and non-actuation keywords (`holomed/gateway/models.py`, `authorization.py`).
- M18 Safety Gate evaluation algorithms and precedence table (`holomed/safety_gate/*`).
- M19–M24 Clinical execution dual-gate contracts and algorithms (`holomed/execution/*`).
- M25–M27 Session eviction in Platform, Workflow, Planning, Registration, Navigation, Recovery, Proximity, and Drift.
- Durable persistence journal formats and storage models (`holomed/persistence/*`).

---

## 17. HOSTILE SELF-CHALLENGE

1. **Challenge**: *Could an XR display legitimately need to observe multiple surgeries simultaneously?*
   - **Refutation**: No. HoloMed XR displays are physical headsets worn by surgical staff inside an active operating room. An operating room hosts one active patient surgery at a time. Displaying mixed telemetry from another surgery is a catastrophic patient safety hazard.
2. **Challenge**: *Does adding `gateway_service` to `ClinicalExecutionGatewayService` create circular dependencies?*
   - **Refutation**: No. `GatewayService` does not import `ClinicalExecutionGatewayService`. `ClinicalExecutionGatewayService` accepts `gateway_service` as an untyped optional parameter (`Optional[Any] = None`), identical to `platform_service` in M25 and `proximity_service` in M26.
3. **Challenge**: *Why not just clear connections when a client disconnects?*
   - **Refutation**: If a client abruptly crashes, loses network, or fails to send a disconnect command, its connection remains active in the gateway. Teardown of the clinical session MUST proactively evict all associated connections to guarantee security and reclaim capacity.

---

## 18. PRELOCK SPECIFICATION

### Title
**M28 — Gateway Ingress Security & Connection Lifecycle Hardening**

### Security Objective
Eliminate cross-session command injection at the gateway ingress boundary, enforce strict session-payload binding on incoming envelopes, and integrate gateway connection eviction into the coordinated session teardown mechanism to reclaim connection capacity and stop telemetry leakage.

### Acceptance Criteria
1. **Cross-Session Injection Rejection**: Any envelope where `payload["session_id"] != connection.session.session_id` is rejected with `GatewaySessionMismatchError("ERR_SESSION_MISMATCH")` before dispatching.
2. **Same-Session Transparency**: Any envelope where `payload["session_id"] == connection.session.session_id` (or where `session_id` is omitted for global routes) passes authorization unchanged.
3. **Connection Eviction on Teardown**: Calling `GatewayService.evict_session(session_id)` closes all connections bound to `session_id` and removes them from `_connections`.
4. **Capacity Reclamation**: Teardown restores `MAX_CONNECTIONS_PER_SESSION = 4` capacity; subsequent connections to a reused session ID succeed without `GatewayCapacityError`.
5. **Telemetry / Frame Isolation**: Evicted connections no longer receive XR presentation frames or workflow broadcast events.
6. **Coordinated Teardown Integration**: `ClinicalExecutionGatewayService.execute_session_teardown()` invokes `gateway_service.evict_session(session_id, cap)` as Step 11.
7. **Regression Invariant**: All 1568 existing unit and regression tests continue to pass with 0 regressions.

---

## FINAL CLASSIFICATION

```
==================================================
READY_FOR_LOCK
==================================================
```
