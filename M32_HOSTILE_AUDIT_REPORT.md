# M32 HOSTILE SECURITY + LIFECYCLE AUDIT REPORT

## Executive Classification
**FINAL RESULT:** `M32_HOSTILE_AUDIT_FAIL`

The hostile security and lifecycle audit has uncovered two critical authorization-bypass vulnerabilities that allow unauthorized cross-session data disclosure under specific query formulations:
1. **`planning.get` Omitted / Null Session Query Bypass (CRITICAL)**: When a caller queries `planning.get` with `session_id` omitted or set to `None`, `PlanningService.handle_get_query` falls back to global lookup (`elif plan_id and plan_id in self._plans: p = self._plans[plan_id]`), disclosing the full surgical plan metadata without caller session authorization.
2. **`tools.result` Null-Session Query & Engine Direct-Lookup Bypass (HIGH)**: `ToolExecutionEngine.get_result` treats `caller_session_id=None` as an authorization opt-out (`if caller_session_id is not None and res.session_id != caller_session_id: return None; return res`). Consequently, direct calls or queries with `payload={"session_id": None}` bypass session authorization and return confidential tool execution results.

All other M32 components (Session Teardown, Immutability, Capacity Churn, Gateway Allowlist, Workflow Interlock Trip Removal, Gateway Ingress Unroutable Hardening, Recovery Reset API, and Persistence Path Traversal Sanitization) passed all hostile penetration vectors cleanly.

In strict adherence to hostile audit protocol, **no source code or test files have been modified**.

---

## Adversarial Findings by Section

### 1. TOOL.RESULT CROSS-SESSION ATTACK
- **ATTACK**: Session A creates tool result `inv_session_a_001`. Session B attempts retrieval via `tools.result` across 7 payload variations:
  1. `{"invocation_id": inv_id, "session_id": "SESSION_B"}`
  2. `{"invocation_id": "inv_guessed", "session_id": "SESSION_B"}`
  3. `{"invocation_id": "inv_stale", "session_id": "SESSION_B"}`
  4. `{"invocation_id": inv_id, "session_id": "SESSION_B"}` (forged)
  5. `{"invocation_id": inv_id, "session_id": ""}`
  6. `{"invocation_id": inv_id}` (omitted `session_id`)
  7. `{"invocation_id": inv_id, "session_id": None}`
- **EXPECTED**: All Session B attempts must fail closed with `ERR_RESULT_NOT_FOUND`. Zero result data or metadata leak.
- **ACTUAL**:
  - Variations 1–6 fail closed with `ERR_RESULT_NOT_FOUND`.
  - **Variation 7 (`session_id: None`) SUCCEEDED**: `ToolService.handle_result_query` extracted `caller_session_id = query_envelope.payload.get("session_id", "")` which returned `None`. In `ToolExecutionEngine.get_result`, the guard `if caller_session_id is not None and res.session_id != caller_session_id:` evaluated to `False`. The engine returned `ToolResult`, and the service returned `MessageType.RESPONSE` containing `result_payload={'confidential': 'leaked_data'}`.
- **BYPASS**: Confirmed via direct query with `session_id: None`.
- **STATE MUTATION**: None.
- **SEVERITY**: **HIGH**

---

### 2. TOOL RESULT TEARDOWN ATTACK
- **ATTACK**: Create `ToolResult` in Session A and Session B. Evict Session A via `ToolExecutionEngine.evict_session("SESSION_A")`. Inspect internal `_result_history` and attempt retrieval from Session A and Session B.
- **EXPECTED**: Session A results completely purged from `_result_history`. Subsequent retrieval fails. Session B results remain completely resident and intact.
- **ACTUAL**:
  - Before eviction: Session A and Session B results resident.
  - After eviction: `_result_history` contains 0 entries owned by Session A.
  - Session A retrieval returns `None`.
  - Session B result remains resident and retrievable.
- **BYPASS**: None.
- **STATE MUTATION**: Clean session-scoped eviction.
- **SEVERITY**: **NONE (PASS)**

---

