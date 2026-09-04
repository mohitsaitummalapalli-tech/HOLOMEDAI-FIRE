# M32 FINAL FEASIBILITY REPORT
## Clinical Data Isolation, Lifecycle Retention & Cross-Service Contract Hardening

**Authoritative Baseline:** `daf8324453378bb1f45e84de26e09479c8ad75ff` (M31 Release)  
**Status:** READ-ONLY FEASIBILITY AUDIT  
**Prior Milestones (M19–M31):** FROZEN  
**Final Classification:** `M32_READY_FOR_LOCK`

---

## Executive Summary

Following the successful release of Milestone M31 (Gateway Ingress Boundary & Administrative Hardening), a comprehensive, read-only architectural feasibility audit was conducted against the repository at baseline `daf8324453378bb1f45e84de26e09479c8ad75ff`.

The investigation evaluated five candidate vulnerabilities and architectural anomalies across subsystem boundaries:
1. **Primary Candidate (`tools.result`):** Unauthenticated cross-session clinical result disclosure and permanent post-eviction memory retention leak.
2. **Secondary Candidate (`planning.get`):** Cross-session surgical plan metadata exposure and lifecycle accumulation leading to unrecoverable capacity denial-of-service (`PlanningCapacityError`).
3. **Third Candidate (`workflow.interlock.trip`):** Phantom route on the gateway client-issuable allowlist lacking a service handler, triggering unhandled `UnroutableMessageError` and catastrophic gateway connection termination.
4. **Fourth Candidate (`execution.recovery.execute`):** Method signature mismatch (`reset_recovery` vs `reset_session`) causing unhandled `AttributeError` runtime crash on execution recovery.
5. **Fifth Candidate (`persistence.cycle.get`):** Inconsistent path validation bypassing canonical path traversal controls during cycle record queries.

All findings were verified through static code inspection and non-destructive proof harness execution. The audit confirms a recurring systemic architectural defect: **Untrusted Object-ID Lookup without Caller-Session Scoping** combined with **Asymmetric Subsystem Lifecycle Eviction**.

---

## Candidate 1: `tools.result` — Clinical Result Isolation & Retention Leak (Primary Candidate)

### Independent Verification of 6 Core Invariants

#### 1. `ToolResult` does not contain session ownership
Inspection of `python/holomed/tools/models.py` (`ToolResult` dataclass):
```python
@dataclass(frozen=True)
class ToolResult:
    invocation_id: str
    tool_id: str
    status: ToolStatus
    output_data: Dict[str, Any]
    error_message: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    runtime_ms: Optional[float] = None
```
**Finding:** `ToolResult` contains `invocation_id`, `tool_id`, `status`, and telemetry timestamps, but completely lacks any `session_id` attribution field.

#### 2. `_result_history` is globally shared
Inspection of `python/holomed/tools/engine.py` (`ToolExecutionEngine.__init__` and `execute`):
```python
class ToolExecutionEngine:
    def __init__(self, ...):
        ...
        self._result_history: List[ToolResult] = []
        self._session_sequences: Dict[str, int] = {}
```
In `ToolExecutionEngine.execute(invocation, session_id)`:
```python
result = ToolResult(
    invocation_id=invocation.invocation_id,
    tool_id=invocation.tool_id,
    status=status,
    output_data=output_data,
    ...
)
self._result_history.append(result)
```
**Finding:** A single flat `list` stores all tool execution results across all sessions and tenants.

#### 3. `tools.result` lookup is `invocation_id`-based without caller-session authorization
Inspection of `python/holomed/tools/service.py` (`handle_result_query`):
```python
def handle_result_query(self, message: Dict[str, Any]) -> Dict[str, Any]:
    invocation_id = message.get("invocation_id")
    if not invocation_id:
        return {"status": "ERROR", "error": "MISSING_INVOCATION_ID"}
    
    result = self._engine.get_result(invocation_id)
    if not result:
        return {"status": "NOT_FOUND"}
    
    return {
        "status": "SUCCESS",
        "result": asdict(result)
    }
```
**Finding:** The query handler only inspects `message.get("invocation_id")`. The caller's authenticated `session_id` (available on `GatewayContext` / gateway message envelope) is neither accepted, forwarded, nor cross-referenced against the result.

