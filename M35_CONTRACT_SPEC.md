# M35 Contract Specification: Durable Persistence In-Memory Session Eviction & Capacity Reclamation

**Status**: Normative Specification
**Subsystems**: Persistence (`holomed.persistence`), Execution (`holomed.execution`)
**Milestone**: M35
**Predecessors**: M09 (Durable Journaling & Persistence), M25 (Coordinated Session Teardown), M27-M29 (Interlock, Gateway & Tool Lifecycle), M32 (Planning Lifecycle), M33 (Frozen Checkpoint Lifecycle), M34 (Frozen Gateway Dispatcher)

---

## 1. Context & Motivation

In the HoloMed clinical architecture, active clinical sessions consume in-memory state across multiple subsystems (`PlatformService`, `ToolService`, `WorkflowService`, `PlanningService`, `GatewayService`, and `PersistenceService`).
Under `MAX_DURABLE_SESSIONS = 16`, `DurableSessionStore` enforces a hard ceiling on concurrent session records maintained in memory.
When clinical execution completes, `ClinicalExecutionGatewayService.execute_session_teardown` coordinates orderly shutdown.
While other subsystems implement `evict_session()`, `DurableSessionStore` and `PersistenceService` previously lacked memory eviction and capacity reclamation, causing durable sessions to permanently occupy in-memory capacity slots even after clinical closure.

This specification establishes the formal contracts for:
1. Reclaiming in-memory capacity slots in `DurableSessionStore`.
2. Delegating eviction through `PersistenceService` with reentrancy protection and strict fail-closed capability validation.
3. Strict teardown ordering: successful final `session_teardown_completed` audit persistence -> `close_session()` -> `evict_session()`.
4. Complete preservation of on-disk historical `.jsonl` journals and cryptographic hash chains.
5. Strict backward compatibility with M25-M34 contracts and test suites.

---

## 2. Normative Contracts

### Contract 1: STOPPED/CLOSED Session Eviction
1. Eviction of a session whose state is `SessionStatus.STOPPED` MUST:
   - Flush pending journal writes (via `writer.flush()`).
   - Transition the corresponding `JournalWriter` to closed (`writer.close()`, setting `writer.is_closed = True`).
   - Remove the `JournalWriter` reference from `_writers`.
   - Remove the `DurableSessionRecord` from `_sessions`.
   - Reclaim the in-memory capacity slot (i.e. `session_count` decrements by 1).
   - Preserve the on-disk `.jsonl` journal file completely unmodified.
   - Return `True` indicating successful eviction.

### Contract 2: ACTIVE Session Eviction Rejection
1. Attempted eviction of an `ACTIVE` clinical session MUST NOT silently succeed, discard data, transition status, or evict memory.
2. Eviction of an `ACTIVE` session MUST deterministically raise `PersistenceLifecycleError` with a descriptive message indicating the session must be `STOPPED` first.
3. The session's in-memory record, writer, and capacity slot MUST remain completely unchanged following the rejected operation.
4. The normal teardown flow MUST explicitly transition the session to `STOPPED` (via `close_session()`) before invoking `evict_session()`.

### Contract 3: Idempotency & Repeated Eviction
1. Calling `evict_session()` on an already evicted session MUST be a safe no-op.
2. Repeated calls MUST return `False` without raising an exception and without corrupting state.

### Contract 4: Unknown Session Handling
1. Calling `evict_session()` with a `session_id` that does not exist in memory MUST return `False` (provided valid capability and arguments).
2. It MUST NOT create any new in-memory records, must not delete any files, and must not raise errors.

### Contract 5: Disk Durability & Cryptographic Integrity
1. Memory eviction MUST NOT delete, truncate, or alter the historical on-disk `.jsonl` journal file.
2. The complete SHA-256 rolling hash chain of the journal MUST remain strictly intact.
3. Deterministic replay via `JournalReader` or `verify_session_replay()` MUST continue to function identically before and after in-memory eviction.