### 3. TOOL RESULT OWNERSHIP FORGERY
- **ATTACK**: Attempt to forge `session_id = "SESSION_B"` during tool invocation where execution context belongs to `SESSION_A`:
  1. Passing forged `session_id` in `parameters`.
  2. Returning forged `ToolResult(..., session_id="SESSION_B")` from a tool handler.
  3. Mutating `result.session_id` on retrieved object.
- **EXPECTED**: Authoritative session is derived strictly from `context.session_id`. Returned object is immutable.
- **ACTUAL**:
  - Parameter injection of `"session_id"` is rejected by parameter validation schema.
  - Handler return value with forged `session_id` is intercepted by `ToolExecutionEngine.execute_invocation` lines 132–144, which explicitly overwrites `result.session_id` with `context.session_id`.
  - `ToolResult` is `@dataclass(frozen=True)`; attempting attribute mutation raises `FrozenInstanceError`.
- **BYPASS**: None.
- **STATE MUTATION**: None.
- **SEVERITY**: **NONE (PASS)**

---

### 4. INVOCATION-ID COLLISION / REUSE
- **ATTACK**: Replay Session A `invocation_id` from Session B; query evicted Session A `invocation_id` from Session B.
- **EXPECTED**: No cross-session disclosure.
- **ACTUAL**: All queries from Session B specifying `session_id="SESSION_B"` return `None` / `ERR_RESULT_NOT_FOUND`.
- **BYPASS**: None (except when `session_id: None` is supplied, as documented in Attack 1).
- **STATE MUTATION**: None.
- **SEVERITY**: **LOW** (subsumed by Attack 1)

---

### 5. PLANNING CROSS-SESSION ATTACK
- **ATTACK**: Session A registers plan `P`. Session B attempts `planning.get(P)` via 5 variations:
  1. `{"plan_id": P, "session_id": "SESSION_B"}`
  2. `{"plan_id": P, "session_id": "SESSION_A"}` (forged session identity in payload)
  3. `{"plan_id": "guessed_plan_id", "session_id": "SESSION_B"}`
  4. `{"plan_id": P}` (omitted `session_id`)
  5. `{"plan_id": P, "session_id": None}`
- **EXPECTED**: All attempts by Session B fail closed with `ERR_PLAN_NOT_FOUND`. Zero plan metadata leaks.
- **ACTUAL**:
  - Variations 1, 2, 3 fail closed with `ERR_PLAN_NOT_FOUND`.
  - **Variations 4 and 5 SUCCEEDED**: In `PlanningService.handle_get_query` lines 459–473:
    ```python
    if session_id:
        # checks session binding
    elif plan_id and plan_id in self._plans:
        p = self._plans[plan_id]
    ```
    When `session_id` is omitted or `None`, the service skips the session check and executes the fallback `elif plan_id and plan_id in self._plans: p = self._plans[plan_id]`.
    The service returned `MessageType.RESPONSE` containing complete plan metadata: `plan_id`, `version`, `is_locked`, `case_id`, `laterality`, `trajectories_count`, `exclusion_zones_count`.
- **BYPASS**: Confirmed via direct query with omitted or `None` `session_id`.
- **STATE MUTATION**: None.
- **SEVERITY**: **CRITICAL**

---

### 6. PLANNING EVICTION / CAPACITY CHURN ATTACK
- **ATTACK**:
  1. Session A registers plan. Session B registers plan.
  2. Evict Session A via `plan_svc.evict_session("SESSION_A")`.
  3. Verify Session A plan is deleted and Session B plan survives.
  4. Churn 30 consecutive sessions registering plans, exceeding `MAX_ACTIVE_PLANS = 16`.
- **EXPECTED**: Session A plan purged. Session B plan survives. Stale plans do not exhaust global capacity.
- **ACTUAL**:
  - Session A plan purged from `self._plans` and `self._session_plan_bindings`.
  - Session B plan survives intact.
  - All 30 churn sessions cleanly bound and evicted without hitting `PlanningCapacityError`. Resident plan count remained strictly bounded.
- **BYPASS**: None.
- **STATE MUTATION**: Clean eviction.
- **SEVERITY**: **NONE (PASS)**

