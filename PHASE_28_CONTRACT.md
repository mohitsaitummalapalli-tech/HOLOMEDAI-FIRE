# PHASE 28 CONTRACT: GATEWAY INGRESS SECURITY & CONNECTION LIFECYCLE HARDENING

**Authoritative Baseline**: `7acac2469a5da864ae926906671f761888715127`  
**Milestone**: M28 — Gateway Ingress Security & Connection Lifecycle Hardening  
**Status**: DRAFT CONTRACT (Awaiting Implementation Authorization)  
**Predecessor**: M27 (Frozen)  

---

## 1. PRIMARY SECURITY OBJECTIVE

Establish a deterministic, fail-closed cryptographic and architectural binding between the authenticated gateway transport session and the `session_id` parameter carried by incoming protocol message envelopes.

An authenticated client for Session A (`session_id="SESS-A"`) MUST NOT be able to target, mutate, inspect, or abort Session B (`session_id="SESS-B"`) merely by specifying `payload["session_id"] = "SESS-B"`. All security failures MUST be detected and rejected at the gateway perimeter before reaching the internal `MessageDispatcher` or any target subsystem.

Additionally, integrate `GatewayService` into the coordinated clinical session teardown architecture established in M25–M27 so that session termination surgically evicts all transport connections bound to the session, reclaims connection capacity (`MAX_CONNECTIONS_PER_SESSION = 4`), and prevents stale connections from observing live surgical presentation frames or broadcast events.

---

## 2. AUTHORIZED REOPEN SET

The source code modifications for M28 are strictly confined to the following **3 production files**:
1. `python/holomed/gateway/authorization.py`
2. `python/holomed/gateway/service.py`
3. `python/holomed/execution/service.py`

Authorized test surface:
4. `tests/unit/gateway/test_m28_gateway_ingress_lifecycle.py`

Authorized documentation/contract artifacts:
5. `PHASE_28_CONTRACT.md`
6. `M28_IMPLEMENTATION_REPORT.md`
7. `M28_HOSTILE_AUDIT_REPORT.md`
8. `M28_FINAL_PRECOMMIT_AUDIT.md`

NO other production files may be changed. All other subsystems (Platform, Workflow, Planning, Registration, Navigation, Recovery, Safety Gate, Proximity, Drift, Persistence, Tools, Devices, XR, Core) remain **STRICTLY FROZEN**.

---

## 3. SESSION BINDING RULE

In `python/holomed/gateway/authorization.py`, `GatewayAuthorizationPolicy.authorize_message(session: ClientSession, envelope: MessageEnvelope)` MUST enforce:

```python
# Enforce Session-Payload Binding (D284.1)
if isinstance(envelope.payload, dict) and "session_id" in envelope.payload:
    payload_session_id = envelope.payload.get("session_id")
    if payload_session_id is not None and str(payload_session_id) != session.session_id:
        raise GatewaySessionMismatchError(
            f"Cross-session injection rejected: envelope declared session_id={payload_session_id!r}, "
            f"authenticated session_id={session.session_id!r}"
        )
```

### Invariants:
1. **Canonical Error Type**: Mismatch raises the existing canonical `GatewaySessionMismatchError` (`error_code="ERR_SESSION_MISMATCH"`) defined in `holomed/gateway/exceptions.py:108`.
2. **Pre-Dispatch Rejection**: Rejection occurs *before* `self._dispatcher.dispatch(envelope)` is called. The envelope NEVER reaches the dispatcher, command handlers, or target services.
3. **No Silent Payload Rewriting**: The gateway MUST NOT silently rewrite caller payloads. Silent mutations can mask caller defects or execute unexpected actions on Session A.
4. **Preservation of Non-Session Routes**: If `envelope.payload` does not contain `"session_id"` (e.g. `gateway.status`, `gateway.clients`, `xr.status`), the binding check passes transparently.
5. **Preservation of Same-Session Commands**: Compliant clients specifying their own `session_id` (`payload["session_id"] == session.session_id`) pass transparently.

---

## 4. COMPLETE INGRESS COVERAGE

