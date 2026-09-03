# PHASE 30 CONTRACT: SAFETY GATE DISPATCHER CONTRACT & EXECUTION BOUNDARY HARDENING

**Authoritative Baseline**: `8c46aa2ad883aca2089da98db13cc2d5ef0b1dcb`  
**Milestone**: M30 — Safety Gate Dispatcher Contract & Execution Boundary Hardening  
**Status**: DRAFT CONTRACT (Awaiting Implementation Authorization)  
**Predecessor**: M29 (Frozen)  

---

## 1. PRIMARY OBJECTIVES

M30 addresses two coupled defects in the safety gate subsystem:

1. **Dispatcher Protocol Non-Compliance (Startup Blocker)**:
   `SafetyGateService` attempts to register concrete topics containing underscores (`"safety_gate.evaluate"` and `"safety_gate.status.get"`), directly violating the canonical M00.4 message bus grammar `^[a-z0-9]+(\.[a-z0-9]+)*$`. Under real runtime wiring, `SafetyGateService.initialize()` crashes unconditionally with `TopicValidationError`. M30 repairs topic grammar compliance, guaranteeing clean initialization with the production `MessageDispatcher`.

2. **Unmediated Mutating Command Ingress Bypass**:
   `SafetyGateService` exposes `safety_gate.evaluate` as a raw, capability-free COMMAND on the dispatcher. Unlike queries, `evaluate()` is state-mutating: it alters `_latest_decisions`, writes persistent audit records to `PersistenceService`, publishes bus events, and consumes slots against `MAX_ACTIVE_GATE_SESSIONS = 16`. Exposing this route allows unmediated callers to mutate session decision state outside the established `ClinicalExecutionGatewayService` orchestration boundary. M30 removes this raw dispatcher command registration, bringing the Safety Gate into strict architectural alignment with M21–M24 (Tools, Recovery, Registration, Planning).

M30 does **NOT** alter safety decision mathematics, 7-tier precedence rules, or execution gateway logic.

---

## 2. AUTHORIZED REOPEN SET

The source code modifications for M30 are strictly confined to the following files:

### Production Files (Maximum 2)
1. `python/holomed/safety_gate/service.py`
2. `python/holomed/safety_gate/constants.py` (optional; for canonical topic definitions)

### Test Surface
3. `tests/unit/safety_gate/test_gate_service.py` (update query route test)
4. `tests/unit/safety_gate/test_m30_safety_gate_dispatcher.py` (new comprehensive M30 verification suite)

### Documentation & Contract Artifacts
5. `PHASE_30_CONTRACT.md`
6. `M30_IMPLEMENTATION_REPORT.md`
7. `M30_HOSTILE_AUDIT_REPORT.md`
8. `M30_FINAL_PRECOMMIT_AUDIT.md`

**STRICTLY FORBIDDEN / FROZEN**:
- `python/holomed/core/*` (MessageDispatcher and SubscriptionRegistry grammar remain 100% frozen).
- `python/holomed/execution/*` (ClinicalExecutionGatewayService remains 100% frozen).
- `python/holomed/safety_gate/evaluator.py` (SafetyGateEvaluator decision logic remains 100% frozen).
- Subsystems: Platform, Workflow, Planning, Registration, Navigation, Recovery, Proximity, Drift, Tools, Gateway, Persistence.

---

## 3. TOPIC GRAMMAR & CANONICAL TOPIC SELECTION

The global dispatcher topic grammar remains strictly immutable:
$$\text{Concrete Topic Regex: } \wedge[a-z0-9]+(\.[a-z0-9]+)*\$$$

Underscores (`_`) remain categorically forbidden on the message bus.

### Topic Remediation Decisions
1. **Deregister Raw Command**:
   - Route `safety_gate.evaluate`: **REMOVED ENTIRELY** from dispatcher registration.
   - No alias is created. Dispatching `safety_gate.evaluate` or `safety.evaluate` over the message bus will return `ERR_NO_HANDLER` (fail-closed).
2. **Rename Status Query**:
   - Legacy Route: `safety_gate.status.get` (invalid grammar).
   - Canonical Route: `safety.status.get` (fully compliant, matching `navigation.status.get`, `proximity.status.get`, `drift.status.get`, `recovery.status.get`, `execution.status.get`).
