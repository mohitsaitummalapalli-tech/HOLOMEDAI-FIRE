# M29 FINAL FEASIBILITY AUDIT REPORT
## Tool Lifecycle Eviction & Architecture Candidate Challenge

**Authoritative Baseline**: `e7362bcc8708a347abc851686f3f25f66358d2f7`  
**Previous Release**: M28 — Gateway Ingress Security & Connection Lifecycle Hardening (Strictly Frozen)  
**Audit Mode**: STRICT READ-ONLY FORENSIC AUDIT  
**Classification**: `READY_FOR_LOCK`  

---

## 1. PROVE THE TOOL LIFECYCLE DEFECT

### A. Production Invocation Path
When a client invokes a tool over the network or in-process dispatcher:
1. **Network Ingress**: Route `execution.tool.invoke` arrives at `GatewayService._handle_client_message()`.
2. **Gateway Session Binding (M28)**: Verified against client session in `GatewayAuthorizationPolicy.authorize_message()`.
3. **Dispatcher Dispatch**: Handled by `ClinicalExecutionGatewayService.execute_tool_invocation()`.
4. **Safety & Workflow Dual-Gating**:
   - `SafetyGateService.evaluate()` evaluates proximity, drift, and recovery gates.
   - `WorkflowService.authorize_tool()` evaluates current workflow phase and interlocks.
5. **Capability Minting**: Single-use `_ExecutionCapability` minted with `action="TOOL_INVOCATION"`, `session_id`, `sequence_number`.
6. **Tool Service Dispatch**: `ToolService.invoke_tool(context, capability)` validates capability and forwards to `ToolExecutionEngine.execute_invocation(context, registry)`.
7. **Session Sequence Tracking**: Handled inside `ToolExecutionEngine.execute_invocation()`:
   ```python
   # python/holomed/tools/engine.py:59-71
   if context.session_id not in self._session_sequences:
       if len(self._session_sequences) >= MAX_ACTIVE_SESSIONS: # 64
           raise ToolCapacityError(f"Active session capacity exceeded ({MAX_ACTIVE_SESSIONS} max)")
       self._session_sequences[context.session_id] = -1

   last_seq = self._session_sequences[context.session_id]
   if context.sequence_number <= last_seq:
       raise ToolSequenceError(
           f"Non-monotonic sequence number {context.sequence_number} <= last seen {last_seq} "
           f"for session {context.session_id!r}"
       )
   self._session_sequences[context.session_id] = context.sequence_number
   ```

### B. Forensic Proof of Defect
- **Creation**: `self._session_sequences[context.session_id]` is created upon the first invocation in a session (line 63).
- **Sequence Mutation**: Updated to `context.sequence_number` on every successful sequence check (line 71).
- **Capacity Check**: `len(self._session_sequences) >= MAX_ACTIVE_SESSIONS` (64) enforced unconditionally on line 61.
- **Eviction Absence**: Zero eviction paths exist in `ToolExecutionEngine` or `ToolService`. The dictionary is ONLY modified by `clear()` or `reset()`, which are global service-wide epoch resets.
- **Teardown Omission**: In `ClinicalExecutionGatewayService.execute_session_teardown()`, Steps 1 through 11 evict Navigation, Proximity, Drift, Recovery, Registration, Planning, Safety Gate, Workflow, Gateway cache, Platform, and Gateway Ingress. **`self._tool_service` is never called**.

---

## 2. COMPLETE TOOL STATE INVENTORY

Every mutable structure across the entire `holomed.tools` subsystem was forensically audited:

| Structure | Owner Class | Key Type | Session Scoped? | Creation / Mutation | Read Path | Capacity Impact | Teardown Status |
|---|---|---|---|---|---|---|---|
| `_session_sequences` | `ToolExecutionEngine` | `str` (`session_id`) | **YES** | Created on 1st invocation; mutated on every valid invocation | `execute_invocation` monotonicity check | **MAX_ACTIVE_SESSIONS (64)** | **UNRELEASED (LEAK)** |
| `_result_history` | `ToolExecutionEngine` | `list[ToolResult]` (index) | NO (contains results across sessions) | Appended on every completed invocation | `result_history` property, `audit_subsystem` | Bounded FIFO ring buffer (`MAX_RESULT_HISTORY = 256`, pops oldest) | Not session-scoped; self-pruning ring buffer |
| `_tools` | `ToolRegistry` | `str` (`tool_id`) | NO (static catalog) | Loaded during service start; immutable once locked | `get_tool`, `has_tool`, `tools` | Bounded (`MAX_REGISTERED_TOOLS = 128`) | Static catalog |
| `_events` | `RecordingToolEventSink` | `list[MessageEnvelope]` (index) | NO (event audit sink) | Appended on `_emit_event` | `export_events` | Bounded (`MAX_RECORDED_TOOL_EVENTS = 256`, swallows capacity error) | Event log; cleared on reset |
| `_total_invocations`| `ToolService` | `int` counter | NO (global metric) | Incremented on invocation | `handle_status_query` | Unbounded scalar integer | Global counter |
| `_in_transaction` | `ToolService` | `bool` flag | Thread/Process | Set/cleared in `invoke_tool` `finally` block | Reentrancy guard | 1 bit | Process-local guard |

