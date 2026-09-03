# PHASE 26 CONTRACT: Perceptual Monitoring Lifecycle & Session Eviction Hardening

**Authoritative Baseline**: `16c5121ecaaae714b62ebe8afd763fa36d938de9`  
**Status**: LOCKED CONTRACT — IMPLEMENTATION AUTHORIZATION SPECIFICATION  
**Predecessor Milestone**: M25 — Coordinated Clinical Session Teardown & Lifecycle Invalidation (FROZEN)  

---

## 1. Objective & Architecture

Extend the synchronous, coordinated session teardown protocol established in M25 to include the perceptual monitoring subsystems: `ProximityService` (M15) and `DriftService` (M16).

### Locked Architecture:
- `ClinicalExecutionGatewayService` remains the sole authoritative coordinator of session teardown.
- M26 is an **additive lifecycle extension** of M25.
- No second gateway, no asynchronous event-bus teardown, and no relocation of lifecycle ownership to M15, M16, or M09.
- Teardown is session-scoped, synchronous, capability-gated, and best-effort with aggregated failure reporting and durable persistence audit.

---

## 2. Authorized Reopen Set

Strictly restricted to:

```
python/holomed/proximity/service.py     # M15 Proximity Protection
python/holomed/drift/service.py         # M16 Landmark Drift
python/holomed/execution/service.py     # M19-M26 Clinical Execution Gateway
tests/unit/execution/test_m26_perceptual_lifecycle.py # M26 Test Suite
```

All other production packages remain strictly frozen:
- M09 Platform
- M10 Workflow
- M12 Planning
- M13 Registration
- M14 Navigation
- M17 Recovery
- M18 Safety Gate
- M04 Persistence
- M05 Client Gateway
- M07 Tools
- All perception models, algorithms, transforms, and math.

---

## 3. New Service Hooks

### ProximityService (M15)
```python
def evict_session(
    self,
    session_id: str,
    capability: Optional[Any] = None,
) -> bool:
    """Evict session-scoped proximity monitoring state and geometries, releasing capacity (M26)."""
```

### DriftService (M16)
```python
def evict_session(
    self,
    session_id: str,
    capability: Optional[Any] = None,
) -> bool:
    """Evict session-scoped landmark definitions, observations, and drift state, releasing capacity (M26)."""
```

### Protocol & Safety Guarantees:
- **Session-scoped only**: Strictly purges state associated with `session_id`. All other active sessions remain intact.
- **Reentrancy guard**: Reentrant call while `self._in_transaction == True` raises `LifecycleError`.
- **Capability authorization**: Accepts optional `capability`; rejects invalid capability if provided.
- **Return value**: Returns `True` if state was found and purged, `False` if session was not resident.
- **Zero global clear**: Must NEVER invoke `self.clear()`.
- **Zero algorithmic change**: Ray-casting, dynamic margins, dwell buffers, and displacement math remain untouched.

---

## 4. Complete M15 ProximityService Eviction Specification

`ProximityService.evict_session(session_id)` must explicitly purge:

1. `_session_states`: `del self._session_states[session_id]`
2. `_monitored_zones`: `del self._monitored_zones[session_id]`
3. `_registration_errors`: `del self._registration_errors[session_id]`
4. `_static_margins`: `del self._static_margins[session_id]`
5. `_latest_geometries`: Filter composite keys:
   ```python
   geom_keys = [k for k in self._latest_geometries if k[0] == session_id]
   for k in geom_keys:
       del self._latest_geometries[k]
   ```
6. `_latest_sequences`: Filter composite keys:
   ```python
   seq_keys = [k for k in self._latest_sequences if k[0] == session_id]
   for k in seq_keys:
       del self._latest_sequences[k]
   ```
7. `_latest_evaluations`: `del self._latest_evaluations[session_id]`
8. `_active_instruments`: `del self._active_instruments[session_id]`
9. `_clearance_history`: Filter composite keys:
   ```python
   hist_keys = [k for k in self._clearance_history if k[0] == session_id]
   for k in hist_keys:
       del self._clearance_history[k]
   ```

---

## 5. Complete M16 DriftService Eviction Specification

`DriftService.evict_session(session_id)` must explicitly purge:

1. `_session_states`: `del self._session_states[session_id]`
2. `_landmarks`: `del self._landmarks[session_id]`
3. `_latest_sequences`: `del self._latest_sequences[session_id]`
4. `_verified_landmarks`: `del self._verified_landmarks[session_id]`
5. `_latest_verifications`: `del self._latest_verifications[session_id]`
6. `_dwell_buffers`: Filter composite keys:
   ```python
   dwell_keys = [k for k in self._dwell_buffers if k[0] == session_id]
   for k in dwell_keys:
       del self._dwell_buffers[k]
   ```

---

## 6. Execution Gateway Teardown Extension

### Route & Payload Stability:
- Route remains: `execution.session.teardown`
- Request model remains: `SessionTeardownExecutionRequest(session_id, sequence_number, now_utc)`
- Response model remains: `SessionTeardownExecutionResult(session_id, execution_status, sequence_number, executed_at_utc, subsystems_purged, failures, error_message)`
- `subsystems_purged` includes `"proximity"` and `"drift"`.