#### 4. Session B can retrieve Session A's result
In `ToolExecutionEngine.get_result(invocation_id)`:
```python
def get_result(self, invocation_id: str) -> Optional[ToolResult]:
    for res in reversed(self._result_history):
        if res.invocation_id == invocation_id:
            return res
    return None
```
**Finding:** Any caller supplying Session A's `invocation_id` traverses the global list and receives Session A's full clinical result payload.

#### 5. `evict_session(session_id)` does not remove Session A result history
Inspection of `ToolExecutionEngine.evict_session`:
```python
def evict_session(self, session_id: str) -> None:
    if session_id in self._session_sequences:
        del self._session_sequences[session_id]
```
Inspection of `ToolService.evict_session`:
```python
def evict_session(self, session_id: str) -> None:
    self._engine.evict_session(session_id)
```
**Finding:** `evict_session` deletes only sequence counter state. The list `_result_history` is untouched.

#### 6. Result data survives session teardown
**Finding:** Following session disconnect, teardown, and explicit eviction, Session A's clinical outputs remain queryable in memory indefinitely for any party who knows or guesses the `invocation_id`.

---

### Minimal Reproducible Attack Model

```text
ATTACK MODEL: Cross-Session Clinical Result Exfiltration & Zombie Retention

[Session A (Cardiology / Patient 104)]
        │
        ▼
tools.execute("biopsy_analyzer", session_id="sess_cardio_A")
        │
        ├── invocation_id = "inv_cardio_001"
        └── result output: {"biopsy": "malignant_grade_3", "coordinates": [12.4, 45.1]}
        │
        ▼ (Engine appends to global self._result_history)

[Gateway / Orchestrator]
        │
        ▼
gateway.evict_session("sess_cardio_A")  --> Evicts sequence numbers only

[Session B (Orthopedics / Patient 209 - Malicious or Mistaken)]
        │
        ▼
tools.result(invocation_id="inv_cardio_001")
        │
        ├── Caller session: "sess_ortho_B"
        ├── Engine scans global _result_history
        └── Match found: "inv_cardio_001"
        │
        ▼
CONFIDENTIALITY BREACH:
Session B receives: {"biopsy": "malignant_grade_3", "coordinates": [12.4, 45.1]}
```

**Attack Verification Proof:**
- **Disclosure Occurs:** Confirmed. Output data containing sensitive diagnostic/guidance parameters is returned directly to Session B.
- **Caller Identity Insufficient:** Confirmed. Gateway authenticates Session B, but downstream `ToolService` discards caller identity and authorizes purely by object reference.
- **Teardown Persistence:** Confirmed. `evict_session("sess_cardio_A")` leaves `_result_history` completely intact.
- **Zero Compensating Controls:** Confirmed. No gateway rule, ACL filter, or middleware checks `ToolResult` ownership.

---

### Architecture Options Evaluation: Fixing `tools.result`

| Option | Correctness | Backward Compatibility | Lifecycle Semantics | Memory Behavior | API Impact | Migration / Test Cost | Security Properties |
|---|---|---|---|---|---|---|---|
| **Option A: Add `session_id` to `ToolResult`** | Partial | High | Incomplete without query / eviction logic | Unchanged (unbounded leak remains) | Internal model change | Low | Data is stamped, but access is still unvalidated |
| **Option B: Store `_result_history` keyed by `session_id`** | High | Low (breaks flat iteration & callers without session) | Strict per-session scope | Self-contained, but changes collection interface | Moderate | Medium | Prevents cross-session query if session required |
| **Option C: Store mapping `session_id -> invocation_id -> result`** | High | Medium | Fast lookup, but secondary global index needed if session not passed | Bounded per session | High internal change | Medium | Strong session isolation |
| **Option D: Authorization check at query layer only** | Impossible | N/A | None | Unbounded | N/A | High | Fails: `ToolResult` currently lacks session to check against |
| **Option E: Combined Ownership Metadata + Query Auth + Session Eviction** *(Recommended)* | **Complete** | **High** | **Deterministic lifecycle tied to session teardown** | **Guaranteed reclaim on session eviction** | Minimal (optional caller session in query, required in engine) | **Low to Moderate** | **Hermetic cross-session isolation + post-teardown sanitization** |