The session-binding security rule protects ALL dispatcher routes accepting `session_id` in their payload. At minimum:
- `workflow.transition` (`WorkflowService.handle_transition_command`)
- `workflow.confirm` (`WorkflowService.handle_confirm_command`)
- `workflow.abort` (`WorkflowService.handle_abort_command`)
- `execution.navigation.execute` (`ClinicalExecutionGatewayService.handle_execute_command`)
- `execution.recovery.execute` (`ClinicalExecutionGatewayService.handle_recovery_execute_command`)
- `execution.trajectory.bind` (`ClinicalExecutionGatewayService.handle_trajectory_bind_command`)
- `execution.tool.invoke` (`ClinicalExecutionGatewayService.handle_tool_invoke_command`)
- `execution.workflow.resume` (`ClinicalExecutionGatewayService.handle_workflow_resume_command`)
- `execution.registration.execute` (`ClinicalExecutionGatewayService.handle_registration_execute_command`)
- `execution.planning.execute` (`ClinicalExecutionGatewayService.handle_planning_execute_command`)
- `execution.session.teardown` (`ClinicalExecutionGatewayService.handle_session_teardown_command`)
- `platform.session.stop` (`PlatformService.handle_stop_session_command`)
- `platform.cycle.advance` (`PlatformService.handle_advance_cycle_command`)
- `safety_gate.evaluate` (`SafetyGateService.handle_evaluate_command`)
- `proximity.evaluate` (`ProximityService.handle_evaluate_command`)
- `drift.evaluate` (`DriftService.handle_evaluate_command`)

Because authorization is centralized in `GatewayAuthorizationPolicy.authorize_message()`, every message processed via `GatewayService.process_client_ingress()` is automatically protected with zero route-specific loopholes.

---

## 5. AUTHORIZATION ORDERING

The conceptual execution ordering for all incoming client messages is:

```
Incoming Framed Envelope Bytes
  → GatewayConnection.read_ingress()
  → deserialize_envelope()
  → Connection State Check (ACTIVE)
  → GatewayAuthorizationPolicy.authorize_message(session, envelope)
      ├── 1. Source Spoofing Check (envelope.source == session.client_id)
      ├── 2. Categorical Actuation Block (forbidden physical actuation keywords)
      ├── 3. Session-Payload Binding (payload["session_id"] == session.session_id) [M28]
      └── 4. Role Capability Check (ClientRole message type restrictions)
  → MessageDispatcher.dispatch(envelope)
  → Downstream Command / Query Handler
  → Target Subsystem State Mutation
```

A mismatched `session_id` MUST be terminated at Step 3, yielding an `ERR_SESSION_MISMATCH` response to the client. It MUST NOT reach downstream services.

---

## 6. CAPABILITY PRESERVATION

M28 strictly preserves all existing `_ExecutionCapability` contracts established in M23–M27:
- **No Capability Bypass**: Capabilities are minted exclusively inside `ClinicalExecutionGatewayService` during execution of clinical commands.
- **No Duplication**: The gateway authorization policy does not touch capabilities; it only enforces perimeter ingress identity.
- **Single-Use Invalidation**: Tokens continue to be invalidated in `finally` blocks upon command completion.
- **Teardown Gating**: The optional `capability` parameter passed to `GatewayService.evict_session(session_id, capability)` preserves teardown coordinator authority.

---

## 7. CONNECTION LIFECYCLE & EVICTION API

Add to `GatewayService` (`python/holomed/gateway/service.py`):

```python
def evict_session(
    self,
    session_id: str,
    capability: Optional[Any] = None,
) -> bool:
    """Surgically evict all active client connections bound to session_id (M28).

    Closes transports, cleans up connection descriptors, and reclaims capacity
    without affecting connections belonging to other active clinical sessions.
    """
```

### Invariants:
1. **Surgical Removal**: Iterates over `list(self._connections.items())` and identifies connections where `conn.session is not None and conn.session.session_id == session_id`.
2. **State Cleanup**:
   - Removes connection from `self._connections`.
   - Flushes pending egress queue.
   - Transitions state to `ConnectionState.CLOSED` and closes underlying transport (`conn.close()`).
3. **Capacity Reclamation**: Eviction immediately decrements the count of connections matching `session_id`, restoring `MAX_CONNECTIONS_PER_SESSION = 4` capacity.
4. **Non-Session State Preservation**: Connections belonging to other active sessions remain untouched in `ConnectionState.ACTIVE`.
5. **No Global Clear**: `self.clear()` or `self.stop()` is strictly prohibited during session eviction.
6. **Return Value**: Returns `True` if one or more connections were evicted, `False` if no connections were registered for `session_id`.

---

## 8. CONNECTION STATE INVENTORY

Complete audit of mutable state in `GatewayService`:

| Structure | Scope | M28 Eviction Handling |
|---|---|---|
| `_connections: Dict[str, GatewayConnection]` | Session-owned (via `conn.session.session_id`) | **EVICTED**: Popped and closed for matching `session_id` |
| `_pending_connections: List[GatewayConnection]` | Pre-authentication (no session attached) | Retained (unauthenticated handshakes in progress) |
| `_total_messages_routed: int` | Process-global telemetry | Retained (monotonic diagnostic counter) |
| `_in_transaction: bool` | Process-global reentrancy guard | Managed per-invocation (reset in `finally`) |
| `GatewayConnection._egress_queue` | Connection-owned | Cleared on `conn.close()` |
| `GatewayConnection._parser` | Connection-owned | Reset on `conn.close()` |
| `GatewayConnection._transport` | Connection-owned | Closed on `conn.close()` |

---

## 9. M28 TEARDOWN INTEGRATION

In `python/holomed/execution/service.py`, extend `ClinicalExecutionGatewayService`:
1. `__init__`: Accept `gateway_service: Optional[Any] = None` and store as `self._gateway_service`.
2. `execute_session_teardown()`: Add **Step 11: Gateway Connections** at the topological position following Step 8 (Workflow) and Step 9 (Gateway Cache):

```
Step 1:  Navigation
Step 2:  Proximity (M26)
Step 3:  Drift (M26)
Step 4:  Recovery
Step 5:  Registration
Step 6:  Planning
Step 7:  Safety Gate
Step 8:  Workflow (M27 Interlocks & Checkpoints)
Step 9:  Execution Gateway Cache (_latest_results, _persisted_states)
Step 10: Platform Session (M25)
Step 11: Gateway Ingress Connections (M28)
```

Teardown failures in `gateway_service` are appended to `failures` list and aggregated into `subsystems_purged`, adhering to M25 best-effort completion semantics.

---

## 10. CONNECTION ISOLATION

Given:
- Connection A established for `session_id="SESS-A"`, `client_id="client_a"`.
- Connection B established for `session_id="SESS-B"`, `client_id="client_b"`.

When `execute_session_teardown(session_id="SESS-A")` is executed:
1. Connection A is removed from `_connections` and closed.
2. Connection B remains in `_connections` in `ConnectionState.ACTIVE`.
3. Broadcast messages and XR frames for Session B are delivered exclusively to Connection B.
4. Connection A receives zero bytes and cannot send commands.
5. Connection A consumes zero capacity towards `MAX_CONNECTIONS_PER_SESSION`.

---

## 11. CAPACITY RECLAMATION

Gateway capacity constraint:
`MAX_CONNECTIONS_PER_SESSION = 4` (`python/holomed/gateway/models.py:15`).

M28 guarantees:
1. Up to 4 connections can be established for Session A.
2. A 5th connection attempt raises `GatewayCapacityError`.
3. Teardown of Session A purges all 4 connections.
4. Subsequent reconnection to Session A succeeds immediately without `GatewayCapacityError`.
5. Session B connection count is completely independent.

---

## 12. STALE CONNECTION SECURITY

After teardown of Session A:
1. Stale Connection A cannot submit commands for Session A (transport closed, connection inactive).
2. Stale Connection A cannot submit commands for Session B (transport closed; even if spoofed before close, rejected by session binding).
3. Stale Connection A cannot receive Session B broadcasts or XR presentation frames.
4. Reusing Session A ID creates fresh connections that do not inherit stale buffer or queue state.

---

## 13. DURABLE PERSISTENCE BOUNDARY

- Historical durable audit logs and session journals on disk (`python/holomed/persistence/`) are immutable regulatory records (FDA 21 CFR Part 11).
- `DurableSessionStore._sessions` stores historical session descriptors and MUST NOT be deleted during teardown.
- M28 is strictly confined to ephemeral runtime gateway connection state. No changes to persistence storage or journal retention are authorized.

---

## 14. REPLAY / ENVELOPE INTEGRITY

- Cross-session replay: Intercepted envelopes from Session A replayed against Session B are rejected at gateway authorization because `payload["session_id"] != connection.session.session_id`.
- Within-session replay: `GatewayConnection.validate_sequence_number()` enforces strict monotonic increasing sequence numbers from client.
- Replay against stopped session: Rejected by downstream lifecycle state checks.

---

## 15. FAILURE SEMANTICS

| Scenario | Behavior | Result | Target Mutation |
|---|---|---|---|
| `payload["session_id"] != session.session_id` | `GatewaySessionMismatchError` | Envelope rejected before dispatch | None |
| Missing `session_id` on session-required command | Passes gateway authorization, rejected by route handler | `ERR_INVALID_ARGS` | None |
| Malformed `session_id` | Validated during handshake or envelope deserialization | `ERR_VALIDATION` | None |
| Reentrant `evict_session` | Guarded by transaction guard | Fails safely | None |
| Partial teardown failure in gateway | Captured in `failures` list | Teardown marked degraded, other steps complete | Isolated |

