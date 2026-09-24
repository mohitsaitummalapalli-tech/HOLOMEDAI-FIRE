# M49.3.5 Phase 3 Architecture Plan

## 1. Phase 3 Objective
Complete the M49.3.5 physical device control closure by implementing full failure recovery, robust telemetry reconciliation, strict physical state isolation, and comprehensive restart behaviors that strictly enforce the immutable Phase 2 contracts (canonical identity, lock hierarchies, and evidence differentiation).

## 2. Evidence-Model Contract
Phase 3 must strictly preserve the sealed Phase 2 evidence model differentiation:
```text
simulation
→ DRIVER_ASSERTED_SOFTWARE_EVIDENCE

future physical hardware integration
→ HARDWARE_AUTHENTICATED_EVIDENCE
```
Since physical hardware integration is explicitly out of scope for Phase 3, the system will continue to rely on the simulation domain. Therefore, capacity release and terminal state resolution currently require `DRIVER_ASSERTED_SOFTWARE_EVIDENCE`. Phase 3 will not mandate `HARDWARE_AUTHENTICATED_EVIDENCE` for capacity release, as this would create an unsatisfiable requirement.

## 3. Explicit In-Scope Functionality
- Reinitialization and state recovery of devices upon controller restart.
- Handling of control plane timeouts vs terminal telemetry events.
- Implementation of the explicit Failure Matrix contracts.
- Integration of `ExecutionResolutionGate` with the durable persistence layer.
- Capacity release driven strictly by `DRIVER_ASSERTED_SOFTWARE_EVIDENCE` from the simulated adapter or safe fallback timeout quarantines.

## 4. Explicit Out-of-Scope Functionality
- New hardware drivers or actual physical adapters.
- Hardware-authenticated evidence requirements for current capacity release.
- Modifications to the immutable Phase 2 semantics.

## 5. Existing Dependencies
- M49.3.5 Phase 2 Baseline (`7638fb414a8858bf1702376a0bcf05b7dc22a13c`)
- `ExecutionResolutionGate` (`holomed/devices/resolution.py`)
- `TelemetryReconciler` (`holomed/devices/reconciler.py`)
- `DurableSessionStore` and `JournalWriter` (`holomed/persistence/sessions.py`, `holomed/persistence/journal.py`)
- `ControllerAuthorityStore` (`holomed/persistence/authority.py`)

## 6. Immutable Phase 2 Contracts
Phase 3 MUST strictly preserve these established contracts:

**Canonical Physical Identity:**
```text
(device_id,
 device_epoch,
 controller_epoch,
 physical_operation_id,
 command_nonce)
```

**Lock Hierarchy:**
```text
EPOCH AUTHORITY LOCK
→ GLOBAL PHYSICAL ADMISSION LOCK
→ SESSION JOURNAL LOCK
```

**Evidence Differentiation:**
```text
DRIVER_ASSERTED_SOFTWARE_EVIDENCE
≠
HARDWARE_AUTHENTICATED_EVIDENCE
```

## 7. New Architectural Components & Repository Impact
- `holomed/devices/control/recovery.py` (New): Hosts `StateRehydrationEngine` to recover controller state from durable journals.
- `holomed/devices/control/daemon.py` (New): Hosts `ReconciliationDaemon` bridging terminal events to `DurableSessionStore`.
- `holomed/devices/control/manager.py` (Updated): Integrates recovery and reconciliation.
- `holomed/persistence/sessions.py` (Updated): Exposes locked recovery APIs.

## 8. Data/Control-Flow Diagrams

**Restart/Recovery Flow:**
```text
CONTROLLER RESTART
        ↓
ControllerAuthorityStore: allocate_next_epoch(E+1)
        ↓
DurableSessionStore: read_and_recover_journal()
        ↓
Identify Active Canonical Identities (E)
        ↓
Attempt Reconnection/Telemetry Sync with Devices
        ↓
Resolve orphaned operations -> FAULTED_UNKNOWN / QUARANTINE
```

## 9. State-Machine Contracts

### A. Controller Restart
- **Predecessor States:** Any (Crash/Halt)
- **Successor States:** `RECOVERING` -> `ACTIVE`
- **Forbidden Transitions:** Proceeding to `ACTIVE` without allocating new `controller_epoch` and rehydrating journal.
- **Durable Record:** Epoch lock allocation (`controller_epoch.json`).
- **Authority Requirement:** `ControllerAuthorityStore` exclusive lock.
- **Capacity Effect:** Active capacity counts are fully restored from journals.
- **Evidence Requirement:** `DurableSessionStore` valid journal prefix.