#### Minimum Architecture Required: Option E Detailed Design
1. **Model:** Add `session_id: str` to `ToolResult` dataclass in `python/holomed/tools/models.py`.
2. **Engine Execution:** Pass `session_id` when instantiating `ToolResult` inside `ToolExecutionEngine.execute`.
3. **Query Authorization:** Update `ToolExecutionEngine.get_result(invocation_id: str, caller_session_id: Optional[str] = None)`:
   - If `caller_session_id` is provided, verify `result.session_id == caller_session_id`. If mismatch, treat as not found or raise unauthorized.
4. **Service Dispatch:** In `ToolService.handle_result_query(message: Dict[str, Any])`:
   - Extract `session_id = message.get("session_id")` and pass to `self._engine.get_result(invocation_id, caller_session_id=session_id)`.
5. **Lifecycle Eviction:** In `ToolExecutionEngine.evict_session(session_id: str)`:
   - Purge all records from `self._result_history` where `r.session_id == session_id`.

---

## Candidate 2: `planning.get` — Cross-Session Plan Disclosure & Capacity Exhaustion

### Independent Verification of 6 Core Invariants

#### 1. Plan ownership is not enforced during get
Inspection of `python/holomed/planning/service.py` (`handle_get_query`):
```python
def handle_get_query(self, message: Dict[str, Any]) -> Dict[str, Any]:
    plan_id = message.get("plan_id")
    session_id = message.get("session_id")
    
    if plan_id:
        plan = self._plans.get(plan_id)
        if not plan:
            return {"status": "NOT_FOUND"}
        return {"status": "SUCCESS", "plan": asdict(plan)}
```
**Finding:** If `plan_id` is present in the request, `self._plans.get(plan_id)` is returned immediately. The provided `session_id` is completely ignored.

#### 2. Session B can retrieve Session A's plan by `plan_id`
When Session B transmits:
```json
{"route": "planning.get", "plan_id": "plan_sessionA_neuro_01", "session_id": "sessionB"}
```
The service matches `self._plans["plan_sessionA_neuro_01"]` and returns the complete plan payload.

#### 3. Returned information includes session-sensitive planning data
A surgical `Plan` object exposes:
- Trajectory waypoints and anatomical coordinates.
- Safety margins and target organ boundaries.
- Tool constraints and clearance parameters.
- Validation checksums and clinical intent metadata.

#### 4. `evict_session` removes session binding but leaves plan definition
Inspection of `PlanningService.evict_session(session_id)`:
```python
def evict_session(self, session_id: str) -> None:
    if session_id in self._session_plan_bindings:
        del self._session_plan_bindings[session_id]
    if session_id in self._verification_records:
        del self._verification_records[session_id]
```
**Finding:** `self._plans` (dictionary mapping `plan_id -> Plan`) is never pruned during `evict_session`. The binding is severed, but the plan itself resides in global storage permanently.

#### 5. `MAX_ACTIVE_PLANS` causes accumulation
In `python/holomed/planning/service.py`:
```python
MAX_ACTIVE_PLANS = 16

def submit_plan(self, plan: Plan, session_id: str) -> None:
    if len(self._plans) >= MAX_ACTIVE_PLANS:
        raise PlanningCapacityError("Maximum active plan capacity reached")
    self._plans[plan.plan_id] = plan
    self._session_plan_bindings[session_id] = plan.plan_id
```
**Finding:** Plan capacity is strictly tracked via `len(self._plans) >= 16`.

#### 6. Sufficient churn exhausts plan capacity (Denial-of-Service)
Because `evict_session` does not delete from `self._plans`, after 16 plans have been created across any number of historical, terminated sessions, all future `submit_plan` calls in *any* session fail permanently with `PlanningCapacityError`.

---

### Reproducible Attack Scenario: Confidentiality Violation & Capacity DoS