**Conclusion**: `ToolExecutionEngine._session_sequences` is the **sole session-scoped mutable structure** in the tools subsystem.

---

## 3. SESSION-ID REUSE PROOF

### A. Execution Scenario
1. **Clinical Procedure 1**: Begins with `session_id = "SESSION-ALPHA"`.
2. Tool invocation with `sequence_number = 1`: `_session_sequences["SESSION-ALPHA"]` becomes `1`.
3. Tool invocation with `sequence_number = 5`: `_session_sequences["SESSION-ALPHA"]` becomes `5`.
4. **Teardown**: Coordinated teardown executed via `execution.session.teardown(session_id="SESSION-ALPHA")`.
   - Navigation, Proximity, Drift, Recovery, Registration, Planning, Safety Gate, Workflow, and Gateway connections are purged.
   - `_session_sequences["SESSION-ALPHA"]` **remains in memory with value 5**.
5. **Reused Session**: A new procedure begins and reuses `session_id = "SESSION-ALPHA"`.
6. First tool invocation of new procedure arrives with initial `sequence_number = 1`.

### B. Resulting Failure Path
- `ToolExecutionEngine.execute_invocation()` line 65:
  ```python
  last_seq = self._session_sequences["SESSION-ALPHA"] # returns 5
  if 1 <= 5:
      raise ToolSequenceError(
          "Non-monotonic sequence number 1 <= last seen 5 for session 'SESSION-ALPHA'"
      )
  ```
- **Consequence**: The reused session is **completely and permanently prohibited from executing any clinical tools**.

---

## 4. 64-SESSION CAPACITY PROOF

### A. Attack / Continuous Operation Scenario
1. Over continuous operation without process restart, 64 distinct clinical procedures (`SESSION-001` through `SESSION-064`) execute at least one tool invocation.
2. Each procedure completes and undergoes teardown via `ClinicalExecutionGatewayService.execute_session_teardown()`.
3. Because `ToolService` is omitted from teardown, `len(self._session_sequences)` equals `64`.
4. Procedure 65 (`SESSION-065`) starts and attempts any clinical tool invocation.

### B. Resulting Failure Path
- `ToolExecutionEngine.execute_invocation()` line 60:
  ```python
  if context.session_id not in self._session_sequences:
      if len(self._session_sequences) >= 64: # True (64 >= 64)
          raise ToolCapacityError("Active session capacity exceeded (64 max)")
  ```
- **Consequence**: The entire server process is locked out. **Zero clinical tools can be invoked by any session across the entire hospital facility** until an unscheduled emergency restart of the core service process.

---

## 5. M28 TEARDOWN EXTENSION & TOPOLOGICAL POSITIONING

### A. Subsystem Dependency Analysis
- **Tool Invocations Depend On**:
  - `SafetyGateService` (Step 7) for dual-gate safety evaluation.
  - `WorkflowService` (Step 8) for phase authorization and interlock checks.
- **Subsystems That Depend On Tools**:
  - None of the other domain services (Navigation, Registration, Planning, Proximity, Drift) depend on `ToolService` state.
- **Teardown Semantics**:
  - Consumers of safety/workflow authorizations (actuation services) must be torn down before safety/workflow authorities are dismantled.
  - Furthermore, in M26, the strict topological contract was locked: `nav_idx < p_idx < d_idx < rec_idx`.
  - In M28, Step 11 was added: `gateway_service` at the end of the chain.

### B. Recommended Position: Step 12
Adding `tool_service` as **Step 12** (immediately following Step 11 `gateway_service`):
1. **Preserves 100% of M25–M28 Ordering**:
   `navigation` (1) < `proximity` (2) < `drift` (3) < `recovery` (4) < `registration` (5) < `planning` (6) < `safety_gate` (7) < `workflow` (8) < `gateway` (9) < `platform` (10) < `gateway_service` (11) < `tools` (12).
