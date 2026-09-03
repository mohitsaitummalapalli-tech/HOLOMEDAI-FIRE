# M28 IMPLEMENTATION REPORT
## Gateway Ingress Security & Connection Lifecycle Hardening

**Authoritative Baseline**: `7acac2469a5da864ae926906671f761888715127`  
**Milestone**: M28 — Gateway Ingress Security & Connection Lifecycle Hardening  
**Status**: IMPLEMENTATION COMPLETE  
**Verification Result**: 1586 PASSED (18 new M28 hostile tests, 12 M25 tests, 59 gateway tests, 1586 full platform regression tests)  

---

## 1. IMPLEMENTATION OVERVIEW

M28 implements deterministic session-payload binding at the network ingress perimeter and extends the coordinated clinical session teardown architecture to `GatewayService`.

### Problem Addressed:
1. **Cross-Session Injection Vulnerability**: In `GatewayAuthorizationPolicy.authorize_message()`, the policy validated `envelope.source == session.client_id`, but completely omitted checking `envelope.payload.get("session_id") == session.session_id`. An authenticated client for Session A could issue commands (`workflow.transition`, `execution.tool.invoke`, `execution.session.teardown`) targeting Session B.
2. **Connection Lifecycle & Capacity Lockout**: `GatewayService` had no `evict_session(session_id)` method. Stale connections survived session teardown, accumulated against `MAX_CONNECTIONS_PER_SESSION = 4`, permanently locked out reused session IDs, and continued receiving broadcast events and XR presentation frames.

### Solution Delivered:
1. **Perimeter Session-Binding Enforcement**: Enforced `payload["session_id"] == session.session_id` in `GatewayAuthorizationPolicy.authorize_message()`. Any mismatch raises the existing canonical `GatewaySessionMismatchError("ERR_SESSION_MISMATCH")` before dispatching.
2. **Surgical Gateway Session Eviction**: Implemented `GatewayService.evict_session(session_id, capability)`. Closes and unregisters matching connections, flushes pending egress, reclaims `MAX_CONNECTIONS_PER_SESSION = 4` capacity, and protects against reentrancy via transaction guard.
3. **Coordinated Teardown Integration**: Extended `ClinicalExecutionGatewayService.execute_session_teardown()` with Step 11: Gateway Ingress Connections, invoking `gateway_service.evict_session()` and aggregating failures without halting teardown.

---

## 2. PRODUCTION FILES MODIFIED

### 1. `python/holomed/gateway/authorization.py`
- Imported canonical `GatewaySessionMismatchError` from `holomed.gateway.exceptions`.
- Added Step 2 to `GatewayAuthorizationPolicy.authorize_message()`:
  ```python
  # 2. Prevent Session Spoofing / Cross-Session Injection (M28)
  if isinstance(envelope.payload, dict) and "session_id" in envelope.payload:
      payload_session_id = envelope.payload.get("session_id")
      if payload_session_id != session.session_id:
          raise GatewaySessionMismatchError(
              f"Cross-session injection rejected: envelope declared session_id={payload_session_id!r}, "
              f"authenticated session_id={session.session_id!r}"
          )
  ```
- **Ordering**: Enforced *after* source validation and *before* actuation block and role capability checks. Mismatched messages are rejected immediately without reaching `MessageDispatcher`.

### 2. `python/holomed/gateway/service.py`
- Implemented `evict_session(session_id: str, capability: Optional[Any] = None) -> bool`:
  - Validates `session_id`.
  - Reentrancy protected via `self._in_transaction`.
  - Surgically pops matching `GatewayConnection` objects from `self._connections`.
  - Closes transport connections and clears egress queues.
  - Emits `gateway.client.disconnected` events.
  - Returns `True` if one or more connections were evicted.
- Updated `handle_presentation_event()` to scope XR frame delivery to `session_id` when present in payload.

### 3. `python/holomed/execution/service.py`
- Updated `ClinicalExecutionGatewayService.__init__()` to accept optional `gateway_service: Optional[Any] = None` and store `self._gateway_service`.
- Added Step 11 to `execute_session_teardown()`:
  ```python
  # Step 11: Gateway Ingress Connections (M28)
  if self._gateway_service is not None:
      try:
          if hasattr(self._gateway_service, "evict_session"):
              self._gateway_service.evict_session(session_id, cap)
          subsystems_purged.append("gateway_service")
      except Exception as exc:
          failures.append(f"gateway_service: {exc}")
  ```

