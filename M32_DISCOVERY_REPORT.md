# M32 DISCOVERY REPORT: FORENSIC ARCHITECTURAL & SECURITY AUDIT

## Milestone Metadata
- **Discovery Milestone**: M32 — Subsystem Cross-Session State Isolation & Clinical Query Lifecycle Hardening
- **Authoritative Baseline**: `daf8324453378bb1f45e84de26e09479c8ad75ff` (M31 Release)
- **Previous Milestones**: M19–M31 = **FROZEN**
- **Date/Time**: 2026-09-04T06:58:00+05:30
- **Classification**: `M32_JUSTIFIED`

---

## 1. Repository State Verification

```
$ git rev-parse HEAD
daf8324453378bb1f45e84de26e09479c8ad75ff

$ git rev-parse origin/main
daf8324453378bb1f45e84de26e09479c8ad75ff

$ git status --short
?? M31_RELEASE_REPORT.md
```
- `HEAD == origin/main` at commit `daf8324453378bb1f45e84de26e09479c8ad75ff`.
- Working tree contains 0 modified tracked files.

---

## 2. Review of M31 Security Boundary & Residual Exposure

### What M31 Guaranteed:
1. **Gateway Disconnect Hardening**: Prohibited cross-session disconnect attacks, enforced role hierarchy, enabled self-disconnect, and eliminated state mutation upon authorization failure.
2. **Client Enumeration Scoping**: Scoped `gateway.clients` query strictly to the caller's session ID, concealing cross-session metadata.
3. **Tools Reset De-registration**: Completely removed `tools.reset` from external dispatcher registration.
4. **Centralized Default-Deny Allowlist**: Established `CLIENT_ISSUABLE_ROUTES` (59 routes) in `GatewayAuthorizationPolicy`, blocking all internal administrative and reset commands (`platform.reset`, `platform.cycle`, `ultron.reset`, `anatomy.reset`, `audio.pipeline.reset`, `gesture.pipeline.reset`, `vision.pipeline.reset`, `xr.reset`).
5. **M28 Boundary Preservation**: Retained envelope session validation and prevented payload session spoofing.

### Residual Attack Surface Exposed Post-M31:
While M31 successfully sealed the outer perimeter of the Gateway and hardened connection-level commands, it relied on backend services to properly honor session scoping for their queries and commands. Our system-wide forensic audit reveals critical vulnerabilities where:
- Subsystem queries do not validate the caller's session against the requested object ID (`tools.result`, `planning.get`).
- Subsystems accumulate global memory buffers that are never cleared when a session is torn down (`ToolExecutionEngine._result_history`, `PlanningService._plans`).
- A phantom route exists in `CLIENT_ISSUABLE_ROUTES` with no backend handler, causing unroutable dispatcher exceptions that crash the gateway connection worker (`workflow.interlock.trip`).
- Subsystem coordination in `ClinicalExecutionGatewayService` contains broken method bindings (`reset_recovery` vs `reset_session`).
- File-system access in `PersistenceService.handle_cycle_get_query` bypasses canonical session path sanitization.

---

## 3. System-Wide Architecture Inventory

