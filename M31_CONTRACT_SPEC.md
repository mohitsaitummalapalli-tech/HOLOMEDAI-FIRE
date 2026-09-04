# M31 Contract Specification: Gateway Ingress Boundary & Subsystem Administrative Contract Hardening

**Authoritative Baseline**: `2a8cc1d070d76b469cb5ccc750e2b06a2fe3ab75`  
**Previous Milestones**: M19–M30 are strictly **FROZEN**.  
**Contract Status**: DESIGN ONLY — NO CODE / TEST MODIFICATIONS.

---

## 1. Overview & Objectives

Milestone M31 hardens the external network ingress boundary of the HoloMed platform and closes legacy administrative state-wiping routes on the message dispatcher.

M31 has four unified security and correctness objectives:
1. **Confine `gateway.disconnect` strictly to the caller's authenticated session** with role hierarchy enforcement, eliminating cross-session denial of service against active surgical consoles.
2. **Isolate `gateway.clients` query results strictly to the caller's authenticated session**, eliminating cross-session tenant reconnaissance and metadata exposure.
3. **Remove `tools.reset` from the public message dispatcher**, eliminating an unmediated command that purges sequence monotonicity tracking across all operating rooms.
4. **Establish a centralized, default-deny Client-Issuable Route Allowlist** in `GatewayAuthorizationPolicy`, preventing external ingress clients from reaching internal lifecycle, supervisor, and pipeline reset commands.

---

## 2. Minimum Reopen Set

### A. Production Files (Exactly 3 Files)
1. `python/holomed/gateway/authorization.py`
   - Define `CLIENT_ISSUABLE_ROUTES: frozenset[str]` (Default-Deny Ingress Policy).
   - Enforce allowlist check in `GatewayAuthorizationPolicy.authorize_message()`.
   - Preserve M28 session-binding and surgical actuation keyword filtering.
2. `python/holomed/gateway/service.py`
   - Scope `handle_clients_query()` strictly to caller's authenticated `session.session_id`.
   - Enforce target session matching in `handle_disconnect_command()`.
   - Enforce role hierarchy in `handle_disconnect_command()`.
3. `python/holomed/tools/service.py`
   - Remove `tools.reset` registration from `initialize()`.
   - Remove dead handler `handle_reset_command()`.

### B. Authorized Test Files (Exactly 3 Files)
1. `tests/unit/gateway/test_m31_gateway_boundary.py` (New dedicated M31 suite)
2. `tests/unit/gateway/test_gateway_authorization.py` (Update authorization tests)
3. `tests/unit/tools/test_tool_service.py` (Update tool service tests)

**No other production or test files are authorized.**

---

## 3. Detailed Contract Requirements

### A. `gateway.disconnect` Authorization Contract

#### Invariant A.1 (Session Boundary)
For any authenticated caller $C$ and target connection $T$:
$$\text{authenticated}(C) \land \text{disconnect}(C, T) \implies \text{session}(T) = \text{session}(C)$$
A client authenticated for `SESSION-A` MUST NEVER be able to disconnect or close a connection belonging to `SESSION-B`.

#### Invariant A.2 (Role Hierarchy)
Within the same session:
- A `SURGEON_CONSOLE` may disconnect any client within its session (subordinate panels or itself).
- An `ASSISTANT_PANEL` may disconnect subordinate panels or itself, but is **strictly prohibited** from disconnecting a `SURGEON_CONSOLE`.
- An `XR_DISPLAY` and `READ_ONLY_OBSERVER` cannot issue `gateway.disconnect` (blocked by general QUERY-only role restriction).
- A client may always disconnect itself ($T = C$).

#### Execution Semantics in `handle_disconnect_command`
1. Validate payload: `client_id = payload.get("client_id")`. If missing or not a non-empty string, return error response `ERR_INVALID_ARGS`.
2. Resolve target connection: `target_conn = self._connections.get(client_id)`.
   - If `target_conn is None`: return error response `ERR_CLIENT_NOT_FOUND` ("Client '...' not found").
   - If `target_conn.session is None`: return error response `ERR_INVALID_ARGS` ("Target connection lacks authenticated session").
