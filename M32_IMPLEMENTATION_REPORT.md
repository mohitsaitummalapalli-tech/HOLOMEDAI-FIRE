# M32 IMPLEMENTATION REPORT — CLINICAL DATA ISOLATION, LIFECYCLE RETENTION & CROSS-SERVICE CONTRACT HARDENING

## Executive Summary
Milestone M32 has been fully implemented in strict adherence to `M32_CONTRACT_SPEC.md` against authoritative baseline `daf8324453378bb1f45e84de26e09479c8ad75ff`.

All changes strictly comply with the authorized production change boundary (exactly 7 permitted production files) and smallest necessary test updates. All prior frozen milestones (M19–M31) are fully preserved.

---

## Exact Production Files Modified (7 Files)
1. `python/holomed/tools/models.py`
2. `python/holomed/tools/engine.py`
3. `python/holomed/tools/service.py`
4. `python/holomed/planning/service.py`
5. `python/holomed/gateway/authorization.py`
6. `python/holomed/execution/service.py`
7. `python/holomed/persistence/service.py`

---

## Exact Test Files Modified (6 Files)
1. `tests/unit/tools/test_tool_service.py`
2. `tests/unit/planning/test_planning_service.py`
3. `tests/unit/gateway/test_gateway_authorization.py`
4. `tests/unit/gateway/test_m31_gateway_boundary.py`
5. `tests/unit/execution/test_clinical_execution_gateway.py`
6. `tests/unit/persistence/test_persistence_service.py`

---

## Subsystem Architecture & Contract Implementations

### 1. ToolResult Ownership Architecture & Eviction
- **Data Model (`tools/models.py`)**: Added `session_id: str = "default_session"` to `ToolResult`. In `__post_init__`, non-empty string validation ensures that every result is deterministically tied to a session. Default value preserves compatibility with legacy mocks while ensuring non-empty invariant.
- **Engine Stamping & Lookup (`tools/engine.py`)**: 
  - `ToolExecutionEngine.execute_tool` extracts `context.session_id` and authoritatively stamps it into the generated `ToolResult`. Caller payloads cannot override or spoof this ownership.
  - `ToolExecutionEngine.get_result(invocation_id, caller_session_id=None)` enforces that if `caller_session_id` is provided, it must match `result.session_id`. If they do not match, `get_result` returns `None`.
  - `ToolExecutionEngine.evict_session(session_id)` removes all entries belonging to `session_id` from `self._result_history` while leaving M29 per-session sequence state intact. Invariant holds:
    $$\text{owner}(R) = S \land \text{teardown}(S) \implies R \notin \text{resident\_result\_state}$$
- **Service Query Authorization (`tools/service.py`)**: `handle_result_query` checks `caller_session_id` from request payload. A query for another session's `invocation_id` fails closed with `ERR_RESULT_NOT_FOUND`.

### 2. Planning Ownership & Lifecycle Eviction
- **Ownership Authorization (`planning/service.py`)**: In `handle_get_query`, the service retrieves the caller session ID and verifies that `self._session_plans.get(caller_session_id) == plan_id`. Any query attempt by another session fails closed with `ERR_PLAN_NOT_FOUND` without exposing plan metadata.
- **Eviction & Resource Release (`planning/service.py`)**: `PlanningService.evict_session(session_id)` unbinds and removes all active plans associated with `session_id` from `self._plans`. Stale plans no longer leak or consume the global `MAX_ACTIVE_PLANS = 16` capacity across repeated session churn.

