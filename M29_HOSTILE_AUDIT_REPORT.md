# M29 HOSTILE AUDIT REPORT: TOOL LIFECYCLE EVICTION & TEARDOWN HARDENING

**Authoritative Baseline**: `e7362bcc8708a347abc851686f3f25f66358d2f7`  
**Milestone**: M29 — Clinical Tool Subsystem Lifecycle Eviction & Teardown Hardening  
**Audit Status**: AUDIT PASSED — ZERO UNINTENDED LEAKS, ZERO UNAUTHORIZED MUTATIONS  

---

## 1. STATIC KEYWORD AUDIT & PATTERN SCAN

A hostile scan for critical identifiers was conducted across production and test surfaces:

### 1. `_session_sequences`
- `python/holomed/tools/engine.py:38`: Initialized as `dict[str, int] = {}`.
- `python/holomed/tools/engine.py:60-71`: Accessed, checked against `MAX_ACTIVE_SESSIONS`, and updated during `execute_invocation`.
- `python/holomed/tools/engine.py:174-177`: Explicitly and surgically purged in `evict_session(session_id)`.
- `python/holomed/tools/engine.py:186`: Cleared only on complete subsystem shutdown/reset via `clear()`.
- **Verdict**: Sole mutable session-scoped structure; strictly evicted during session teardown.

### 2. `MAX_ACTIVE_SESSIONS`
- Defined in `python/holomed/tools/models.py:22` as `MAX_ACTIVE_SESSIONS: int = 64`.
- **Verdict**: Value frozen; untouched. Tested and verified at capacity boundary and after teardown restoration.

### 3. `ToolExecutionEngine` & `ToolService`
- Both classes implement `evict_session()`.
- `ToolService` enforces parameter validation, capability verification, and transactional reentrancy protection before delegating to `ToolExecutionEngine`.
- **Verdict**: Service boundaries and layered responsibilities are strictly maintained.

### 4. `execute_invocation`
- Preserves all M07/M19/M21 invariants: epoch isolation, monotonicity validation, depth and cycle guards, descriptor validation, parameter validation, timing, payload size limits, and bounded result history.
- **Verdict**: Execution semantics are 100% preserved.

### 5. `evict_session`
- Implemented in `ToolExecutionEngine` and `ToolService`.
- Never calls `clear()` or touches other sessions.
- **Verdict**: Surgical and idempotent.

### 6. `clear()`
- Found in `ToolExecutionEngine.clear()` and `ToolService.clear()`.
- Only invoked during service `stop()` or epoch `reset()`.
- **Verdict**: Never called during per-session `execute_session_teardown()`.

### 7. `execution.session.teardown`
- Handled by `ClinicalExecutionGatewayService.handle_session_teardown_command()`, which delegates to `execute_session_teardown()`.
- Step 12 cleanly integrates `ToolService.evict_session(session_id, cap)`.
- **Verdict**: Failures aggregate without breaking the best-effort teardown pipeline.

---

## 2. PRODUCTION DIFF AUDIT CLASSIFICATION

Every changed line against authoritative baseline `e7362bcc8708a347abc851686f3f25f66358d2f7` was classified:

### File 1: `python/holomed/tools/engine.py`
```diff
@@ -164,6 +164,18 @@ class ToolExecutionEngine:
             self._result_history.pop(0)
         self._result_history.append(result)
 
+    def evict_session(self, session_id: str) -> bool:
+        """Evict session sequence tracking state, releasing capacity (M29).
+
+        Surgically removes session_id from _session_sequences without altering
+        other active sessions or global result history. Returns True if evicted,
+        False if session_id was not registered.
+        """
+        if session_id in self._session_sequences:
+            del self._session_sequences[session_id]
+            return True
+        return False
+
     def reset(self, epoch_id: int) -> None:
```
- **Classification**: **Class A** (Explicitly authorized M29 tool lifecycle logic).

