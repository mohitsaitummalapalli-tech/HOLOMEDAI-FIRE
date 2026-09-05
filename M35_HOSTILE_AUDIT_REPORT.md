# M35 Hostile Audit Report: Durable Persistence In-Memory Session Eviction & Capacity Reclamation

**Status**: Verified & Audited  
**Milestone**: M35  
**Subsystems**: `holomed.persistence`, `holomed.execution`  
**Verdict**: **M35_HOSTILE_AUDIT_PASS**  

---

## 1. Exact Original Capacity Defect

In the HoloMed clinical persistence subsystem (`holomed.persistence`), `DurableSessionStore` manages session records (`DurableSessionRecord`) and per-session journal writers (`JournalWriter`). The subsystem enforces a strict hard ceiling on concurrent in-memory session records:
```python
MAX_DURABLE_SESSIONS = 16
```
Prior to M35, while other clinical subsystems implemented `evict_session()` methods invoked during clinical teardown, `DurableSessionStore` and `PersistenceService` had no mechanism to evict closed/stopped sessions from in-memory structures (`_sessions` and `_writers`).

As a consequence:
1. Every started session permanently consumed an in-memory capacity slot in `_sessions`.
2. Even after `close_session()` was called or full clinical session teardown executed (`execute_session_teardown`), `session_count` never decremented.
3. After 16 sessions were started across the process lifetime, any subsequent `start_session()` unconditionally raised `PersistenceCapacityError("Maximum durable session capacity reached (16)")`.
4. The system could not sustain long-running operations or interleaved clinical workflows exceeding 16 lifetime sessions, despite sessions having concluded and their audit data being durably flushed to disk.

---

## 2. Threat Model & Security Posture

### Threats Addressed
1. **Unsafe Active Session Eviction / Silent Data Loss**:
   - *Threat*: An attacker or premature caller initiates eviction on an `ACTIVE` session, causing volatile clinical state to be discarded or subsequent cycle writes to fail silently.
   - *Defense*: `DurableSessionStore.evict_session` deterministically rejects active sessions with `PersistenceLifecycleError("Cannot evict active session ...; session must be STOPPED first")`. Active session memory, writers, and journals remain strictly intact.
2. **Cross-Session Spoofing / Unauthorized Eviction**:
   - *Threat*: A rogue or compromised component holding a capability for `SESSION-A` attempts to evict `SESSION-B`.
   - *Defense*: Strict fail-closed capability verification in `PersistenceService.evict_session`:
     - Missing capability (`None`) raises `PersistenceSecurityError`.
     - Inactive/expired capability raises `PersistenceSecurityError`.
     - Wrong action (not `SESSION_TEARDOWN`) raises `PersistenceSecurityError`.
     - Session ID mismatch (`capability.session_id != session_id`) raises `PersistenceSecurityError`.
3. **On-Disk Journal Deletion / Cryptographic Hash Chain Corruption**:
   - *Threat*: Memory eviction inadvertently deletes, truncates, or corrupts the on-disk `.jsonl` audit log, breaking auditability or forensic replay.
   - *Defense*: Eviction is strictly memory-only (`del self._sessions[session_id]`, `del self._writers[session_id]`). No filesystem mutation occurs. Byte-for-byte identity is cryptographically verified before and after eviction.
4. **Premature Writer Teardown / Lost Audit Records**:
   - *Threat*: Evicting persistence state before clinical teardown writes its final `session_teardown_completed` audit event causes the final audit record to be lost or rejected.
   - *Defense*: Strict teardown sequence in `ClinicalExecutionGatewayService.execute_session_teardown`:
     `record_audit()` MUST succeed and append to disk BEFORE `close_session()` and `evict_session()` are invoked. If audit recording fails, eviction is aborted and session state is preserved for crash inspection.

---

## 3. Contract Requirements (`M35_CONTRACT_SPEC.md`)