### Contract 6: Capacity Reclamation & Restoration
1. When `DurableSessionStore` reaches `MAX_DURABLE_SESSIONS = 16`, starting an additional session is blocked with `PersistenceCapacityError`.
2. Evicting a stopped session decrements `session_count`, liberating a capacity slot.
3. Following eviction, a new session can be started up to the capacity limit.
4. Capacity reclamation supports repeated interleaved operations across 32+ total sessions across the lifecycle, maintaining at most 16 concurrent in-memory sessions at any time.
5. An evicted session CAN subsequently be reconstructed into memory using `restore_session_from_disk(session_id)`, provided capacity is available.

### Contract 7: Teardown Sequencing in Execution Gateway
1. In `ClinicalExecutionGatewayService.execute_session_teardown`:
   - Subsystem state purging across Navigation, Proximity, Drift, Recovery, Registration, Planning, Safety Gate, Workflow, Gateway cache, Platform, Gateway Service, and Tools proceeds first (Steps 1–12).
   - Execution status and audit event are determined (`session_teardown_completed` or `session_teardown_degraded`).
   - The final audit record is submitted to `PersistenceService.record_audit()` and written into the session's durable journal while the session is still registered in `PersistenceService`.
   - **Prerequisite Invariant**: Successful return from `record_audit()` is a strict prerequisite for eviction:
     ```
     FINAL session_teardown_completed audit successfully persists
     ↓
     PersistenceService.close_session() (if ACTIVE)
     ↓
     PersistenceService.evict_session(session_id, capability)
     ↓
     in-memory session is removed
     ```
   - If final audit persistence fails:
     - The persistence session MUST NOT be evicted.
     - The in-memory session and on-disk journal MUST be preserved for crash recovery and audit inspection.
     - The audit failure MUST be recorded in `failures.append(f"persistence_audit: {exc}")`.
   - If `close_session` or `evict_session` fails:
     - The failure MUST be recorded in `failures.append(...)`.
   - **M25 Compatibility**: To preserve exact backward compatibility with existing tests that assert exact contents of `subsystems_purged` (e.g. M25 test suite), `"persistence"` is NOT appended to `subsystems_purged`.

### Contract 8: Fail-Closed Capability Authorization Invariant
1. In `PersistenceService.evict_session(session_id, capability)`:
   - Capability validation is **STRICTLY FAIL-CLOSED**:
     - Missing capability (`capability is None`) MUST NOT silently authorize eviction; MUST raise `PersistenceSecurityError("Missing execution capability for session eviction")`.
     - Inactive capability (`not getattr(capability, "is_active", False)`) MUST raise `PersistenceSecurityError("Teardown capability is inactive or expired")`.
     - Action mismatch (`getattr(capability, "action", None) != "SESSION_TEARDOWN"`) MUST raise `PersistenceSecurityError(...)`.
     - Session binding mismatch (`getattr(capability, "session_id", None) != session_id`, i.e. requested `session_id != capability.session_id`) MUST raise `PersistenceSecurityError(...)`.
2. A capability issued for `SESSION-A` CANNOT be used to evict `SESSION-B`.

### Contract 9: JournalWriter Lifecycle & Resource Model
1. `JournalWriter` uses per-append synchronous writes (`with open(..., "ab") as f: f.write(...); f.flush()`).
2. `JournalWriter` does NOT maintain a long-lived open OS file handle across its lifecycle.
3. The lifecycle closure contract consists of:
   - `flush()`: Guaranteed flush of any pending writes (no-op since writes are per-append synchronous).
   - `close()`: Transition in-memory `is_closed` marker to `True`.
   - Subsequent calls to `append_entry()` MUST be rejected with `PersistenceLifecycleError(f"JournalWriter for session {session_id} is closed")`.

### Contract 10: Verified `close_session()` Behavior
1. `close_session(session_id)` idempotently transitions an active session to `SessionStatus.STOPPED`.
2. If the session is `ACTIVE`:
   - It appends a `JournalEntryType.SESSION_CLOSED` entry with payload `{"action": "SESSION_CLOSED", "session_id": session_id}` into the session's `.jsonl` journal.
   - It transitions the in-memory record to `SessionStatus.STOPPED`.
   - It emits `"persistence.session.closed"`.
3. If the session is already `STOPPED`:
   - It returns the existing record immediately as an idempotent no-op without writing to journal or emitting events.
4. If the session does not exist:
   - It raises `PersistenceValidationError`.