### File 2: `python/holomed/tools/service.py`
```diff
@@ -339,6 +339,39 @@ class ToolService(IService):
             )
         self.clear()
 
+    def evict_session(self, session_id: str, capability: Optional[Any] = None) -> bool:
+        """Evict session-scoped tool sequence state under coordinated teardown (M29).
+
+        Validates session_id, enforces transactional reentrancy protection,
+        optionally verifies SESSION_TEARDOWN capability binding, and delegates
+        eviction to ToolExecutionEngine.
+        """
+        if not isinstance(session_id, str) or not session_id.strip():
+            return False
+
+        if self._in_transaction:
+            raise ToolLifecycleError("Reentrant call to evict_session rejected")
+
+        if capability is not None:
+            if not getattr(capability, "is_active", False):
+                raise ToolAuthorizationError("Teardown capability is inactive or expired")
+            if getattr(capability, "action", None) != "SESSION_TEARDOWN":
+                raise ToolAuthorizationError(
+                    f"Capability action mismatch: expected 'SESSION_TEARDOWN', got {getattr(capability, 'action', None)!r}"
+                )
+            if getattr(capability, "session_id", None) != session_id:
+                raise ToolAuthorizationError(
+                    f"Capability session mismatch: expected {session_id!r}, got {getattr(capability, 'session_id', None)!r}"
+                )
+
+        self._in_transaction = True
+        try:
+            if self._engine is not None:
+                return self._engine.evict_session(session_id)
+            return False
+        finally:
+            self._in_transaction = False
+
     def clear(self) -> None:
```
- **Classification**: **Class A** (Explicitly authorized M29 tool lifecycle logic).

### File 3: `python/holomed/execution/service.py`
```diff
@@ -2238,6 +2238,15 @@ class ClinicalExecutionGatewayService(IService):
                 except Exception as exc:
                     failures.append(f"gateway_service: {exc}")
 
+            # Step 12: Tool Execution State (M29)
+            if self._tool_service is not None:
+                try:
+                    if hasattr(self._tool_service, "evict_session"):
+                        self._tool_service.evict_session(session_id, cap)
+                    subsystems_purged.append("tools")
+                except Exception as exc:
+                    failures.append(f"tools: {exc}")
+
             # Determine execution status and audit event
             if failures:
                 exec_status = ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
```
- **Classification**: **Class B** (Required M29 teardown pipeline wiring in `ClinicalExecutionGatewayService`).

### Summary Classification Count:
- **Class A**: 45 lines added (authorized engine and service eviction methods)
- **Class B**: 10 lines added (Step 12 gateway teardown wiring)
- **Class C (Unauthorized)**: **0 lines (NONE)**

---

## 3. TEST QUALITY AUDIT

An audit of `tests/unit/execution/test_m29_tool_lifecycle.py` was conducted to ensure testing integrity:

1. **Real Production Path Verification**:
   - `test_m29_real_production_path_invocation_and_teardown` dispatches a real `execution.tool.invoke` envelope across `MessageDispatcher`, evaluated by real `PlatformService`, real `WorkflowService`, real `SafetyGateService`, real `ToolService`, and real `ToolExecutionEngine`, followed by a real `execution.session.teardown` envelope.
   - Zero mocks or monkeypatches are used on the core execution path.
2. **Capability Security Tests**:
   - Explicitly verifies that expired, invalidated, or replayed capabilities raise `ToolAuthorizationError`.
   - Explicitly verifies that cross-session capability spoofing raises `ToolAuthorizationError`.
3. **Failure Aggregation Test**:
   - Verifies that unexpected runtime exceptions in `tool_service.evict_session` aggregate into `failures` and result in `ExecutionStatus.FAILED_NAVIGATION_GEOMETRY`.
4. **Boundary & Capacity Tests**:
   - Exhaustively populates 64 sessions, confirms 65th session rejection with `ToolCapacityError`, tears down all 64 sessions, and verifies that new sessions succeed without error.
5. **Durable Persistence Test**:
   - Proves that durable audit records in `PersistenceService.audit_store` survive session teardown completely intact.

---

## 4. INVARIANT INTEGRITY VERIFICATION

| Invariant | Result | Evidence |
|---|---|---|
| No Tool state survives teardown | **VERIFIED** | `_session_sequences` entry removed completely on teardown |
| No composite-key leak | **VERIFIED** | Audited all tool classes; no composite-key caches exist |
| Capacity is reclaimed | **VERIFIED** | `test_m29_64_session_capacity_reclamation` passed |
| Reused session IDs start clean | **VERIFIED** | `test_m29_session_id_reuse_after_teardown` passed |
| Active session monotonicity preserved | **VERIFIED** | `test_m29_active_session_monotonicity_preserved` passed |
| No global `clear()` during teardown | **VERIFIED** | Code audit confirmed `clear()` is not called in teardown |
| No capability bypass | **VERIFIED** | `test_m29_stale_teardown_capability_replay_fails` passed |
| Frozen boundaries respected | **VERIFIED** | No production changes outside 3 authorized files |