| Subsystem | Primary Role | Ingress Routes (Client-Issuable) | State Scope | Lifecycle Owner | Reset/Teardown Behavior |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Gateway** | Client connection & ingress authorization | `gateway.status`, `gateway.clients`, `gateway.disconnect` | Session-scoped connections & global metrics | GatewayService | `evict_session`: closes connections; M31 isolates disconnect |
| **Execution** | Universal clinical execution coordinator | `execution.navigation.execute`, `execution.planning.execute`, `execution.recovery.execute`, `execution.registration.execute`, `execution.session.teardown`, `execution.status.get`, `execution.tool.invoke`, `execution.trajectory.bind`, `execution.workflow.resume` | Session-scoped results & deduplication cache | ClinicalExecutionGatewayService | `execute_session_teardown`: orchestrates 12-step cascade |
| **Workflow** | Phase transitions & safety interlocks | `workflow.start`, `workflow.transition`, `workflow.confirm`, `workflow.abort`, `workflow.interlock.trip` [PHANTOM], `workflow.status` | Session-scoped state machines & interlocks | WorkflowService | `evict_session`: purges session state machines & interlocks |
| **Tools** | Surgical tool catalog & deterministic execution | `tools.status`, `tools.registry`, `tools.result` | Monotonic sequences (session-scoped); Result history (GLOBAL) | ToolService | `evict_session`: purges sequence counter; **FAILS** to purge result history |
| **Planning** | Preoperative surgical plan management | `planning.get` | Plan bindings (session-scoped); Plan definitions (GLOBAL) | PlanningService | `evict_session`: purges bindings; **FAILS** to purge plan definitions |
| **Registration**| Patient-to-plan point-cloud registration | `registration.get` | Session-scoped registrations & clouds | RegistrationService | `evict_session`: purges registrations & clouds |
| **Navigation** | Real-time tool guidance & trajectory monitoring | `navigation.status.get` | Session-scoped poses, sequences, deviations | NavigationService | `evict_session`: purges poses & deviations |
| **Safety Gate** | Inline safety evaluation & interlock gating | `safety.status.get` | Session-scoped evaluation decisions | SafetyGateService | `evict_session`: purges cached decisions |
| **Drift** | Anatomical landmark displacement tracking | `drift.status.get`, `drift.landmarks.get` | Session-scoped landmarks & observations | DriftService | `evict_session`: purges landmarks & drift state |
| **Proximity** | Boundary zone proximity evaluation | `proximity.status.get`, `proximity.zones.get` | Session-scoped exclusion zones & breaches | ProximityService | `evict_session`: purges zones & breach states |
| **Recovery** | Registration loss & clinical recovery | `recovery.status.get` | Session-scoped recovery candidates & states | RecoveryService | `evict_session`: purges recovery records |
| **Persistence** | Durable append-only journal & audit store | `persistence.status`, `persistence.session.get`, `persistence.cycle.get` | Session-scoped jsonl journals on disk | PersistenceService | Session close flushes file; clear() purges memory store |
| **Devices** | Telemetry accumulation & cross-plane bridging | `device.coordination.health`, `device.orchestration.status`, `device.orchestration.audit` | Global hardware bus (session-agnostic) | Coordinator / Orchestrator | Hardware lifecycle |
| **Perception** | Vision, Gesture, Audio, XR, Anatomy, ULTRON | `vision.*`, `gesture.*`, `audio.*`, `xr.*`, `anatomy.*`, `ultron.*` | Global sensory pipelines (session-agnostic) | Respective Services | Pipeline reset (internal only) |

---

## 4. Search for Remaining High-Risk Patterns

1. **Global Unscoped Data Structures**:
   - `ToolExecutionEngine._result_history: list[ToolResult]` (bounded ring buffer, but flat across all sessions; entries do not store `session_id`).
   - `PlanningService._plans: dict[str, SurgicalPlanDefinition]` (keyed only by `plan_id`; never pruned by `evict_session`).
2. **Missing Session Correlation in Handlers**:
   - `ToolService.handle_result_query()`: checks only `invocation_id == res.invocation_id`.
   - `PlanningService.handle_get_query()`: accepts `plan_id` without verifying that the caller's session owns the plan.
   - `PersistenceService.handle_cycle_get_query()`: constructs `journal_path = self._storage_root / f"{session_id}.jsonl"` without validating `session_id` with `validate_session_path()`.
3. **Dispatcher Route / Allowlist Asymmetry**:
   - `workflow.interlock.trip` is present in `CLIENT_ISSUABLE_ROUTES` in `python/holomed/gateway/authorization.py`, but `WorkflowService` registers zero handlers for `workflow.interlock.trip` (interlocks are triggered via internal evaluation, and the topic is an event `workflow.interlock.tripped`).
4. **Mismatched Service API Calls**:
   - In `ClinicalExecutionGatewayService.execute_recovery_reorientation()` (line 866), `recovery_operation == "RESET"` calls `self._recovery_service.reset_recovery(session_id)`. `RecoveryService` has no such method; its method is `reset_session(session_id)`.

---

## 5. Cross-Session Isolation Audit

### Finding CS-1: Cross-Session Tool Result Leakage (`tools.result`)
- **Vulnerability**: `ToolResult` is defined in `python/holomed/tools/models.py` without a `session_id` field. When `ToolExecutionEngine.execute_invocation()` records a result into `self._result_history`, the result is disassociated from its originating session.
- **Exploitation**: Client `client_b` in `session_B` submits `tools.result` query with `invocation_id` matching an invocation executed in `session_A`.
- **Result**: `ToolService.handle_result_query()` locates the entry in `self._engine.result_history` and returns the clinical tool result payload, confidence score, uncertainty metric, and diagnostic message to `session_B`.
- **Verification Proof**:
  ```python
  # Session A executed tool with confidential biopsy data
  res_a = engine.execute_invocation(ctx_a, registry)
  # Session B queries tools.result
  q_b = create_query("tools.result", "client_b", payload={"invocation_id": "inv_session_a_123"})
  resp_b = tool_svc.handle_result_query(q_b)
  assert resp_b.payload["result_payload"]["secret_biopsy_data"] == "PATIENT_A_TUMOR_POSITIVE"  # LEAK CONFIRMED!
  ```

