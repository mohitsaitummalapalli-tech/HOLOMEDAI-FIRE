# M32 CONTRACT SPECIFICATION
## Clinical Data Isolation, Lifecycle Retention & Cross-Service Contract Hardening

**Authoritative Baseline:** `daf8324453378bb1f45e84de26e09479c8ad75ff`  
**Milestone Predecessors:** M19–M31 (FROZEN)  
**Status:** LOCKED CONTRACT SPECIFICATION  
**Classification:** `M32_READY_FOR_IMPLEMENTATION`

---

## 1. Primary Contract: Tool Result Ownership & Isolation

### 1.1 Formal Ownership Model
Every stored `ToolResult` $R$ produced by the platform has an immutable, authoritative session owner $S$:
$$\text{owner}(R) = S$$

Access to $R$ is strictly governed by the authenticated session identity of the caller $C$. An authenticated caller $C$ with session $\text{session}(C)$ is authorized to retrieve $R$ if and only if:
$$\text{owner}(R) = \text{session}(C)$$

A caller-supplied `invocation_id` is an object reference, **not** an authorization token. An `invocation_id` alone MUST NEVER be sufficient to authorize retrieval of a tool execution result.

### 1.2 Formal Security Invariant
$$\forall C, R: \quad \text{authenticated}(C) \land \text{query\_result}(C, R) \implies \text{owner}(R) = \text{session}(C)$$

If an authenticated caller queries an `invocation_id` where $\text{owner}(R) \neq \text{session}(C)$, the query MUST fail closed without disclosing result metadata, execution status, or verifying whether the target `invocation_id` exists in another session.

---

## 2. Tool Result Storage Model

### 2.1 Option E: Combined Ownership Metadata + Query Authorization + Session Eviction
In accordance with the M32 Feasibility Audit, the platform adopts **Option E**:

1. **Ownership Metadata:** `ToolResult` stores an immutable `session_id: str` attribute.
2. **Storage Structure:** `ToolExecutionEngine._result_history` stores results stamped with `session_id`.
3. **Query Authorization:** Result retrieval via `ToolExecutionEngine.get_result` and `ToolService.handle_result_query` mandates both `invocation_id` and `caller_session_id`. Global unfiltered lookups are structurally disallowed.
4. **Lifecycle Eviction:** When `evict_session(session_id)` is invoked, all records $R$ where $\text{owner}(R) = \text{session\_id}$ are purged from resident engine memory.

### 2.2 Canonical Lookup Semantics
The query API requires:
```python
def get_result(self, invocation_id: str, caller_session_id: str) -> Optional[ToolResult]:
    for res in reversed(self._result_history):
        if res.invocation_id == invocation_id:
            if res.session_id == caller_session_id:
                return res
            # Session mismatch: fail closed
            return None
    return None
```
Under this contract:
- An invocation owned by `session_A` returns `None` (or raises `ERR_RESULT_NOT_FOUND`) when queried with `caller_session_id = "session_B"`.
- A missing `caller_session_id` in `ToolService.handle_result_query` is rejected with `INVALID_RESULT_QUERY` / `ERR_UNAUTHORIZED`.

---

## 3. Tool Result Lifecycle

### 3.1 State Transitions
The full clinical lifecycle for tool results follows a deterministic progression:

```text
[CREATE Context]
       │
       ▼
[ASSIGN SESSION OWNER] ──► context.session_id assigned from authenticated caller
       │
       ▼
[EXECUTE & STORE]      ──► ToolResult stamped with session_id, appended to _result_history
       │
       ▼
[QUERY (Scoped)]       ──► Query permitted iff caller_session_id == result.session_id
       │
       ▼
[SESSION TEARDOWN]     ──► Coordinated session termination
       │
       ▼
[EVICT]                ──► evict_session(session_id) purges all results for session_id
```

### 3.2 Formal Teardown Invariant
$$\forall R: \quad \text{owner}(R) = S \land \text{teardown}(S) \implies R \notin \text{resident\_result\_state}$$

Following `evict_session(S)`:
- All `ToolResult` instances $R$ with $\text{owner}(R) = S$ are deleted from `_result_history`.
- Memory is released immediately.
- Post-teardown queries for $R$ by any caller (including reconnected sessions) return `ERR_RESULT_NOT_FOUND`.