---

## 16. FROZEN BOUNDARIES

The following contracts remain 100% frozen:
- M18 Safety Gate precedence and evaluation tables.
- M19–M24 Clinical execution algorithms and dual-gate validation.
- M25–M27 Session eviction across Navigation, Proximity, Drift, Recovery, Registration, Planning, Safety Gate, Workflow, and Platform.
- Workflow phase transitions, interlocks, and checkpoint evaluation.
- Protocol codec, models, and serialization schemas.

---

## 17. REQUIRED HOSTILE TEST SPECIFICATION

Test file: `tests/unit/gateway/test_m28_gateway_ingress_lifecycle.py`

### Mandatory Test Matrix (19 Tests):
1. `test_cross_session_payload_injection_rejected`: Authenticated Session A client sending `workflow.transition` for Session B is rejected with `ERR_SESSION_MISMATCH`.
2. `test_cross_session_tool_invoke_rejected`: Spoofed `execution.tool.invoke` targeting Session B is rejected before reaching tool executor.
3. `test_cross_session_teardown_rejected`: Spoofed `execution.session.teardown` targeting Session B cannot destroy Session B runtime state.
4. `test_same_session_payload_allowed`: Client for Session A specifying `payload["session_id"] == "SESS-A"` executes cleanly.
5. `test_omitted_session_id_on_global_routes`: Global queries (`gateway.status`, `gateway.clients`) without `session_id` in payload pass authorization.
6. `test_malformed_payload_session_id`: Non-string or mismatched type `session_id` rejected.
7. `test_cross_session_capability_tampering_fails_closed`: Replaying an envelope across sessions does not mint or invoke capabilities.
8. `test_replayed_envelope_rejected_by_sequence`: Replaying an older sequence number is rejected by `GatewayConnection`.
9. `test_connection_capacity_limit_enforced`: 4 connections succeed, 5th raises `GatewayCapacityError`.
10. `test_session_eviction_closes_all_session_connections`: Calling `evict_session("SESS-A")` closes all Session A connections.
11. `test_session_eviction_preserves_foreign_connections`: Calling `evict_session("SESS-A")` leaves Session B connections active.
12. `test_capacity_reclaimed_after_eviction`: After eviction of 4 connections, 4 new connections can be established for the same `session_id`.
13. `test_evicted_connection_cannot_receive_xr_frames`: XR presentation frames are not delivered to evicted connections.
14. `test_evicted_connection_cannot_receive_workflow_broadcasts`: Workflow broadcast events are not delivered to evicted connections.
15. `test_evicted_connection_cannot_submit_commands`: Evicted connection transport is closed; cannot send ingress data.
16. `test_teardown_step11_integration`: `ClinicalExecutionGatewayService.execute_session_teardown()` invokes `gateway_service.evict_session()`.
17. `test_teardown_failure_aggregation`: An exception during gateway eviction is recorded in `failures` without halting teardown.
18. `test_reentrant_gateway_eviction_guard`: Reentrant call to `evict_session()` fails safely under transaction guard.
19. `test_m25_regression_pass`: Existing M25 session teardown tests continue to pass 100%.

---

## 18. VERIFICATION PROTOCOL

```bash
# 1. Run M28 Ingress Lifecycle Suite
python -m pytest tests/unit/gateway/test_m28_gateway_ingress_lifecycle.py -q -ra

# 2. Run M25 Teardown Regression Suite
python -m pytest tests/unit/execution/test_m25_session_teardown.py -q -ra

# 3. Run M26 Perceptual Lifecycle Suite
python -m pytest tests/unit/execution/test_m26_perceptual_lifecycle.py -q -ra

# 4. Run M27 Workflow Interlock Lifecycle Suite
python -m pytest tests/unit/execution/test_m27_workflow_interlock_lifecycle.py -q -ra

# 5. Run Full Platform Regression
python -m pytest -q -ra

# 6. Verify Clean Git Diff
git diff --check
git status --short
```

---

## 19. RELEASE GATE CLASSIFICATION

The post-implementation audit will yield one of:
- `M28_PRECOMMIT_PASS`
- `M28_PRECOMMIT_BLOCKED`
- `M28_TEST_COVERAGE_INSUFFICIENT`

Only `M28_PRECOMMIT_PASS` authorizes commit and push in a subsequent step.