### Exact Topological Teardown Ordering:
```
1.  NavigationService.evict_session(session_id, cap)   # Leaf tool motion tracking
2.  ProximityService.evict_session(session_id, cap)    # Leaf proximity protection (M26)
3.  DriftService.evict_session(session_id, cap)        # Leaf landmark drift monitoring (M26)
4.  RecoveryService.evict_session(session_id, cap)     # Spatial recovery candidates & authorizations
5.  RegistrationService.evict_session(session_id, cap) # Spatial patient-to-image registration
6.  PlanningService.evict_session(session_id, cap)     # Preoperative surgical plan bindings
7.  SafetyGateService.evict_session(session_id, cap)   # Safety gate decision records
8.  WorkflowService.evict_session(session_id, cap)     # Clinical workflow state machine
9.  Gateway Cache                                      # Gateway result & signature caches
10. PlatformService.evict_session(session_id)          # Platform session context
```

---

## 7. Capability Security

- Mint single ephemeral capability: `_create_execution_capability(service_instance_id=id(self), session_id=session_id, action="SESSION_TEARDOWN", sequence_number=sequence_number)`.
- Reused across all 8 subsystem eviction calls within the single transaction.
- Explicitly invalidated in the `finally:` block of `execute_session_teardown`.
- Replay of pre-teardown capabilities on any clinical service fails closed.

---

## 8. Safety Requirements

- Stale `CRITICAL_BREACH` or `INTERLOCKED` states in `ProximityService` must NEVER persist past teardown.
- Stale `DRIFT_EXCEEDED` or `UNSTABLE` states in `DriftService` must NEVER persist past teardown.
- Reusing a `session_id` after teardown guarantees that `SafetyGateEvaluator.evaluate()` receives clean/empty perceptual evidence, preventing false-positive interlocks.

---

## 9. Capacity Requirements

- **M16 Drift**: 16-session capacity limit (`MAX_ACTIVE_DRIFT_SESSIONS = 16`) is fully reclaimed upon teardown. Running 16 sessions with teardown permits session 17 to bind landmarks without `DriftCapacityError`.
- **M15 Proximity**: 32-session capacity limit (`MAX_ACTIVE_PROXIMITY_SESSIONS = 32`) is fully reclaimed upon teardown. Running 32 sessions with teardown permits session 33 to bind zones without `ProximityCapacityError`.

---

## 10. Failure Semantics & Audit

- **Best-effort aggregation**: Failure in `ProximityService` or `DriftService` eviction does NOT abort teardown of remaining subsystems.
- All failures are aggregated in `res.failures`.
- Durable audit persisted via `PersistenceService`:
  - `session_teardown_completed` (all succeeded -> `EXECUTED_CLEAR`)
  - `session_teardown_degraded` (partial failure -> `FAILED_NAVIGATION_GEOMETRY`)
  - `session_teardown_failed` (total failure -> `FAILED_NAVIGATION_GEOMETRY`)

---

## 11. Frozen Boundaries

The following areas are strictly frozen and MUST NOT be modified:
- Proximity Ray-casting and margin inflation algorithms (`proximity/evaluator.py`, `proximity/models.py`).
- Drift landmark displacement and dwell stability math (`drift/evaluator.py`, `drift/models.py`).
- Safety Gate precedence hierarchy and evaluation rules (`safety_gate/evaluator.py`).
- Registration Horn's quaternion algorithms and TRE/FRE calculations (`registration/*`).
- Navigation trajectory deviation algorithms (`navigation/*`).
- Workflow state machine transition tables (`workflow/*`).
- Planning verification algorithms (`planning/*`).
- All existing execution command routes and handlers.

---

## 12. Required Test Suite

Create `tests/unit/execution/test_m26_perceptual_lifecycle.py` with hostile coverage:
1. `test_m26_proximity_complete_session_eviction`: All 9 structures purged.
2. `test_m26_drift_complete_session_eviction`: All 6 structures purged.
3. `test_m26_proximity_composite_key_eviction`: Geometries, sequences, clearance history purged.
4. `test_m26_drift_composite_key_eviction`: Dwell buffers purged.
5. `test_m26_cross_session_isolation`: Teardown of Session A leaves Session B perceptual state intact.
6. `test_m26_session_id_reuse_clean_perceptual_state`: Zero residual proximity/drift evidence on reuse.
7. `test_m26_16_session_drift_capacity_reclaimed`: 16 sequential sessions with teardown permit session 17.
8. `test_m26_32_session_proximity_capacity_reclaimed`: 32 sequential sessions with teardown permit session 33.
9. `test_m26_stale_critical_breach_cannot_contaminate_reused_session`: Reused session is NOT blocked by safety gate.
10. `test_m26_stale_drift_cannot_contaminate_reused_session`: Reused session is NOT blocked by drift interlock.
11. `test_m26_partial_proximity_failure_aggregates_and_continues`: Teardown proceeds if proximity fails.
12. `test_m26_partial_drift_failure_aggregates_and_continues`: Teardown proceeds if drift fails.
13. `test_m26_reentrant_eviction_fails_closed`: Reentrancy guard verified on both services.
14. `test_m26_full_regression_compatibility`: Full test suite passes without regressions.

---

## 13. Audit & Verification Gates

1. `python -m pytest tests/unit/execution/test_m26_perceptual_lifecycle.py -q -ra`
2. `python -m pytest -q -ra` (Full regression across all milestones).
3. `git diff --check` (Zero formatting/syntax errors).
4. Authoring of:
   - `M26_IMPLEMENTATION_REPORT.md`
   - `M26_HOSTILE_AUDIT_REPORT.md`
   - `M26_FINAL_PRECOMMIT_AUDIT.md`

---

## 14. Release Gate

- Strict mode: ZERO commits during implementation.
- Final classification must be `M26_PRECOMMIT_PASS` before commit authorization.
- Exactly one atomic commit authorized upon approval:
  `feat(M26): harden perceptual monitoring lifecycle and session eviction`
- Push to `origin/main`.