3. Session check:
   - Identify caller session: The connection on which the ingress envelope arrived (`connection.session.session_id`) or envelope source matching.
   - If `target_conn.session.session_id != caller_session_id`:
     - Raise `GatewaySessionMismatchError` with error code `ERR_SESSION_MISMATCH`.
     - Zero state mutation: `target_conn` remains connected; transport is NOT closed.
4. Role hierarchy check:
   - If `target_conn.session.client_role == ClientRole.SURGEON_CONSOLE` and `caller_role != ClientRole.SURGEON_CONSOLE`:
     - Raise `GatewayAuthorizationError` with error code `ERR_AUTHORIZATION_FAILED`.
     - Zero state mutation.
5. Execution:
   - If all checks pass: `self.disconnect_client(client_id, reason)`.
   - Remove connection, invoke `conn.close()`, emit `gateway.client.disconnected`.
   - Return response envelope with payload `{"disconnected_client_id": client_id}`.

---

### B. `gateway.clients` Visibility Semantics

#### Invariant B.1 (Tenant Isolation)
For any caller $C$ arriving via external gateway ingress:
$$\text{VisibleConnections}(C) = \{ conn \in \text{Connections} \mid conn.session \neq \text{None} \land conn.session.session\_id = session(C) \}$$
No client may observe connections, client IDs, or roles belonging to other clinical sessions.

#### Execution Semantics in `handle_clients_query`
1. Extract caller session:
   - If arriving from external client ingress, the caller's bound session is `connection.session.session_id`.
   - If arriving via message envelope, `caller_session_id = envelope.payload.get("session_id")` or resolved from `envelope.source`.
2. Filter active connections:
   - Only include connections $conn \in self.\_connections$ where:
     $conn.session \neq \text{None} \land conn.session.session\_id == caller\_session\_id$.
3. Return payload:
   ```json
   {
     "clients": [
       {
         "client_id": "...",
         "client_role": "...",
         "session_id": "...",
         "queue_depth": 0
       }
     ]
   }
   ```
4. Behavior for edge cases:
   - Unauthenticated / pending connections: Never returned.
   - Empty session (caller is sole connection): Returns a single-element list containing the caller.
   - Closed / evicted connections: Excluded (already removed from `_connections`).

---

### C. `tools.reset` Public Dispatch Contract

#### Invariant C.1 (Removal of Unmediated Reset)
$$\text{external\_gateway\_client} \not\to \text{tools.reset}$$
The `tools.reset` command handler is **permanently deregistered** from `MessageDispatcher`.

#### Execution Semantics
1. In `ToolService.initialize()`:
   - Remove: `self._dispatcher.register_command_handler("tools.reset", self.handle_reset_command, self.name)`.
2. In `ToolService`:
   - Delete `handle_reset_command()`.
3. Preserved internal APIs:
   - `ToolService.evict_session(session_id, capability)` remains the authoritative path for teardown eviction, requiring an active `_ExecutionCapability` with `action == "SESSION_TEARDOWN"`.
   - `ToolService.reset(epoch_id)` remains available for in-process supervisor epoch transitions.
   - `ToolService.clear()` remains an in-process diagnostic helper for unit test fixtures.
   - No replacement public reset route is introduced.

---

### D. Gateway Client-Issuable Route Allowlist

#### Invariant D.1 (Default Deny)
For any message $m$ received at gateway ingress:
$$\text{client\_issuable}(m.message\_name) = \text{True} \iff m.message\_name \in \text{CLIENT\_ISSUABLE\_ROUTES}$$
If $m.message\_name \notin \text{CLIENT\_ISSUABLE\_ROUTES}$, the message is rejected **before** dispatcher forwarding.

