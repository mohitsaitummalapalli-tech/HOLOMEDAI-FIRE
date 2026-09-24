# M49.3.5 Phase 3 Implementation Map

This document maps the sealed Phase 3.1 architecture to concrete implementation tasks, explicitly detailing the files, functions, dependency order, migration impact, and required tests.

## Sequence 1: Durable Recovery/Rehydration Primitives

**Architecture Link:** Section 8 (Restart/Recovery Flow), Section 9A (Controller Restart), Section 12 (Recovery Behavior).

**Objective:** Enable the system to safely read existing journals on boot, recover active canonical identities, truncate corrupted tails, and hold retained capacity.

**Target Files & Functions:**
- `holomed/persistence/sessions.py`
  - Modify: `restore_session_from_disk()` to ensure it faithfully reconstructs `active_reservations` and sets up `DurableSessionRecord` correctly.
  - Modify/Add: `get_active_reservations()` or similar to expose the currently retained capacity boundaries.
- `holomed/devices/control/recovery.py` (New)
  - Add: `StateRehydrationEngine` class.
  - Add: `StateRehydrationEngine.rehydrate_controller_state(session_store)`
  - Add: `StateRehydrationEngine.rehydrate_device_state(device_id, device_epoch)`

**Dependencies:** None. This is the foundational layer.

**Migration Impact:** None (schema remains `PERSISTENCE_SCHEMA_VERSION`).

**Required Tests:**
- `test_journal_tail_failure`: Validate safe truncation of incomplete journal entries.
- `test_rehydration_active_capacity`: Validate that `ADMITTED` but not `TERMINATED` operations hold capacity across rehydration.
- `test_rehydration_terminal_capacity`: Validate that `TERMINATED` operations do not hold capacity after rehydration.

---

## Sequence 2: Recovery State Machine Integration

**Architecture Link:** Section 7 (State-Machine Changes), Section 9A/9B (Controller & Device Restart).

**Objective:** Bind the `StateRehydrationEngine` to the `DeviceControlManager` to handle transitions upon controller boot and device re-registration.

**Target Files & Functions:**
- `holomed/devices/control/manager.py`
  - Modify: `DeviceControlManager.__init__` to invoke `StateRehydrationEngine`.
  - Modify: `DeviceControlManager.register_device` to trigger Device Restart logic (quarantining active ops from old device epochs).

**Dependencies:** Sequence 1.

**Required Tests:**
- `test_restart_during_operation`: Validate that active operations are recovered as `FAULTED_UNKNOWN` if the device is unreachable/stale, and capacity is retained.
- `test_restart_during_termination`: Validate that terminated operations are fully flushed and capacity is released.
- `test_stale_device_epoch`: Validate that a device re-registering increments epoch and invalidates old operations.

---

## Sequence 3: Telemetry/Reconciliation Durable Resolution Path

**Architecture Link:** Section 8 (Telemetry Reconciliation Flow), Section 9D/9F (Telemetry Reconciliation & Terminal Resolution).

**Objective:** Bridge the `ExecutionResolutionGate` terminal state with the `DurableSessionStore` to durably append `OPERATION_TERMINATED` and release capacity strictly based on `DRIVER_ASSERTED_SOFTWARE_EVIDENCE`.

**Target Files & Functions:**
- `holomed/devices/control/daemon.py` (New)
  - Add: `ReconciliationDaemon` class.
  - Add: `ReconciliationDaemon.run_reconciliation_cycle()` (drains transport, processes via reconciler, and writes to journal).
- `holomed/devices/control/manager.py`
  - Modify: Integrate `ReconciliationDaemon` lifecycle.

**Dependencies:** Sequence 1, Sequence 2.

**Required Tests:**
- `test_duplicate_telemetry`: Validate monotonic sequence check ignores duplicates without state oscillation.
- `test_replayed_telemetry`: Validate sequence regression is ignored.
- `test_wrong_generation`: Validate telemetry with wrong generation is rejected.
- `test_terminal_resolution_capacity`: Validate that a terminal physical event successfully writes `OPERATION_TERMINATED` and releases global/endpoint capacity.

---

## Sequence 4: Timeout vs Terminal-Evidence Handling

**Architecture Link:** Section 9C (Operation Timeout), Section 14 (Evidence Requirements).

**Objective:** Ensure that control plane timeouts map to `FAULTED_UNKNOWN` (quarantine) and NEVER release physical capacity, as they do not constitute physical termination evidence.

**Target Files & Functions:**
- `holomed/devices/resolution.py`
  - Ensure: `resolve_timeout` correctly flags `quarantine_consequence=True` and `timeout_status=True`.
- `holomed/devices/control/daemon.py`
  - Ensure: Timeouts resolved from the gate are mapped to `FAULTED_UNKNOWN` in the journal, explicitly NOT releasing capacity.

**Dependencies:** Sequence 3.

**Required Tests:**
- `test_timeout_hardware_active`: Validate that an operation timing out while hardware might be active results in `FAULTED_UNKNOWN`, and capacity remains consumed.

---

## Sequence 5: Isolation & Reinitialization Semantics

**Architecture Link:** Section 9E (Quarantine), Section 11 (Failure Matrix: Isolation failure, Reinit failure).

**Objective:** Handle the escalation of `FAULTED_UNKNOWN` to global device `QUARANTINE` and eventual manual `MANUAL_RECOVERY_REQUIRED` or reinitialization.

**Target Files & Functions:**
- `holomed/devices/control/manager.py`
  - Modify: Add `quarantine_device(device_id)` and `recover_device(device_id)`.
  - Enforce: No new leases can be issued to a `QUARANTINED` device.

**Dependencies:** Sequence 4.

**Required Tests:**
- `test_isolation_failure`: Validate operations cannot proceed on a quarantined device.
- `test_reinit_failure`: Validate a device remains quarantined if reinitialization fails.

---

## Sequence 6: Failure Matrix & Cross-Component Integration

**Architecture Link:** Section 11 (Failure Matrix), Section 6 (Immutable Contracts).

**Objective:** Complete any remaining edge cases from the Failure Matrix. Implement the strict file-lock based `ControllerAuthorityStore` epoch mismatch rejection within `DeviceControlManager`.

**Target Files & Functions:**
- `holomed/devices/control/manager.py`
  - Ensure: Lock hierarchy (Epoch -> Admission -> Journal) is strictly maintained during admission and termination.

**Dependencies:** Sequences 1-5.

**Required Tests:**
- `test_stale_controller_epoch`: Validate rejection of admission intent if controller epoch is stale.
- `test_duplicate_command`: Validate idempotent acceptance of duplicate commands.
- `test_wrong_endpoint` / `test_wrong_device` / `test_wrong_lease` / `test_wrong_identity`: Validate parameter spoofing rejection.
- `test_rollover_during_admission` & `test_rollover_during_execution`: Validate TOCTOU races with epoch rollovers.
- `test_cross_process_contention`: Validates serialization via OS file lock.

---

## Sequence 7: Adversarial Verification & Integrity Audit

**Architecture Link:** Section 20 (Acceptance Gates).

**Objective:** Execute the full failure matrix test suite and the baseline-relative Pyright audit.

**Target Files & Functions:**
- `tests/unit/devices/control/test_m49_failure_matrix.py` (New or expanded)
- `tests/unit/persistence/test_m49_recovery.py` (New or expanded)

**Dependencies:** Sequences 1-6.

## Pre-Implementation Verification
- No missing contract items identified.
- No arbitrary modules invented; `recovery.py` and `daemon.py` directly map to architectural components requested in Phase 3.1.
- Lock boundaries, identity, and evidence distinctions explicitly noted in test requirements.