### 3.3 Boundary Cases & Lifecycle Behavior
- **Unknown `invocation_id`:** Returns `ERR_RESULT_NOT_FOUND`.
- **Cross-Session `invocation_id`:** Returns `ERR_RESULT_NOT_FOUND` (fails closed, zero existence disclosure).
- **Result already evicted:** Returns `ERR_RESULT_NOT_FOUND`.
- **Duplicate `evict_session(S)`:** Idempotent; returns `False` if no state remains.
- **Stale / Reused `invocation_id`:** Does not collide; searches strictly match the active session.

---

## 4. Tool Result API Compatibility

### 4.1 Consumer Analysis & Field Stability
Existing consumers of `ToolResult` inspect:
- `invocation_id: str`
- `tool_id: str`
- `status: ToolExecutionStatus`
- `result_payload: Mapping[str, Any]`
- `execution_time_ms: float`
- `confidence: float`
- `uncertainty_metric: float`
- `epoch_id: int`
- `is_simulated: bool`
- `diagnostic_message: Optional[str]`

### 4.2 Concrete Field Definition
To preserve full backward compatibility with existing tests and mock tool handlers that construct `ToolResult` with positional parameters:
```python
@dataclass(frozen=True)
class ToolResult:
    invocation_id: str
    tool_id: str
    status: ToolExecutionStatus
    result_payload: Mapping[str, Any]
    execution_time_ms: float
    confidence: float
    uncertainty_metric: float
    epoch_id: int
    session_id: str = "default_session"
    is_simulated: bool = True
    diagnostic_message: Optional[str] = None
```
- In `ToolExecutionEngine.execute_invocation`, `session_id` is **always** explicitly populated from `context.session_id`.
- `ToolResult.session_id` is non-empty, validated in `__post_init__`, and serialized into event payloads and query responses.

---

## 5. Planning Ownership Contract

### 5.1 Formal Plan Ownership Model
Every active `SurgicalPlanDefinition` $P$ registered in `PlanningService` has an authoritative session owner:
$$\text{owner}(P) = S \quad \text{where} \quad \text{\_session\_plan\_bindings}[S] = P.\text{plan\_id}$$

An authenticated caller $C$ may retrieve $P$ if and only if:
$$\text{owner}(P) = \text{session}(C)$$

### 5.2 Formal Invariant
$$\forall C, P: \quad \text{authenticated}(C) \land \text{planning\_get}(C, P) \implies \text{owner}(P) = \text{session}(C)$$

### 5.3 Canonical Retrieval Semantics
In `PlanningService.handle_get_query`:
1. The caller's authenticated `session_id` is mandatory.
2. If `plan_id` is supplied:
   - The service resolves `bound_plan_id = self._session_plan_bindings.get(session_id)`.
   - The query succeeds if and only if `bound_plan_id == plan_id` and `plan_id in self._plans`.
   - If `bound_plan_id != plan_id` or `plan_id not in self._plans`, the query returns `ERR_PLAN_NOT_FOUND` (fail-closed; never confirms plan existence in other sessions).
3. If `session_id` is supplied without `plan_id`:
   - Returns the active plan bound to `session_id` if present; otherwise `ERR_PLAN_NOT_FOUND`.

---

## 6. Planning Eviction & Capacity Contract

### 6.1 Formal Eviction Invariant
$$\forall P: \quad \text{owner}(P) = S \land \text{teardown}(S) \implies P \notin \text{active\_plan\_storage}$$

When `PlanningService.evict_session(session_id)` is invoked:
1. `bound_plan_id = self._session_plan_bindings.pop(session_id, None)`
2. If `bound_plan_id` is present:
   - `self._plans.pop(bound_plan_id, None)` (removes plan definition from resident storage).
   - `self._verification_records.pop(bound_plan_id, None)` (clears verification cache).
3. Active plan capacity `len(self._plans)` is decremented, releasing a slot against `MAX_ACTIVE_PLANS = 16`.

### 6.2 Capacity Accounting
Active capacity remains global (`MAX_ACTIVE_PLANS = 16`). Because eviction purges definitions immediately upon session teardown, historical session churn cannot leak plan slots or cause false `PlanningCapacityError` lockups.

---

## 7. Workflow Interlock Route Contract