The M35 specification defines the following normative contracts:
1. **STOPPED/CLOSED session eviction**: Flush journal, close `JournalWriter`, delete in-memory record and writer, decrement `session_count`, preserve on-disk `.jsonl` file.
2. **ACTIVE session eviction rejection**: Fail-closed with `PersistenceLifecycleError`. No state mutation.
3. **Already-evicted session idempotency**: Calling eviction repeatedly safely returns `False` without exception.
4. **Unknown session handling**: Calling eviction on non-existent `session_id` safely returns `False` without creating state or modifying files.
5. **Disk durability**: Eviction is purely in-memory. SHA-256 rolling hash chains and deterministic replayability are 100% preserved.
6. **Capacity reclamation**: Eviction liberates slot immediately. Supports 32+ interleaved sessions beyond `MAX_DURABLE_SESSIONS = 16`.
7. **Teardown ordering**: Final `session_teardown_completed` audit event persisted to disk prior to persistence eviction.
8. **Capability binding**: Strict fail-closed validation of active `SESSION_TEARDOWN` capability matching target `session_id`.
9. **JournalWriter lifecycle**: Writer transitions to `is_closed = True`; subsequent appends raise `PersistenceLifecycleError`.
10. **Verified `close_session()` behavior**: Idempotent transition to `STOPPED` with `SESSION_CLOSED` journal record.

---

## 4. Implementation Changes

### A. `python/holomed/persistence/journal.py`
- Added `_is_closed: bool = False` to `JournalWriter.__init__`.
- Added `@property def is_closed(self) -> bool` lifecycle query.
- Added `def flush(self) -> None: pass` adhering to synchronous per-append I/O model.
- Added `def close(self) -> None: self._is_closed = True`.
- Added closed guard in `append_entry()`: raises `PersistenceLifecycleError(f"JournalWriter for session {self._session_id} is closed")`.

### B. `python/holomed/persistence/sessions.py`
- Implemented `DurableSessionStore.evict_session(session_id: str) -> bool`:
  - Returns `False` if `session_id not in self._sessions`.
  - Raises `PersistenceLifecycleError` if `rec.status == SessionStatus.ACTIVE`.
  - Flushes and closes `JournalWriter`, deletes writer from `_writers`.
  - Deletes session from `_sessions`.
  - Decrements `session_count` and reclaims slot.

### C. `python/holomed/persistence/service.py`
- Added `has_session(self, session_id: str) -> bool`.
- Added `@property def session_count(self) -> int`.
- Implemented `PersistenceService.evict_session(self, session_id: str, capability: Any) -> bool`:
  - Fail-closed validation for non-empty string `session_id`.
  - Strict capability verification (presence, `is_active`, `action == "SESSION_TEARDOWN"`, `session_id` match).
  - Transaction reentrancy guard via `self._in_transaction`.
  - Delegates to `_session_store.evict_session(session_id)`.

### D. `python/holomed/execution/service.py`
- Imported `SessionStatus` from `holomed.platform.models`.
- Updated `ClinicalExecutionGatewayService.execute_session_teardown`:
  - Enforced strict teardown ordering: `record_audit()` executed first.
  - If audit succeeds (`audit_persisted = True`): checks if session is `ACTIVE`, calls `close_session()`, then `evict_session(session_id, cap)`.
  - If audit fails: records in `failures`, aborts eviction, preserving memory and disk state.
  - Preserved `subsystems_purged` unchanged (no `"persistence"` appended) ensuring 100% backward compatibility with M25.

---

## 5. Teardown Ordering Evidence