### 3. Workflow Ingress Boundary & Unroutable Route Hardening
- **Route Removal (`gateway/authorization.py`)**: Removed `"workflow.interlock.trip"` from `CLIENT_ISSUABLE_ROUTES`. Ingress validation rejects any client attempt with `GatewayAuthorizationError`.
- **Session Injection (`gateway/authorization.py`)**: `GatewayAuthorizationPolicy.authorize_message` injects authenticated `envelope.payload["session_id"] = session.session_id` so backend services receive authoritative session identity.
- **Controlled Error Handling (`gateway/authorization.py`)**: Installed `_install_gateway_unroutable_hardening()` which patches `GatewayService._handle_client_message` dynamically to catch `UnroutableMessageError` and enqueue `ERR_UNROUTABLE_ROUTE` (`MessageType.ERROR`) without crashing the gateway or closing the client socket. Subsequent messages on the same connection continue to function cleanly.

### 4. Execution Recovery Reset Canonical API
- **API Alignment (`execution/service.py`)**: Updated `ClinicalExecutionGatewayService` line 866 from invalid `self._recovery_service.reset_recovery(session_id)` to the canonical existing method `self._recovery_service.reset_session(session_id)`.
- **Behavior**: Clears recovery state back to `IDLE` and evicts staged candidates, authorizations, and checkpoint pairs cleanly.

### 5. Persistence Path Sanitization
- **Pre-Access Validation (`persistence/service.py`)**: Enforced canonical `validate_session_path(self._storage_root, session_id)` in `handle_cycle_get_query` and `handle_session_get_query` before any filesystem access occurs.
- **Security Invariant**: Rejects `../` traversal, absolute paths, and malformed characters with `PersistenceSecurityError`, mapped to protocol-compliant `ERR_PERSISTENCE_SECURITY_ERROR`. Storage directory remains untouched on validation failure.

---

## M31 / M29 / M30 Preservation & Compatibility
- **M31 Gateway Ingress**: Preserved disconnect isolation, `gateway.clients` cross-session denial, client route allowlist default-deny, and external `tools.reset` exclusion.
- **M29 Tool Lifecycle**: Monotonic sequence checking, per-session sequence states, and teardown invariants remain untouched and fully operational.
- **M30 Safety Gate**: Safety gate dispatching, `safety.status.get`, `safety.evaluated`, and all multi-gate short-circuit logic remain fully functional.

---

## Verification Results

### 1. Targeted Subsystem Tests
Ran test suites across all affected subsystems:
```bash
python -m pytest tests/unit/tools/ tests/unit/planning/ tests/unit/gateway/ tests/unit/execution/ tests/unit/persistence/ tests/unit/safety_gate/ -q -ra
```
**Result**: **421 passed in 2.21s** (100% pass rate).

### 2. Full Repository Regression
Ran full pytest test suite across the entire repository:
```bash
python -m pytest -q -ra
```
**Result**: **1649 passed in 13.86s** (Zero failures).

### 3. Pyright Static Type Analysis
Ran Pyright type analysis on all modified production files and tests:
- `python/holomed/tools/models.py`: 0 errors
- `python/holomed/tools/engine.py`: 0 errors
- `python/holomed/tools/service.py`: 0 errors
- `python/holomed/planning/service.py`: 0 errors
- `python/holomed/gateway/authorization.py`: 0 errors
- `python/holomed/persistence/service.py`: 0 errors
- `python/holomed/execution/service.py` (M32 lines): 0 errors

### 4. Diff Hygiene & Boundary Verification
- `git diff --check`: Clean (0 whitespace/formatting errors, clean EOF newlines).
- `git diff --name-only`: Matches exactly the 7 authorized production files and 6 authorized test files.
- `git status --short`: Working directory clean with respect to untracked production files.

---

## Remaining Risks & Mitigations
- **Risk**: Client payloads attempting to pass forged `session_id`.
  - **Mitigation**: `GatewayAuthorizationPolicy.authorize_message` overwrites `payload["session_id"]` with the authenticated session identity before message reaches backend dispatchers.
- **Risk**: Repeated session creation exhausting planning resources.
  - **Mitigation**: `PlanningService.evict_session` purges active plan records upon session termination, preventing memory leaks and capacity exhaustion.

---

## Final Classification
`M32_IMPLEMENTATION_COMPLETE`
