# Sequence 4 Closure Audit

This document accounts for the final changes made to achieve closure for M49.3.5 Phase 3.2 Sequence 4.

## 1. Production Timeout Durability (Blocker A)
- **`manager.py`**: Fixed `durably_record_terminal_state` to properly stringify `CommandState` enums using `.value` instead of `str(state)`.
- **`manager.py`**: Fixed a test-seam threading deadlock in `trigger_test_timeout` by correctly isolating the lock check.
- **`test_m49_phase4_sequence4.py`**: Removed all invalid manual invocations of `store.record_operation_terminated` that were bypassing the production timeout path.
- **`test_m49_phase4_sequence4.py`**: Appended a regression proof `test_production_timeout_durable_outcome` to prove that `DeviceControlManager._timeout_loop` effectively persists `FAULTED_UNKNOWN` while legitimately retaining physical capacity.

## 2. Stale-Epoch Rejection Proof (Blocker B)
- **`exceptions.py`**: Added the `StaleEpochError` type (previously missing/untracked in the original Sequence 4 state).
- **`daemon.py`**: Upgraded `_resolve_terminal_record` to explicitly evaluate `controller_epoch < self._session_store._epoch_id` (the canonical admission epoch against current epoch), increment the `self.stale_epoch_rejections` counter, and reject via `StaleEpochError` (which is safely caught in the loop).
- **`test_m49_phase4_sequence4.py`**: `test_restart_durable_outcome_evidence_end_to_end` now strictly asserts `daemon2.stale_epoch_rejections == 1`, proving that Stale E1 events are explicitly dropped for being stale (and not due to generic exceptions).
- **`test_m49_phase3_sequence3.py` (Unexplained File Resolution)**: Sequence 3's `test_real_g8_telemetry_trust_path` previously asserted that *Historical E1 Telemetry is ACCEPTED* (a Sequence 3 rule that conflicted with Sequence 4's strict Stale-Epoch Rejection). This file was updated to assert that historical telemetry is indeed REJECTED by the `ReconciliationDaemon` under Sequence 4 rules, resolving the regression correctly while adhering to the capacity retention guarantee.

## 3. Test File Integrity
- No instances of `time.sleep` (0 matches).
- No instances of `_get_lock` (0 matches).
- No instances of `_get_or_create_record` (0 matches).
- No instances of `_resolve_terminal_record` (0 matches).
- No raw direct tests overriding `store.record_operation_terminated` (all legitimate remaining usages are mock hooks or initialization bindings).

## 4. Pyright Provenance
Baseline Errors: 340
Final Errors: 340
Baseline Warnings: 1
Final Warnings: 1
New Errors = 0
New Warnings = 0

**Closure Declared.**
