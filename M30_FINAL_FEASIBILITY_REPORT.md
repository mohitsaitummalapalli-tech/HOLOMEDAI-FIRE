# M30 FINAL FEASIBILITY AUDIT — SAFETY GATE DISPATCHER CONTRACT

**Authoritative Baseline:** `8c46aa2ad883aca2089da98db13cc2d5ef0b1dcb`  
**Previous Release:** M29 — Clinical Tool Subsystem Lifecycle Eviction & Teardown Hardening (`M29_FROZEN`)  
**Status:** READ-ONLY FEASIBILITY AUDIT  
**Scope Changes:** ZERO (0 source files modified, 0 test files modified, 0 commits, 0 pushes)

---

## 1. Proof of Dispatcher Initialization Failure

### 1.1 Real Production Path Execution
Using the authoritative `MessageDispatcher` (`holomed.core.dispatcher.MessageDispatcher`) and `SafetyGateService` (`holomed.safety_gate.service.SafetyGateService`) under a canonical `RuntimeContext`, the smallest real initialization path was executed:

```python
from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel
from holomed.core.dispatcher import MessageDispatcher
from holomed.runtime.context import RuntimeContext
from holomed.safety_gate.service import SafetyGateService

cfg = AppConfig(
    app_name="holomed",
    environment=EnvironmentProfile.DEVELOPMENT,
    host="127.0.0.1",
    port=8000,
    log_level=LogLevel.DEBUG,
)
ctx = RuntimeContext(app_config=cfg, epoch_id=1)

disp = MessageDispatcher()
disp.initialize(ctx)

sg = SafetyGateService(dispatcher=disp)
sg.initialize(ctx)
```

### 1.2 Exact Failure Stack Trace
The execution aborted with an unhandled fatal exception:

```
Traceback (most recent call last):
  File "scratch/prove_failure.py", line 22, in <module>
    sg.initialize(ctx)
  File "python/holomed/safety_gate/service.py", line 135, in initialize
    self._dispatcher.register_command_handler("safety_gate.evaluate", self.handle_evaluate_command, self.name)
  File "python/holomed/core/dispatcher.py", line 433, in register_command_handler
    return self._subscription_registry.register_command(topic, handler, service_name)
  File "python/holomed/core/subscription.py", line 224, in register_command
    validated = validate_concrete_topic(topic)
  File "python/holomed/core/subscription.py", line 78, in validate_concrete_topic
    raise TopicValidationError(
        f"Concrete topic must match ^[a-z0-9]+(\\.[a-z0-9]+)*$, got {topic!r}"
    )
holomed.core.exceptions.TopicValidationError: Concrete topic must match ^[a-z0-9]+(\.[a-z0-9]+)*$, got 'safety_gate.evaluate'
```