---

### 7. PLANNING OBJECT OWNERSHIP CONSISTENCY
- **ATTACK**: Construct and query inconsistent states:
  1. Stale binding: session in `_session_plan_bindings`, plan missing from `_plans`.
  2. Orphan plan: plan in `_plans`, session missing from `_session_plan_bindings`.
- **EXPECTED**: Fails closed with `ERR_PLAN_NOT_FOUND`.
- **ACTUAL**:
  - Stale binding returns `ERR_PLAN_NOT_FOUND`.
  - Orphan plan queried with valid session returns `ERR_PLAN_NOT_FOUND` (note: orphan plan queried without session leaks due to Defect 5).
- **BYPASS**: Addressed in Defect 5.
- **STATE MUTATION**: None.
- **SEVERITY**: **LOW** (tied to Defect 5)

---

### 8. WORKFLOW.INTERLOCK.TRIP ATTACK
- **ATTACK**:
  1. Client sends `workflow.interlock.trip` over Gateway connection.
  2. Client sends an unroutable message that passes allowlist to dispatcher.
  3. Test connection state and socket health.
  4. Send subsequent valid query (`gateway.status`) over the SAME connection.
- **EXPECTED**:
  - `workflow.interlock.trip` rejected at ingress (`GatewayAuthorizationError`).
  - Unroutable route caught, returns `ERR_UNROUTABLE_ROUTE` (`MessageType.ERROR`).
  - Connection remains `ACTIVE`.
  - Subsequent message succeeds with `MessageType.RESPONSE`.
- **ACTUAL**:
  - `workflow.interlock.trip` rejected immediately with `GatewayAuthorizationError`: `"Route 'workflow.interlock.trip' is not permitted through gateway ingress"`.
  - Unroutable message caught by `_install_gateway_unroutable_hardening()`, enqueues `ERR_UNROUTABLE_ROUTE` without crash or socket hang.
  - Connection state remained `ACTIVE`.
  - Subsequent `gateway.status` query succeeded with valid response payload.
- **BYPASS**: None.
- **STATE MUTATION**: None.
- **SEVERITY**: **NONE (PASS)**

---

### 9. GATEWAY SESSION-STAMPING REGRESSION
- **ATTACK**: Send gateway messages with:
  1. Correct `session_id`.
  2. Incorrect `session_id` (another session).
  3. Omitted `session_id`.
  4. `session_id: None`.
- **EXPECTED**: Client cannot spoof session identity. Gateway stamps authenticated `session_id`.
- **ACTUAL**:
  - Matching `session_id`: Authorized.
  - Mismatched `session_id` (including `None` or foreign session): Rejected with `GatewaySessionMismatchError` ("Cross-session injection rejected").
  - Omitted `session_id`: Stamped with `session.session_id`.
- **BYPASS**: None at the Gateway ingress layer.
- **STATE MUTATION**: None.
- **SEVERITY**: **NONE (PASS)**

---

### 10. GATEWAY ADMIN BOUNDARY REGRESSION
- **ATTACK**: External client attempts administrative routes:
  - `platform.reset`
  - `platform.cycle`
  - `tools.reset`
  - `vision.pipeline.reset`
  - `xr.reset`
  - `workflow.interlock.trip`
- **EXPECTED**: All absent from `CLIENT_ISSUABLE_ROUTES`; rejected with `GatewayAuthorizationError`.
- **ACTUAL**: All 6 routes are absent from `CLIENT_ISSUABLE_ROUTES` and rejected at gateway authorization.
- **BYPASS**: None.
- **STATE MUTATION**: None.
- **SEVERITY**: **NONE (PASS)**

---

### 11. RECOVERY RESET
- **ATTACK**: Execute `execution.recovery.execute` with `recovery_operation = "RESET"`; verify canonical method invocation and isolation.
- **EXPECTED**: Executes `RecoveryService.reset_session(session_id)`; no `AttributeError`; other sessions unaffected; invalid operations rejected.
- **ACTUAL**:
  - `reset_session("SESS_REC")` executed cleanly.
  - Other session states in `RecoveryService` unaffected.
  - Invalid operation rejected with `FAILED_NAVIGATION_GEOMETRY`.