### B. Device Restart
- **Predecessor States:** `ACTIVE`
- **Successor States:** `QUARANTINED` or `REINITIALIZING`
- **Forbidden Transitions:** `ACTIVE` -> `ACTIVE` without epoch increment.
- **Durable Record:** Telemetry termination of active commands.
- **Authority Requirement:** Device Authority (`device_epoch`).
- **Capacity Effect:** Retained until all active commands linked to old `device_epoch` are terminally resolved (or quarantined).
- **Evidence Requirement:** `DRIVER_ASSERTED_SOFTWARE_EVIDENCE`.

### C. Operation Timeout
- **Predecessor States:** `ADMITTED`, `RUNNING`
- **Successor States:** `FAULTED_UNKNOWN`
- **Forbidden Transitions:** Timeout resolving to `COMPLETED` or capacity-released without quarantine.
- **Durable Record:** `OPERATION_TERMINATED` with `resolution="FAULTED_UNKNOWN"`.
- **Authority Requirement:** Control Plane (Timeout).
- **Capacity Effect:** Retained indefinitely.
- **Evidence Requirement:** Control plane clock.

### D. Telemetry Reconciliation
- **Predecessor States:** `ADMITTED`, `RUNNING`
- **Successor States:** `COMPLETED`, `FAILED`, `INTERLOCKED`, `PREEMPTED`
- **Forbidden Transitions:** Regression of `event_sequence`. Cross-generation mutation.
- **Durable Record:** `OPERATION_TERMINATED`.
- **Authority Requirement:** Telemetry Authority (`event_sequence`).
- **Capacity Effect:** Released (if terminal and not quarantined).
- **Evidence Requirement:** `DRIVER_ASSERTED_SOFTWARE_EVIDENCE`.

### E. Quarantine
- **Predecessor States:** `FAULTED_UNKNOWN`, `INTERLOCKED`
- **Successor States:** `MANUAL_RECOVERY_REQUIRED`
- **Forbidden Transitions:** `QUARANTINE` -> `ACTIVE` without explicit operator intervention.
- **Durable Record:** Device-level quarantine flag.
- **Authority Requirement:** Control Plane / Operator.
- **Capacity Effect:** Locks endpoint capacity.
- **Evidence Requirement:** Operator authenticated evidence.

### F. Terminal Resolution
- **Predecessor States:** `RUNNING`
- **Successor States:** `COMPLETED`
- **Forbidden Transitions:** `COMPLETED` -> `FAILED` (Once terminal, always terminal).
- **Durable Record:** `OPERATION_TERMINATED` (in session journal).
- **Authority Requirement:** `GLOBAL PHYSICAL ADMISSION LOCK`.
- **Capacity Effect:** Decrements global and endpoint physical capacity by 1.
- **Evidence Requirement:** `DRIVER_ASSERTED_SOFTWARE_EVIDENCE`.

### G. FAULTED_UNKNOWN Lifecycle
- **Definition:** Represents a state where the control plane has lost track of the physical operation's actual state (e.g. timeout, controller crash, device epoch rollover) and cannot safely assume it has terminated.
- **Persisted/Transient:** Strictly persisted as a terminal resolution in the session journal.
- **Terminality:** It is technically a terminal software state (the operation tracking is ended), BUT it implies an unknown physical state.
- **Capacity-Holding:** Retains physical capacity globally and locally on the endpoint. Does NOT release capacity.
- **Ownership:** Control Plane (Wait/Timeout) -> Manual Operator / Quarantine System.
- **Legal Transitions:** 
  - `RUNNING` / `ADMITTED` -> `FAULTED_UNKNOWN`
  - `FAULTED_UNKNOWN` -> `QUARANTINED` -> `MANUAL_RECOVERY_REQUIRED`
- **Evidence Requirements:** Control plane timeout, epoch mismatch, or controller crash (process-level evidence), rather than device telemetry.

## 10. Failure Matrix Implementation Contracts

