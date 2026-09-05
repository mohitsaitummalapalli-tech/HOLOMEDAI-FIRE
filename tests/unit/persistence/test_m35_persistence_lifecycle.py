# -*- coding: utf-8 -*-
"""Hostile and Lifecycle Unit Tests for M35: Persistence In-Memory Session Eviction & Capacity Reclamation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
import pytest

from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.execution._capability import _create_execution_capability
from holomed.execution.exceptions import (
    ExecutionLifecycleError,
    ExecutionValidationError,
)
from holomed.execution.models import (
    ExecutionStatus,
    SessionTeardownExecutionRequest,
    SessionTeardownExecutionResult,
)
from holomed.execution.service import ClinicalExecutionGatewayService
from holomed.navigation.service import NavigationService
from holomed.persistence.exceptions import (
    PersistenceCapacityError,
    PersistenceLifecycleError,
    PersistenceSecurityError,
    PersistenceValidationError,
)
from holomed.persistence.journal import JournalReader, JournalWriter
from holomed.persistence.models import (
    MAX_DURABLE_SESSIONS,
    JournalEntryType,
)
from holomed.persistence.service import PersistenceService
from holomed.planning.service import PlanningService
from holomed.platform.models import CycleStatus, CycleSummary, SessionStatus
from holomed.platform.service import PlatformService
from holomed.proximity.service import ProximityService
from holomed.recovery.service import RecoveryService
from holomed.registration.service import RegistrationService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.service import ServiceState
from holomed.safety_gate.service import SafetyGateService
from holomed.tools.service import ToolService
from holomed.workflow.service import WorkflowService


@pytest.fixture
def persistence_service(
    temp_storage_root: Path,
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> PersistenceService:
    """Fixture providing a started PersistenceService."""
    srv = PersistenceService(
        dispatcher=message_dispatcher,
        storage_root=temp_storage_root,
        secret_filter=secret_filter,
    )
    srv.initialize(runtime_context)
    message_dispatcher.start()
    srv.start()
    return srv


def _make_teardown_capability(session_id: str, is_active: bool = True, action: str = "SESSION_TEARDOWN"):
    cap = _create_execution_capability(
        service_instance_id=12345,
        session_id=session_id,
        action=action,
        sequence_number=1,
    )
    if not is_active:
        cap.invalidate()
    return cap


# =============================================================================
# Test 1: STOPPED Session Eviction Reclaims Capacity
# =============================================================================
def test_stopped_session_eviction_reclaims_capacity(
    persistence_service: PersistenceService,
    temp_storage_root: Path,
) -> None:
    session_id = "SESSION-STOPPED-01"
    persistence_service.start_session(session_id)
    assert persistence_service.has_session(session_id) is True
    assert persistence_service.get_session(session_id).status == SessionStatus.ACTIVE

    # Close session to transition to STOPPED
    persistence_service.close_session(session_id)
    assert persistence_service.get_session(session_id).status == SessionStatus.STOPPED

    store = persistence_service._session_store
    assert store is not None
    writer = store._writers[session_id]
    assert writer.is_closed is False

    cap = _make_teardown_capability(session_id)
    evicted = persistence_service.evict_session(session_id, cap)
    assert evicted is True

    # Writer was closed upon eviction
    assert writer.is_closed is True
    assert session_id not in store._writers

    # Memory state purged and capacity slot reclaimed
    assert persistence_service.has_session(session_id) is False
    assert persistence_service.session_count == 0
    with pytest.raises(PersistenceValidationError):
        persistence_service.get_session(session_id)

    # Historical journal file remains intact on disk
    journal_path = temp_storage_root / f"{session_id}.jsonl"
    assert journal_path.exists()
    assert journal_path.stat().st_size > 0


# =============================================================================
# Test 2: ACTIVE Session Eviction Strictly Rejected
# =============================================================================
def test_active_session_eviction_strictly_rejected(
    persistence_service: PersistenceService,
) -> None:
    session_id = "SESSION-ACTIVE-02"
    persistence_service.start_session(session_id)
    assert persistence_service.get_session(session_id).status == SessionStatus.ACTIVE

    cap = _make_teardown_capability(session_id)
    with pytest.raises(PersistenceLifecycleError) as exc_info:
        persistence_service.evict_session(session_id, cap)

    assert "must be STOPPED first" in str(exc_info.value)
    # Verify session remains ACTIVE, in-memory, and occupying capacity
    assert persistence_service.has_session(session_id) is True
    assert persistence_service.get_session(session_id).status == SessionStatus.ACTIVE
    assert persistence_service.session_count == 1


# =============================================================================
# Test 3: Idempotency & Repeated Eviction
# =============================================================================
def test_evict_session_idempotent(
    persistence_service: PersistenceService,
) -> None:
    session_id = "SESSION-IDEMPOTENT-03"
    persistence_service.start_session(session_id)
    persistence_service.close_session(session_id)

    cap = _make_teardown_capability(session_id)
    assert persistence_service.evict_session(session_id, cap) is True
    # Subsequent eviction calls on already evicted session return False safely
    assert persistence_service.evict_session(session_id, cap) is False
    assert persistence_service.evict_session(session_id, cap) is False
    assert persistence_service.has_session(session_id) is False


# =============================================================================
# Test 4: Unknown Session Handling
# =============================================================================
def test_evict_session_unknown_id(
    persistence_service: PersistenceService,
) -> None:
    session_id = "SESSION-UNKNOWN-04"
    cap = _make_teardown_capability(session_id)
    assert persistence_service.evict_session(session_id, cap) is False
    assert persistence_service.has_session(session_id) is False


# =============================================================================
# Test 5: On-Disk Journal Preservation & Deterministic Replay Post-Eviction
# =============================================================================
def test_journal_file_preservation_and_replay_after_eviction(
    persistence_service: PersistenceService,
    temp_storage_root: Path,
) -> None:
    session_id = "SESSION-REPLAY-05"
    persistence_service.start_session(session_id)

    summary = CycleSummary(
        cycle_id="CYC-01",
        epoch_id=1,
        session_id=session_id,
        sequence_number=0,
        status=CycleStatus.COMPLETED,
        execution_time_ms=10.0,
        phases_completed=("PERCEPTION", "REASONING"),
        fused_entity_count=1,
        tool_invocations_count=0,
        xr_node_count=0,
        confidence=1.0,
        uncertainty_metric=0.0,
    )
    persistence_service.record_cycle(session_id, 0, summary)
    persistence_service.close_session(session_id)

    journal_path = temp_storage_root / f"{session_id}.jsonl"
    bytes_before = journal_path.read_bytes()

    cap = _make_teardown_capability(session_id)
    assert persistence_service.evict_session(session_id, cap) is True

    bytes_after = journal_path.read_bytes()
    # Byte-for-byte exact equality proves journal was never deleted, truncated, or rewritten
    assert bytes_before == bytes_after

    # Deterministic replay succeeds identically post-eviction
    report = persistence_service.replay_session(session_id)
    assert report.hash_chain_valid is True
    assert report.total_entries_verified == 3  # SESSION_STARTED, CYCLE_COMPLETED, SESSION_CLOSED
    assert len(report.state_divergences) == 0


# =============================================================================
# Test 6: JournalWriter Lifecycle & Rejection of Appends After Close
# =============================================================================
def test_journal_writer_closed_rejects_append(
    temp_storage_root: Path,
) -> None:
    session_id = "SESSION-WRITER-06"
    writer = JournalWriter(temp_storage_root, session_id, epoch_id=1)
    writer.initialize_storage()
    assert writer.is_closed is False

    now_utc = datetime.now(timezone.utc).isoformat()
    entry = writer.append_entry(
        entry_type=JournalEntryType.SESSION_STARTED,
        sequence_number=0,
        timestamp_utc=now_utc,
        payload={"action": "START"},
    )
    assert entry.sequence_number == 0

    # Flush is a safe no-op on synchronous writer
    writer.flush()

    # Close transitions lifecycle state
    writer.close()
    assert writer.is_closed is True

    # Subsequent append_entry deterministically raises PersistenceLifecycleError
    with pytest.raises(PersistenceLifecycleError) as exc_info:
        writer.append_entry(
            entry_type=JournalEntryType.CYCLE_COMPLETED,
            sequence_number=1,
            timestamp_utc=now_utc,
            payload={"action": "CYCLE"},
        )
    assert "is closed" in str(exc_info.value)


# =============================================================================
# Test 7: Restore Evicted Session From Disk
# =============================================================================
def test_restore_session_from_disk_after_eviction(
    persistence_service: PersistenceService,
) -> None:
    session_id = "SESSION-RESTORE-07"
    persistence_service.start_session(session_id)
    summary = CycleSummary(
        cycle_id="CYC-01",
        epoch_id=1,
        session_id=session_id,
        sequence_number=1,
        status=CycleStatus.COMPLETED,
        execution_time_ms=12.0,
        phases_completed=("PERCEPTION", "REASONING"),
        fused_entity_count=1,
        tool_invocations_count=0,
        xr_node_count=0,
        confidence=1.0,
        uncertainty_metric=0.0,
    )
    persistence_service.record_cycle(session_id, 1, summary)
    persistence_service.close_session(session_id)

    cap = _make_teardown_capability(session_id)
    assert persistence_service.evict_session(session_id, cap) is True
    assert persistence_service.has_session(session_id) is False

    # Restore session from on-disk journal into memory
    store = persistence_service._session_store
    assert store is not None
    restored = store.restore_session_from_disk(session_id)
    assert restored.session_id == session_id
    assert restored.status == SessionStatus.STOPPED
    assert restored.total_cycles == 1
    assert restored.last_sequence == 1
    assert persistence_service.has_session(session_id) is True


# =============================================================================
# Test 8: Capacity Saturation, Reclamation & Interleaved 32+ Sessions
# =============================================================================
def test_capacity_reclamation_and_interleaved_32_sessions(
    persistence_service: PersistenceService,
) -> None:
    # 1. Fill exactly to MAX_DURABLE_SESSIONS = 16
    for i in range(MAX_DURABLE_SESSIONS):
        persistence_service.start_session(f"SESSION-CAP-{i:02d}")
    assert persistence_service.session_count == 16

    # 2. Attempting to create 17th simultaneous session raises PersistenceCapacityError
    with pytest.raises(PersistenceCapacityError):
        persistence_service.start_session("SESSION-CAP-OVERFLOW")

    # 3. Stop and evict one session -> slot reclaimed
    persistence_service.close_session("SESSION-CAP-00")
    cap_00 = _make_teardown_capability("SESSION-CAP-00")
    assert persistence_service.evict_session("SESSION-CAP-00", cap_00) is True
    assert persistence_service.session_count == 15

    # 4. Now 17th session can be started
    persistence_service.start_session("SESSION-CAP-16")
    assert persistence_service.session_count == 16

    # 5. Perform repeated interleaved operations across 36 total distinct sessions
    #    Maintaining concurrent active/in-memory count <= 16 at all times
    for i in range(17, 36):
        # Pick an active session to stop and evict
        evict_id = f"SESSION-CAP-{i - 16:02d}"
        persistence_service.close_session(evict_id)
        cap = _make_teardown_capability(evict_id)
        assert persistence_service.evict_session(evict_id, cap) is True
        assert persistence_service.session_count <= 15

        # Create new distinct session
        new_id = f"SESSION-CAP-{i:02d}"
        persistence_service.start_session(new_id)
        assert persistence_service.session_count <= 16

    assert persistence_service.session_count == 16


# =============================================================================
# Test 9: Capability Validation — Cross-Session Spoofing Hostile Attack
# =============================================================================
def test_capability_validation_session_mismatch_hostile(
    persistence_service: PersistenceService,
) -> None:
    session_a = "SESSION-ALICE-09"
    session_b = "SESSION-BOB-09"

    persistence_service.start_session(session_a)
    persistence_service.close_session(session_a)

    persistence_service.start_session(session_b)
    persistence_service.close_session(session_b)

    # Capability issued strictly for SESSION-A
    cap_a = _make_teardown_capability(session_a)

    # Attempt to evict SESSION-B using SESSION-A's capability
    with pytest.raises(PersistenceSecurityError) as exc_info:
        persistence_service.evict_session(session_b, cap_a)

    assert "Capability session mismatch" in str(exc_info.value)
    assert session_b in str(exc_info.value)
    assert session_a in str(exc_info.value)

    # Prove SESSION-B was NOT evicted
    assert persistence_service.has_session(session_b) is True


# =============================================================================
# Test 10: Capability Validation — Missing, Inactive, and Action Mismatch Attacks
# =============================================================================
def test_capability_validation_missing_inactive_and_action_mismatch(
    persistence_service: PersistenceService,
) -> None:
    session_id = "SESSION-CAP-ATTACK-10"
    persistence_service.start_session(session_id)
    persistence_service.close_session(session_id)

    # Attack 1: Missing capability (capability=None)
    with pytest.raises(PersistenceSecurityError) as exc_info:
        persistence_service.evict_session(session_id, None)  # type: ignore
    assert "Missing execution capability" in str(exc_info.value)
    assert persistence_service.has_session(session_id) is True

    # Attack 2: Inactive/invalidated capability
    cap_inactive = _make_teardown_capability(session_id, is_active=False)
    with pytest.raises(PersistenceSecurityError) as exc_info:
        persistence_service.evict_session(session_id, cap_inactive)
    assert "inactive or expired" in str(exc_info.value)
    assert persistence_service.has_session(session_id) is True

    # Attack 3: Capability with unauthorized action
    cap_wrong_action = _make_teardown_capability(session_id, action="TOOL_INVOCATION")
    with pytest.raises(PersistenceSecurityError) as exc_info:
        persistence_service.evict_session(session_id, cap_wrong_action)
    assert "Capability action mismatch" in str(exc_info.value)
    assert persistence_service.has_session(session_id) is True


# =============================================================================
# Test 11: Reentrant evict_session() Call Rejected
# =============================================================================
def test_reentrant_evict_session_rejected(
    persistence_service: PersistenceService,
) -> None:
    session_id = "SESSION-REENTRANT-11"
    persistence_service.start_session(session_id)
    persistence_service.close_session(session_id)
    cap = _make_teardown_capability(session_id)

    persistence_service._in_transaction = True
    try:
        with pytest.raises(PersistenceLifecycleError) as exc_info:
            persistence_service.evict_session(session_id, cap)
        assert "Reentrant call to evict_session rejected" in str(exc_info.value)
    finally:
        persistence_service._in_transaction = False

    assert persistence_service.has_session(session_id) is True


# =============================================================================
# Test 12: Invalid session_id Types Safely Rejected
# =============================================================================
def test_invalid_session_id_types_rejected(
    persistence_service: PersistenceService,
) -> None:
    cap = _make_teardown_capability("ANY")
    assert persistence_service.evict_session("", cap) is False
    assert persistence_service.evict_session("   ", cap) is False
    assert persistence_service.evict_session(None, cap) is False  # type: ignore
    assert persistence_service.evict_session(12345, cap) is False  # type: ignore


# =============================================================================
# Helper fixture for Execution Gateway tests
# =============================================================================
@pytest.fixture
def execution_gateway_fixture(runtime_context: RuntimeContext, temp_storage_root: Path, secret_filter: SecretFilter):
    dispatcher = MessageDispatcher()
    dispatcher.initialize(runtime_context)

    platform = PlatformService()
    platform.initialize(runtime_context)
    platform.start()

    workflow = WorkflowService(dispatcher=dispatcher, platform_service=platform)
    workflow.initialize(runtime_context)
    workflow.start()

    planning = PlanningService(dispatcher=dispatcher, workflow_service=workflow)
    planning.initialize(runtime_context)
    planning.start()

    registration = RegistrationService(dispatcher=dispatcher, planning_service=planning)
    registration.initialize(runtime_context)
    registration.start()

    navigation = NavigationService(dispatcher=dispatcher, registration_service=registration)
    navigation.initialize(runtime_context)
    navigation.start()

    recovery = RecoveryService(dispatcher=dispatcher, registration_service=registration)
    recovery.initialize(runtime_context)
    recovery.start()

    safety_gate = SafetyGateService(dispatcher=None, workflow_service=workflow)
    safety_gate.initialize(runtime_context)
    safety_gate.start()

    persistence = PersistenceService(
        dispatcher=dispatcher,
        storage_root=temp_storage_root,
        secret_filter=secret_filter,
    )
    persistence.initialize(runtime_context)
    persistence.start()

    gateway = ClinicalExecutionGatewayService(
        dispatcher=dispatcher,
        safety_gate_service=safety_gate,
        workflow_service=workflow,
        navigation_service=navigation,
        persistence_service=persistence,
        recovery_service=recovery,
        registration_service=registration,
        planning_service=planning,
        platform_service=platform,
    )
    gateway.initialize(runtime_context)
    gateway.start()
    dispatcher.start()

    return {
        "gateway": gateway,
        "persistence": persistence,
        "storage_root": temp_storage_root,
    }


# =============================================================================
# Test 13: Teardown Ordering & Invocation — Audit Persisted Before Eviction
# =============================================================================
def test_teardown_ordering_audit_persisted_before_eviction(
    execution_gateway_fixture: dict[str, Any],
) -> None:
    gateway: ClinicalExecutionGatewayService = execution_gateway_fixture["gateway"]
    persistence: PersistenceService = execution_gateway_fixture["persistence"]
    storage_root: Path = execution_gateway_fixture["storage_root"]

    session_id = "SESSION-TEARDOWN-13"
    persistence.start_session(session_id)
    assert persistence.has_session(session_id) is True

    # Spy on evict_session to prove teardown explicitly invokes persistence eviction
    real_evict = persistence.evict_session
    evict_spy = MagicMock(side_effect=real_evict)
    persistence.evict_session = evict_spy  # type: ignore

    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=10,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)

    assert res.execution_status == ExecutionStatus.EXECUTED_CLEAR
    assert len(res.failures) == 0

    # Verify teardown actually invoked persistence eviction
    assert evict_spy.called is True
    call_session_id, call_cap = evict_spy.call_args[0]
    assert call_session_id == session_id
    assert getattr(call_cap, "session_id", None) == session_id
    assert getattr(call_cap, "action", None) == "SESSION_TEARDOWN"

    # In-memory persistence session was evicted post-teardown
    assert persistence.has_session(session_id) is False

    # Historical journal file contains session_teardown_completed audit record
    journal_path = storage_root / f"{session_id}.jsonl"
    entries, recovered = JournalReader.read_and_recover_journal(journal_path)
    entry_types = [e.entry_type for e in entries]

    assert JournalEntryType.AUDIT_SNAPSHOT in entry_types
    assert JournalEntryType.SESSION_CLOSED in entry_types

    # Order check: AUDIT_SNAPSHOT appears before SESSION_CLOSED
    audit_idx = entry_types.index(JournalEntryType.AUDIT_SNAPSHOT)
    closed_idx = entry_types.index(JournalEntryType.SESSION_CLOSED)
    assert audit_idx < closed_idx


# =============================================================================
# Test 14: Teardown Audit Failure Preserves Persistence Session & Journal
# =============================================================================
def test_teardown_audit_failure_preserves_persistence_session(
    execution_gateway_fixture: dict[str, Any],
) -> None:
    gateway: ClinicalExecutionGatewayService = execution_gateway_fixture["gateway"]
    persistence: PersistenceService = execution_gateway_fixture["persistence"]

    session_id = "SESSION-AUDIT-FAIL-14"
    persistence.start_session(session_id)
    assert persistence.has_session(session_id) is True

    # Simulate audit persistence failure (e.g. disk full or capacity exhaustion)
    persistence.record_audit = MagicMock(side_effect=PersistenceCapacityError("Simulated disk error during audit"))

    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=20,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)

    assert res.execution_status == ExecutionStatus.FAILED_NAVIGATION_GEOMETRY
    assert any("persistence_audit" in f for f in res.failures)

    # Invariant: session MUST NOT be evicted and MUST be preserved for crash recovery
    assert persistence.has_session(session_id) is True
    rec = persistence.get_session(session_id)
    assert rec.status == SessionStatus.ACTIVE


# =============================================================================
# Test 15: M25 Subsystems Purged Compatibility Invariant
# =============================================================================
def test_teardown_m25_subsystems_purged_unchanged(
    execution_gateway_fixture: dict[str, Any],
) -> None:
    gateway: ClinicalExecutionGatewayService = execution_gateway_fixture["gateway"]
    session_id = "SESSION-M25-COMPAT-15"

    req = SessionTeardownExecutionRequest(
        session_id=session_id,
        sequence_number=30,
        now_utc=datetime.now(timezone.utc).isoformat(),
    )
    res = gateway.execute_session_teardown(req)

    # Invariant: "persistence" is NOT added to subsystems_purged; M25 set preserved
    expected_subsystems = {
        "navigation",
        "recovery",
        "registration",
        "planning",
        "safety_gate",
        "workflow",
        "gateway",
        "platform",
    }
    assert set(res.subsystems_purged) == expected_subsystems
    assert "persistence" not in res.subsystems_purged


# =============================================================================
# Test 16: Eviction Isolation — One Session's Eviction Cannot Affect Another
# =============================================================================
def test_eviction_isolation_cannot_affect_other_sessions(
    persistence_service: PersistenceService,
    temp_storage_root: Path,
) -> None:
    session_a = "SESSION-ISO-A"
    session_b = "SESSION-ISO-B"

    persistence_service.start_session(session_a)
    persistence_service.start_session(session_b)

    summary_a = CycleSummary(
        cycle_id="CYC-A0",
        epoch_id=1,
        session_id=session_a,
        sequence_number=0,
        status=CycleStatus.COMPLETED,
        execution_time_ms=10.0,
        phases_completed=("PERCEPTION", "REASONING"),
        fused_entity_count=1,
        tool_invocations_count=0,
        xr_node_count=0,
        confidence=1.0,
        uncertainty_metric=0.0,
    )
    persistence_service.record_cycle(session_a, 0, summary_a)

    summary_b0 = CycleSummary(
        cycle_id="CYC-B0",
        epoch_id=1,
        session_id=session_b,
        sequence_number=0,
        status=CycleStatus.COMPLETED,
        execution_time_ms=10.0,
        phases_completed=("PERCEPTION", "REASONING"),
        fused_entity_count=1,
        tool_invocations_count=0,
        xr_node_count=0,
        confidence=1.0,
        uncertainty_metric=0.0,
    )
    persistence_service.record_cycle(session_b, 0, summary_b0)

    assert persistence_service.session_count == 2

    # Stop and evict Session A
    persistence_service.close_session(session_a)
    cap_a = _make_teardown_capability(session_a)
    assert persistence_service.evict_session(session_a, cap_a) is True

    # Session A is evicted
    assert persistence_service.has_session(session_a) is False
    assert persistence_service.session_count == 1

    # Session B is completely unaffected: remains ACTIVE and in-memory
    assert persistence_service.has_session(session_b) is True
    rec_b = persistence_service.get_session(session_b)
    assert rec_b.status == SessionStatus.ACTIVE
    assert rec_b.total_cycles == 1

    # Session B can continue to append cycles without error
    summary_b1 = CycleSummary(
        cycle_id="CYC-B1",
        epoch_id=1,
        session_id=session_b,
        sequence_number=1,
        status=CycleStatus.COMPLETED,
        execution_time_ms=15.0,
        phases_completed=("PERCEPTION", "REASONING", "ACTUATION"),
        fused_entity_count=2,
        tool_invocations_count=1,
        xr_node_count=1,
        confidence=0.99,
        uncertainty_metric=0.01,
    )
    persistence_service.record_cycle(session_b, 1, summary_b1)
    assert persistence_service.get_session(session_b).total_cycles == 2

    # Session B journal remains valid and replayable
    report_b = persistence_service.replay_session(session_b)
    assert report_b.hash_chain_valid is True
    assert report_b.total_entries_verified == 3  # SESSION_STARTED, CYC-B0, CYC-B1

    # Session A journal on disk is preserved unchanged
    journal_path_a = temp_storage_root / f"{session_a}.jsonl"
    assert journal_path_a.exists()
    report_a = persistence_service.replay_session(session_a)
    assert report_a.hash_chain_valid is True
    assert report_a.total_entries_verified == 3  # SESSION_STARTED, CYC-A0, SESSION_CLOSED

