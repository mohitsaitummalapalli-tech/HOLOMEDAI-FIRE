# M26 FINAL FEASIBILITY REPORT — PERCEPTUAL MONITORING LIFECYCLE & SESSION EVICTION HARDENING

**Authoritative Baseline**: `16c5121ecaaae714b62ebe8afd763fa36d938de9`  
**Previous Milestone**: M25 — Coordinated Clinical Session Teardown & Lifecycle Invalidation (FROZEN)  
**Feasibility Mode**: READ-ONLY / HOSTILE AUDIT  
**Classification**: `READY_FOR_LOCK`  

---

## 1. Authoritative Ownership & Architecture

### Finding:
In HoloMed AI, perceptual monitoring is divided between two services:
1. `ProximityService` (M15): Tracks instrument proximity relative to planned safety exclusion zones, computes static and dynamic margin inflation, and detects exclusion zone breaches.
2. `DriftService` (M16): Tracks patient tracker and anatomical landmark stability, calculates spatial drift, and manages dwell stability buffers.

Both services are authoritative primary evidence sources directly queried by `SafetyGateEvaluator.evaluate()` during clinical operations.

### Coordination Authority:
Under the unified execution gateway architecture established in M19–M25, `ClinicalExecutionGatewayService` is the sole authoritative coordinator for synchronous session teardown (`execution.session.teardown`).
M25 established the coordinated teardown pattern across M14 Navigation, M17 Recovery, M13 Registration, M12 Planning, M18 Safety Gate, M10 Workflow, and M09 Platform.
`ProximityService` and `DriftService` are clinical perceptual services with session-scoped lifecycles that strictly belong to this coordination domain. `ClinicalExecutionGatewayService` is the correct and only appropriate coordinator for their session-scoped eviction.

---

## 2. Complete M15 ProximityService State Inventory

Audit of `python/holomed/proximity/service.py`:

| Field Name | Type / Key Structure | Creation Point | Mutation Point | Read Point | Safety Relevance |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `_session_states` | `Dict[str, ProximityState]` | `bind_zones` | `evaluate_proximity` | `get_proximity_status` | **CRITICAL**: Read by Safety Gate Precedence 1 |
| `_monitored_zones` | `Dict[str, Tuple[SafetyExclusionZone, ...]]` | `bind_zones` | `bind_zones` | `get_monitored_zones` | Boundary geometry definition |
| `_registration_errors` | `Dict[str, float]` | `bind_zones` | `bind_zones` | `evaluate_proximity` | Margin calculation |
| `_static_margins` | `Dict[str, float]` | `bind_zones` | `bind_zones` | `evaluate_proximity` | Margin calculation |
| `_latest_geometries` | `Dict[Tuple[str, str], ToolClearanceGeometry]` | `submit_geometry` | `submit_geometry` | `evaluate_proximity` | Composite key `(session_id, instrument_id)` |
| `_latest_sequences` | `Dict[Tuple[str, str], int]` | `submit_geometry` | `submit_geometry` | `submit_geometry` | Composite key `(session_id, instrument_id)` |
| `_latest_evaluations` | `Dict[str, ProximityEvaluationRecord]` | `evaluate_proximity` | `evaluate_proximity` | `get_proximity_status` | Evidence record |
| `_active_instruments` | `Dict[str, str]` | `submit_geometry` | `submit_geometry` | `get_proximity_status` | Active instrument identification |
| `_clearance_history` | `Dict[Tuple[str, str], Tuple[float, str]]` | `evaluate_proximity` | `evaluate_proximity` | `evaluate_proximity` | Composite key `(session_id, zone_id)` |

**Capacity Constraint**: `len(_session_states) >= MAX_ACTIVE_PROXIMITY_SESSIONS (32)` raises `ProximityCapacityError`.  
**Current Eviction**: ZERO. No `evict_session()` exists.

---

## 3. Complete M16 DriftService State Inventory

Audit of `python/holomed/drift/service.py`:

| Field Name | Type / Key Structure | Creation Point | Mutation Point | Read Point | Safety Relevance |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `_session_states` | `Dict[str, DriftState]` | `bind_landmarks` | `evaluate_drift` | `get_drift_status` | **CRITICAL**: Read by Safety Gate Precedence 4 |
| `_landmarks` | `Dict[str, Dict[str, LandmarkDefinition]]` | `bind_landmarks` | `bind_landmarks` | `get_landmarks` | Planned landmark coordinates |
| `_latest_sequences` | `Dict[str, int]` | `submit_observation` | `submit_observation` | `submit_observation` | Monotonic sequence validation |
| `_verified_landmarks` | `Dict[str, Set[str]]` | `bind_landmarks` | `evaluate_drift` | `get_drift_status` | Verified landmark tracking |
| `_latest_verifications` | `Dict[str, LandmarkVerificationRecord]` | `evaluate_drift` | `evaluate_drift` | `get_drift_status` | Verification snapshot |
| `_dwell_buffers` | `Dict[Tuple[str, str], List[LandmarkObservation]]` | `submit_observation`| `submit_observation`| `evaluate_drift` | Composite key `(session_id, landmark_id)` |