| Trigger | Detection Boundary | Current State | Required Next State | Durable Evidence | Capacity Effect | Physical Ownership Effect | Recovery Action | Retry/Idempotency | Test |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Stale controller epoch** | Persistence layer | ADMITTED (intent) | REJECTED | None | None | None | Discard command | Return error | `test_stale_controller_epoch` |
| **Stale device epoch** | Endpoint submission | ADMITTED (intent) | REJECTED | None | None | None | Discard command | Return error | `test_stale_device_epoch` |
| **Duplicate command** | Persistence lock | ADMITTED (intent) | ADMITTED (idempotent) | Single `OPERATION_ADMITTED` | Consumed once | Owned once | None | Return success (idempotent) | `test_duplicate_command` |
| **Duplicate telemetry** | Resolution Gate | RUNNING | RUNNING | First event | None | Retained | None | Ignored (monotonic seq check) | `test_duplicate_telemetry` |
| **Replayed telemetry** | Resolution Gate | COMPLETED | COMPLETED | First event | None | Released | None | Ignored (seq regression) | `test_replayed_telemetry` |
| **Wrong endpoint** | Persistence layer | ADMITTED (intent) | REJECTED | None | None | None | Reject | Return error | `test_wrong_endpoint` |
| **Wrong device** | Persistence layer | ADMITTED (intent) | REJECTED | None | None | None | Reject | Return error | `test_wrong_device` |
| **Wrong lease** | Control Manager | PENDING | REJECTED | None | None | None | Reject | Return error | `test_wrong_lease` |
| **Wrong generation** | Resolution Gate | RUNNING | REJECTED | None | None | None | Reject | Ignored | `test_wrong_generation` |
| **Wrong op identity** | Control Manager | PENDING | REJECTED | None | None | None | Reject | Return error | `test_wrong_identity` |
| **Restart during op** | Control Manager (boot) | RUNNING | FAULTED_UNKNOWN | `OPERATION_TERMINATED` (Faulted) | Retained | Quarantined | Reconcile / Quarantine | Manual operator | `test_restart_during_operation` |
| **Restart during term** | Control Manager (boot) | TERMINATED | TERMINATED | `OPERATION_TERMINATED` | Released | None | Rehydrate terminal | Idempotent skip | `test_restart_during_termination` |
| **Timeout active hw** | Resolution Gate | RUNNING | FAULTED_UNKNOWN | `OPERATION_TERMINATED` (Faulted) | Retained | Quarantined | Hardware quarantine | Manual operator | `test_timeout_hardware_active` |
| **Controller rollover during admission** | Persistence lock | ADMITTED (intent) | REJECTED | None | None | None | Fail admission | Reject | `test_rollover_during_admission` |
| **Controller rollover during execution** | Control Manager | RUNNING | FAULTED_UNKNOWN | `OPERATION_TERMINATED` (Faulted) | Retained | Quarantined | Hardware quarantine | Manual operator | `test_rollover_during_execution` |
| **Device epoch rollover during execution** | Control Manager | RUNNING | FAULTED_UNKNOWN | `OPERATION_TERMINATED` (Faulted) | Retained | Quarantined | Hardware quarantine | Manual operator | `test_device_rollover` |
| **Capacity exhaustion** | Persistence layer | PENDING | REJECTED | None | None | None | Fail admission | Return error | `test_capacity_exhaustion` |
| **Cross-process contention**| OS file lock | PENDING | ADMITTED (serialized) | Sequenced | Serialized | Serialized | OS retry/block | Handled by OS lock | `test_cross_process_contention` |
| **Journal corruption** | Persistence layer | ANY | HALT | Truncated/Garbage | Locked | Locked | Process crash | Manual operator | `test_journal_corruption` |
| **Journal tail failure** | Persistence layer | ANY | TRUNCATED | Valid prefix | Rolled back | Rolled back | Safe truncation | Auto-recovery | `test_journal_tail_failure` |
| **Fsync failure** | Persistence layer | PENDING | HALT | OS cache only | None | None | Process crash | Process restart | `test_fsync_failure` |
| **Isolation failure** | Persistence layer | PENDING | HALT | None | None | None | Process crash | Process restart | `test_isolation_failure` |
| **Reinit failure** | Control Manager | REINITIALIZING | QUARANTINED | None | Locked | Quarantined | Hardware quarantine | Manual operator | `test_reinit_failure` |

## 11. Test Strategy & Pyright Gate
- The Pyright gate is baseline-relative: `new Phase 3 errors = 0` compared to baseline diagnostic count. We do not claim absolute zero type errors.
- Adversarial TOCTOU tests for race conditions using deterministic event logs (Phase 2 Blocker A style).
- Zero private state manipulation in tests. All states must be verified via durable evidence.

## 12. Phase 3.1 Acceptance Criteria
- Architecture document is internally consistent.
- Every in-scope failure case has a detailed state/recovery contract.
- Every affected file/module is accurately mapped to the real repository.
- Evidence semantics and capacity semantics are explicitly defined.
- Restart/recovery and concurrency boundaries are explicitly modeled.
- Migration/rollback behavior is clearly defined (fail-closed on corruption).
- Phase 3.2 implementation scope is finite and exact.

## 13. Phase 3 Milestones
- **Phase 3.1** Architecture / contract seal
- **Phase 3.2** Core implementation (Rehydration & Telemetry-Persistence bridge)
- **Phase 3.3** Adversarial verification (Failure Matrix tests)
- **Phase 3.4** Recovery / restart verification
- **Phase 3.5** Integration verification
- **Phase 3.6** Integrity audit
- **Phase 3.7** Repository closure