#### Centralized Allowlist Definition
`python/holomed/gateway/authorization.py`:
```python
CLIENT_ISSUABLE_ROUTES: frozenset[str] = frozenset({
    # 1. Clinical Execution Orchestration (M19-M25)
    "execution.navigation.execute",
    "execution.planning.execute",
    "execution.recovery.execute",
    "execution.registration.execute",
    "execution.session.teardown",
    "execution.tool.invoke",
    "execution.trajectory.bind",
    "execution.workflow.resume",
    "execution.status.get",

    # 2. Workflow State Machine & Interlocks (M10)
    "workflow.start",
    "workflow.transition",
    "workflow.confirm",
    "workflow.abort",
    "workflow.interlock.trip",
    "workflow.status",

    # 3. Clinical Subsystem Queries (Read-Only)
    "navigation.status.get",
    "planning.get",
    "recovery.status.get",
    "registration.get",
    "safety.status.get",
    "drift.status.get",
    "drift.landmarks.get",
    "proximity.status.get",
    "proximity.zones.get",
    "tools.status",
    "tools.registry",
    "tools.result",
    "persistence.status",
    "persistence.session.get",
    "persistence.cycle.get",

    # 4. Gateway Diagnostics & Connection Management
    "gateway.status",
    "gateway.clients",
    "gateway.disconnect",

    # 5. Presentation & Perceptual Queries (Read-Only)
    "xr.status",
    "xr.node",
    "xr.viewport.status",
    "xr.frame",
    "anatomy.status",
    "anatomy.entity",
    "anatomy.query",
    "anatomy.simulation.status",
    "audio.pipeline.status",
    "audio.pipeline.audit",
    "audio.tracker.tracks",
    "gesture.pipeline.status",
    "gesture.pipeline.audit",
    "gesture.tracks",
    "vision.pipeline.status",
    "vision.pipeline.audit",
    "vision.tracker.tracks",
    "ultron.status",
    "ultron.context",
    "ultron.reasoning",
    "ultron.audit",
    "device.coordination.health",
    "device.orchestration.status",
    "device.orchestration.audit",
    "platform.status",
    "platform.audit",
})
```

#### Routes Explicitly Forbidden at Ingress
- `platform.reset` (Supervisor epoch reset)
- `platform.cycle` (Supervisor cycle driver)
- `platform.session.start`, `platform.session.stop` (Supervisor session orchestration)
- `persistence.cycle.record` (Internal supervisor telemetry)
- `persistence.replay` (State store reconstruction)
- `drift.evaluate` (Tracking hardware observation)
- `proximity.evaluate` (Tracking hardware observation)
- `anatomy.reset`, `audio.pipeline.reset`, `gesture.pipeline.reset`, `vision.pipeline.reset`, `xr.reset`, `ultron.reset` (Pipeline resets)
- `tools.reset` (Deregistered from dispatcher)

---

## 4. Authorization Evaluation Order at Gateway Ingress

Evaluation in `GatewayService._handle_client_message()` / `GatewayAuthorizationPolicy.authorize_message()` MUST proceed strictly in this sequence:

```
[Incoming Client Message]
        │
        ▼
1. Connection Authentication Check
   (Assert connection.state == ACTIVE and connection.session is not None)
        │
        ▼
2. Source Spoofing Check
   (Assert envelope.source == session.client_id)
        │
        ▼
3. Client-Issuable Route Allowlist Check (M31)
   (Assert envelope.message_name in CLIENT_ISSUABLE_ROUTES)
   [FAIL: GatewayAuthorizationError("ERR_AUTHORIZATION_FAILED")]
        │
        ▼
4. Categorical Surgical Actuation Check
   (Assert no keyword in CATEGORICAL_SURGICAL_KEYWORDS)
   [FAIL: GatewayAuthorizationError("ERR_AUTHORIZATION_FAILED")]
        │
        ▼
5. Role-Based Message Type Check
   (READ_ONLY_OBSERVER, XR_DISPLAY -> QUERY only)
   (ASSISTANT_PANEL -> no workflow.confirm)
   [FAIL: GatewayAuthorizationError("ERR_AUTHORIZATION_FAILED")]
        │
        ▼
6. Session-Binding Target Check (M28 + M31)
   - If "session_id" in payload: assert payload["session_id"] == session.session_id
   - If message_name == "gateway.disconnect": assert target_conn.session.session_id == session.session_id
   [FAIL: GatewaySessionMismatchError("ERR_SESSION_MISMATCH")]
        │
        ▼
7. Synchronous Dispatcher Dispatch
   (MessageDispatcher.dispatch(envelope))
```

---

## 5. Interaction with M28 & Target Selector Forensics

### A. Preservation of M28
M28's invariant:
```python
if isinstance(envelope.payload, dict) and "session_id" in envelope.payload:
    if envelope.payload.get("session_id") != session.session_id:
        raise GatewaySessionMismatchError(...)
```
is fully preserved.