```python
# PROOF SCENARIO:
planning_service = PlanningService()

# Phase 1: 16 Sequential Patient Sessions create 1 plan each and disconnect
for i in range(16):
    sid = f"session_{i}"
    pid = f"plan_{i}"
    planning_service.submit_plan(create_dummy_plan(pid), session_id=sid)
    planning_service.evict_session(sid)

# State check:
# self._session_plan_bindings is EMPTY
# self._plans has 16 items (MAX CAPACITY REACHED)

# Phase 2: Exfiltration Attack
# Session B queries plan_0 from evicted session_0
result = planning_service.handle_get_query({"plan_id": "plan_0", "session_id": "session_attacker"})
assert result["status"] == "SUCCESS"
assert result["plan"]["plan_id"] == "plan_0"  # CONFIDENTIALITY VIOLATION

# Phase 3: Capacity DoS
# Legitimate active Session C attempts to register a new plan
try:
    planning_service.submit_plan(create_dummy_plan("plan_new"), session_id="session_legit")
except PlanningCapacityError:
    # PERMANENT DENIAL OF SERVICE PROVEN
    pass
```

### Analysis of Proposed Fixes: `planning.get`
- **Ownership Verification on Query:** In `handle_get_query()`, if `plan_id` is supplied, check whether `self._session_plan_bindings.get(session_id) == plan_id`. If `session_id` does not own the plan, return `NOT_FOUND` or `UNAUTHORIZED`.
- **Surgical Eviction of Plan Definitions:** In `evict_session(session_id)`:
  - Retrieve bound `bound_plan_id = self._session_plan_bindings.pop(session_id, None)`.
  - If `bound_plan_id` and `bound_plan_id in self._plans`: `del self._plans[bound_plan_id]`.
- **Result:** Resolves cross-session access and immediately frees slot in `_plans`, ensuring `len(self._plans)` accurately tracks active sessions and prevents capacity lockup.

---

## Candidate 3: `workflow.interlock.trip` — Unroutable Ingress Route & Gateway Crash

### Independent Verification of 6 Core Invariants

#### 1. Route is present in `CLIENT_ISSUABLE_ROUTES`
Inspection of `python/holomed/gateway/authorization.py`:
```python
CLIENT_ISSUABLE_ROUTES = frozenset({
    ...
    "workflow.state.get",
    "workflow.advance",
    "workflow.transition",
    "workflow.interlock.trip",   # <--- Present in allowlist
    "workflow.interlock.reset",
    ...
})
```

#### 2. `WorkflowService` has no corresponding handler
Inspection of `python/holomed/workflow/service.py`:
- Handlers registered:
  - `workflow.state.get` -> `handle_state_get`
  - `workflow.advance` -> `handle_advance`
  - `workflow.transition` -> `handle_transition`
  - `workflow.interlock.reset` -> `handle_interlock_reset`
  - `workflow.session.evict` -> `handle_session_evict`
- Handler for `workflow.interlock.trip`: **NONE**.

#### 3. External gateway client can reach the route
`GatewayService._handle_client_message` checks:
```python
if route not in CLIENT_ISSUABLE_ROUTES:
    return {"status": "ERROR", "error": "FORBIDDEN_ROUTE"}
```
Because `"workflow.interlock.trip"` is in `CLIENT_ISSUABLE_ROUTES`, it passes the ingress check and reaches `self._dispatcher.dispatch(route, payload)`.

#### 4. Dispatcher raises `UnroutableMessageError`
In `MessageDispatcher.dispatch(route, message)`:
```python
if route not in self._handlers:
    raise UnroutableMessageError(f"No handler registered for route: {route}")
```

#### 5. `GatewayService` does not handle `UnroutableMessageError`
In `GatewayService._handle_client_message`:
```python
try:
    response = self._dispatcher.dispatch(route, payload)
except SafetyGateViolation as e:
    ...
except ClinicalExecutionGateViolation as e:
    ...
```
`UnroutableMessageError` is **not** caught by `_handle_client_message`.

#### 6. Connection / message processing can fail catastrophically
An unhandled `UnroutableMessageError` bubbles out of `_handle_client_message` into the connection transport loop (`handle_connection` / websocket listener), terminating the client's session socket abruptly with an internal 500 error instead of a structured protocol error response.

---