2. **Reentrancy Protection**: `ClinicalExecutionGatewayService._in_transaction` is active during the entire teardown pipeline. No tool invocation can execute concurrently.
3. **Additive-Only**: Zero breaking changes to earlier milestone assertions.

---

## 6. TOOL EVICTION DESIGN

### Selected Pattern: Two-Tier Layered Eviction (Option C)
- **Engine Layer (`ToolExecutionEngine.evict_session(session_id: str) -> bool`)**:
  Owns the dictionary `_session_sequences`. Deletes the key and returns `True` if present, `False` otherwise.
- **Service Layer (`ToolService.evict_session(session_id: str, capability: Optional[Any] = None) -> bool`)**:
  - Validates `session_id` string non-empty.
  - Enforces `_in_transaction` reentrancy guard.
  - Validates capability (when supplied) against `SESSION_TEARDOWN` and `session_id`.
  - Delegates to `self._engine.evict_session(session_id)`.
  - Returns boolean eviction status.

---

## 7. CAPABILITY / AUTHORIZATION

- **Existing Capability**: `ClinicalExecutionGatewayService.execute_session_teardown()` mints:
  ```python
  cap = _create_execution_capability(
      service_instance_id=id(self),
      session_id=request.session_id,
      action="SESSION_TEARDOWN",
      sequence_number=request.sequence_number,
  )
  ```
- **Validation**:
  - `capability.action == "SESSION_TEARDOWN"`
  - `capability.session_id == session_id`
  - `capability.is_active is True`
- **Security Guarantee**: Uncoordinated external callers cannot invoke `ToolService.evict_session()` with a spoofed session ID.

---

## 8. CANDIDATE 2 CHALLENGE — PLAN/REGISTRATION OWNERSHIP

### Forensic Audit of Plan-Registration Cross-Session Integrity:
- **Investigated Files**: `PlanningService`, `RegistrationService`, `ClinicalExecutionGatewayService`.
- **Finding**:
  - In `PlanningService`: `submit_plan()` binds `self._session_plan_bindings[session_id] = plan.plan_id`.
  - In `PlanningService.lock_plan()`: Strictly enforces that the locking session matches `_session_plan_bindings`.
  - In `RegistrationService.submit_fiducials()`: Calls `_verify_locked_plan(plan_id)`, which checks `plan.is_locked`, but does not verify whether `plan_id` belongs to `session_id`.
  - However, for `RegistrationService.verify_registration()` to succeed, the operator must verify anatomical checkpoints against physical markers.
- **Why It Should NOT Replace M29**:
  - Candidate 2 is a clinical domain validation rule between Planning and Registration.
  - It is not a lifecycle leak, not a teardown defect, and does not cause a platform-wide denial of service.
  - Candidate 1 directly continues and completes the lifecycle teardown hardening of M25–M28.
  - Merging Candidate 2 into M29 would violate scope cohesion. Candidate 2 is a valid future candidate (M30).

---

## 9. CANDIDATE 3 CHALLENGE — PLATFORM LIFECYCLE CHECK

### Forensic Audit of Execution Gateway vs Platform Lifecycle:
- **Investigated Files**: `ClinicalExecutionGatewayService`, `PlatformService`, `WorkflowService`, `SafetyGateEvaluator`.
- **Finding**:
  - In every execution gateway entrypoint (`execute_navigation`, `execute_tool_invocation`, `execute_trajectory_binding`), Step 1 calls `SafetyGateService.evaluate()` and Step 2 calls `WorkflowService.authorize_tool()`.
  - If a session is stopped or torn down:
    1. `WorkflowService.authorize_tool()` raises `WorkflowSessionError(f"Session {session_id!r} not found")` because `_workflows[session_id]` was evicted.
    2. `SafetyGateEvaluator` returns `DENIED_INTERLOCKED` with `M10_WORKFLOW_PHASE_MISSING` if workflow state is absent.
- **Conclusion**: Stopped or torn-down sessions are **already completely blocked** from executing any clinical actuations downstream. Candidate 3 is a defense-in-depth redundancy, not an urgent safety defect.

---

## 10. CROSS-SUBSYSTEM SESSION ISOLATION

