# PHASE 29 CONTRACT: CLINICAL TOOL SUBSYSTEM LIFECYCLE EVICTION & TEARDOWN HARDENING

**Authoritative Baseline**: `e7362bcc8708a347abc851686f3f25f66358d2f7`  
**Milestone**: M29 — Clinical Tool Subsystem Lifecycle Eviction & Teardown Hardening  
**Status**: DRAFT CONTRACT (Awaiting Implementation Authorization)  
**Predecessor**: M28 (Frozen)  

---

## 1. PRIMARY OBJECTIVE

Complete coordinated clinical session teardown for the Tool execution subsystem.

M29 eliminates:
1. **Stale Tool Sequence Contamination**: Session sequence state surviving session teardown in `ToolExecutionEngine._session_sequences`.
2. **Session-ID Reuse Lockout**: New procedures reusing a prior `session_id` being rejected with `ToolSequenceError` upon their first tool invocation (`sequence_number 1 <= last_seq`).
3. **Permanent Server Denial of Service**: Progressive exhaustion of `MAX_ACTIVE_SESSIONS = 64` across long-running operation, permanently locking out all future tool invocations platform-wide with `ToolCapacityError`.

M29 seamlessly extends the coordinated teardown architecture established in M25–M28.

---

## 2. AUTHORIZED REOPEN SET

The source code modifications for M29 are strictly confined to the following **3 production files**:
1. `python/holomed/tools/engine.py`
2. `python/holomed/tools/service.py`
3. `python/holomed/execution/service.py`

Authorized test surface:
4. `tests/unit/execution/test_m29_tool_lifecycle.py`

Authorized documentation/contract artifacts:
5. `PHASE_29_CONTRACT.md`
6. `M29_IMPLEMENTATION_REPORT.md`
7. `M29_HOSTILE_AUDIT_REPORT.md`
8. `M29_FINAL_PRECOMMIT_AUDIT.md`

**NO OTHER PRODUCTION FILES** may be changed. All other subsystems (Platform, Workflow, Planning, Registration, Navigation, Recovery, Safety Gate, Proximity, Drift, Persistence, Devices, XR, Core, Gateway) remain **STRICTLY FROZEN**.

---

## 3. TOOL STATE OWNERSHIP

A comprehensive audit of the `holomed.tools` subsystem identifies the exact state structures:

| Structure | Owner | Scope | Key | Eviction Action |
|---|---|---|---|---|
| `_session_sequences` | `ToolExecutionEngine` | **Session-Scoped** | `session_id: str` | **Purged on teardown via `evict_session(session_id)`** |
| `_result_history` | `ToolExecutionEngine` | Process-Global | FIFO Ring Buffer (`list`) | Preserved (bounded by `MAX_RESULT_HISTORY = 256`, auto-prunes) |
| `_tools` | `ToolRegistry` | Static Catalog | `tool_id: str` | Preserved (immutable once locked) |
| `_events` | `RecordingToolEventSink` | Process-Global | FIFO List | Preserved (bounded by `MAX_RECORDED_TOOL_EVENTS = 256`) |
| `_total_invocations` | `ToolService` | Global Metric | Integer scalar | Preserved |
| `_in_transaction` | `ToolService` | Local Reentrancy | Boolean flag | Reentrancy guard |

`_session_sequences` in `ToolExecutionEngine` is the sole session-scoped mutable structure in the subsystem.

---

## 4. TOOL EVICTION API

### A. Engine Layer (`ToolExecutionEngine`)
Add to `python/holomed/tools/engine.py`:
```python
def evict_session(self, session_id: str) -> bool:
    """Evict session sequence tracking state, releasing capacity (M29).
    
    Surgically removes session_id from _session_sequences without altering
    other active sessions or global result history. Returns True if evicted,
    False if session_id was not registered.
    """
    if session_id in self._session_sequences:
        del self._session_sequences[session_id]
        return True
    return False
```

### B. Service Layer (`ToolService`)
Add to `python/holomed/tools/service.py`:
```python
def evict_session(self, session_id: str, capability: Optional[Any] = None) -> bool:
    """Evict session-scoped tool sequence state under coordinated teardown (M29).
    
    Validates session_id, enforces transactional reentrancy protection,
    optionally verifies SESSION_TEARDOWN capability binding, and delegates
    eviction to ToolExecutionEngine.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        return False

    if self._in_transaction:
        raise ToolLifecycleError("Reentrant call to evict_session rejected")

    if capability is not None:
        if not getattr(capability, "is_active", False):
            raise ToolAuthorizationError("Teardown capability is inactive or expired")
        if getattr(capability, "action", None) != "SESSION_TEARDOWN":
            raise ToolAuthorizationError(
                f"Capability action mismatch: expected 'SESSION_TEARDOWN', got {getattr(capability, 'action', None)!r}"
            )
        if getattr(capability, "session_id", None) != session_id:
            raise ToolAuthorizationError(
                f"Capability session mismatch: expected {session_id!r}, got {getattr(capability, 'session_id', None)!r}"
            )

    self._in_transaction = True
    try:
        if self._engine is not None:
            return self._engine.evict_session(session_id)
        return False
    finally:
        self._in_transaction = False
```