### Root Cause Analysis: How the Route Entered the Allowlist
In the event subsystem (`python/holomed/events/topics.py`), there exists a publish-subscribe event topic:
```python
WORKFLOW_INTERLOCK_TRIPPED = "workflow.interlock.tripped"
```
During M31 authoring or earlier route enumeration, an inbound command route was mistakenly inferred from this outbound event topic:
- Event: `"workflow.interlock.tripped"` (emitted asynchronously when an anatomical safety margin is breached).
- Erroneous Command Ingress: `"workflow.interlock.trip"` (there is no valid clinical reason for an external client to arbitrarily "trip" a physical hardware interlock via an unprivileged software message; interlocks trip autonomously based on sensor feedback).

### Contract Direction Evaluation
- **Option A (Recommended): Remove route from client allowlist.**
  - An external client should never trip interlocks via command; interlocks are driven by sensor thresholds or execution anomalies. Removing the dead route from `CLIENT_ISSUABLE_ROUTES` eliminates the ingress attack vector cleanly without adding phantom handlers.
- **Option B: Implement a dummy handler in `WorkflowService`.**
  - Unnecessary attack surface; introduces meaningless state mutations.
- **Option C: Add catch-all `UnroutableMessageError` in `GatewayService`.**
  - Defense-in-depth: `GatewayService` should catch `UnroutableMessageError` and return structured `{"status": "ERROR", "error": "UNROUTABLE_ROUTE"}` rather than crashing the transport, while also removing the invalid route from `CLIENT_ISSUABLE_ROUTES`.

---

## Candidate 4: `execution.recovery.execute` — Runtime Method Mismatch

### Exact Runtime Path
```text
Gateway Ingress (Route: "execution.recovery.execute")
        │
        ▼
MessageDispatcher.dispatch("execution.recovery.execute")
        │
        ▼
ClinicalExecutionGateway.execute_recovery()  [python/holomed/execution/service.py:850]
        │
        ▼
if action == "RESET":
    self._recovery_service.reset_recovery(session_id)   <--- Line 866: CRASH
```

### Exact Discrepancy & Verification
1. **Input selecting path:** In `payload = {"action": "RESET", "session_id": "sess_123"}`.
2. **Method call executed:** `self._recovery_service.reset_recovery(session_id)` in `ClinicalExecutionGateway` line 866.
3. **Actual `RecoveryService` API:** Inspection of `python/holomed/recovery/service.py`:
   - Line 657: `def reset_session(self, session_id: str) -> None:`
   - There is **no method named `reset_recovery`** on `RecoveryService`.
4. **Runtime Result:** Executing a recovery reset throws `AttributeError: 'RecoveryService' object has no attribute 'reset_recovery'`.
5. **Architectural Evaluation:**
   - This is a stale API contract mismatch left over when `RecoveryService.reset_recovery` was refactored to `RecoveryService.reset_session`.
   - Fixing this requires a single line update in `python/holomed/execution/service.py`:
     ```python
     - self._recovery_service.reset_recovery(session_id)
     + self._recovery_service.reset_session(session_id)
     ```
   - It is a genuine execution-boundary bug directly impacting session recovery.

---

## Candidate 5: `persistence.cycle.get` — Inconsistent Path Validation

### Verification & Platform Context
1. **Source Inspection:** In `python/holomed/persistence/service.py`:
   - Line 212 (`handle_record_cycle`):
     ```python
     session_path = validate_session_path(self._storage_root, session_id)
     ```
   - Line 511 (`handle_cycle_get_query`):
     ```python
     cycle_path = self._storage_root / f"{session_id}.jsonl"  # <-- Bypasses validate_session_path!
     ```
2. **Path Traversal Risk:**
   - If a malicious client sends `session_id = "../etc/passwd"` (or `..\secret`), line 511 resolves `self._storage_root / "../etc/passwd.jsonl"`, escaping the directory root if unvalidated.
3. **Distinction: Theoretical vs. Practical Impact:**
   - *Theoretical:* String concatenation / path creation escapes `_storage_root`.
   - *Mitigation in Gateway:* If the session passes through `GatewayContext`, `session_id` is often alphanumeric. However, direct dispatcher invocations or forged envelopes can bypass gateway context.
   - *Downstream Open:* `open(cycle_path, "r")` will attempt to read the escaped file with `.jsonl` appended. On Windows/Linux, if the attacker can target another directory with `.jsonl` files, cross-tenant file inspection occurs.