**Capacity Constraint**: `len(_session_states) >= MAX_ACTIVE_DRIFT_SESSIONS (16)` raises `DriftCapacityError`.  
**Current Eviction**: ZERO. No `evict_session()` exists.

---

## 4. Concrete Safety Contamination Path Proof

Source-level trace proving false-positive safety lockout:

1. **Step 1 — Zone Breach in Session A**:
   `ProximityService.evaluate_proximity("SESS-A", ...)` detects instrument inside an exclusion zone.
   `_session_states["SESS-A"]` transitions to `ProximityState.CRITICAL_BREACH`.
2. **Step 2 — Teardown of Session A**:
   Client calls `execution.session.teardown` with `session_id="SESS-A"`.
   `ClinicalExecutionGatewayService` evicts Navigation, Recovery, Registration, Planning, Safety Gate, Workflow, and Platform.
   **`ProximityService` is untouched.** `_session_states["SESS-A"]` remains `ProximityState.CRITICAL_BREACH`.
3. **Step 3 — Reusing Session A**:
   A new surgical case starts with `session_id="SESS-A"`.
   Platform, workflow, registration, and navigation start clean.
4. **Step 4 — Safety Gate Evaluation**:
   Client attempts navigation or tool execution.
   Gateway calls `SafetyGateService.evaluate()`.
   `SafetyGateEvaluator.evaluate()` invokes:
   ```python
   prox_status = proximity_service.get_proximity_status("SESS-A")
   # Returns status with state == "CRITICAL_BREACH"
   ```
5. **Step 5 — False Denial**:
   `SafetyGateEvaluator.py` line 254:
   ```python
   if m15_state in ("CRITICAL_BREACH", "INTERLOCKED"):
       return GateStatusRecord(
           session_id="SESS-A",
           decision=GateDecision.DENIED_CRITICAL,
           severity=GateSeverity.CRITICAL,
           reason_code=GateReasonCode.CRITICAL_EXCLUSION_ZONE_BREACH,
           ...
       )
   ```
   **The new surgical procedure is blocked from navigating or actuating tools.** The denial is a false positive caused entirely by unpurged residual state.

---

## 5. Capacity Attack Proof

1. **Drift 16-Session Lockout**:
   - `python/holomed/drift/constants.py`: `MAX_ACTIVE_DRIFT_SESSIONS = 16`.
   - In 16 sequential operations, landmarks are bound via `bind_landmarks()`.
   - Each session is terminated through `execution.session.teardown`.
   - Session 17 calls `bind_landmarks("SESS-017", ...)`:
     ```python
     if session_id not in self._session_states and len(self._session_states) >= MAX_ACTIVE_DRIFT_SESSIONS:
         raise DriftCapacityError(f"Max active drift sessions ({MAX_ACTIVE_DRIFT_SESSIONS}) exceeded")
     ```
   - **Result**: `DriftCapacityError` halts session 17. The entire landmark tracking subsystem is locked out until process restart.
2. **Proximity 32-Session Lockout**:
   - `MAX_ACTIVE_PROXIMITY_SESSIONS = 32`.
   - Session 33 calls `bind_zones("SESS-033", ...)`: raises `ProximityCapacityError`.

---

## 6. Session-ID Reuse Attack Proof

When a session ID is reused after teardown:
- In `ProximityService`:
  - Old `_monitored_zones` persist.
  - Old `_registration_errors` and `_static_margins` persist.
  - Old `_latest_geometries` and `_latest_sequences` persist under `(session_id, instrument_id)`.
  - Old `_clearance_history` persists under `(session_id, zone_id)`.
  - New observation with low sequence number is rejected by `_latest_sequences` check.
- In `DriftService`:
  - Old `_landmarks` persist.
  - Old `_dwell_buffers` persist under `(session_id, landmark_id)`.
  - New dwell observations are polluted with previous patient's landmark data.
  - Sequence numbers < old sequence number are rejected.

---

## 7. M25 / M26 Boundary & Extension Architecture

M26 will follow **Architecture Option A (Additive Lifecycle Extension)**:
- Extend M25's `ClinicalExecutionGatewayService.execute_session_teardown` to coordinate eviction in M15 and M16.
- Do NOT alter any existing M25 eviction hook or semantics for the existing 7 subsystems.
- Do NOT introduce a second teardown route or capability.

---

## 8. Capability Design

The existing `_ExecutionCapability` with `action="SESSION_TEARDOWN"` is fully sufficient:
- Single-use, minted per teardown transaction in `ClinicalExecutionGatewayService`.
- Bound to `session_id`.
- Passed to `ProximityService.evict_session(session_id, capability)` and `DriftService.evict_session(session_id, capability)`.
- Invalidated in the `finally:` block.

---

## 9. Minimum Reopen Set