### Invariants:
1. **Surgical Isolation**: Evicting `Session A` never deletes or alters sequence numbers belonging to `Session B`.
2. **Idempotence**: Calling `evict_session()` repeatedly on the same `session_id` succeeds safely and returns `False` on subsequent invocations.
3. **No Global Wipes**: `clear()` and `reset()` MUST NOT be called during per-session teardown.

---

## 5. M29 TEARDOWN INTEGRATION

In `python/holomed/execution/service.py`, extend `ClinicalExecutionGatewayService.execute_session_teardown()`:

```python
            # Step 12: Tool Execution State (M29)
            if self._tool_service is not None:
                try:
                    if hasattr(self._tool_service, "evict_session"):
                        self._tool_service.evict_session(session_id, cap)
                    subsystems_purged.append("tools")
                except Exception as exc:
                    failures.append(f"tools: {exc}")
```

### Established Teardown Sequence (Strictly Preserved):
1. Navigation (`navigation`)
2. Proximity (`proximity`, M26)
3. Drift (`drift`, M26)
4. Recovery (`recovery`)
5. Registration (`registration`)
6. Planning (`planning`)
7. Safety Gate (`safety_gate`)
8. Workflow (`workflow`, M27)
9. Gateway Cache (`gateway`)
10. Platform Session (`platform`, M25)
11. Gateway Ingress Connections (`gateway_service`, M28)
12. **Tool Execution State (`tools`, M29)**

`subsystems_purged` appends `"tools"` upon successful eviction.

---

## 6. CAPABILITY SECURITY

1. **Reused Capability**: Uses the canonical `_ExecutionCapability` minted by `ClinicalExecutionGatewayService.execute_session_teardown()`:
   - `action = "SESSION_TEARDOWN"`
   - `session_id = request.session_id`
   - `sequence_number = request.sequence_number`
2. **Single-Use Invalidation**: The capability is invalidated immediately in the `finally` block of `execute_session_teardown()`.
3. **Cross-Session Defense**: Passing a capability for Session A to evict Session B raises `ToolAuthorizationError`.
4. **Replay Defense**: Attempting to reuse an invalidated or expired capability raises `ToolAuthorizationError`.

---

## 7. SESSION-ID REUSE

### Guaranteed Sequence Invariant:
```
Session A
  → invoke tool with sequence 1 (succeeds)
  → invoke tool with sequence 5 (succeeds, last_seq=5)
  → execute_session_teardown(Session A)
  → reuse Session A
  → invoke tool with sequence 1 (MUST SUCCEED)
```
After M29 teardown, `_session_sequences` for Session A is deleted. The new session starts with `last_seq = -1`, allowing sequence 0 or 1 to proceed without `ToolSequenceError`.

---

## 8. CAPACITY RECLAMATION

- **Canonical Limit**: `MAX_ACTIVE_SESSIONS = 64` (defined in `python/holomed/tools/models.py:27`).
- **Invariant**:
  - Filling `_session_sequences` to 64 active sessions and subsequently tearing them down reduces active session count to 0.
  - Session 65 executes normally without raising `ToolCapacityError`.
  - The constant `MAX_ACTIVE_SESSIONS` is NOT modified.

---

## 9. CROSS-SESSION ISOLATION

Given concurrent sessions `Session A` and `Session B`:
1. `Session A` reaches sequence 10; `Session B` reaches sequence 3.
2. `Session A` is torn down.
3. Assertions:
   - `Session A` is absent from `_session_sequences`.
   - `Session B` remains in `_session_sequences` with sequence 3.
   - `Session B` can execute sequence 4 normally.
   - `Session B` attempting sequence 2 still raises `ToolSequenceError(2 <= 3)`.

---

## 10. TOOL EXECUTION SEMANTICS

The following execution behaviors remain **STRICTLY PRESERVED**:
- Parameter validation against tool descriptors (`validate_tool_parameters`).
- Tool safety classification evaluation (`ToolSafetyClassification`).
- Dual-gate evaluation (`SafetyGateService` and `WorkflowService.authorize_tool`).
- Payload size validation (`MAX_TOOL_RESULT_BYTES = 32768`).
- Bounded invocation history (`MAX_RESULT_HISTORY = 256`).
- Event sink recording (`MAX_RECORDED_TOOL_EVENTS = 256`).

---

## 11. REPLAY / SEQUENCE SEMANTICS

- Monotonic sequence checks (`sequence_number <= last_seq`) are strictly enforced for active sessions.
- In-flight or replayed tool invocations with equal or lower sequence numbers continue to be rejected with `ToolSequenceError`.
- Eviction occurs strictly upon explicit `SESSION_TEARDOWN`.

---

## 12. FAILURE SEMANTICS

- In `execute_session_teardown()`, exceptions in `ToolService.evict_session()` are caught and appended to `failures`.
- Best-effort teardown continues to completion.
- If `failures` is non-empty, teardown reports `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY` and audits `session_teardown_degraded`.