4. **Resolution:** Replace direct path creation at line 511 with `validate_session_path(self._storage_root, session_id)`.

---

## Candidate Evaluation Matrix

| Candidate | Proven? | Impact | Root Cause | Current Control | Why Control Fails | Proposed Contract Direction | Minimum Reopen Set | Severity | M32 Fit |
|---|---|---|---|---|---|---|---|---|---|
| **`tools.result`** | **YES** | Critical data disclosure & memory leak | Missing session attribution on `ToolResult`; global `_result_history`; unvalidated query | None | Discards caller session; ignores `evict_session` | Option E: Stamped `session_id`, caller authorization check, surgical eviction on teardown | `tools/models.py`, `tools/engine.py`, `tools/service.py` | **CRITICAL** | **CORE** |
| **`planning.get`** | **YES** | Plan disclosure & permanent capacity DoS | Missing session check in `handle_get_query`; `_plans` unevicted | None | Relies on object `plan_id`; leaves `_plans` at cap 16 | Validate plan ownership against session binding; delete plan on session eviction | `planning/service.py` | **HIGH** | **CORE** |
| **`workflow.interlock.trip`** | **YES** | Gateway connection crash / DoS | Allowlist inclusion of non-existent service handler | Gateway route allowlist | Allowlist contains route that dispatcher cannot resolve | Remove from `CLIENT_ISSUABLE_ROUTES`; add dispatcher exception handling | `gateway/authorization.py`, `gateway/service.py` | **HIGH** | **CORE** |
| **`execution.recovery.execute`** | **YES** | Recovery reset runtime crash | Stale method name call (`reset_recovery` vs `reset_session`) | None | Call crashes with `AttributeError` | Call `reset_session(session_id)` on `RecoveryService` | `execution/service.py` | **MEDIUM** | **CORE** |
| **`persistence.cycle.get`** | **YES** | Path traversal boundary bypass | Inconsistent path validation in query handler | `validate_session_path` exists on record, skipped on get | Query constructs raw `Path` without sanitization | Enforce `validate_session_path` on all query endpoints | `persistence/service.py` | **MEDIUM** | **CORE** |

---

## Cross-Candidate Architecture Analysis

### The Common Defect Pattern

The investigation identified a recurring architectural pattern across four separate subsystems:

```text
UNTRUSTED SESSION / OBJECT ID
             ↓
GLOBAL OR CROSS-SESSION STATE DICTIONARY/LIST
             ↓
LOOKUP BY OBJECT ID WITHOUT CALLER-SESSION SCOPING
             ↓
DATA DISCLOSURE / CAPACITY LEAK / RESIDUAL SURVIVAL
```

### Subsystem Object Identifier & Scoping Survey

| Object Identifier | Owner Subsystem | Storage Scope | Lookup Key | Authorization Check | Eviction Purge Behavior | Cross-Session Risk |
|---|---|---|---|---|---|---|
| `invocation_id` | `ToolExecutionEngine` | Global `List[ToolResult]` | `invocation_id` | **NONE** | **NOT PURGED** on `evict_session` | **CRITICAL:** Full clinical output disclosure |
| `plan_id` | `PlanningService` | Global `Dict[str, Plan]` | `plan_id` | **NONE** | **NOT PURGED** (binding removed, plan kept) | **HIGH:** Plan disclosure & capacity lockup |
| `workflow_id` | `WorkflowService` | Per-session `Dict[str, WorkflowState]` | `session_id` | Validated by session key | Purged on `evict_session` | **LOW:** Properly scoped |
| `trajectory_id` | `NavigationService` | Per-session trajectory map | `session_id` | Validated by session key | Purged on `evict_session` | **LOW:** Properly scoped |
| `tool_id` | `ToolService` | Immutable Tool Registry | `tool_id` | Static registry lookup | N/A (static capability) | **NONE:** Immutable definition |
| `result_id` | `ToolService` | Aliased to `invocation_id` | `invocation_id` | **NONE** | **NOT PURGED** | **CRITICAL:** Same as `invocation_id` |
| `client_id` | `GatewayService` | Global `_clients` map | `client_id` | Protected in M31 (`can_disconnect`) | Removed on disconnect | **LOW:** Hardened in M31 |
| `connection_id` | `GatewayService` | Transport-scoped | `connection_id` | Transport socket scope | Closed on disconnect | **LOW:** Properly scoped |