3. **Rename Emitted Protocol Event**:
   - Legacy Event Topic: `safety_gate.evaluated` (invalid grammar).
   - Canonical Event Topic: `safety.evaluated` (fully compliant).

---

## 4. SAFETY EVALUATION AUTHORITY & EXECUTION BOUNDARY

### Separation of Authority
- **`SafetyGateService`**: The sole authoritative decision engine evaluating cross-subsystem safety state against the 7-tier precedence hierarchy.
- **`ClinicalExecutionGatewayService`**: The sole authoritative orchestration boundary for all clinical operations (Navigation, Tools, Planning, Registration, Recovery, Teardown).
- **`_ExecutionCapability`**: The non-forgeable token verifying that an operation is executing within an authorized gateway transaction.

### Architectural Boundary Enforcement
1. `SafetyGateService.evaluate(request: GateRequest) -> GateStatusRecord` remains a public in-process method.
2. `ClinicalExecutionGatewayService` continues to invoke `self._safety_gate_service.evaluate(gate_req)` directly in Step 1 of all execution operations.
3. No external client, transport, or rogue in-process component may trigger `evaluate()` via the `MessageDispatcher`. The backdoor is sealed.

---

## 5. STATUS QUERY SPECIFICATION (`safety.status.get`)

The query route `safety.status.get` provides read-only observability of the latest cached safety decision for a session:

- **Topic**: `safety.status.get`
- **Type**: `MessageType.QUERY`
- **Handler**: `SafetyGateService.handle_get_status_query`
- **Purity**: Strictly read-only. Calls `self.get_gate_status(session_id)`, returning an immutable snapshot or `status: UNINITIALIZED`. Mutates zero internal state.
- **Payload Contract**:
  - Ingress: `{"session_id": "<session-id>"}`
  - Missing `session_id`: Returns `ERR_INVALID_ARGS` error envelope.
  - Unknown `session_id`: Returns payload with `decision: None`, `status: "UNINITIALIZED"`.
  - Known `session_id`: Returns serialized `GateStatusRecord` fields (`decision`, `severity`, `reason_code`, `action`, `sequence_number`, `evaluated_at_utc`).

---

## 6. SESSION BINDING & INGRESS SECURITY

Public query `safety.status.get` obeys the frozen M28 Ingress Security model:
1. **Source Spoofing**: `GatewayAuthorizationPolicy` enforces `envelope.source == session.client_id`.
2. **Cross-Session Spoofing**: `GatewayAuthorizationPolicy` enforces `envelope.payload["session_id"] == session.session_id`. A client authenticated on Session A cannot query Session B's status through the gateway.
3. **Role Authorization**: All authenticated client roles (`SURGEON_CONSOLE`, `ASSISTANT_PANEL`, `XR_DISPLAY`, `READ_ONLY_OBSERVER`) are permitted to issue `QUERY` messages.

---

## 7. STATE INVENTORY & CAPACITY RETENTION

| State Structure | Scope | Mutated by `evaluate()` | Mutated by `handle_get_status_query()` | Teardown Eviction |
| :--- | :--- | :--- | :--- | :--- |
| `_latest_decisions` | Session-scoped (`dict[str, GateStatusRecord]`) | **YES** (Decision cache update) | **NO** (Read-only `.get()`) | Step 7 of Teardown (`evict_session`) |
| `_persisted_states` | Session-scoped (`dict[str, tuple]`) | **YES** (Deduplication signature) | **NO** (Untouched) | Step 7 of Teardown (`evict_session`) |
| `_audit_store` | Durable filesystem | **YES** (Append via `PersistenceService`) | **NO** (Untouched) | Preserved (Durable history) |
| `_in_transaction` | Service-local | Reentrancy guard | Reentrancy guard | Untouched |

`MAX_ACTIVE_GATE_SESSIONS = 16` remains strictly enforced. Capacity reclamation via `evict_session(session_id)` remains strictly intact (tested in M25).

---

## 8. STARTUP GUARANTEE & TEST COVERAGE MIGRATION