### 7.1 Removal of Phantom Route from Allowlist
Inspection proved `"workflow.interlock.trip"` has no service handler in `WorkflowService` and was added to `CLIENT_ISSUABLE_ROUTES` in error (derived from the outbound event topic `"workflow.interlock.tripped"`).

**Contract Resolution:**
- Remove `"workflow.interlock.trip"` from `CLIENT_ISSUABLE_ROUTES` in `python/holomed/gateway/authorization.py`.
- Preserve default-deny: Any client message targeting `"workflow.interlock.trip"` is blocked at gateway ingress with `GatewayAuthorizationError` (`FORBIDDEN_ROUTE`).

### 7.2 Controlled Gateway Dispatcher Exception Handling
If an unroutable message route ever bypasses the allowlist, `GatewayService._handle_client_message` MUST NOT crash the connection loop.

**Contract Resolution:**
- In `GatewayService._handle_client_message`:
  ```python
  try:
      resp = self._dispatcher.dispatch(envelope)
      if resp is not None:
          connection.enqueue_envelope(resp)
  except UnroutableMessageError as e:
      err = create_error_response(
          envelope,
          self.name,
          error_code="ERR_UNROUTABLE_ROUTE",
          error_message=f"Route {envelope.message_name!r} is not registered on dispatcher",
      )
      connection.enqueue_envelope(err)
  ```
- Connection remains healthy; client receives structured protocol error.

---

## 8. Execution Recovery Reset Contract

### 8.1 Method Signature Alignment
In `python/holomed/execution/service.py` (`ClinicalExecutionGatewayService` line 866):
- Feasibility proved that line 866 executes `self._recovery_service.reset_recovery(session_id)`, but `RecoveryService` (line 657) implements `reset_session(session_id)`.

**Contract Resolution:**
- Update line 866 to call the canonical existing API:
  ```python
  elif op == "RESET":
      self._recovery_service.reset_session(session_id)
  ```
- Do **not** create compatibility aliases or redundant wrappers.

### 8.2 Operational Semantics
- Valid `RESET`: Clears `RecoveryState` back to `IDLE`, clears staged candidates, authorizations, and checkpoint pairs for `session_id`.
- Invalid action: Raises `ExecutionValidationError`.
- Missing session: Fails validation.
- Zero unintended mutations: Does not mutate recovery state for any other session.

---

## 9. Persistence Path Security Contract

### 9.1 Mandatory Path Sanitization
In `python/holomed/persistence/service.py` (`handle_cycle_get_query` line 511):
- Feasibility proved line 511 directly constructs `self._storage_root / f"{session_id}.jsonl"` without validation.

**Contract Resolution:**
- Enforce the existing canonical validator `validate_session_path` in `handle_cycle_get_query`:
  ```python
  journal_path = validate_session_path(self._storage_root, session_id)
  ```