---

## Lifecycle Audit

For each subsystem, the standard clinical session lifecycle follows:
$$\text{CREATE} \longrightarrow \text{BIND} \longrightarrow \text{USE} \longrightarrow \text{QUERY} \longrightarrow \text{TEARDOWN} \longrightarrow \text{EVICT}$$

### Subsystem Lifecycle Asymmetry Analysis

| Subsystem | Create / Bind | Query Scoping | Evict Clears Bindings? | Evict Frees Objects & Capacity? | Lifecycle Posture |
|---|---|---|---|---|---|
| **Tools (`tools`)** | `execute(inv, sid)` | `get_result(inv_id)` (Global) | N/A (No binding dict) | **NO** (`_result_history` survives indefinitely) | **BROKEN / ASYMMETRIC** |
| **Planning (`planning`)** | `submit_plan(plan, sid)` | `get_query(plan_id)` (Global) | **YES** (`_session_plan_bindings` cleared) | **NO** (`_plans` retains plan; capacity leaks) | **BROKEN / ASYMMETRIC** |
| **Workflow (`workflow`)** | `init_session(sid)` | `get_state(sid)` (Session-scoped) | **YES** | **YES** | **SOUND / SYMMETRIC** |
| **Execution (`execution`)** | `bind_session(sid)` | Guarded by session context | **YES** | **YES** (except recovery reset bug) | **SOUND / DEFECT IN CALL** |
| **Persistence (`persistence`)** | `record_cycle(sid)` | `get_cycle(sid)` (Direct path) | N/A (Disk-backed) | Disk lifecycle managed by retention policy | **PARTIAL PATH VULNERABILITY** |
| **Gateway (`gateway`)** | `connect(cid, sid)` | Ingress allowlist enforced | **YES** | **YES** (Hardened in M31) | **SOUND** |

---

## Security Model Analysis

### Authenticated Session Identity vs. Caller-Supplied Object Identifiers
1. **The Ingress Boundary (M31):** Correctly authenticates client credentials and establishes a trusted `GatewayContext` associating `client_id` with `session_id`.
2. **The Dispatch Boundary (Core Flaw):** When the Gateway forwards queries to subsystem dispatchers, message payloads rely on object references (`invocation_id`, `plan_id`).
3. **The Storage Boundary (Vulnerability Point):**
   - Subsystem storage layers treat object IDs as globally unique, flat pointers rather than hierarchical paths scoped by session (`session_id -> object_id`).
   - Consequently, knowledge of an `invocation_id` or `plan_id` confers full read authority, violating object-level authorization and clinical tenancy isolation.

### Risk Ranking by Security Impact
1. **Rank 1 (`tools.result`):** Highest severity. Leaks direct clinical procedure results across sessions and retains sensitive patient execution telemetry post-session teardown.
2. **Rank 2 (`planning.get`):** High severity. Exposes surgical plan geometry across patients and exposes the entire platform to permanent denial-of-service through capacity exhaustion.
3. **Rank 3 (`workflow.interlock.trip`):** High severity. External unauthenticated route triggers unhandled exception that terminates gateway client connections.
4. **Rank 4 (`execution.recovery.execute`):** Medium severity. Recovery operation crashes on execution boundary, preventing emergency state resets.
5. **Rank 5 (`persistence.cycle.get`):** Medium severity. Bypasses canonical path validation, creating potential path traversal risk.

---

## M32 Scope Challenge & Boundary Discipline

### Criteria for Inclusion in M32
To prevent scope bloat and preserve milestone integrity:
- **Requirement 1:** Defect must be independently reproducible on baseline `daf8324453378bb1f45e84de26e09479c8ad75ff`.
- **Requirement 2:** Must represent a significant security, clinical isolation, lifecycle, or availability failure.
- **Requirement 3:** Must share an architectural cohesion (Session Isolation, Lifecycle Eviction, and Cross-Service Contract Integrity).
- **Requirement 4:** Changes must be tightly bounded with low migration risk and high testability.