### Finding CS-2: Cross-Session Surgical Plan Metadata Disclosure (`planning.get`)
- **Vulnerability**: `PlanningService.handle_get_query()` inspects `query_envelope.payload.get("plan_id")`. If `plan_id` is present, it directly retrieves `p = self._plans[plan_id]` without checking `self._session_plan_bindings.get(session_id)`.
- **Exploitation**: Client `client_b` on `session_B` submits `planning.get` with `payload={"plan_id": "plan_a_sensitive"}`.
- **Result**: `PlanningService` discloses `case_id`, `laterality`, `trajectories_count`, and `exclusion_zones_count` belonging to `session_A`.
- **Verification Proof**:
  ```python
  plan_svc.submit_plan(p_a, "session_A", PlanCap(), 1)
  q_plan_b = create_query("planning.get", "client_b", payload={"plan_id": "plan_a_sensitive"})
  resp_plan_b = plan_svc.handle_get_query(q_plan_b)
  assert resp_plan_b.payload["case_id"] == "case_patient_A"  # LEAK CONFIRMED!
  ```

---

## 6. State-Lifecycle Audit

### Finding LC-1: Unevicted Tool Execution Result History Post-Teardown
- **Vulnerability**: When a session undergoes teardown (`execution.session.teardown`), `ClinicalExecutionGatewayService` calls `self._tool_service.evict_session(session_id)`. In `ToolExecutionEngine.evict_session(session_id)`, only `self._session_sequences[session_id]` is deleted.
- **Result**: All results generated by `session_A` remain permanently in `self._result_history`. Even after `session_A` is completely evicted, disconnected, and logged out, any client can continue querying its clinical tool results via `tools.result`.

### Finding LC-2: Memory Leak & Plan Capacity Denial of Service (`PlanningService._plans`)
- **Vulnerability**: When `PlanningService.evict_session(session_id)` is invoked, it executes:
  ```python
  if session_id in self._session_plan_bindings:
      del self._session_plan_bindings[session_id]
  if session_id in self._verification_records:
      del self._verification_records[session_id]
  ```
  It **never** removes the plan from `self._plans[plan_id]`.
- **Result**: `self._plans` retains plan definitions indefinitely. `PlanningService` enforces `MAX_ACTIVE_PLANS = 16`. Once 16 plans have been registered across successive sessions over the lifetime of the service, `submit_plan()` permanently throws:
  `PlanningCapacityError: Maximum active plans capacity (16) reached`
  This causes a permanent denial of service for all subsequent clinical procedures until the entire service is restarted.

---

## 7. Dispatcher Contract & Route Integrity Audit

### Finding DC-1: Unroutable Ingress Route (`workflow.interlock.trip`)
- **Vulnerability**: In `python/holomed/gateway/authorization.py`, line 43:
  `CLIENT_ISSUABLE_ROUTES` includes `"workflow.interlock.trip"`.
  However, `WorkflowService.initialize()` registers only:
  - `workflow.status`
  - `workflow.start`
  - `workflow.transition`
  - `workflow.confirm`
  - `workflow.abort`
  No handler is ever registered for `"workflow.interlock.trip"`.
- **Impact**: When a client sends a `workflow.interlock.trip` command:
  1. `GatewayAuthorizationPolicy.authorize_message()` passes the message because it is on the allowlist.
  2. `MessageDispatcher.dispatch(envelope)` raises `UnroutableMessageError("No command handler registered for topic 'workflow.interlock.trip'")`.
  3. `GatewayService._handle_client_message()` has no exception handler around `dispatch()`, causing an unhandled exception that crashes the client processing pipeline.
- **Verification Proof**:
  ```python
  cmd = create_command("workflow.interlock.trip", "client_1", payload={})
  # Raises UnroutableMessageError!
  ```

---

## 8. Coordination & Method Contract Audit

### Finding MC-1: Method Name Mismatch on Recovery Operation RESET
- **Vulnerability**: In `ClinicalExecutionGatewayService.execute_recovery_reorientation()`:
  ```python
  elif op == "RESET":
      self._recovery_service.reset_recovery(session_id)
  ```
  `RecoveryService` defines `reset_session(self, session_id: str) -> None` (line 657), and does **not** define `reset_recovery()`.
- **Impact**: Any authorized client or internal coordinator requesting recovery reset triggers an unhandled `AttributeError: 'RecoveryService' object has no attribute 'reset_recovery'`.
- **Verification Proof**: Verified with `verify_m32_candidates.py` producing `AttributeError`.