### B. Alternate Target Selector Defense
1. **`client_id`**: For `gateway.disconnect`, the target connection's session is looked up and asserted equal to `session.session_id`.
2. **`plan_id`**: For `planning.get`, if `plan_id` is supplied without `session_id`, `PlanningService` looks up `self._plans[plan_id]`. To prevent cross-session plan probing, `GatewayAuthorizationPolicy` requires that if a route is session-scoped, the client's `session_id` must match.
3. No alternate selector can bypass session boundary validation.

---

## 6. Failure Semantics & Error Codes

All security rejections fail closed with zero state mutation and zero unmediated dispatcher dispatch:

| Denied Scenario | Exception Raised | Wire Error Code | Dispatched? | State Mutated? |
|---|---|---|---|---|
| Unallowlisted Route | `GatewayAuthorizationError` | `ERR_AUTHORIZATION_FAILED` | NO | NO |
| Source Spoofing | `GatewayValidationError` | `ERR_GATEWAY_VALIDATION` | NO | NO |
| Cross-Session Payload `session_id` | `GatewaySessionMismatchError` | `ERR_SESSION_MISMATCH` | NO | NO |
| Cross-Session `gateway.disconnect` | `GatewaySessionMismatchError` | `ERR_SESSION_MISMATCH` | NO | NO |
| Target Client Not Found | N/A (Handler Response) | `ERR_CLIENT_NOT_FOUND` | YES (Local) | NO |
| Role Hierarchy Violation (`ASSISTANT_PANEL` disconnecting `SURGEON_CONSOLE`) | `GatewayAuthorizationError` | `ERR_AUTHORIZATION_FAILED` | YES (Local) | NO |
| Actuation Keyword Violation | `GatewayAuthorizationError` | `ERR_AUTHORIZATION_FAILED` | NO | NO |
| Observer Attempting Command | `GatewayAuthorizationError` | `ERR_AUTHORIZATION_FAILED` | NO | NO |

---

## 7. Auditability & Observability

When an unauthorized route or cross-session disconnect attempt is rejected:
1. `GatewayAuthorizationPolicy` raises a `GatewayError` subclass with descriptive details (`error_code`, `message`).
2. `GatewayService._handle_client_message()` catches the error, redacts any sensitive tokens via `SecretFilter`, and enqueues a standard error response envelope to the calling client.
3. An audit event is emitted over the dispatcher:
   `gateway.ingress.rejected` with payload:
   ```json
   {
     "client_id": "...",
     "session_id": "...",
     "message_name": "...",
     "error_code": "..."
   }
   ```

---

## 8. Scope Boundaries: What M31 Will NOT Change

M31 strictly preserves:
- **`ClinicalExecutionGatewayService`**: Zero changes to transaction, capability creation, or routing logic.
- **`SafetyGateService`**: Zero changes to safety mathematics, checks, or session result caching.
- **`ToolExecutionEngine`**: Zero changes to sequence validation logic, depth tracking, or execution budgets.
- **`WorkflowService`**: Zero changes to state machine transitions, interlocks, or capability validation.
- **`PlatformService`**: Zero changes to supervisor state, cycle stepping, or epoch migration.
- **Teardown Contract**: The 12-step sequence established in M25–M29 remains identical.

---

## 9. Test Contract: High-Risk Invariants

The test suite in `tests/unit/gateway/test_m31_gateway_boundary.py` must prove:

1. **Cross-session `gateway.disconnect` Rejected**:
   - Actor: `SESSION-A`, `SURGEON_CONSOLE`
   - Target: `SESSION-B`, `SURGEON_CONSOLE`
   - Route: `gateway.disconnect`
   - Result: REJECTED with `ERR_SESSION_MISMATCH`. Target connection remains `ACTIVE`.
2. **Same-session `gateway.disconnect` by `SURGEON_CONSOLE` Allowed**:
   - Actor: `SESSION-A`, `SURGEON_CONSOLE`
   - Target: `SESSION-A`, `ASSISTANT_PANEL`
   - Route: `gateway.disconnect`
   - Result: SUCCESS. Target connection state transitions to `CLOSED`.
3. **Role Hierarchy Violation in `gateway.disconnect` Rejected**:
   - Actor: `SESSION-A`, `ASSISTANT_PANEL`
   - Target: `SESSION-A`, `SURGEON_CONSOLE`
   - Route: `gateway.disconnect`
   - Result: REJECTED with `ERR_AUTHORIZATION_FAILED`. Target console remains `ACTIVE`.