### 1.3 Exact Source Location & Root Cause
- **Failing Line**: [python/holomed/safety_gate/service.py:135](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/safety_gate/service.py#L135)
  ```python
  self._dispatcher.register_command_handler("safety_gate.evaluate", self.handle_evaluate_command, self.name)
  ```
- **Secondary Failing Line**: [python/holomed/safety_gate/service.py:136](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/safety_gate/service.py#L136)
  ```python
  self._dispatcher.register_query_handler("safety_gate.status.get", self.handle_get_status_query, self.name)
  ```
- **Enforcing Invariant**: [python/holomed/core/subscription.py:77-80](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/core/subscription.py#L77-L80)
  ```python
  _CONCRETE_TOPIC_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9]+)*$")
  if not _CONCRETE_TOPIC_RE.match(topic):
      raise TopicValidationError(
          f"Concrete topic must match ^[a-z0-9]+(\\.[a-z0-9]+)*$, got {topic!r}"
      )
  ```
- **Verdict**: The underscore character (`_`) in `"safety_gate.evaluate"` and `"safety_gate.status.get"` violates `_CONCRETE_TOPIC_RE`. The failure is **100% deterministic and fatal**.

---

## 2. Route Registration State

A complete forensic inspection of all safety-related route registrations across the repository confirmed:

| Topic Name | Handler Method | Registration Type | Topic Grammar Compliance | Capability Gated | Gateway Mediated | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `safety_gate.evaluate` | `handle_evaluate_command` | **COMMAND** | **VIOLATION** (contains `_`) | **NO** (Capability-free) | Yes (Surgeon/Assistant) | **CRITICAL ANOMALY** |
| `safety_gate.status.get`| `handle_get_status_query` | **QUERY** | **VIOLATION** (contains `_`) | NO (Read-only query) | Yes (All roles) | **NAMING DEFECT** |

- **Aliases**: Exactly **0** aliases exist.
- **Legacy Routes**: Exactly **0** legacy fallback routes exist.
- **Internal / External Status**: Registered directly on `MessageDispatcher`. If the dispatcher allowed the topic grammar, any client on the message bus or connecting through `GatewayService` with role `SURGEON_CONSOLE` or `ASSISTANT_PANEL` could dispatch raw `safety_gate.evaluate` commands.

---

## 3. Production Impact

### 3.1 Impact on Real Runtime Engine & Startup
In a production deployment, services are orchestrated and wired to the central message bus:
1. `MessageDispatcher` initializes cleanly.
2. When `SafetyGateService` is provided the initialized `MessageDispatcher` (as required by its declared dependency `dependencies = ("dispatcher",)`), calling `SafetyGateService.initialize(ctx)` immediately crashes with `TopicValidationError`.
3. Startup aborts in `INITIALIZED` phase; `SafetyGateService` never reaches `STARTED`.
4. Event emission via `_emit_event("safety_gate.evaluated", ...)` also fails if dispatched over a validating bus.

### 3.2 Unit Test vs Production Construction
- **In Unit Tests**: Every existing execution test (M25 `test_m25_session_teardown.py:66`, M26 `test_m26_perceptual_lifecycle.py:130`, M27 `test_m27_workflow_interlock_lifecycle.py:78`, M29 `test_m29_tool_lifecycle.py:146`) explicitly bypassed the dispatcher by passing `dispatcher=None` to `SafetyGateService`:
  ```python
  safety_gate = SafetyGateService(dispatcher=None, workflow_service=workflow)
  ```
- **In Safety Gate Unit Tests**: All tests in `tests/unit/safety_gate/` instantiated `mock_dispatcher = MagicMock(spec=MessageDispatcher)`. The mock swallowed `register_command_handler` and `register_query_handler`, preventing `validate_concrete_topic` from ever executing.
- **Production Truth**: `SafetyGateService` has **never** been capable of running with a real `MessageDispatcher`.

---

## 4. `safety_gate.evaluate` Authority Analysis

A line-by-line trace of `safety_gate.evaluate` through `handle_evaluate_command` to `self.evaluate(req)`:

```python
dispatcher.dispatch(envelope)
  └─> SafetyGateService.handle_evaluate_command(command_envelope)
        └─> SafetyGateService.evaluate(request: GateRequest)
              ├─> Capacity Check: allocates slot in self._latest_decisions[session_id]
              ├─> Pure Evaluation: SafetyGateEvaluator.evaluate(...)
              ├─> Deduplication Signature Check: (decision, reason_code)
              ├─> Persistence Mutation: self._persistence_service.record_audit(...)
              ├─> Event Publication: self._emit_event("safety_gate.evaluated", ...)
              └─> Decision Cache Mutation: self._latest_decisions[session_id] = decision_record
```

### Forensic Verdict
1. **Mutates State**: **YES**. Mutates `self._latest_decisions[session_id]` and `self._persisted_states[session_id]`.
2. **Consumes Capacity**: **YES**. If `session_id` is new, increments active session count against `MAX_ACTIVE_GATE_SESSIONS = 16`.
3. **Persists Data**: **YES**. Calls `self._persistence_service.record_audit(...)` directly, writing durable audit records to disk.
4. **Requires Capability**: **NO**. It is completely capability-free.
5. **Affects Downstream Execution**:
   - `ClinicalExecutionGatewayService` executes `self._safety_gate_service.evaluate(...)` fresh during every clinical operation; it does **not** rely on `_latest_decisions`.
   - However, `safety_gate.status.get` and `get_gate_status()` read directly from `_latest_decisions`. An unmediated call alters the observable cached safety status of a session and pollutes the durable audit journal.

---

## 5. Architectural Boundary Test

### 5.1 Comparison with M19–M24 Hardening Precedents
In milestones M21–M24, raw subsystem mutation routes were systematically removed from dispatcher registration:
- **M21**: Removed `tools.invoke` from dispatcher. All tool invocations must route through `execution.tool.invoke` (with capability `TOOL_INVOCATION`).
- **M22**: Removed `recovery.stage`, `recovery.verify`, `recovery.activate` from dispatcher. All recovery must route through `execution.recovery.execute` (with capability `RECOVERY_ACTIVATION`).
- **M23**: Removed `registration.submit`, `registration.solve`, `registration.verify` from dispatcher. All registration must route through `execution.registration.execute` (with capability `REGISTRATION_ALIGNMENT`).
- **M24**: Removed `planning.submit`, `planning.verify`, `planning.lock` from dispatcher. All planning must route through `execution.planning.execute` (with capability `PLANNING_MODIFICATION`).

In all four milestones, raw domain commands were removed from the public message bus because:
1. `ClinicalExecutionGatewayService` is the sole authoritative transaction boundary for all clinical operations.
2. Clinical operations require pre-flight checks, workflow validation, safety gate evaluation, capability minting, monotonic sequencing, and coordinated persistence.
3. Exposing a raw command on the dispatcher allows callers to bypass the gateway's sequencing, workflow gating, and capability verification.

### 5.2 Verdict on `safety_gate.evaluate`
`SafetyGateService` is an internal synchronous decision dependency of `ClinicalExecutionGatewayService`.
Exposing `safety_gate.evaluate` as a public dispatcher command violates the single-gateway execution architecture established in M19–M24.

---

## 6. Session Binding & Cross-Session Spoofing

### 6.1 Ingress Vector (External Client via GatewayService)
- Handshake establishes `session.session_id`.
- If an authenticated client on `Session A` sends `safety_gate.evaluate` targeting `Session B`:
  `GatewayAuthorizationPolicy.authorize_message` enforces `payload.session_id == session.session_id`.
  The cross-session request is **REJECTED** with `GatewaySessionMismatchError`.

### 6.2 Internal Bus Vector (In-Process / Inter-Service)
- Any in-process component or compromised service on the dispatcher can publish `safety_gate.evaluate` with `session_id = "Session-B"`.
- Because the route is an ungated COMMAND without capability or caller validation:
  1. An audit record is written for Session B in `PersistenceService`.
  2. `_latest_decisions["Session-B"]` is mutated to the result of that evaluation.
  3. `MAX_ACTIVE_GATE_SESSIONS` capacity is consumed.
- **Verdict**: Cross-session state pollution and audit falsification is possible over the internal bus.

---

## 7. Capability Analysis

- `safety_gate.evaluate` requires **zero capabilities**.
- `_create_execution_capability` is never invoked.
- `SafetyGateService` has no capability validation in `handle_evaluate_command` or `evaluate()`.
- Only `evict_session(session_id, capability)` validates capabilities (verifying `SESSION_TEARDOWN`).

---

## 8. Safety State Mutation Breakdown

| State Structure | Owner | Mutation Type | Lifetime | Session Scoped | Persistence Impact | Eviction Behavior | Downstream Consumers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `_latest_decisions` | `SafetyGateService` | Key insert / overwrite | Runtime in-memory | Yes (`dict[session_id, GateStatusRecord]`) | None directly | Cleared by Step 7 of teardown | `get_gate_status()`, `safety_gate.status.get` |
| `_persisted_states` | `SafetyGateService` | Key insert / overwrite | Runtime in-memory | Yes (`dict[session_id, tuple[decision, reason]]`) | None directly | Cleared by Step 7 of teardown | Deduplication gate for `record_audit` |
| `_audit_store` | `PersistenceService`| Append record | **Durable filesystem** | Yes (`session_id` tag) | **Permanent append** | Survives session teardown | Clinical audit trail, compliance |
| Event bus (`safety_gate.evaluated`) | `MessageDispatcher` | Dispatch event | Ephemeral | Yes (`session_id` payload) | None | N/A | Any event subscribers |

**Verdict**: `evaluate()` is **STATE-MUTATING**, not pure.

---

## 9. Route Naming / Protocol Design

Both issues are real, distinct architectural bugs:

1. **Bug A (Topic Naming Invariant Defect)**:
   - Topic strings `"safety_gate.evaluate"` and `"safety_gate.status.get"` contain underscores (`_`).
   - M00.4 Subscription Registry regex: `^[a-z0-9]+(\.[a-z0-9]+)*$`.
   - All other 74 routes across all subsystems adhere to this invariant.
   - Result: Fatal crash on initialization.

2. **Bug B (Architectural Route Exposure Defect)**:
   - `safety_gate.evaluate` is registered as a COMMAND route on the public dispatcher.
   - Clinical execution architecture requires all execution actions to route through `ClinicalExecutionGatewayService`.
   - `SafetyGateService.evaluate` is an in-process evaluation gate, not a dispatcher command.
   - Result: Architectural bypass, capacity leak, and audit pollution surface.

---

## 10. Minimum Fix Design for Route Naming

### Comparison of Options
- **Option A: Rename route to `safety.gate.evaluate`**
  - Fixes regex validation, but preserves Bug B (public exposure of raw evaluation command).
- **Option B: Relax topic validator in `core/subscription.py` to allow underscores**
  - **UNACCEPTABLE**: Violates frozen M00.4 core dispatcher protocol; reopens core infrastructure for a domain subsystem defect.
- **Option C: Introduce alias**
  - Unnecessary complexity; zero external clients depend on the failing route.
- **Option D (RECOMMENDED): Remove `safety_gate.evaluate` command registration entirely; rename query route to `safety.status.get`**
  - Eliminates Bug B by removing the raw command route (matching M21–M24).
  - Eliminates Bug A by making the remaining status query topic compliant: `safety.status.get`.
  - Also updates the emitted event name from `safety_gate.evaluated` to `safety.evaluated` (or `safetygate.evaluated`).

---

## 11. Route Authority Fix Design

**Recommended Architecture**:
1. **Deregister Command Route**: In `SafetyGateService.initialize()`, do not register `safety_gate.evaluate` (or any command handler) with `_dispatcher`.
2. **Preserve Synchronous In-Process API**: `SafetyGateService.evaluate(request: GateRequest) -> GateStatusRecord` remains public and unchanged for `ClinicalExecutionGatewayService`.
3. **Register Canonical Query Route**: Register `safety.status.get` (or `safetygate.status.get`) as the sole read-only query route on the dispatcher.
4. **Fix Event Topic**: Update `_emit_event` to use compliant concrete topic `safety.evaluated`.

---

## 12. Historical Context & Test Masking

- **100% of Existing Tests Masked the Defect**:
  - `test_m25_session_teardown.py:66`: `safety_gate = SafetyGateService(dispatcher=None, ...)`
  - `test_m26_perceptual_lifecycle.py:130`: `safety_gate = SafetyGateService(dispatcher=None, ...)`
  - `test_m27_workflow_interlock_lifecycle.py:78`: `safety_gate = SafetyGateService(dispatcher=None, ...)`
  - `test_m29_tool_lifecycle.py:146`: `safety_gate = SafetyGateService(dispatcher=None, ...)`
  - `test_gate_service.py`: `dispatcher = MagicMock(spec=MessageDispatcher)`
- Because `dispatcher=None` or `MagicMock` was passed in every test, `MessageDispatcher.register_command_handler` and `SubscriptionRegistry.register_command` were **never once called** with `SafetyGateService` under test.
- This is a documented test-coverage deficiency where test fixtures masked a fatal production initialization crash.

---

## 13. Other Dispatcher Topic Violations

A forensic scan of all 76 registered dispatcher routes in the entire repository yielded:
- **Total Registered Routes**: 76
- **Grammar Compliant Routes**: 74
- **Violating Routes**: Exactly **2**
  1. `safety_gate.evaluate` (`python/holomed/safety_gate/service.py:135`)
  2. `safety_gate.status.get` (`python/holomed/safety_gate/service.py:136`)

**Zero other services contain topic grammar violations.**  
Scope is strictly confined to `SafetyGateService`.

---

## 14. Failure Semantics

| Failure Condition | Expected Behavior | Safety Impact |
| :--- | :--- | :--- |
| Dispatcher Initialization with `SafetyGateService` | Must complete cleanly without `TopicValidationError` | **Nominal startup restored** |
| Missing `session_id` in `safety.status.get` | Returns `ERR_INVALID_ARGS` error envelope | Fail-closed |
| Unknown `session_id` in `safety.status.get` | Returns `status = "UNINITIALIZED"` or `decision = None` | Informational read-only |
| Direct Dispatch of `safety_gate.evaluate` | Returns `ERR_NO_HANDLER` (unroutable message) | **Bypass eliminated** |
| Evaluator Exception in Execution Gateway | Transaction aborts with `FAILED_NAVIGATION_GEOMETRY` | Fail-closed |

---

## 15. Minimum Reopen Set

### Production Files
1. `python/holomed/safety_gate/service.py`:
   - Remove `register_command_handler("safety_gate.evaluate", ...)`
   - Change query route to `register_query_handler("safety.status.get", ...)`
   - Change emitted event to `"safety.evaluated"`
2. `python/holomed/safety_gate/constants.py` (Optional):
   - Define `TOPIC_SAFETY_STATUS_GET = "safety.status.get"`
   - Define `TOPIC_SAFETY_EVALUATED = "safety.evaluated"`

### Test Files
3. `tests/unit/safety_gate/test_gate_service.py`:
   - Update query test from `safety_gate.status.get` to `safety.status.get`
   - Remove direct dispatcher command dispatch assertion
4. `tests/unit/safety_gate/test_m30_safety_gate_dispatcher.py` (NEW):
   - Test clean initialization of `SafetyGateService` with real production `MessageDispatcher`
   - Test route dispatch of `safety.status.get` over real dispatcher
   - Test unroutable rejection of legacy `safety_gate.evaluate`
   - Test event publication of `safety.evaluated` over real dispatcher
   - Test integration with `ClinicalExecutionGatewayService` using real dispatcher throughout

### Strictly Excluded Files
- `python/holomed/core/*`: **FROZEN** (do not touch dispatcher or subscription logic).
- `python/holomed/execution/*`: **FROZEN** (execution gateway remains untouched).
- `python/holomed/safety_gate/evaluator.py`: **FROZEN** (decision logic untouched).

---

## 16. Hostile Candidate Challenge

| Challenge Question | Forensic Defense |
| :--- | :--- |
| **Is this only a test-fixture issue?** | **NO**. Production code at `service.py:135` attempts to register an illegal topic. The bug is in production code; tests merely avoided triggering it. |
| **Does the real application initialize differently?** | **NO**. Any runtime orchestrator that wires services to `MessageDispatcher` hits `TopicValidationError`. |
| **Is `safety_gate.evaluate` pure?** | **NO**. It writes persistent audit records, mutates caches, publishes events, and consumes session capacity. |
| **Is the underscore naming merely cosmetic?** | **NO**. It is a fatal runtime exception preventing service startup. |
| **Could relaxing core dispatcher grammar solve this?** | **REJECTED**. Weakening the central bus protocol regex breaks foundational M00.4 architecture. The fix belongs in the offending service. |
| **Does M30 duplicate earlier milestones?** | **NO**. M21–M24 hardened Tools, Recovery, Registration, and Planning. M25–M29 hardened teardown eviction. M30 completes the command-deregistration pattern for Safety Gate and repairs real dispatcher integration. |

---

## 17. Frozen Boundaries

The following boundaries remain **100% FROZEN**:
- `holomed.core.subscription._CONCRETE_TOPIC_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9]+)*$")`
- `SafetyGateEvaluator` logic, mathematical tolerance checks, and 7-tier precedence rules
- `ClinicalExecutionGatewayService` execution sequence and `execute_session_teardown` Steps 1–12
- M28 Ingress Gateway session binding and connection lifecycle contracts
- All domain subsystems (`navigation`, `planning`, `registration`, `recovery`, `proximity`, `drift`, `tools`, `workflow`, `platform`)

---

## 18. M30 Pre-Lock Contract Draft

### Title
M30 — Safety Gate Dispatcher Contract & Route Hardening

### Problem Statement
`SafetyGateService` attempts to register `safety_gate.evaluate` (COMMAND) and `safety_gate.status.get` (QUERY) on `MessageDispatcher`. The underscore character violates the canonical topic grammar `^[a-z0-9]+(\.[a-z0-9]+)*$`, causing a fatal `TopicValidationError` on service initialization. Furthermore, exposing `safety_gate.evaluate` as a raw dispatcher command creates an unmediated, capability-free audit-polluting bypass of `ClinicalExecutionGatewayService`.

### Authorized Scope
- Production:
  - `python/holomed/safety_gate/service.py`
  - `python/holomed/safety_gate/constants.py` (if constants defined)
- Tests:
  - `tests/unit/safety_gate/test_gate_service.py`
  - `tests/unit/safety_gate/test_m30_safety_gate_dispatcher.py` (new)
- Audit / Documentation:
  - `PHASE_30_CONTRACT.md`
  - `M30_IMPLEMENTATION_REPORT.md`
  - `M30_FINAL_PRECOMMIT_AUDIT.md`

### Acceptance Criteria
1. `SafetyGateService` initializes cleanly when provided a real production `MessageDispatcher` (`disp.initialize(ctx) -> sg.initialize(ctx)` exits 0 with zero exceptions).
2. Raw command `safety_gate.evaluate` is removed from dispatcher registration; dispatching it returns `ERR_NO_HANDLER`.
3. Query route `safety.status.get` is registered and callable over the real `MessageDispatcher`, returning canonical status payload.
4. Event `safety.evaluated` is emitted with a grammar-compliant topic name over the message bus.
5. All clinical execution paths in `ClinicalExecutionGatewayService` continue to call `self._safety_gate_service.evaluate(...)` in-process with zero regressions.
6. All 1,609 regression tests and all new M30 tests pass with zero failures.

---

## 19. Final Classification

**`READY_FOR_LOCK`**