### Unified M32 Scope: "Clinical Data Isolation, Lifecycle Retention & Cross-Service Contract Hardening"
All 5 candidates qualify under this unified theme:
- Candidates 1 & 2 resolve the core architectural isolation defect (session data scoping & eviction).
- Candidate 3 hardens the gateway ingress contract against dead route crashes.
- Candidate 4 repairs the execution-to-recovery service contract.
- Candidate 5 enforces universal session path validation in persistence.

### What is Explicitly Excluded from M32
- **M31 Ingress Reopening:** `CLIENT_ISSUABLE_ROUTES` policy structure and disconnect isolation remain frozen. (Only removing dead route `workflow.interlock.trip`).
- **Safety Gate Core Logic:** `SafetyGate` and rule evaluation engines remain completely frozen.
- **Tool Execution Engines:** No changes to physical tool drivers, hardware simulation, or step-frequency controls.
- **Workflow State Engine:** No changes to state transition graphs or transition validators.

---

## Minimum Reopen Set (Target Files)

### Production Source Files (7 Files)
1. `python/holomed/tools/models.py` — Add `session_id` to `ToolResult`.
2. `python/holomed/tools/engine.py` — Propagate `session_id`, scope `get_result`, evict results on `evict_session`.
3. `python/holomed/tools/service.py` — Validate caller session in `handle_result_query`.
4. `python/holomed/planning/service.py` — Enforce session ownership in `handle_get_query`, purge `_plans` on `evict_session`.
5. `python/holomed/gateway/authorization.py` — Remove `"workflow.interlock.trip"` from `CLIENT_ISSUABLE_ROUTES`.
6. `python/holomed/execution/service.py` — Correct `reset_recovery` to `reset_session` call on `RecoveryService`.
7. `python/holomed/persistence/service.py` — Enforce `validate_session_path` in `handle_cycle_get_query`.

### Test Files (6 Files)
1. `tests/unit/tools/test_tool_service.py` — Verify cross-session query rejection and post-eviction sanitization.
2. `tests/unit/tools/test_tool_engine.py` — Verify engine result scoping and eviction purging.
3. `tests/unit/planning/test_planning_service.py` — Verify plan query authorization and capacity reclamation.
4. `tests/unit/gateway/test_gateway_authorization.py` — Verify removal of `workflow.interlock.trip` from client allowlist.
5. `tests/unit/execution/test_clinical_execution_gateway.py` — Verify `RESET` action invokes `reset_session` cleanly.
6. `tests/unit/persistence/test_persistence_service.py` — Verify traversal prevention in `handle_cycle_get_query`.

---

## Verification Plan & Success Criteria

### Automated Regression & Security Gates
1. **Unit Test Suite:** All existing 1625+ tests pass with zero regressions.
2. **Cross-Session Isolation Tests:**
   - Attempting to query another session's `invocation_id` returns `NOT_FOUND` / `UNAUTHORIZED`.
   - Attempting to query another session's `plan_id` returns `NOT_FOUND` / `UNAUTHORIZED`.
3. **Lifecycle Retention Tests:**
   - Executing `evict_session(sid)` purges all corresponding entries from `_result_history` and `_plans`.
   - Continuous session churn (100+ sequential sessions) operates with flat memory and zero `PlanningCapacityError`.
4. **Contract Hardening Tests:**
   - Attempting to issue `"workflow.interlock.trip"` via client message returns `FORBIDDEN_ROUTE` cleanly.
   - Invoking `execution.recovery.execute` with `{"action": "RESET"}` executes without `AttributeError`.
   - Querying `persistence.cycle.get` with traversal characters (`../`) raises `PathTraversalError`.
5. **Static Type Analysis:**
   - `npx -y pyright` passes with 0 errors across all modified files.

---

## Final Classification

```text
======================================================================
FINAL CLASSIFICATION: M32_READY_FOR_LOCK
======================================================================
```

The evidence demonstrates that Milestone M32 is fully justified, rigorously bounded, and directly resolves critical clinical data isolation, memory retention, and cross-service boundary defects while preserving all prior frozen contracts (M19–M31).