---

## 13. PARTIAL MUTATION ANALYSIS

- Monotonicity checks happen before handler execution.
- If handler execution raises an unhandled exception:
  - Sequence number remains advanced (reflecting that the sequence slot was consumed).
  - An error `ToolResult` with status `EXECUTION_ERROR` is recorded in history.
- `_in_transaction` is cleared in a `finally` block in both `invoke_tool()` and `evict_session()`.

---

## 14. PERSISTENCE BOUNDARY

- **Ephemeral**: `_session_sequences` is purged on teardown.
- **Durable**: Audit records written to `PersistenceService` (e.g. `execution.tool.completed`) reside on disk in append-only `.jsonl` journals and are NEVER deleted or truncated during runtime session teardown.

---

## 15. MANDATORY TEST SUITE

File: `tests/unit/execution/test_m29_tool_lifecycle.py`

### Required Test Cases (18 minimum):
1. `test_m29_tool_session_state_creation`: Invoking tool initializes `_session_sequences[session_id]`.
2. `test_m29_complete_tool_session_eviction`: Direct `evict_session()` removes sequence entry.
3. `test_m29_tool_eviction_idempotence`: Repeated `evict_session()` calls return `False` safely.
4. `test_m29_cross_session_tool_isolation`: Teardown of Session A preserves Session B sequence state.
5. `test_m29_session_id_reuse_after_teardown`: Reused Session A can invoke tools starting at sequence 1.
6. `test_m29_sequence_state_reset_after_teardown`: Confirms `_session_sequences` entry is fully purged, not just zeroed.
7. `test_m29_64_session_capacity_reclamation`: Tearing down 64 sessions restores capacity to 0.
8. `test_m29_replacement_session_after_capacity_exhaustion`: 65th session succeeds after teardown of prior sessions.
9. `test_m29_real_production_path_invocation_and_teardown`: Tool invocation through `ClinicalExecutionGatewayService` followed by teardown.
10. `test_m29_subsystems_purged_includes_tools`: `res.subsystems_purged` includes `"tools"`.
11. `test_m29_teardown_ordering_integrity`: Verifies `"tools"` is purged at Step 12 after `"gateway_service"`.
12. `test_m29_stale_teardown_capability_replay_fails`: Expired/inactive capability rejected with `ToolAuthorizationError`.
13. `test_m29_cross_session_teardown_capability_fails`: Capability with mismatched `session_id` rejected.
14. `test_m29_reentrant_tool_eviction_fails_safely`: Eviction during active transaction raises `ToolLifecycleError`.
15. `test_m29_partial_tool_eviction_failure_aggregated`: Failure in `tool_service` aggregates into degraded teardown status.
16. `test_m29_durable_audit_preserved_after_teardown`: Tool audit events in `PersistenceService` remain intact.
17. `test_m29_active_session_monotonicity_preserved`: Sequence monotonicity enforcement within active session unchanged.
18. `test_m29_regression_preservation`: M25, M26, M27, and M28 teardown tests pass without regression.

---

## 16. HOSTILE SOURCE AUDIT CHECKLIST

Post-implementation audit checks:
- No session-scoped Tool state survives teardown.
- No global `clear()` is invoked during teardown.
- `MAX_ACTIVE_SESSIONS` capacity is properly restored.
- Reused session IDs start clean without `ToolSequenceError`.
- No capability bypass exists.

---

## 17. FROZEN BOUNDARIES

STRICTLY FROZEN:
- M19–M28 clinical execution architecture.
- Gateway ingress and session binding.
- Workflow safety interlock and checkpoint semantics.
- Safety-gate precedence hierarchy.
- Domain subsystems: Planning, Registration, Navigation, Recovery, Proximity, Drift.
- Durable persistence and audit trail.

---

## 18. DIFF AUDIT CLASSIFICATION

Every changed line must fall into:
- **Class A**: Explicitly authorized M29 tool lifecycle logic.
- **Class B**: Required M29 teardown pipeline wiring in `ClinicalExecutionGatewayService`.
- **Class C**: Unauthorized (Blocker).

---

## 19. VERIFICATION COMMANDS

```bash
# Targeted M29 suite
python -m pytest tests/unit/execution/test_m29_tool_lifecycle.py -q -ra

# Prior teardown regression suites
python -m pytest tests/unit/execution/test_m25_session_teardown.py -q -ra
python -m pytest tests/unit/execution/test_m26_perceptual_lifecycle.py -q -ra
python -m pytest tests/unit/execution/test_m27_workflow_interlock_lifecycle.py -q -ra
python -m pytest tests/unit/gateway/test_m28_gateway_ingress_lifecycle.py -q -ra

# Full platform regression suite
python -m pytest -q -ra

# Git hygiene
git diff --check
git status --short
```

---

## 20. RELEASE GATE

Final classification upon verification MUST be:
- `M29_PRECOMMIT_PASS`
- `M29_PRECOMMIT_BLOCKED`
- `M29_TEST_COVERAGE_INSUFFICIENT`

Zero commits or pushes until explicit authorization.
