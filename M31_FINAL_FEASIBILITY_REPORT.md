# M31 Final Feasibility Report: Gateway Ingress & Administrative Boundary Hardening

**Authoritative Baseline**: `2a8cc1d070d76b469cb5ccc750e2b06a2fe3ab75`  
**Previous Release**: M30 (`feat(M30): harden safety gate dispatcher boundary`)  
**Frozen Predecessors**: M19–M30  
**Audit Mode**: READ ONLY — FORENSIC PROOF & ARCHITECTURAL CONTRACT SPECIFICATION  
**Deliverable**: `M31_FINAL_FEASIBILITY_REPORT.md`  
**Final Classification**: `READY_FOR_LOCK`

---

## 1. Proof of `gateway.disconnect` Cross-Session Attack

### A. Execution Trace Analysis
The actual code execution path through the live repository was traced from wire ingress to connection mutation:
1. **Network Ingress**: Client frame arrives on `ITransport` $\rightarrow$ parsed by `FrameParser` $\rightarrow$ deserialized to `MessageEnvelope` $\rightarrow$ passed to `GatewayService._handle_client_message(connection, envelope)`.
2. **Authorization Boundary**: [`GatewayAuthorizationPolicy.authorize_message(session, envelope)`](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/gateway/authorization.py#L31):
   - Check 1 (Source spoofing): `envelope.source == session.client_id` (Passes).
   - Check 2 (Session spoofing): `if isinstance(envelope.payload, dict) and "session_id" in envelope.payload:` — **The command payload `{"client_id": "CLIENT-B"}` contains NO `"session_id"` key.** Check 2 is skipped completely.
   - Check 3 (Actuation keyword): `disconnect` matches no forbidden surgical actuation keywords (Passes).
   - Check 4 (Role check): `ClientRole.SURGEON_CONSOLE` and `ClientRole.ASSISTANT_PANEL` are authorized to issue commands (Passes).
3. **Dispatcher Delivery**: Envelope dispatched to `MessageDispatcher.dispatch(envelope)` $\rightarrow$ matched to concrete route `gateway.disconnect` registered at [`GatewayService.handle_disconnect_command`](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/gateway/service.py#L459).
4. **Target Lookup & State Mutation**:
   ```python
   # python/holomed/gateway/service.py:459-466
   def handle_disconnect_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
       client_id = command_envelope.payload.get("client_id")
       reason = command_envelope.payload.get("reason", "Operator disconnect command")
       if not client_id:
           return create_error_response(command_envelope, self.name, "ERR_INVALID_ARGS", "Missing client_id")

       self.disconnect_client(str(client_id), str(reason))
       return create_response(command_envelope, self.name, payload={"disconnected_client_id": client_id})
   ```
   [`disconnect_client()`](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/gateway/service.py#L366-L372) pops `client_id` from `self._connections`, executes `conn.close()` (closing transport socket/stream), and marks connection state `ConnectionState.CLOSED`.

### B. Empirical Verification
An executable in-memory attack test proved that when `CLIENT-A` authenticated in `SESSION-A` sends `gateway.disconnect` targeting `CLIENT-B` authenticated in `SESSION-B`:
- Gateway authorization passes without error.
- MessageDispatcher dispatches the command.
- `CLIENT-B` connection state immediately transitions to `CLOSED`.
- `CLIENT-B` is removed from `GatewayService._connections`.
- Downstream protection: **None exists in the codebase.**

### C. Threat Impact Classification
- **Availability (CRITICAL)**: An auxiliary or compromised console in one OR can terminate the primary surgical console in another OR during live surgery, causing immediate loss of visualization, tracking updates, and workflow interaction.
- **Safety (CRITICAL)**: Abrupt severance of surgical guidance while an instrument is engaged in patient anatomy creates unmitigated clinical risk.
- **Integrity (HIGH)**: The victim session's connection descriptor table is corrupted, potentially triggering capacity lockouts (`MAX_CONNECTIONS_PER_SESSION`) upon reconnection attempts.
- **Privacy (MEDIUM)**: Confirms presence and state of target client identifier.

---

## 2. Proof of `gateway.clients` Cross-Session Metadata Leak

### A. Execution Trace & Disclosed Information
A client issues query `gateway.clients`. The request passes `GatewayAuthorizationPolicy` because queries are permitted for all roles, including `READ_ONLY_OBSERVER`.
`GatewayService.handle_clients_query` executes:
```python
# python/holomed/gateway/service.py:447-457
def handle_clients_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
    clients = [
        {
            "client_id": conn.client_id,
            "client_role": conn.client_role.value if conn.client_role else None,
            "session_id": conn.session.session_id if conn.session else None,
            "queue_depth": conn.queue_depth,
        }
        for cid, conn in sorted(self._connections.items())
    ]
    return create_response(query_envelope, self.name, payload={"clients": clients})
```
Disclosed metadata includes:
- Active `client_id` for every connected console across all hospital sessions.
- Assigned `client_role` (`SURGEON_CONSOLE`, `ASSISTANT_PANEL`, `XR_DISPLAY`, etc.).
- Active `session_id` (frequently embedding patient case IDs or procedure markers).
- Transport `queue_depth`.

### B. Observer Privilege Escalation
A low-privilege `READ_ONLY_OBSERVER` device connected to `SESSION-A` obtains full reconnaissance data on all active clinical sessions hospital-wide.

### C. Policy Assessment
This global visibility is **not** an intentional clinical requirement. Gateway connections represent clinical client endpoints attached to specific operating room sessions. Cross-session exposure violates tenant isolation and facilitates targeted denial of service attacks by exposing victim `client_id` handles.

---

## 3. Proof of `tools.reset` External Reset Vulnerability

### A. Execution Trace Analysis
1. `ToolService` registers `tools.reset` as a public dispatcher command:
   ```python
   # python/holomed/tools/service.py:161-163
   self._dispatcher.register_command_handler(
       "tools.reset", self.handle_reset_command, self.name
   )
   ```
2. External client sends `tools.reset` with payload `{"epoch_id": 1}` via Gateway transport.
3. `GatewayAuthorizationPolicy` permits the message: `"session_id"` is not present, no forbidden actuation keywords exist, and the caller is a non-observer role.
4. `ToolService.handle_reset_command` verifies only that `req_epoch == self._epoch_id` and invokes `self.clear()`.
5. `self.clear()` delegates to `self._engine.clear()`:
   ```python
   # python/holomed/tools/engine.py:184-188
   def clear(self) -> None:
       """Clear all active sessions and result history."""
       self._session_sequences.clear()
       self._result_history.clear()
   ```

### B. Empirical Verification
An executable test confirmed that when `CLIENT-A` in `SESSION-A` issues `tools.reset`:
- `ToolExecutionEngine._session_sequences` for concurrent session `SESSION-B` (set to sequence 42) is completely wiped to `{}`.
- Global `_result_history` is cleared.
- Capability required: **None.**
- Session binding: **None.**
- Role restriction: **None beyond non-observer.**

### C. Impact
Wiping active session sequence numbers breaks contiguous sequence monotonicity ($seq_{k+1} = seq_k + 1$), enabling sequence replay attacks and destabilizing active surgical tool execution.

---

## 4. Complete Inventory of Dispatcher Routes & Classification

All 75 routes reachable through `GatewayService._handle_client_message` were audited and classified:

### A. Clinical Execution & Orchestration (Client-Issuable, Session-Scoped)
| Route Name | Type | Classification | Required Role | Session Bound |
|---|---|---|---|---|
| `execution.navigation.execute` | COMMAND | `CLINICAL_MUTATION` | SURGEON_CONSOLE, ASSISTANT_PANEL | YES |
| `execution.planning.execute` | COMMAND | `CLINICAL_MUTATION` | SURGEON_CONSOLE, ASSISTANT_PANEL | YES |
| `execution.recovery.execute` | COMMAND | `CLINICAL_MUTATION` | SURGEON_CONSOLE, ASSISTANT_PANEL | YES |
| `execution.registration.execute`| COMMAND | `CLINICAL_MUTATION` | SURGEON_CONSOLE, ASSISTANT_PANEL | YES |
| `execution.session.teardown` | COMMAND | `LIFECYCLE` | SURGEON_CONSOLE | YES |
| `execution.tool.invoke` | COMMAND | `CLINICAL_MUTATION` | SURGEON_CONSOLE, ASSISTANT_PANEL | YES |
| `execution.trajectory.bind` | COMMAND | `CLINICAL_MUTATION` | SURGEON_CONSOLE, ASSISTANT_PANEL | YES |
| `execution.workflow.resume` | COMMAND | `CLINICAL_MUTATION` | SURGEON_CONSOLE, ASSISTANT_PANEL | YES |
| `execution.status.get` | QUERY | `READ_ONLY` | ALL ROLES | YES |
| `workflow.start` | COMMAND | `LIFECYCLE` | SURGEON_CONSOLE, ASSISTANT_PANEL | YES |
| `workflow.transition` | COMMAND | `LIFECYCLE` | SURGEON_CONSOLE, ASSISTANT_PANEL | YES |
| `workflow.confirm` | COMMAND | `LIFECYCLE` | SURGEON_CONSOLE ONLY | YES |
| `workflow.abort` | COMMAND | `LIFECYCLE` | SURGEON_CONSOLE, ASSISTANT_PANEL | YES |
| `workflow.interlock.trip` | COMMAND | `LIFECYCLE` | SURGEON_CONSOLE, ASSISTANT_PANEL | YES |
| `workflow.status` | QUERY | `READ_ONLY` | ALL ROLES | YES |

### B. Subsystem Status & Query Routes (Client-Issuable, Read-Only)
| Route Name | Type | Classification | Required Role | Session Bound |
|---|---|---|---|---|
| `navigation.status.get` | QUERY | `READ_ONLY` | ALL ROLES | YES |
| `planning.get` | QUERY | `READ_ONLY` | ALL ROLES | YES |
| `recovery.status.get` | QUERY | `READ_ONLY` | ALL ROLES | YES |
| `registration.get` | QUERY | `READ_ONLY` | ALL ROLES | YES |
| `safety.status.get` | QUERY | `READ_ONLY` | ALL ROLES | YES |
| `drift.status.get` | QUERY | `READ_ONLY` | ALL ROLES | YES |
| `drift.landmarks.get` | QUERY | `READ_ONLY` | ALL ROLES | YES |
| `proximity.status.get` | QUERY | `READ_ONLY` | ALL ROLES | YES |
| `proximity.zones.get` | QUERY | `READ_ONLY` | ALL ROLES | YES |
| `tools.status` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `tools.registry` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `tools.result` | QUERY | `READ_ONLY` | ALL ROLES | SESSION_SCOPED |
| `persistence.status` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `persistence.session.get` | QUERY | `READ_ONLY` | ALL ROLES | YES |
| `persistence.cycle.get` | QUERY | `READ_ONLY` | ALL ROLES | YES |
| `xr.status` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `xr.node` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `xr.viewport.status` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `xr.frame` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `anatomy.status` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `anatomy.entity` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `anatomy.query` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `anatomy.simulation.status` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `audio.pipeline.status` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `audio.pipeline.audit` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `audio.tracker.tracks` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `gesture.pipeline.status` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `gesture.pipeline.audit` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `gesture.tracks` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `vision.pipeline.status` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `vision.pipeline.audit` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `vision.tracker.tracks` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `ultron.status` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `ultron.context` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `ultron.reasoning` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `ultron.audit` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `device.coordination.health` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `device.orchestration.status`| QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `device.orchestration.audit` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `platform.status` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `platform.audit` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `gateway.status` | QUERY | `READ_ONLY` | ALL ROLES | PUBLIC_SAFE |
| `gateway.clients` | QUERY | `READ_ONLY` | ALL ROLES | SESSION_SCOPED (M31) |

### C. Gateway Connection Management (Client-Issuable, Constrained)
| Route Name | Type | Classification | Required Role | Session Bound |
|---|---|---|---|---|
| `gateway.disconnect` | COMMAND | `ADMINISTRATIVE` | SURGEON_CONSOLE, ASSISTANT_PANEL | SESSION_SCOPED (M31) |

### D. Internal / Administrative / Destructive Routes (MUST NOT BE CLIENT-ISSUABLE)
| Route Name | Type | Classification | Risk Profile | M31 Disposition |
|---|---|---|---|---|
| `tools.reset` | COMMAND | `RESET/DESTRUCTIVE` | Global sequence wipe | **Deregister from Dispatcher** |
| `platform.reset` | COMMAND | `RESET/DESTRUCTIVE` | Supervisor epoch migration | Block at Gateway Ingress |
| `platform.cycle` | COMMAND | `ADMINISTRATIVE` | Stepping supervisor loop | Block at Gateway Ingress |
| `platform.session.start` | COMMAND | `LIFECYCLE` | Supervisor session control | Block at Gateway Ingress |
| `platform.session.stop` | COMMAND | `LIFECYCLE` | Supervisor session control | Block at Gateway Ingress |
| `persistence.cycle.record` | COMMAND | `INTERNAL_ONLY` | Synchronous state logging | Block at Gateway Ingress |
| `persistence.replay` | COMMAND | `ADMINISTRATIVE` | State journal reconstruction | Block at Gateway Ingress |
| `drift.evaluate` | COMMAND | `PERCEPTUAL_INPUT` | Landmark drift observation | Block at Gateway Ingress |
| `proximity.evaluate` | COMMAND | `PERCEPTUAL_INPUT` | Zone proximity observation | Block at Gateway Ingress |
| `anatomy.reset` | COMMAND | `RESET/DESTRUCTIVE` | Pipeline hardware reset | Block at Gateway Ingress |
| `audio.pipeline.reset` | COMMAND | `RESET/DESTRUCTIVE` | Pipeline hardware reset | Block at Gateway Ingress |
| `gesture.pipeline.reset`| COMMAND | `RESET/DESTRUCTIVE` | Pipeline hardware reset | Block at Gateway Ingress |
| `vision.pipeline.reset` | COMMAND | `RESET/DESTRUCTIVE` | Pipeline hardware reset | Block at Gateway Ingress |
| `xr.reset` | COMMAND | `RESET/DESTRUCTIVE` | Pipeline hardware reset | Block at Gateway Ingress |
| `ultron.reset` | COMMAND | `RESET/DESTRUCTIVE` | AI pipeline reset | Block at Gateway Ingress |

---

## 5. Gateway Ingress Allowlist Architecture

### A. Architectural Evaluation
1. **Option A: Explicit Client-Issuable Topic Allowlist (RECOMMENDED)**:
   - *Design*: Maintain a frozen `frozenset[str]` of permitted external client message names in `GatewayAuthorizationPolicy`. Reject any incoming client message whose topic is not in the set with `GatewayAuthorizationError`.
   - *Security Strength*: Maximum (Default-Deny). Automatically defends against newly introduced internal commands.
   - *Maintainability*: High. Centralized, explicit, and audited.
   - *Migration Risk*: Low. Preserves 100% of legitimate client commands and queries.
2. **Option B: Explicit Denylist**:
   - *Critique*: Inherently fragile; default-allow posture guarantees future vulnerabilities when new internal topics are created.
3. **Option C: Per-Route Dispatcher Metadata**:
   - *Critique*: Requires modifying `register_command_handler()` and `register_query_handler()` across all 24 services, violating frozen milestones.
4. **Option D: Dual Dispatchers (Public vs Internal)**:
   - *Critique*: Excessive complexity, event routing duplication, and high regression risk.

### B. Recommendation
Adopt **Option A**. It provides the strongest security guarantee with the minimal code footprint and zero disruption to the frozen internal microkernel.

---

## 6. `gateway.disconnect` Design Specification

### A. Authorization Invariants
1. When `gateway.disconnect` is processed:
   - The command envelope payload MUST contain a valid `client_id` string.
   - `GatewayService` looks up the target connection for `client_id`.
   - If the target connection does not exist, return `ERR_CLIENT_NOT_FOUND`.
   - **Session Verification**: The target connection's `session_id` MUST match the authenticated caller's `session_id`.
     $$\text{target\_conn.session.session\_id} == \text{caller\_session.session\_id}$$
   - If there is a mismatch, raise `GatewaySessionMismatchError` and fail closed.
2. **Role Hierarchy**:
   - An `ASSISTANT_PANEL` is prohibited from disconnecting a `SURGEON_CONSOLE`.
   - A `SURGEON_CONSOLE` may disconnect subordinate panels or its own session connections.
   - A client may always disconnect itself (`client_id == caller_session.client_id`).
3. **Cross-Session Administration**:
   - There is NO valid clinical use case for an external client in one operating room to disconnect a console in another operating room. Platform-level evictions occur via `ClinicalExecutionGatewayService.execute_session_teardown()` $\rightarrow$ `GatewayService.evict_session()`, which operates in-process and does not rely on wire commands.

---

## 7. `gateway.clients` Design Specification

### A. Scoped Visibility Model
1. Default behavior for `gateway.clients`:
   - `handle_clients_query()` filters active connections strictly by caller session:
     ```python
     caller_session_id = query_envelope.payload.get("session_id") or ...
     # Filter connections strictly matching caller_session_id
     ```
   - A client attached to `SESSION-A` receives ONLY connections attached to `SESSION-A`.
2. Global administrative queries:
   - Permitted only if the query originates from an internal in-process service or supervisor context (where `query_envelope.source` is an internal service name, e.g. `platform_service` or `monitoring_service`).
   - Network clients arriving via `GatewayService.process_client_ingress()` are strictly scoped to their authenticated `session.session_id`.

---

## 8. `tools.reset` Design Specification

### A. Disposition: Complete Deregistration from MessageDispatcher
1. Remove `self._dispatcher.register_command_handler("tools.reset", ...)` from `ToolService.initialize()`.
2. Remove `ToolService.handle_reset_command()`.
3. In-process lifecycle management:
   - Session sequence eviction is already handled safely by `ToolService.evict_session(session_id, capability)` during Step 12 of `execute_session_teardown()`.
   - Global epoch resets remain accessible via `ToolService.reset(epoch_id)` when invoked by the platform supervisor during service transitions.
4. Consequence: The backdoor enabling external or cross-session sequence destruction is permanently closed.

---

## 9. Capability Security & Confused-Deputy Audit

| Operation | Current Capability Status | Confused-Deputy Vulnerability | M31 Hardened State |
|---|---|---|---|
| `gateway.disconnect` | None | Gateway forwards external disconnect command to kill arbitrary cross-session targets | Target connection session verified against caller session before mutation |
| `gateway.clients` | None | Gateway leaks all OR connections to any observer | Output filtered strictly to caller's authenticated session |
| `tools.reset` | None | Dispatcher exposes global state wipe to external clients | Route eliminated from dispatcher entirely |

---

## 10. Gateway Session Binding Interaction & Target Identity Forensics

M28 enforced: `if "session_id" in envelope.payload: payload_session_id == session.session_id`.
To prevent target identity migration from `session_id` to secondary fields:

1. **`client_id` (in `gateway.disconnect`)**:
   - Must be resolved to target connection's session and asserted equal to authenticated `session.session_id`.
2. **`plan_id` (in `planning.get`)**:
   - `planning.get` must require `session_id` or verify that the requested `plan_id` is bound to the caller's authenticated session via `_session_plan_bindings`.
3. **`invocation_id` (in `tools.result`)**:
   - `tools.result` query must require `session_id` in payload, ensuring M28 session matching protects tool result queries.

---

## 11. Stale Connection & Disconnect Races

1. `GatewayConnection` instances maintain a unique `connection_id = str(uuid.uuid4())`.
2. Even if a `client_id` string is reused in a subsequent session, `gateway.disconnect` strictly validates `target_conn.session.session_id == caller_session.session_id`.
3. A delayed or replayed disconnect command from an old session cannot terminate a connection in a new session because the session IDs will not match.

---

## 12. Reset & Administrative Command Inventory

Auditing all commands with destructive or administrative patterns:
- `platform.reset`: Supervisor epoch migration $\rightarrow$ Blocked by Gateway Ingress Allowlist.
- `platform.cycle`: Supervisor cycle dispatch $\rightarrow$ Blocked by Gateway Ingress Allowlist.
- `tools.reset`: Engine sequence clear $\rightarrow$ Deregistered from MessageDispatcher.
- `anatomy.reset`, `audio.pipeline.reset`, `gesture.pipeline.reset`, `vision.pipeline.reset`, `xr.reset`, `ultron.reset`: Pipeline state clears $\rightarrow$ Blocked by Gateway Ingress Allowlist.
- `persistence.replay`: Journal replay $\rightarrow$ Blocked by Gateway Ingress Allowlist.

---

## 13. Session Metadata Privacy Classification

| Data Leak | Endpoint | Severity | M31 Mitigation |
|---|---|---|---|
| Cross-session Client IDs | `gateway.clients` | HIGH | Scope query output to caller's `session_id` |
| Cross-session Session IDs | `gateway.clients` | HIGH | Scope query output to caller's `session_id` |
| Cross-session Client Roles | `gateway.clients` | MEDIUM | Scope query output to caller's `session_id` |
| Unbound Surgical Plan Metadata | `planning.get` | MEDIUM | Require `session_id` matching in Gateway |
| Unbound Tool Execution Results | `tools.result` | LOW | Require `session_id` matching in Gateway |

---

## 14. Failure Semantics

All security checks fail closed before state mutation:
- **Forbidden Ingress Route**: Raise `GatewayAuthorizationError("Route '...' is not permitted through gateway ingress")` $\rightarrow$ return `ERR_GATEWAY_UNAUTHORIZED_ROUTE`.
- **Cross-Session Disconnect Attempt**: Raise `GatewaySessionMismatchError("Cross-session disconnect rejected: target client belongs to session '...', authenticated caller is '...'")` $\rightarrow$ return `ERR_GATEWAY_SESSION_MISMATCH`.
- **Unknown Target Client**: Return error response `ERR_CLIENT_NOT_FOUND`.
- **Role Hierarchy Violation**: Raise `GatewayAuthorizationError("ASSISTANT_PANEL cannot disconnect SURGEON_CONSOLE")` $\rightarrow$ return `ERR_GATEWAY_AUTHORIZATION`.

---

## 15. Audit & Observability

When an unauthorized route or cross-session disconnect is rejected:
1. `GatewayService` emits event `gateway.ingress.rejected` with payload:
   ```json
   {
     "client_id": "...",
     "session_id": "...",
     "attempted_route": "...",
     "reason": "..."
   }
   ```
2. Rejections are logged through `SecretFilter` to avoid credential leakage.

---

## 16. Minimum Reopen Set

The minimum production files required to implement M31:

1. `python/holomed/gateway/authorization.py`:
   - Add `CLIENT_ISSUABLE_ROUTES: frozenset[str]`.
   - Add allowlist enforcement in `authorize_message()`.
2. `python/holomed/gateway/service.py`:
   - Scope `handle_clients_query()` to authenticated caller session.
   - Enforce session match in `handle_disconnect_command()`.
   - Enforce role hierarchy in `handle_disconnect_command()`.
3. `python/holomed/tools/service.py`:
   - Remove `tools.reset` registration from `initialize()`.
   - Remove `handle_reset_command()`.

**Authorized Test Files**:
- `tests/unit/gateway/test_m31_gateway_boundary.py` (New dedicated M31 suite)
- `tests/unit/gateway/test_gateway_authorization.py` (Update for allowlist)
- `tests/unit/tools/test_tool_service.py` (Verify `tools.reset` deregistration)

**Zero other production files required.** `ClinicalExecutionGatewayService`, `MessageDispatcher`, `WorkflowService`, and `SafetyGateService` remain untouched.

---

## 17. Frozen Boundaries

- **M28 Session Binding**: Fully preserved and reinforced.
- **M30 SafetyGate Boundary**: Fully preserved; safety gate evaluation remains behind execution gateway.
- **M29 Tool Subsystem Lifecycle**: Fully preserved; `evict_session(session_id, cap)` remains the authoritative teardown path.
- **M25–M29 Teardown Ordering Contract**: Untouched (12-step sequence intact).
- **Capability Architecture**: Untouched.

---

## 18. Candidate Consolidation Assessment

Findings 1 (`gateway.disconnect`), 2 (`gateway.clients`), 3 (`tools.reset`), and 4 (ingress allowlist) form a single, coherent security perimeter: **the external-to-internal boundary**.
- Disconnect and client enumeration weaknesses exist inside `GatewayService`.
- `tools.reset` is directly exploitable through the Gateway ingress.
- An ingress allowlist in `GatewayAuthorizationPolicy` immediately neutralizes `tools.reset` and all other administrative resets at the network boundary, while deregistering `tools.reset` eliminates it from the internal dispatcher.

These findings are tightly coupled and belong strictly together in M31.

---

## 19. Hostile Self-Challenge

1. **Does the allowlist break existing integration tests?**
   - *Test Analysis*: All integration tests simulating surgeon console commands use routes in the allowlist (`execution.*`, `workflow.*`, queries). Administrative setup in tests executes directly via Python service handles, not via mock client ingress envelopes.
2. **Is `tools.reset` needed by any test?**
   - *Grep Proof*: `tools.reset` is referenced in zero unit or integration tests across the repository.
3. **Could an operator legitimately need to disconnect a client in another OR?**
   - *Clinical Analysis*: In an operating theatre, OR sessions are strictly segregated. Allowing one OR to terminate connections in another is an unacceptable hazard.
4. **Does filtering `gateway.clients` break platform health monitoring?**
   - *Analysis*: `PlatformService` and `MonitoringService` query service health via internal Python method calls (`service.health()`), not via client wire queries.

---

## 20. Prelock Contract Proposal

```markdown
### M31 Prelock Contract: Gateway Ingress & Administrative Boundary Hardening

1. Ingress Topic Allowlist:
   - GatewayAuthorizationPolicy enforces CLIENT_ISSUABLE_ROUTES (default-deny).
   - All internal reset commands (platform.reset, tools.reset, pipeline resets) blocked at ingress.

2. gateway.disconnect Session Boundary:
   - Must resolve target connection and verify target.session_id == caller.session_id.
   - ASSISTANT_PANEL cannot disconnect SURGEON_CONSOLE.
   - Cross-session disconnect attempts rejected with GatewaySessionMismatchError.

3. gateway.clients Session Scoping:
   - Query response filtered strictly to caller's authenticated session_id.
   - Cross-session client enumeration eliminated.

4. tools.reset Removal:
   - Dispatcher registration for tools.reset removed from ToolService.
   - ToolExecutionEngine sequence state protected from unmediated external wiping.

5. Regression Safety:
   - 1,625 platform tests must pass.
   - 0 unauthorized production files modified.
```

---

## 21. Final Classification

```
================================================================================
FINAL CLASSIFICATION: READY_FOR_LOCK
================================================================================
```

The feasibility audit is complete, the vulnerabilities are proven, the allowlist is inventoried, and the prelock contract is defined without ambiguity. M31 is ready for contract lock.