---

## 9. Failure & Security Semantics Audit

### Finding FS-1: Path Traversal Vulnerability in `PersistenceService.handle_cycle_get_query`
- **Vulnerability**: In `python/holomed/persistence/service.py` (line 511):
  ```python
  def handle_cycle_get_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
      session_id = query_envelope.payload.get("session_id")
      sequence = query_envelope.payload.get("sequence_number")
      ...
      journal_path = self._storage_root / f"{session_id}.jsonl"
  ```
  While `PersistenceService.create_session()` and `JournalWriter` strictly sanitize `session_id` using `validate_session_path()`, `handle_cycle_get_query()` directly constructs `self._storage_root / f"{session_id}.jsonl"`.
- **Impact**: A manipulated `session_id` with traversal components (e.g., `../../target`) causes path resolution outside `storage_root`.

---

## 10. Concurrency & Reentrancy Review

- Reentrancy locks (`_in_transaction`) in `ClinicalExecutionGatewayService`, `WorkflowService`, `ToolService`, `PlanningService`, and `RecoveryService` function correctly.
- Synchronous dispatcher message handling prevents interleaving of state changes during individual transaction executions.

---

## 11. Safety-Critical Paths Review

- The safety gate evaluation pipeline (`SafetyGateService.evaluate()`) remains fully mediated and hardened by M30.
- `ClinicalExecutionGatewayService` correctly evaluates M18 safety gates prior to tool execution, navigation execution, and recovery operations.
- However, the inability to reset recovery state due to the `reset_recovery` bug (MC-1) impairs the clinical recovery lifecycle during an actual intraoperative navigation failure.

---

## 12. Information Leakage Assessment

- `gateway.clients`: Scoped in M31 (no leakage).
- `gateway.disconnect`: Scoped in M31 (no cross-session target mutation).
- `tools.result`: **LEAKS** cross-session execution outputs and patient data (CS-1).
- `planning.get`: **LEAKS** surgical plans and anatomical exclusion zones across sessions (CS-2).

---

## 13. M32 Candidate Ranking

| Candidate | Title | Location | Severity | Clinical / Security Impact |
| :---: | :--- | :--- | :---: | :--- |
| **C1** | **`tools.result` Cross-Session Disclosure & Retention Leak** | `holomed/tools/service.py`<br>`holomed/tools/engine.py`<br>`holomed/tools/models.py` | **CRITICAL** | Discloses confidential patient tool outputs across sessions; fails to evict results on teardown. |
| **C2** | **`planning.get` Cross-Session Plan Disclosure & DOS Capacity Leak** | `holomed/planning/service.py` | **HIGH** | Discloses preoperative plans across sessions; unevicted plans permanently exhaust plan capacity (DOS). |
| **C3** | **`workflow.interlock.trip` Phantom Allowlist Ingress Route** | `holomed/gateway/authorization.py` | **HIGH** | Exposed allowlist route raises `UnroutableMessageError` in dispatcher, crashing gateway connection handling. |
| **C4** | **`execution.recovery.execute` Crash on Operation RESET** | `holomed/execution/service.py` | **MEDIUM** | Calling recovery reset fails with `AttributeError` due to missing `reset_recovery` method on `RecoveryService`. |
| **C5** | **`persistence.cycle.get` Unvalidated Storage Path Construction** | `holomed/persistence/service.py` | **MEDIUM** | Bypasses canonical path sanitization `validate_session_path()`, permitting path traversal risk. |

---

## 14. Scope Discipline & Recommendation

Milestone M32 is **fully justified**. The discovered defects directly breach cross-session boundaries established in M28–M31, leak protected clinical patient data, cause denial of service through capacity exhaustion, and crash gateway connection threads.

### Minimum Reopen Set for M32:
1. `python/holomed/tools/models.py` (add `session_id: str` to `ToolResult`)
2. `python/holomed/tools/engine.py` (session-scope `_result_history` or record `session_id`; prune on `evict_session`)
3. `python/holomed/tools/service.py` (enforce `session_id` validation in `handle_result_query`)
4. `python/holomed/planning/service.py` (enforce session binding check in `handle_get_query`; prune `_plans` in `evict_session`)
5. `python/holomed/gateway/authorization.py` (remove phantom `"workflow.interlock.trip"` from `CLIENT_ISSUABLE_ROUTES`)
6. `python/holomed/execution/service.py` (fix `self._recovery_service.reset_session(session_id)`)
7. `python/holomed/persistence/service.py` (use `validate_session_path` in `handle_cycle_get_query`)

---

======================================================================
FINAL CLASSIFICATION: M32_JUSTIFIED
======================================================================