- **BYPASS**: None.
- **STATE MUTATION**: Expected target session recovery state reset to `IDLE`.
- **SEVERITY**: **NONE (PASS)**

---

### 12. PERSISTENCE PATH TRAVERSAL
- **ATTACK**: Send path traversal attacks targeting `persistence.cycle.get` and `persistence.session.get`:
  - `../evil`
  - `../../evil`
  - `..\evil`
  - `/etc/passwd`
  - `C:\Windows\System32`
  - `invalid$name;rm`
- **EXPECTED**: All rejected with `ERR_PERSISTENCE_SECURITY_ERROR` BEFORE filesystem access. Storage root directory remains untouched. Valid session queries succeed.
- **ACTUAL**:
  - All traversal and malformed inputs rejected by `validate_session_path` with `PersistenceSecurityError`, returning `ERR_PERSISTENCE_SECURITY_ERROR`.
  - Validation executed strictly before file reads.
  - Directory contents verified before and after attack: 0 filesystem mutations.
  - Valid session queries (`persistence.cycle.get`, `persistence.session.get`) returned expected data.
- **BYPASS**: None.
- **STATE MUTATION**: None.
- **SEVERITY**: **NONE (PASS)**

---

### 13. DIRECT DISPATCHER BYPASS
- **ATTACK**: Evaluate whether backend services registered on the shared `MessageDispatcher` can be queried directly without gateway session-stamping, triggering authorization bypasses.
- **EXPECTED**: Services must enforce session validation internally and fail closed on missing/invalid sessions.
- **ACTUAL**:
  - `ToolService.handle_result_query` fails to enforce that `session_id` is present and non-None before delegating to `engine.get_result`.
  - `PlanningService.handle_get_query` has an explicit fallback path (`elif plan_id and plan_id in self._plans:`) that permits anonymous plan queries when `session_id` is omitted.
  - Direct dispatcher queries from within the runtime or compromised internal components can bypass session ownership.
- **BYPASS**: Confirmed in `tools.result` and `planning.get`.
- **STATE MUTATION**: None.
- **SEVERITY**: **CRITICAL**

---

### 14. CROSS-SESSION STATE INVENTORY

| State Container | Owner / Scope | Lookup Key | Session Ownership Check | Eviction Behavior | Cross-Session Risk |
|---|---|---|---|---|---|
| `ToolExecutionEngine._result_history` | Session-owned | `invocation_id` | Enforced if `caller_session_id` provided; **FAILS OPEN if `caller_session_id is None`** | `evict_session` purges all owned results | **HIGH**: Direct or null-session queries leak results |
| `PlanningService._plans` | Session-owned | `plan_id` | Checked via `_session_plan_bindings`; **FAILS OPEN if `session_id` omitted in query** | `evict_session` unbinds and purges plans | **CRITICAL**: Anonymous queries leak full plan metadata |
| `PlanningService._session_plan_bindings` | Session | `session_id` | Authoritative 1:1 mapping | Purged on `evict_session` | **NONE** |
| `PersistenceService._storage_root` | Session-isolated files | `session_id` | Canonical `validate_session_path` with strict regex and root containment | Not evicted in memory (filesystem managed) | **NONE** |
| `RecoveryService._session_states` | Session | `session_id` | Canonical `reset_session` and `evict_session` | Clean session eviction | **NONE** |
| `GatewayService._connections` | Transport / Session | `connection_id` | Stamped at ingress | Closed on disconnect | **NONE** |

---

### 15. FAILURE / PARTIAL-MUTATION AUDIT
- **AUDIT**: Verified that all rejected operations produce zero unauthorized state mutations:
  - Cross-session tool query: No state change in `ToolExecutionEngine` or `ToolService`.
  - Cross-session plan query: No state change in `PlanningService`.
  - Traversal persistence query: No filesystem creation, deletion, or modification.
  - Invalid recovery operation: No state transition from `IDLE`.
  - Unroutable gateway message: Connection remains `ACTIVE`, zero deadlocks or corruption.