- Any `session_id` containing path traversal (`../`, `..\`), path separators, or illegal characters raises `PersistenceSecurityError`.
- `handle_cycle_get_query` catches `PersistenceSecurityError` and returns structured protocol error response (`error_code="PersistenceSecurityError"`).

---

## 10. Cross-Service Object Ownership Audit Contract

### 10.1 Identifier Security Classification

| Identifier | Classification | Owner / Scope | Storage Layer | Query Authorization Requirement |
|---|---|---|---|---|
| `invocation_id` | **SESSION-OWNED** | Session | `ToolExecutionEngine._result_history` | **Mandatory:** Caller session MUST match result `session_id`. |
| `plan_id` | **SESSION-OWNED** | Session | `PlanningService._plans` | **Mandatory:** Caller session MUST be bound to `plan_id`. |
| `workflow_id` | **SESSION-OWNED** | Session | `WorkflowService._session_states` | **Mandatory:** Keyed directly by `session_id`. |
| `trajectory_id` | **SESSION-OWNED** | Session | Surgical Plan / Navigation | **Mandatory:** Contained within session-owned plan. |
| `tool_id` | **GLOBAL / PUBLIC** | Global | `ToolRegistry._tools` | **Public:** Static catalog descriptor lookup. |
| `result_id` | **SESSION-OWNED** | Session | Tool Result Alias | **Mandatory:** Same as `invocation_id`. |
| `client_id` | **SESSION-BOUND** | Gateway | `GatewayService._connections` | **Ingress Check:** Must match envelope source and session. |
| `connection_id` | **INTERNAL** | Transport | `GatewayConnection` | **Internal:** Bound to socket lifecycle. |

---

## 11. Authorization Source of Truth

1. **Gateway Context is Authoritative:** The caller's authenticated `session_id` is established during handshake and stored in `GatewayConnection.session.session_id`.
2. **Payload Non-Override:** Payloads cannot override the authenticated identity. If a client message includes a payload `session_id`, `GatewayAuthorizationPolicy` enforces `payload["session_id"] == session.session_id`.
3. **Dispatcher Context Stamping:** When forwarding envelopes to downstream services, the gateway ensures the envelope and payload are stamped with the authenticated caller's `session_id`.
4. **Subsystem Enforcement:** Downstream services (`ToolService`, `PlanningService`, `PersistenceService`) extract `session_id` from the verified envelope payload and validate ownership.

---

## 12. Failure Semantics & Error Responses

| Operation / Attack Attempt | Exception / Code | Error Code Returned | Dispatcher State | Filesystem Touched? | State Mutated? | Event Emitted? |
|---|---|---|---|---|---|---|
| Query result owned by other session | N/A | `ERR_RESULT_NOT_FOUND` | Success (Handled) | No | No | No |
| Query result with missing `session_id` | `ToolValidationError` | `INVALID_RESULT_QUERY` | Success (Handled) | No | No | No |
| Query plan owned by other session | N/A | `ERR_PLAN_NOT_FOUND` | Success (Handled) | No | No | No |
| Issue `workflow.interlock.trip` | `GatewayAuthorizationError` | `FORBIDDEN_ROUTE` | Not invoked | No | No | Security Audit |
| Dispatch unroutable route | `UnroutableMessageError` | `ERR_UNROUTABLE_ROUTE` | Failed (Caught in GW) | No | No | No |
| Recovery RESET executed | None | `SUCCESS` | Success | No | Recovery State -> IDLE | `recovery.session.reset` |
| Traversal in `persistence.cycle.get` | `PersistenceSecurityError` | `PersistenceSecurityError` | Success (Handled) | No (Blocked) | No | Security Audit |

---

## 13. Test Contract (26 Verification Points)

### Tool Results
1. **Test 1:** Session A invokes tool $\rightarrow$ result stored with `owner = Session A`.
2. **Test 2:** Session A queries own `invocation_id` $\rightarrow$ returns `SUCCESS` with result payload.
3. **Test 3:** Session B queries Session A's `invocation_id` with `session_id = Session B`.
4. **Test 4:** Session B query is denied with `ERR_RESULT_NOT_FOUND`; zero state mutation.
5. **Test 5:** Session A disconnects and `evict_session("Session A")` is executed.
6. **Test 6:** Resident `_result_history` contains 0 entries for Session A.
7. **Test 7:** Session B queries Session A's `invocation_id` post-eviction $\rightarrow$ `ERR_RESULT_NOT_FOUND`.
8. **Test 8:** Query for nonexistent `invocation_id` $\rightarrow$ `ERR_RESULT_NOT_FOUND`.

### Planning
9. **Test 9:** Session A submits plan $\rightarrow$ bound to Session A.
10. **Test 10:** Session A queries own `plan_id` $\rightarrow$ returns `SUCCESS` with plan payload.
11. **Test 11:** Session B queries Session A's `plan_id` with `session_id = Session B`.
12. **Test 12:** Session B query is denied with `ERR_PLAN_NOT_FOUND`; zero state mutation.
13. **Test 13:** Session A disconnects and `evict_session("Session A")` is executed.
14. **Test 14:** `_plans` storage no longer contains Session A's plan definition; `_session_plan_bindings` cleared.
15. **Test 15:** 20 sequential sessions register 1 plan each and evict $\rightarrow$ zero `PlanningCapacityError`, active plans count remains 0 or 1.

### Workflow
16. **Test 16:** Gateway client sends `workflow.interlock.trip` $\rightarrow$ rejected at gateway ingress with `FORBIDDEN_ROUTE`.
17. **Test 17:** Dispatcher receives unroutable route $\rightarrow$ `GatewayService` catches `UnroutableMessageError` and returns `ERR_UNROUTABLE_ROUTE` without closing transport.

### Recovery
18. **Test 18:** `execution.recovery.execute` with `action = "RESET"` calls `RecoveryService.reset_session()` cleanly and returns `IDLE`.
19. **Test 19:** `execution.recovery.execute` with unknown action $\rightarrow$ raises `ExecutionValidationError` safely.

### Persistence
20. **Test 20:** `persistence.cycle.get` with valid session identifier $\rightarrow$ returns cycle record.
21. **Test 21:** `persistence.cycle.get` with `../` path traversal $\rightarrow$ rejected with `PersistenceSecurityError`.
22. **Test 22:** `persistence.cycle.get` with absolute path or illegal characters $\rightarrow$ rejected with `PersistenceSecurityError`.
23. **Test 23:** Canonical helper `validate_session_path` is proven to guard all persistence query access.

### Regression Protection
24. **Test 24:** M31 Gateway isolation contracts (`gateway.disconnect` caller checks, `gateway.clients` self-only) remain 100% passing.
25. **Test 25:** M29 Tool sequence tracking and monotonicity validation remain 100% passing.
26. **Test 26:** M30 SafetyGate dispatcher boundaries and capability validation remain 100% passing.

---

## 14. Hostile Security Requirements

The implementation MUST withstand:
1. **Invocation ID Enumeration:** Guessing UUIDs / invocation IDs across sessions yields zero data.
2. **Plan ID Enumeration:** Guessing plan IDs returns `ERR_PLAN_NOT_FOUND`.
3. **Post-Teardown Snooping:** Accessing an evicted session's artifacts is completely impossible.
4. **Session Churn Exhaustion:** Massive session turnover does not exhaust memory or capacity caps.
5. **Payload Session Spoofing:** Supplying spoofed `session_id` in payload is caught at gateway ingress or rejected downstream.
6. **Path Traversal Escape:** Directory traversal payloads in persistence queries are blocked before disk access.

---

## 15. Minimum Production Scope (7 Files)

Only the following 7 files are authorized for modification during M32 implementation:

1. [models.py](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/tools/models.py) — Add `session_id` field and validation to `ToolResult`.
2. [engine.py](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/tools/engine.py) — Propagate `session_id` to results; scope `get_result`; purge results in `evict_session`.
3. [service.py (tools)](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/tools/service.py) — Enforce `session_id` in `handle_result_query`.
4. [service.py (planning)](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/planning/service.py) — Enforce session ownership in `handle_get_query`; purge `_plans` in `evict_session`.
5. [authorization.py](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/gateway/authorization.py) — Remove `"workflow.interlock.trip"` from `CLIENT_ISSUABLE_ROUTES`.
6. [service.py (execution)](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/execution/service.py) — Fix `reset_recovery` call to `reset_session`.
7. [service.py (persistence)](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/persistence/service.py) — Use `validate_session_path` in `handle_cycle_get_query`.

---

## 16. Authorized Test Scope

Authorized test files for verification:
- `tests/unit/tools/test_tool_service.py`
- `tests/unit/tools/test_tool_engine.py`
- `tests/unit/planning/test_planning_service.py`
- `tests/unit/gateway/test_gateway_authorization.py`
- `tests/unit/execution/test_clinical_execution_gateway.py`
- `tests/unit/persistence/test_persistence_service.py`
- Optional new dedicated suite: `tests/unit/gateway/test_m32_clinical_isolation.py`

---

## 17. Explicit Exclusions

The following areas are strictly **FROZEN** and excluded from M32:
- No modifications to M31 gateway disconnect or clients logic.
- No modifications to M31 route allowlist architecture (other than removing dead route).
- No modifications to M29 tool sequence number algorithms.
- No modifications to M30 SafetyGate rule engine or actuation interlocks.
- No modifications to physical tool simulators, device drivers, or step timers.
- No modifications to workflow state transitions or graph definitions.

---

## 18. Acceptance Gate

This specification is locked under the following criteria:
- **Deterministic:** All failure modes and success paths have explicit outputs.
- **Fail-Closed:** All cross-session queries deny access without leaking object existence.
- **Lifecycle-Complete:** Eviction cleanses all resident objects and restores capacity.
- **Backward-Compatible:** Preserves existing tool descriptors, handlers, and frozen milestones.
- **Minimal:** Restricted to 7 production files.

---

```text
======================================================================
FINAL CLASSIFICATION: M32_READY_FOR_IMPLEMENTATION
======================================================================
```