5. It does NOT require a capability parameter in the existing codebase.
6. Teardown ordering `record_audit -> close_session -> evict_session` is fully safe, atomic, and preserves complete audit trail.

---

## 3. Subsystem Interface Specifications

### 3.1 `JournalWriter` (`holomed.persistence.journal`)
```python
class JournalWriter:
    @property
    def is_closed(self) -> bool:
        """Indicate whether the journal writer has been closed."""
        ...

    def flush(self) -> None:
        """Flush pending writes to storage."""
        ...

    def close(self) -> None:
        """Close writer; subsequent append_entry calls raise PersistenceLifecycleError."""
        ...
```

### 3.2 `DurableSessionStore` (`holomed.persistence.sessions`)
```python
class DurableSessionStore:
    def evict_session(self, session_id: str) -> bool:
        """Evict stopped session from in-memory cache, releasing capacity (M35).
        
        Returns:
            True if session was in memory and evicted.
            False if session was unknown or already evicted.
            
        Raises:
            PersistenceLifecycleError: If session is currently ACTIVE.
        """
        ...
```

### 3.3 `PersistenceService` (`holomed.persistence.service`)
```python
class PersistenceService(Service):
    def has_session(self, session_id: str) -> bool:
        """Return True if session_id is present in memory store."""
        ...

    def evict_session(self, session_id: str, capability: Any) -> bool:
        """Evict session-scoped durable session records from memory (M35).
        
        Validates session_id format, enforces reentrancy guards,
        enforces strict fail-closed SESSION_TEARDOWN capability binding
        (capability is not None and requested session_id == capability.session_id),
        and delegates to DurableSessionStore.
        
        Returns:
            True if evicted, False otherwise.
            
        Raises:
            PersistenceLifecycleError: If called reentrantly or session is ACTIVE.
            PersistenceSecurityError: If capability is missing, invalid, inactive, or mismatched.
        """
        ...
```

---

## 4. Hostile Test Matrix Requirements

The implementation must pass 15 hostile lifecycle tests:
1. `test_stopped_session_eviction_reclaims_capacity`: Evict STOPPED session, releasing memory and capacity slot.
2. `test_active_session_eviction_strictly_rejected`: Evict ACTIVE session raises `PersistenceLifecycleError`, preserving in-memory session.
3. `test_evict_session_idempotent`: Multiple `evict_session()` calls on same session safely return False without error.
4. `test_evict_session_unknown_id`: Unknown `session_id` returns False safely.
5. `test_journal_file_preservation_and_replay_after_eviction`: Byte-for-byte `.jsonl` file integrity, rolling hash chain, and replay verification succeed post-eviction.
6. `test_journal_writer_closed_rejects_append`: Closed writer disallows further `append_entry()` with `PersistenceLifecycleError`.
7. `test_restore_session_from_disk_after_eviction`: Evicted session can be reconstructed from disk using `restore_session_from_disk()`.
8. `test_capacity_reclamation_and_interleaved_32_sessions`: Fill to 16 -> reject 17th -> stop & evict session -> capacity freed -> interleaved lifecycle across 32+ total sessions with concurrent count <= 16.
9. `test_capability_validation_session_mismatch_hostile`: Capability issued for `SESSION-A` cannot evict `SESSION-B`, raises `PersistenceSecurityError`.
10. `test_capability_validation_missing_inactive_and_action_mismatch`: Missing (`capability=None`), inactive, or non-TEARDOWN capability raises `PersistenceSecurityError`.
11. `test_reentrant_evict_session_rejected`: Reentrant call to `PersistenceService.evict_session()` is rejected.
12. `test_invalid_session_id_types_rejected`: Non-string or whitespace `session_id` safely returns False.
13. `test_teardown_ordering_audit_persisted_before_eviction`: `session_teardown_completed` audit event is recorded in the journal before eviction.
14. `test_teardown_audit_failure_preserves_persistence_session`: Simulating final audit persistence failure blocks eviction; in-memory session and journal remain intact for recovery.
15. `test_teardown_m25_subsystems_purged_unchanged`: `execute_session_teardown` does NOT append `"persistence"` to `subsystems_purged`, ensuring M25 test compatibility.