In `ClinicalExecutionGatewayService.execute_session_teardown`:
```python
# 1. Final audit record submitted to persistence journal
if self._persistence_service is not None:
    audit_persisted = False
    try:
        self._persistence_service.record_audit(...)
        audit_persisted = True
    except Exception as exc:
        failures.append(f"persistence_audit: {exc}")

    # 2. Eviction only executes IF audit persisted successfully
    if audit_persisted:
        try:
            has_sess = getattr(self._persistence_service, "has_session", None)
            if callable(has_sess) and has_sess(session_id):
                rec = self._persistence_service.get_session(session_id)
                if rec.status == SessionStatus.ACTIVE and hasattr(self._persistence_service, "close_session"):
                    self._persistence_service.close_session(session_id)
            if hasattr(self._persistence_service, "evict_session"):
                self._persistence_service.evict_session(session_id, cap)
        except Exception as exc:
            failures.append(f"persistence_eviction: {exc}")
```
Evidence verified by:
- `test_teardown_ordering_audit_persisted_before_eviction`:
  - Proves `JournalReader` finds `AUDIT_SNAPSHOT` in on-disk `.jsonl` file *before* `SESSION_CLOSED`.
  - Proves `evict_session` spy was called with valid capability *after* audit was durably appended.
  - Proves in-memory session was evicted cleanly.
- `test_teardown_audit_failure_preserves_persistence_session`:
  - Simulating an exception during `record_audit()` halts the teardown sequence before eviction.
  - Session remains `ACTIVE` in memory with writer and journal intact for post-mortem forensics.

---

## 6. Hostile Test Suite Coverage (`tests/unit/persistence/test_m35_persistence_lifecycle.py`)

All 16 hostile tests execute and pass:

| # | Test Function | Objective & Invariant Verified | Result |
|---|---|---|---|
| 1 | `test_stopped_session_eviction_reclaims_capacity` | Evicting STOPPED session purges memory, closes writer, removes writer, frees slot, keeps disk file | **PASSED** |
| 2 | `test_active_session_eviction_strictly_rejected` | ACTIVE session eviction raises `PersistenceLifecycleError`; session remains ACTIVE and occupying slot | **PASSED** |
| 3 | `test_evict_session_idempotent` | Repeated `evict_session()` on already evicted session safely returns `False` without error | **PASSED** |
| 4 | `test_evict_session_unknown_id` | Eviction of non-existent session ID safely returns `False` without side-effects | **PASSED** |
| 5 | `test_journal_file_preservation_and_replay_after_eviction` | Byte-for-byte SHA-256 identity of `.jsonl` file before and after eviction; replay passes | **PASSED** |
| 6 | `test_journal_writer_closed_rejects_append` | Closed writer transitions `is_closed=True`; subsequent `append_entry()` raises `PersistenceLifecycleError` | **PASSED** |
| 7 | `test_restore_session_from_disk_after_eviction` | `restore_session_from_disk()` successfully reconstructs evicted session from on-disk journal | **PASSED** |
| 8 | `test_capacity_reclamation_and_interleaved_32_sessions` | Fill to 16 -> reject 17th -> stop & evict -> start 17th -> cycle across 36 total sessions with count <= 16 | **PASSED** |
| 9 | `test_capability_validation_session_mismatch_hostile` | Capability issued for `SESSION-A` cannot evict `SESSION-B`, raises `PersistenceSecurityError` | **PASSED** |
| 10 | `test_capability_validation_missing_inactive_and_action_mismatch` | Missing (`None`), inactive, or non-TEARDOWN capability raises `PersistenceSecurityError` | **PASSED** |
| 11 | `test_reentrant_evict_session_rejected` | Reentrant call to `PersistenceService.evict_session()` rejected by `_in_transaction` guard | **PASSED** |
| 12 | `test_invalid_session_id_types_rejected` | Non-string, whitespace, or None session IDs safely return `False` | **PASSED** |
| 13 | `test_teardown_ordering_audit_persisted_before_eviction` | Teardown records `session_teardown_completed` before eviction; verifies teardown invokes eviction | **PASSED** |
| 14 | `test_teardown_audit_failure_preserves_persistence_session` | Audit failure aborts eviction; session remains ACTIVE and journal preserved for recovery | **PASSED** |
| 15 | `test_teardown_m25_subsystems_purged_unchanged` | `subsystems_purged` remains identical to M25 specification (`"persistence"` not added) | **PASSED** |
| 16 | `test_eviction_isolation_cannot_affect_other_sessions` | Evicting Session A leaves concurrent Session B intact, active, able to record cycles, and replayable | **PASSED** |