### Reopened for Additive Implementation:
1. `python/holomed/proximity/service.py`: Add `evict_session(session_id: str, capability: Optional[Any] = None) -> bool`.
2. `python/holomed/drift/service.py`: Add `evict_session(session_id: str, capability: Optional[Any] = None) -> bool`.
3. `python/holomed/execution/service.py`: Accept optional `proximity_service` and `drift_service`, wire into `execute_session_teardown()`.
4. Test suites.

### Strictly Frozen:
- M09 Platform
- M10 Workflow
- M12 Planning
- M13 Registration
- M14 Navigation
- M17 Recovery
- M18 Safety Gate
- All math, algorithms, geometries, Ray-casting, Horn's quaternion methods, and deviation math.

---

## 10. `clear()` Invariance

Both `ProximityService` and `DriftService` have public `clear()` methods that wipe all sessions indiscriminately.
M26 guarantees:
- Runtime teardown strictly invokes `evict_session(session_id)`.
- Global `clear()` is NEVER invoked during runtime teardown.

---

## 11. Exact Topological Eviction Ordering

Teardown must proceed in strict topological dependency order:
```
1. NavigationService.evict_session(session_id)      # Leaf tool motion tracking
2. ProximityService.evict_session(session_id)       # Leaf perceptual proximity protection
3. DriftService.evict_session(session_id)           # Leaf perceptual landmark tracking
4. RecoveryService.evict_session(session_id)        # Spatial recovery candidates & authorizations
5. RegistrationService.evict_session(session_id)    # Spatial patient-to-image registration
6. PlanningService.evict_session(session_id)        # Preoperative surgical plans
7. SafetyGateService.evict_session(session_id)      # Cross-subsystem safety decisions
8. WorkflowService.evict_session(session_id)        # Clinical workflow state machine
9. Gateway Cache                                    # Gateway deduplication caches
10. PlatformService.evict_session(session_id)       # Platform session context
```

### Justification:
- `NavigationService`, `ProximityService`, and `DriftService` are operational leaf services driven by incoming sensor telemetry. They must be silenced first.
- `RecoveryService`, `RegistrationService`, and `PlanningService` provide coordinate frames and plan references to the operational services. They are purged second.
- `SafetyGateService` and `WorkflowService` govern clinical authorization and must be purged third.
- Gateway Cache and `PlatformService` hold core session identity and are purged last.

---

## 12. Restart, Reconnect & Epoch Lifecycle

- **Process Restart**: Discards all in-memory perceptual state. Clean.
- **Reconnect**: Client reconnect does not alter active perceptual tracking state.
- **Platform Session Stop**: Stops platform state; does not evict perceptual state (M25 teardown must be called).
- **Epoch Migration**: Resets all services via `reset(epoch_id)`.

---

## 13. Safety Impact Classification

| Defect | Severity | Category |
| :--- | :--- | :--- |
| `DriftService` 16-session permanent capacity lockout | **CRITICAL** | Production Reliability / Availability |
| `ProximityService` 32-session permanent capacity lockout | **CRITICAL** | Production Reliability / Availability |
| Stale `CRITICAL_BREACH` contaminating Safety Gate on reuse | **CRITICAL** | Safety Correctness / False Interlock |
| Monotonic sequence counter corruption across session reuse | **HIGH** | Protocol Consistency |
| Stale landmark dwell buffer pollution across session reuse | **HIGH** | Clinical Accuracy |

---

## 14. Scope Test & Justification

- **Is this theoretical?** No. `MAX_ACTIVE_DRIFT_SESSIONS = 16` is a concrete source constant. 17 sessions guarantee an unhandled `DriftCapacityError`.
- **Can it be postponed?** No. HoloMed AI cannot be considered reliable if 16 surgical procedures cause a permanent denial of service.
- **Is it too broad?** No. It touches exactly 2 leaf services (`ProximityService`, `DriftService`) and wires them into 1 existing gateway method (`ClinicalExecutionGatewayService.execute_session_teardown`).

---

## 15. Contract Draft Prelock

### Title:
**M26 — Perceptual Monitoring Lifecycle & Session Eviction Hardening**

### Objectives:
1. Implement `ProximityService.evict_session(session_id, capability)` purging all 9 session-keyed structures (including composite keys).
2. Implement `DriftService.evict_session(session_id, capability)` purging all 6 session-keyed structures (including composite keys).
3. Wire `proximity_service` and `drift_service` into `ClinicalExecutionGatewayService.execute_session_teardown` following the exact topological sequence: Navigation -> Proximity -> Drift -> Recovery -> Registration -> Planning -> Safety Gate -> Workflow -> Gateway Cache -> Platform Session.
4. Update `SessionTeardownExecutionResult.subsystems_purged` to include `"proximity"` and `"drift"`.
5. Maintain best-effort failure aggregation and durable audit persistence.
6. Guarantee 16-session drift capacity recovery, 32-session proximity capacity recovery, and 100% clean baseline on session ID reuse.

---

## FINAL CLASSIFICATION

```
==================================================
READY_FOR_LOCK
==================================================
```