### Production Startup Invariant
The following sequence must complete with zero exceptions:
```python
ctx = RuntimeContext(app_config=cfg, epoch_id=1)
disp = MessageDispatcher()
disp.initialize(ctx)
disp.start()

sg = SafetyGateService(dispatcher=disp, ...)
sg.initialize(ctx)
sg.start()
```

### Elimination of Test Masking
Existing tests used `dispatcher=None` or `MagicMock(spec=MessageDispatcher)`.
M30 mandates dedicated integration tests instantiating real `MessageDispatcher` instances with real topic validation enabled, confirming:
1. Clean startup of `SafetyGateService`.
2. Clean dispatch of `safety.status.get` through `dispatcher.dispatch()`.
3. Dispatching `safety_gate.evaluate` yields `ERR_NO_HANDLER`.
4. Event emission of `safety.evaluated` succeeds without topic validation errors.

---

## 9. REQUIRED TEST MATRIX (15 MANDATORY VERIFICATIONS)

1. **Real Dispatcher Initialization**: `SafetyGateService` initializes cleanly with real production `MessageDispatcher` (zero `TopicValidationError`).
2. **Grammar Compliance**: All topics registered by `SafetyGateService` strictly match `^[a-z0-9]+(\.[a-z0-9]+)*$`.
3. **Legacy Command Deregistration**: `safety_gate.evaluate` is not registered; dispatching returns `ERR_NO_HANDLER`.
4. **Command Ingress Sealing**: No raw safety evaluation command can be dispatched over the message bus to mutate `_latest_decisions`.
5. **Gateway Execution Unbroken**: `ClinicalExecutionGatewayService` navigation, tool, recovery, registration, and planning executions continue to invoke in-process safety evaluation cleanly.
6. **Decision Invariance**: All 7-tier precedence safety decisions (`DENIED_CRITICAL`, `DENIED_INTERLOCKED`, `PERMITTED_WITH_CAUTION`, `PERMITTED_CLEAR`) evaluate identically before and after M30.
7. **Read-Only Status Query**: `safety.status.get` returns accurate cached gate decisions over the real dispatcher without mutating state.
8. **Query Argument Validation**: `safety.status.get` with missing `session_id` returns `ERR_INVALID_ARGS`.
9. **Query Unknown Session**: `safety.status.get` for an uninitialized session returns nominal empty/uninitialized status.
10. **M28 Gateway Cross-Session Protection**: Cross-session targeting in `safety.status.get` through `GatewayService` is rejected with `GatewaySessionMismatchError`.
11. **Event Topic Compliance**: `SafetyGateService` emits `safety.evaluated` over real dispatcher with zero validation errors.
12. **Evaluator Failure Fail-Closed**: Evaluator errors bubble up and fail closed in `ClinicalExecutionGatewayService` transactions.
13. **Persistence Failure Fail-Closed**: Persistence audit failures abort execution transactions cleanly.
14. **Safety Gate Unit Regression**: All tests in `tests/unit/safety_gate/` pass.
15. **Milestone Regression & Platform Integrity**: M25–M29 focused suites and full platform test suite (1,609+ tests) pass with zero failures.

---

## 10. EXACT PLANNED CODE MODIFICATIONS

### In `python/holomed/safety_gate/service.py`:

```diff
@@ -134,2 +134,1 @@
         if self._dispatcher is not None:
-            self._dispatcher.register_command_handler("safety_gate.evaluate", self.handle_evaluate_command, self.name)
-            self._dispatcher.register_query_handler("safety_gate.status.get", self.handle_get_status_query, self.name)
+            self._dispatcher.register_query_handler("safety.status.get", self.handle_get_status_query, self.name)
@@ -271,1 +270,1 @@
-                    "safety_gate.evaluated",
+                    "safety.evaluated",
```

*Note: `handle_evaluate_command` may be retained as a legacy/internal helper or deprecated, but is completely removed from the dispatcher.*

---

## 11. FINAL PRECOMMIT AUDIT CLASSIFICATION STANDARD

Following implementation, the pre-commit audit must achieve:

**`M30_PRECOMMIT_PASS`**

Requirements:
- Zero unauthorized production file modifications.
- Real `MessageDispatcher` integration verified.
- `git diff --check` clean.
- All 1,609+ tests passing with 0 skips and 0 failures.