---

## 7. Capacity Reclamation Evidence

Evidence from `test_capacity_reclamation_and_interleaved_32_sessions`:
1. Started 16 sessions (`SESSION-CAP-00` to `SESSION-CAP-15`): `session_count == 16`.
2. Attempted to start `SESSION-CAP-OVERFLOW`: raised `PersistenceCapacityError`.
3. Closed and evicted `SESSION-CAP-00`: `session_count` decremented to 15.
4. Successfully started `SESSION-CAP-16`: `session_count` returned to 16.
5. Interleaved creation and eviction executed across 36 total sessions (`SESSION-CAP-00` through `SESSION-CAP-35`).
6. At no point during the 36-session run did `session_count` exceed 16, and no `PersistenceCapacityError` occurred.

---

## 8. Writer-Close Evidence

Evidence from `test_stopped_session_eviction_reclaims_capacity` & `test_journal_writer_closed_rejects_append`:
- Before eviction: `writer.is_closed` is `False`.
- Upon eviction: `writer.flush()` and `writer.close()` are called; `writer.is_closed` becomes `True`.
- `session_id` is removed from `_writers`.
- Direct append attempt on closed writer raises:
  `PersistenceLifecycleError("JournalWriter for session SESSION-WRITER-06 is closed")`.

---

## 9. Disk Preservation Evidence

Evidence from `test_journal_file_preservation_and_replay_after_eviction`:
- `bytes_before = journal_path.read_bytes()`
- Session evicted via `persistence_service.evict_session(session_id, cap)`
- `bytes_after = journal_path.read_bytes()`
- Exact equality verified: `assert bytes_before == bytes_after`.
- On-disk file size > 0, un-truncated, un-modified.
- SHA-256 rolling hash chain verified valid via `replay_session()`.

---

## 10. Recovery Evidence

Evidence from `test_restore_session_from_disk_after_eviction`:
- Session `SESSION-RESTORE-07` stopped and evicted: `has_session(session_id) == False`.
- `restore_session_from_disk("SESSION-RESTORE-07")` called.
- Resulting `DurableSessionRecord`:
  - `session_id == "SESSION-RESTORE-07"`
  - `status == SessionStatus.STOPPED`
  - `total_cycles == 1`
  - `last_sequence == 1`
- `has_session("SESSION-RESTORE-07") == True`.

---

## 11. Regression Test Results

All regression suites executed with 100% pass rate:

| Test Suite | Command | Tests Run | Result |
|---|---|---|---|
| M35 Persistence Lifecycle | `python -m pytest tests/unit/persistence/test_m35_persistence_lifecycle.py -v` | 16 | **PASSED** (0.45s) |
| Persistence Subsystem | `python -m pytest tests/unit/persistence/ -q` | 49 | **PASSED** (0.70s) |
| Execution Subsystem | `python -m pytest tests/unit/execution/ -q` | 164 | **PASSED** (0.64s) |
| Gateway Subsystem | `python -m pytest tests/unit/gateway/ -q` | 88 | **PASSED** (0.48s) |
| Workflow Subsystem | `python -m pytest tests/unit/workflow/ -q` | 87 | **PASSED** (0.55s) |
| Frozen M33 Checkpoint Lifecycle | `python -m pytest tests/unit/workflow/test_m33_checkpoint_lifecycle.py -v` | 19 | **PASSED** (0.18s) |
| Frozen M34 Gateway Lifecycle | `python -m pytest tests/unit/gateway/test_m34_gateway_lifecycle.py -v` | 13 | **PASSED** (0.10s) |
| **Full Repository Test Suite** | `python -m pytest -q -ra` | **1699** | **PASSED** (6.43s) |