---

## 3. TEST SURFACE CREATED

`tests/unit/gateway/test_m28_gateway_ingress_lifecycle.py` (18 comprehensive tests):

| # | Test Name | Invariant Tested | Path Exercised |
|---|---|---|---|
| 1 | `test_cross_session_payload_injection_rejected` | Cross-session payload mismatch rejected | Full transport + framing + ingress |
| 2 | `test_spoofed_workflow_transition_cannot_mutate_b` | Spoofed `workflow.transition` rejected; Session B phase intact | Gateway -> Ingress -> WorkflowService |
| 3 | `test_spoofed_execution_tool_invoke_cannot_mutate_b` | Spoofed `execution.tool.invoke` rejected; zero tool results for Session B | Gateway -> Ingress -> ExecutionService |
| 4 | `test_spoofed_execution_session_teardown_cannot_destroy_b` | Spoofed `execution.session.teardown` rejected; Session B remains active | Gateway -> Ingress -> Execution -> Platform |
| 5 | `test_same_session_payload_allowed` | Compliant same-session commands pass transparently | Gateway -> Dispatcher -> WorkflowService |
| 6 | `test_omitted_session_id_on_global_routes` | Global queries (`gateway.status`) without `session_id` succeed | Gateway -> Ingress -> Handler |
| 7 | `test_malformed_and_none_session_id_handling` | None, empty, or non-string `session_id` fails closed | `authorize_message()` boundary |
| 8 | `test_cross_session_replay_fails_closed` | Intercepted envelope from Session A replayed on Session B rejected | Client B transport -> Ingress |
| 9 | `test_connection_capacity_limit_enforced` | Exactly 4 connections allowed; 5th raises `GatewayCapacityError` | Handshake capacity enforcement |
| 10 | `test_session_eviction_closes_all_session_connections` | `evict_session()` closes all session connections and pops descriptors | `evict_session()` API |
| 11 | `test_session_eviction_preserves_foreign_connections` | Evicting Session A preserves Session B connections in ACTIVE state | Multi-session isolation |
| 12 | `test_capacity_reclaimed_after_eviction` | Capacity fully restored; 4 new connections connect cleanly after eviction | Reconnection & reuse |
| 13 | `test_evicted_connection_cannot_receive_xr_frames` | Evicted connection cannot receive XR presentation frames | Presentation event broadcast |
| 14 | `test_evicted_connection_cannot_receive_workflow_broadcasts` | Evicted connection cannot receive workflow broadcast events | Workflow event broadcast |
| 15 | `test_evicted_connection_cannot_submit_commands` | Closed transport raises `GatewayTransportError` on send | Transport closed invariant |
| 16 | `test_teardown_step11_gateway_integration` | `execute_session_teardown()` invokes Step 11 gateway eviction | Full execution gateway teardown |
| 17 | `test_teardown_gateway_failure_aggregation` | Gateway eviction exception captured in `failures` without halting teardown | Multi-stage failure resilience |
| 18 | `test_reentrant_gateway_eviction_fails_safely` | Reentrant call rejected by transaction guard | Transaction guard invariant |

---

## 4. REGRESSION & DIFF VERIFICATION

- M28 Suite: `pytest tests/unit/gateway/test_m28_gateway_ingress_lifecycle.py` -> 18 passed in 0.17s.
- M25 Suite: `pytest tests/unit/execution/test_m25_session_teardown.py` -> 12 passed in 0.07s.
- M26 Suite: `pytest tests/unit/execution/test_m26_perceptual_lifecycle.py` -> 13 passed in 0.07s.
- M27 Suite: `pytest tests/unit/execution/test_m27_workflow_interlock_lifecycle.py` -> 13 passed in 0.06s.
- Gateway Suite: `pytest tests/unit/gateway/` -> 59 passed in 0.32s.
- Full Regression: `pytest -q -ra` -> **1586 passed in 5.50s (0 failures, 0 regressions)**.
- `git diff --check`: Clean (0 whitespace/formatting errors).