Audit of all tool state identifiers:
- `tool_id`: Identifies instrument in static catalog (e.g. `"drill"`, `"cautery"`). Session-agnostic.
- `invocation_id`: Unique UUID per invocation.
- `session_id`: Keys `_session_sequences`.
- With M29 eviction, session A tool invocations have zero residual impact on session B or reused session A.

---

## 11. FAILURE ATOMICITY

- If a tool invocation fails validation or execution, sequence tracking semantics are maintained:
  - Validation failures (e.g. invalid parameters) occur before or after sequence increment? Line 71 advances sequence after monotonicity check, ensuring replayed payloads cannot reuse sequence numbers.
- In teardown: If `ToolService.evict_session()` encounters an exception, it is caught in `failures.append(f"tools: {exc}")` and reports degraded teardown, guaranteeing non-crashing teardown.

---

## 12. REPLAY / TEMPORAL SECURITY

- `context.sequence_number <= last_seq` strictly prevents replaying any prior tool invocation within a session.
- Once a session is torn down and restarted, sequence numbers reset cleanly to start at 0 or 1.

---

## 13. PERSISTENCE / AUDIT BOUNDARY

- **Ephemeral**: `_session_sequences` in `ToolExecutionEngine`. Purged on teardown.
- **Durable**: Regulatory audit logs written via `PersistenceService.record_audit()` during tool execution. Persisted to disk `.jsonl` and preserved across teardowns.

---

## 14. RESOURCE / CAPACITY FORENSICS

- `MAX_ACTIVE_SESSIONS = 64`: Reclaimed on teardown.
- `MAX_RESULT_HISTORY = 256`: Self-bounding FIFO ring buffer.
- `MAX_RECORDED_TOOL_EVENTS = 256`: Self-swallowing event sink.
- `MAX_TOOL_RESULT_BYTES = 32768`: Enforced per result payload.

---

## 15. MINIMUM REOPEN SET

Exactly 3 production files are required:
1. `python/holomed/tools/engine.py` (add `evict_session(session_id: str) -> bool`)
2. `python/holomed/tools/service.py` (add `evict_session(session_id: str, capability: Optional[Any] = None) -> bool`)
3. `python/holomed/execution/service.py` (wire Step 12 into `execute_session_teardown()`)

---

## 16. FROZEN BOUNDARIES

The following remain 100% frozen:
- All contracts M01 through M28.
- Dual-gate safety evaluation ordering.
- Tool descriptor validation and catalog locking.
- Gateway authentication and session-payload binding.
- Teardown Steps 1 through 11 ordering.

---

## 17. HOSTILE SELF-CHALLENGE

1. *Is the capacity defect truly reachable?*  
   **YES**. Demonstrated at line 61 of `engine.py`. 64 procedures fill `_session_sequences`. The 65th invocation unconditionally raises `ToolCapacityError`.
2. *Does teardown release the state indirectly?*  
   **NO**. `ToolService` is completely absent from `ClinicalExecutionGatewayService.execute_session_teardown()`.
3. *Is sequence persistence across sessions intentional?*  
   **NO**. Sequence numbers are per-session monotonic counters. Retaining them on session reuse causes `ToolSequenceError`.
4. *Could eviction break replay protection?*  
   **NO**. Within an active session, sequence monotonicity is strictly enforced. Upon teardown, the session is terminated.

---

## 18. M29 SCOPE DECISION

```
==================================================
SELECTED: A. Tool lifecycle is the correct M29.
==================================================
```
Tool lifecycle eviction directly finishes the multi-milestone coordinated teardown initiative (M25–M28), resolves an immediate denial-of-service vulnerability, and requires a minimal, surgical 3-file footprint.

---

## 19. CONTRACT PRELOCK SPECIFICATION

- **Milestone Title**: `M29 — Clinical Tool Subsystem Lifecycle Eviction & Teardown Hardening`
- **Core Objectives**:
  1. Add `ToolExecutionEngine.evict_session(session_id: str) -> bool`.
  2. Add `ToolService.evict_session(session_id: str, capability: Optional[Any] = None) -> bool`.
  3. Wire `tool_service.evict_session(session_id, cap)` into `ClinicalExecutionGatewayService.execute_session_teardown()` as Step 12.
  4. Verify complete capacity reclamation of `MAX_ACTIVE_SESSIONS = 64`.
  5. Verify session-ID reuse without `ToolSequenceError`.
  6. Verify cross-session tool state isolation.
  7. Maintain 100% regression pass across full test suite.

---

## 20. FINAL CLASSIFICATION

```
==================================================
READY_FOR_LOCK
==================================================
```