4. **Cross-session `gateway.clients` Scoped**:
   - Actor: `SESSION-A`, `READ_ONLY_OBSERVER`
   - Target: All connections
   - Route: `gateway.clients`
   - Result: SUCCESS, but returned clients list contains strictly `SESSION-A` clients; 0 `SESSION-B` clients visible.
5. **`tools.reset` Deregistered**:
   - Route: `tools.reset` dispatched on `MessageDispatcher`.
   - Result: Returns error response `TopicValidationError` or `ERR_ROUTE_NOT_FOUND`.
6. **Gateway Ingress Blocks `platform.reset`**:
   - Actor: `SESSION-A`, `SURGEON_CONSOLE`
   - Route: `platform.reset`
   - Result: REJECTED at ingress with `ERR_AUTHORIZATION_FAILED`. Supervisor epoch untouched.
7. **Gateway Ingress Blocks `platform.cycle`**:
   - Actor: `SESSION-A`, `SURGEON_CONSOLE`
   - Route: `platform.cycle`
   - Result: REJECTED at ingress with `ERR_AUTHORIZATION_FAILED`.
8. **Gateway Ingress Blocks Pipeline Reset Routes**:
   - Routes tested: `vision.pipeline.reset`, `xr.reset`, `ultron.reset`, `anatomy.reset`, `audio.pipeline.reset`, `gesture.pipeline.reset`.
   - Result: All REJECTED at ingress with `ERR_AUTHORIZATION_FAILED`.
9. **Valid Client-Issuable Routes Pass Ingress**:
   - Routes tested: `execution.navigation.execute`, `workflow.start`, `workflow.status`, `navigation.status.get`, `gateway.status`.
   - Result: Authorized and dispatched to handlers.
10. **M28 Cross-Session Payload Injection Preserved**:
    - Actor: `SESSION-A`, `SURGEON_CONSOLE`
    - Payload: `{"session_id": "SESSION-B"}`
    - Route: `execution.navigation.execute`
    - Result: REJECTED with `ERR_SESSION_MISMATCH`.
11. **Unknown Route Rejected**:
    - Route: `unknown.bogus.command`
    - Result: REJECTED at ingress with `ERR_AUTHORIZATION_FAILED`.
12. **Observer Command Rejection Preserved**:
    - Actor: `SESSION-A`, `READ_ONLY_OBSERVER`
    - Route: `execution.navigation.execute`
    - Result: REJECTED with `ERR_AUTHORIZATION_FAILED`.

---

## 10. Formal Security Invariants

$$\begin{aligned}
\mathbf{Invariant\ 1\ (Disconnect\ Isolation):} & \quad \forall C, T, \; \text{authenticated}(C) \land \text{disconnect}(C, T) \implies \text{session}(T) = \text{session}(C) \\
\mathbf{Invariant\ 2\ (Role\ Hierarchy):} & \quad \forall C, T, \; \text{disconnect}(C, T) \land \text{role}(T) = \text{SURGEON\_CONSOLE} \implies \text{role}(C) = \text{SURGEON\_CONSOLE} \\
\mathbf{Invariant\ 3\ (Metadata\ Scoping):} & \quad \forall C, \; \text{VisibleConnections}(C) \subseteq \text{Connections}(\text{session}(C)) \\
\mathbf{Invariant\ 4\ (Ingress\ Default\ Deny):} & \quad \forall m, \; \text{dispatched\_from\_gateway}(m) \implies m.\text{topic} \in \text{CLIENT\_ISSUABLE\_ROUTES} \\
\mathbf{Invariant\ 5\ (Tool\ Reset\ Removal):} & \quad \text{tools.reset} \notin \text{DispatcherRoutes}
\end{aligned}$$

---

## 11. Acceptance Gate

The contract is accepted when:
- All 12 test specifications pass deterministically.
- Full regression suite (all 1,625 tests) passes.
- Exactly 3 production files modified.
- Zero modifications to M28–M30 frozen components.
- Git working tree diff is strictly minimal and clean.

---

```
================================================================================
M31 CONTRACT CLASSIFICATION: READY_FOR_IMPLEMENTATION
================================================================================
```