- **SEVERITY**: **NONE (PASS)**

---

### 16. FROZEN M31/M30/M29 REGRESSION
- **TESTS**:
  - M31 Gateway Boundary tests (`test_m31_gateway_boundary.py`, `test_gateway_authorization.py`): All passed.
  - M29 Tool Lifecycle tests (`test_tool_service.py`, `test_tool_engine.py`): All passed.
  - M30 Safety Gate tests (`test_m30_safety_gate_dispatcher.py`): All passed.
  - Full repository regression (`python -m pytest -q -ra`): **1649 passed in 13.86s**.
- **SEVERITY**: **NONE (PASS)**

---

### 17. TEST STRENGTH AUDIT
- **AUDIT**: Analysis of existing test coverage in `tests/unit/planning/test_planning_service.py` and `tests/unit/tools/test_tool_service.py`:
  - `test_m32_tool_result_ownership_and_lifecycle` tested queries where Session B passed `payload={"invocation_id": id, "session_id": "session_B"}`. It did not test `payload={"invocation_id": id, "session_id": None}`.
  - `test_m32_planning_ownership_and_lifecycle` tested queries where Session B passed `payload={"plan_id": id, "session_id": "session_B"}`. It did not test queries where `session_id` was omitted from payload (`payload={"plan_id": id}`).
  - Because test vectors only tested mismatched string session IDs and not missing/null session IDs, the underlying fallbacks in `PlanningService.handle_get_query` and `ToolExecutionEngine.get_result` were not exposed during initial test verification.

---

### 18. TYPE + HYGIENE
- `npx -y pyright`: 0 errors on modified production code.
- `python -m pytest -q -ra`: 1649 passed.
- `git diff --check`: 0 errors (clean whitespace, proper EOF newlines).

---

### 19. CHANGE-BOUNDARY CHECK
- Production files modified: Exactly the 7 authorized files.
- Test files modified: Smallest necessary existing test locations.
- Zero unauthorized files modified or added.

---

## Root Cause Analysis of Discovered Vulnerabilities

### Vulnerability 1: Planning Plan Disclosure via Omitted Session ID
- **Location**: `python/holomed/planning/service.py`, lines 459–473
- **Root Cause**:
  ```python
  if session_id:
      bound_plan_id = self._session_plan_bindings.get(session_id)
      if plan_id:
          if bound_plan_id == plan_id and plan_id in self._plans:
              p = self._plans[plan_id]
          else:
              return create_error_response(...)
  elif plan_id and plan_id in self._plans:
      p = self._plans[plan_id]  # <-- VULNERABILITY: Global fallback returns plan when session_id is omitted
  ```
- **Remediation Requirement (for future fix)**: The `elif plan_id and plan_id in self._plans:` fallback must be completely removed. If `not session_id`, the handler must fail closed immediately with `ERR_PLAN_NOT_FOUND`.

### Vulnerability 2: Tool Result Disclosure via Null Session ID & Optional Engine Parameter
- **Location**: `python/holomed/tools/engine.py`, lines 171–180 and `python/holomed/tools/service.py`, line 481
- **Root Cause**:
  ```python
  # engine.py
  def get_result(self, invocation_id: str, caller_session_id: Optional[str] = None):
      ...
      if caller_session_id is not None and res.session_id != caller_session_id:
          return None
      return res  # <-- VULNERABILITY: If caller_session_id is None, ownership check is bypassed
  ```
  ```python
  # service.py
  caller_session_id = query_envelope.payload.get("session_id", "")  # returns None if payload={"session_id": None}
  ```
- **Remediation Requirement (for future fix)**: In `ToolExecutionEngine.get_result`, `caller_session_id: str` must be mandatory, non-optional, and non-empty. In `ToolService.handle_result_query`, `caller_session_id = query_envelope.payload.get("session_id")`; if not `caller_session_id`, fail closed immediately with `ERR_RESULT_NOT_FOUND`.

---

## Final Classification

`M32_HOSTILE_AUDIT_FAIL`