---

## 12. Static Analysis Results

Pyright static analysis executed on M35 changed files:
```
npx -y pyright python/holomed/persistence/sessions.py python/holomed/persistence/service.py python/holomed/persistence/journal.py tests/unit/persistence/test_m35_persistence_lifecycle.py
```
Output:
```
0 errors, 0 warnings, 0 informations
```

Git whitespace & formatting check:
```
git diff --check
```
Output:
```
Clean (0 whitespace / line-ending errors)
```

---

## 13. Exact Changed Files

### Production Files Changed for M35:
1. [`python/holomed/persistence/journal.py`](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/persistence/journal.py) (Lines added: `is_closed`, `flush`, `close`, append guard)
2. [`python/holomed/persistence/sessions.py`](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/persistence/sessions.py) (Lines added: `evict_session`)
3. [`python/holomed/persistence/service.py`](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/persistence/service.py) (Lines added: `has_session`, `session_count`, `evict_session`)
4. [`python/holomed/execution/service.py`](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/python/holomed/execution/service.py) (Import `SessionStatus`, integrate audit + eviction ordering)

### Test Files Created for M35:
1. [`tests/unit/persistence/test_m35_persistence_lifecycle.py`](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/tests/unit/persistence/test_m35_persistence_lifecycle.py) (16 hostile unit tests)

### Specification & Audit Documents:
1. [`M35_CONTRACT_SPEC.md`](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/M35_CONTRACT_SPEC.md) (Normative contracts 1–10)
2. [`M35_HOSTILE_AUDIT_REPORT.md`](file:///c:/Users/mohit/OneDrive/Desktop/HOLOMEDAI-FIRE/M35_HOSTILE_AUDIT_REPORT.md) (This audit document)

---

## 14. M33 Preservation Evidence

- M33 production code (`python/holomed/workflow/checkpoints.py`, `python/holomed/workflow/service.py`) remains unmodified during M35.
- M33 contract specification (`M33_CONTRACT_SPEC.md`) remains untouched.
- M33 hostile test suite (`tests/unit/workflow/test_m33_checkpoint_lifecycle.py`) runs 19 tests: **19 passed in 0.18s**.
- Checkpoint dual-store consistency, cross-owner security, and plan lock churn invariants are fully preserved.

---

## 15. M34 Preservation Evidence

- M34 production code (`python/holomed/gateway/service.py`, `python/holomed/planning/service.py`) remains unmodified during M35.
- M34 contract specification (`M34_CONTRACT_SPEC.md`) remains untouched.
- M34 hostile test suite (`tests/unit/gateway/test_m34_gateway_lifecycle.py`) runs 13 tests: **13 passed in 0.10s**.
- Disconnect fail-closed security, client query isolation, and role hierarchy invariants are fully preserved.

---

## 16. Final Classification

Every condition required for M35 release has been verified and confirmed:
- [x] Safe stopped-session eviction works
- [x] Capacity is actually reclaimed
- [x] 32+ interleaved sessions work
- [x] ACTIVE-session behavior is safe and contract-compliant
- [x] Repeated eviction is safe and idempotent
- [x] Unknown-session behavior is safe
- [x] Writers are closed upon eviction
- [x] Disk journals survive unchanged with byte-for-byte fidelity
- [x] Evicted sessions can be restored from disk
- [x] Teardown ordering preserves the final audit event before eviction
- [x] Persistence eviction is wired into clinical teardown
- [x] Persistence (49/49), execution (164/164), gateway (88/88), workflow (87/87) suites pass
- [x] Full repository suite passes (1699/1699)
- [x] Pyright passes on all M35 files with 0 errors
- [x] Git diff --check passes with 0 whitespace errors
- [x] M33 and M34 remain frozen and passing

**Classification**: **`M35_HOSTILE_AUDIT_PASS`**
