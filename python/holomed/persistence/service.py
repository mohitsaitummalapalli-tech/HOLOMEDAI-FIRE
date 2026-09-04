# -*- coding: utf-8 -*-
"""Durable Clinical Session Persistence & Event Journaling Service (M09)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional

from holomed.core.dispatcher import MessageDispatcher
from holomed.core.models import DispatcherState
from holomed.devices.models import DeviceShutdownFailureRecord
from holomed.persistence.audit import DurableAuditStore
from holomed.persistence.events import RecordingPersistenceEventSink
from holomed.persistence.exceptions import (
    PersistenceCapacityError,
    PersistenceCorruptionError,
    PersistenceEpochMismatchError,
    PersistenceLifecycleError,
    PersistenceResourceIntegrityError,
    PersistenceSecurityError,
    PersistenceShutdownError,
    PersistenceValidationError,
)
from holomed.persistence.journal import JournalReader, JournalWriter, validate_session_path
from holomed.persistence.models import (
    MAX_RECORDED_PERSISTENCE_EVENTS,
    PERSISTENCE_SCHEMA_VERSION,
    DurableAuditRecord,
    DurableSessionRecord,
    JournalEntryType,
    PersistenceHealthStatus,
    ReplayVerificationReport,
)
from holomed.persistence.replay import ReplayEngine
from holomed.persistence.serialization import serialize_canonical_bytes
from holomed.persistence.sessions import DurableSessionStore
from holomed.platform.models import CycleSummary, SessionStatus
from holomed.platform.service import PlatformService
from holomed.protocol.builders import (
    create_error_response,
    create_event,
    create_response,
)
from holomed.protocol.models import MessageEnvelope
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceState

STRUCTURAL_RESOURCE_IDS: tuple[str, ...] = (
    "persistence.journal",
    "persistence.sessions",
    "persistence.replay",
    "persistence.metrics",
)


class PersistenceService(IService):
    """Clinical Session Persistence & Event Journaling Service implementing IService."""

    def __init__(
        self,
        platform_service: Optional[PlatformService] = None,
        dispatcher: Optional[MessageDispatcher] = None,
        storage_root: Optional[str | Path] = None,
        secret_filter: Optional[SecretFilter] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._platform_service = platform_service
        self._dispatcher = dispatcher
        self._storage_root_param = Path(storage_root) if storage_root else None
        self._secret_filter = secret_filter
        self._logger = logger or StructuredLogger("persistence_service", secret_filter=secret_filter)

        self._state: ServiceState = ServiceState.UNINITIALIZED
        self._context: Optional[RuntimeContext] = None
        self._resources: Optional[OwnedResourceSet] = None
        self._epoch_id: int = 0
        self._storage_root: Optional[Path] = None

        # Subsystems
        self._session_store: Optional[DurableSessionStore] = None
        self._audit_store: Optional[DurableAuditStore] = None
        self._replay_engine: Optional[ReplayEngine] = None
        self._event_sink: Optional[RecordingPersistenceEventSink] = None

        # Reentrancy Guard (synchronous, not a threading lock)
        self._in_transaction: bool = False
        self._total_writes: int = 0
        self._recovered_truncations: int = 0

    @property
    def name(self) -> str:
        return "persistence_service"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ("platform_service",)

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise PersistenceLifecycleError("Service has not been initialized; resources unavailable")
        return self._resources

    @property
    def session_store(self) -> Optional[DurableSessionStore]:
        return self._session_store

    @property
    def audit_store(self) -> Optional[DurableAuditStore]:
        return self._audit_store

    @property
    def replay_engine(self) -> Optional[ReplayEngine]:
        return self._replay_engine

    @property
    def event_sink(self) -> Optional[RecordingPersistenceEventSink]:
        return self._event_sink

    def initialize(self, context: RuntimeContext) -> None:
        """Initialize persistence service and acquire exactly 4 structural handles."""
        if self._state not in (ServiceState.UNINITIALIZED, ServiceState.STOPPED):
            raise PersistenceLifecycleError(f"Cannot initialize PersistenceService in state {self._state.name}")

        self._context = context
        self._epoch_id = context.epoch_id
        self._resources = OwnedResourceSet(self.name, self._epoch_id)

        # Acquire 4 structural handles
        for res_id in STRUCTURAL_RESOURCE_IDS:
            self._resources.acquire(res_id)

        # Resolve storage root
        if self._storage_root_param is not None:
            self._storage_root = self._storage_root_param.resolve()
        else:
            self._storage_root = (Path("artifacts") / "persistence").resolve()

        self._storage_root.mkdir(parents=True, exist_ok=True)

        # Instantiate components
        self._session_store = DurableSessionStore(self._storage_root, epoch_id=self._epoch_id)
        self._audit_store = DurableAuditStore(secret_filter=self._secret_filter)
        self._replay_engine = ReplayEngine(self._storage_root)
        self._event_sink = RecordingPersistenceEventSink()

        # Register Dispatcher Routes strictly during INITIALIZED
        if self._dispatcher is not None:
            self._dispatcher.register_query_handler(
                "persistence.status",
                self.handle_status_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "persistence.session.get",
                self.handle_session_get_query,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "persistence.cycle.get",
                self.handle_cycle_get_query,
                self.name,
            )
            self._dispatcher.register_command_handler(
                "persistence.replay",
                self.handle_replay_command,
                self.name,
            )
            self._dispatcher.register_query_handler(
                "persistence.audit",
                self.handle_audit_query,
                self.name,
            )

        self._state = ServiceState.INITIALIZED

    def start(self) -> None:
        """Transition service to STARTED. Acquires zero new resources."""
        if self._state != ServiceState.INITIALIZED:
            raise PersistenceLifecycleError(f"Cannot start PersistenceService in state {self._state.name}")

        # Validate storage root accessibility
        if self._storage_root is None or not self._storage_root.exists():
            self._state = ServiceState.FAILED
            raise PersistenceResourceIntegrityError(f"Storage root inaccessible: {self._storage_root}")

        self._state = ServiceState.STARTED
        self._emit_event(
            "persistence.started",
            {"epoch_id": self._epoch_id, "storage_root": str(self._storage_root)},
        )

    def stop(self) -> None:
        """Flush journal streams, release 4 structural handles idempotently."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        if self._in_transaction:
            raise PersistenceLifecycleError("Cannot stop PersistenceService during an active transaction")

        self._in_transaction = True
        failures: list[DeviceShutdownFailureRecord] = []
        try:
            self.clear()

            if self._resources is not None:
                for handle in list(self._resources.outstanding_handles):
                    try:
                        self._resources.release(handle.resource_id)
                    except Exception as e:
                        self._resources.mark_release_failed(handle.resource_id, str(e))
                        raw_err = str(e)
                        redacted_err = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
                        failures.append(
                            DeviceShutdownFailureRecord(
                                device_id=self.name,
                                error_type=type(e).__name__,
                                error_message=redacted_err,
                                execution_index=len(failures),
                                unreleased_resources=(handle.resource_id,),
                            )
                        )

            if failures:
                self._state = ServiceState.FAILED
                raise PersistenceShutdownError(
                    f"PersistenceService teardown encountered {len(failures)} resource failure(s)",
                    failures,
                )

            self._state = ServiceState.STOPPED
        finally:
            self._in_transaction = False

    def health(self) -> ServiceHealth:
        """Evaluate and return in-process health snapshot."""
        now_utc = datetime.now(timezone.utc).isoformat()
        if self._state != ServiceState.STARTED:
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.FAILED if self._state == ServiceState.FAILED else HealthStatus.UNHEALTHY,
                message=f"Service state is {self._state.name}",
                timestamp_utc=now_utc,
            )

        if (
            self._resources is None
            or len(self._resources.outstanding_handles) != len(STRUCTURAL_RESOURCE_IDS)
            or self._storage_root is None
            or not self._storage_root.exists()
        ):
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.UNHEALTHY,
                message="Structural resource or storage root corruption detected",
                timestamp_utc=now_utc,
            )

        return ServiceHealth(
            name=self.name,
            status=HealthStatus.HEALTHY,
            message=f"Durable sessions: {self._session_store.session_count if self._session_store else 0}, writes: {self._total_writes}",
            timestamp_utc=now_utc,
        )

    # -------------------------------------------------------------------------
    # Public Core API
    # -------------------------------------------------------------------------

    def start_session(
        self,
        session_id: str,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> DurableSessionRecord:
        """Register and persist a new or existing active clinical session."""
        if self._state != ServiceState.STARTED:
            raise PersistenceLifecycleError(f"Cannot start session in state {self._state.name}")
        if self._in_transaction:
            raise PersistenceLifecycleError("Reentrant call to start_session rejected by transaction guard")

        self._in_transaction = True
        try:
            if self._session_store is None:
                raise PersistenceResourceIntegrityError("Session store uninitialized")

            record = self._session_store.start_session(
                session_id=session_id,
                epoch_id=self._epoch_id,
                metadata=metadata,
            )
            self._total_writes += 1
            self._emit_event(
                "persistence.session.created",
                {"session_id": session_id, "epoch_id": self._epoch_id},
            )
            return record
        finally:
            self._in_transaction = False

    def record_cycle(
        self,
        session_id: str,
        sequence_number: int,
        summary: CycleSummary,
    ) -> DurableSessionRecord:
        """Persist a completed or degraded cycle summary into the session journal."""
        if self._state != ServiceState.STARTED:
            raise PersistenceLifecycleError(f"Cannot record cycle in state {self._state.name}")
        if self._in_transaction:
            raise PersistenceLifecycleError("Reentrant call to record_cycle rejected by transaction guard")

        self._in_transaction = True
        try:
            if self._session_store is None:
                raise PersistenceResourceIntegrityError("Session store uninitialized")

            # Redact diagnostic message if present
            diag = summary.diagnostic_message
            if diag and self._secret_filter:
                diag = self._secret_filter.redact(diag)

            sanitized_summary = CycleSummary(
                cycle_id=summary.cycle_id,
                epoch_id=summary.epoch_id,
                session_id=summary.session_id,
                sequence_number=summary.sequence_number,
                status=summary.status,
                execution_time_ms=summary.execution_time_ms,
                phases_completed=summary.phases_completed,
                fused_entity_count=summary.fused_entity_count,
                tool_invocations_count=summary.tool_invocations_count,
                xr_node_count=summary.xr_node_count,
                confidence=summary.confidence,
                uncertainty_metric=summary.uncertainty_metric,
                is_simulated=summary.is_simulated,
                diagnostic_message=diag,
            )

            record = self._session_store.record_cycle(
                session_id=session_id,
                sequence_number=sequence_number,
                summary=sanitized_summary,
            )
            self._total_writes += 1
            self._emit_event(
                "persistence.cycle.recorded",
                {
                    "session_id": session_id,
                    "cycle_id": summary.cycle_id,
                    "sequence_number": sequence_number,
                    "status": summary.status.value,
                },
            )
            return record
        finally:
            self._in_transaction = False

    def close_session(self, session_id: str) -> DurableSessionRecord:
        """Idempotently close an active session."""
        if self._state != ServiceState.STARTED:
            raise PersistenceLifecycleError(f"Cannot close session in state {self._state.name}")
        if self._in_transaction:
            raise PersistenceLifecycleError("Reentrant call to close_session rejected by transaction guard")

        self._in_transaction = True
        try:
            if self._session_store is None:
                raise PersistenceResourceIntegrityError("Session store uninitialized")

            record = self._session_store.close_session(session_id)
            self._total_writes += 1
            self._emit_event("persistence.session.closed", {"session_id": session_id})
            return record
        finally:
            self._in_transaction = False

    def get_session(self, session_id: str) -> DurableSessionRecord:
        """Retrieve registered session record."""
        if self._session_store is None:
            raise PersistenceResourceIntegrityError("Session store uninitialized")
        return self._session_store.get_session(session_id)

    def record_audit(
        self,
        audit_report: Mapping[str, Any],
        session_id: Optional[str] = None,
    ) -> DurableAuditRecord:
        """Persist a consistency audit record."""
        if self._state != ServiceState.STARTED:
            raise PersistenceLifecycleError(f"Cannot record audit in state {self._state.name}")
        if self._in_transaction:
            raise PersistenceLifecycleError("Reentrant call to record_audit rejected by transaction guard")

        self._in_transaction = True
        try:
            if self._audit_store is None or self._session_store is None:
                raise PersistenceResourceIntegrityError("Stores uninitialized")

            writer: Optional[JournalWriter] = None
            if session_id and self._session_store.has_session(session_id):
                writer = self._session_store._writers.get(session_id)

            rec = self._audit_store.record_audit(
                audit_report=audit_report,
                epoch_id=self._epoch_id,
                journal_writer=writer,
            )
            self._total_writes += 1
            return rec
        finally:
            self._in_transaction = False

    def replay_session(self, session_id: str) -> ReplayVerificationReport:
        """Run deterministic verification replay over an on-disk session journal."""
        if self._replay_engine is None:
            raise PersistenceResourceIntegrityError("Replay engine uninitialized")

        rep = self._replay_engine.verify_session_replay(session_id)
        self._emit_event(
            "persistence.replay.completed",
            {
                "session_id": session_id,
                "status": rep.replay_status.value,
                "verified_count": rep.total_entries_verified,
            },
        )
        return rep

    def clear(self) -> None:
        """Clear transient session and audit caches (preserves disk files)."""
        if self._session_store is not None:
            self._session_store.clear()
        if self._audit_store is not None:
            self._audit_store.clear()
        if self._event_sink is not None:
            self._event_sink.clear()

    # -------------------------------------------------------------------------
    # Dispatcher Handlers
    # -------------------------------------------------------------------------

    def handle_status_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        h = self.health()
        payload = {
            "service_name": self.name,
            "state": self._state.name,
            "epoch_id": self._epoch_id,
            "health_status": h.status.name,
            "health_message": h.message,
            "durable_sessions_count": self._session_store.session_count if self._session_store else 0,
            "active_sessions_count": self._session_store.active_session_count if self._session_store else 0,
            "total_writes": self._total_writes,
        }
        return create_response(query_envelope, self.name, payload=payload)

    def handle_session_get_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        session_id = query_envelope.payload.get("session_id")
        if not session_id:
            return create_error_response(
                query_envelope,
                self.name,
                error_code="ERR_INVALID_SESSION_ID",
                error_message="Missing session_id",
            )
        try:
            if self._storage_root is not None:
                validate_session_path(self._storage_root, str(session_id))
            rec = self.get_session(str(session_id))
            payload = {
                "session_id": rec.session_id,
                "epoch_id": rec.epoch_id,
                "status": rec.status.value,
                "created_timestamp_utc": rec.created_timestamp_utc,
                "closed_timestamp_utc": rec.closed_timestamp_utc,
                "total_cycles": rec.total_cycles,
                "last_sequence": rec.last_sequence,
                "schema_version": rec.schema_version,
            }
            return create_response(query_envelope, self.name, payload=payload)
        except PersistenceSecurityError as e:
            raw_err = str(e)
            redacted_err = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(
                query_envelope,
                self.name,
                error_code="ERR_PERSISTENCE_SECURITY_ERROR",
                error_message=redacted_err,
            )
        except Exception as e:
            raw_err = str(e)
            redacted_err = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            err_code = f"ERR_{type(e).__name__.upper()}"
            if len(err_code) > 64:
                err_code = err_code[:64]
            return create_error_response(
                query_envelope,
                self.name,
                error_code=err_code,
                error_message=redacted_err,
            )

    def handle_cycle_get_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        session_id = query_envelope.payload.get("session_id")
        sequence = query_envelope.payload.get("sequence_number")
        if not session_id or sequence is None or self._storage_root is None:
            return create_error_response(
                query_envelope,
                self.name,
                error_code="ERR_INVALID_ARGUMENTS",
                error_message="Missing session_id or sequence_number, or storage uninitialized",
            )
        try:
            journal_path = validate_session_path(self._storage_root, session_id)
            entries, _ = JournalReader.read_and_recover_journal(journal_path)
            target_entry = next(
                (e for e in entries if e.sequence_number == int(sequence) and e.entry_type in (
                    JournalEntryType.CYCLE_COMPLETED,
                    JournalEntryType.CYCLE_DEGRADED,
                )),
                None,
            )
            if target_entry is None:
                return create_error_response(
                    query_envelope,
                    self.name,
                    error_code="ERR_CYCLE_NOT_FOUND",
                    error_message=f"Sequence {sequence} not found for session {session_id}",
                )
            return create_response(query_envelope, self.name, payload=dict(target_entry.payload))
        except PersistenceSecurityError as e:
            raw_err = str(e)
            redacted_err = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(
                query_envelope,
                self.name,
                error_code="ERR_PERSISTENCE_SECURITY_ERROR",
                error_message=redacted_err,
            )
        except Exception as e:
            raw_err = str(e)
            redacted_err = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            err_code = f"ERR_{type(e).__name__.upper()}"
            if len(err_code) > 64:
                err_code = err_code[:64]
            return create_error_response(
                query_envelope,
                self.name,
                error_code=err_code,
                error_message=redacted_err,
            )

    def handle_replay_command(self, command_envelope: MessageEnvelope) -> MessageEnvelope:
        session_id = command_envelope.payload.get("session_id")
        if not session_id:
            return create_error_response(
                command_envelope,
                self.name,
                error_code="INVALID_SESSION_ID",
                error_message="Missing session_id",
            )
        try:
            rep = self.replay_session(str(session_id))
            payload = {
                "session_id": rep.session_id,
                "replay_status": rep.replay_status.value,
                "total_entries_verified": rep.total_entries_verified,
                "hash_chain_valid": rep.hash_chain_valid,
                "state_divergences": list(rep.state_divergences),
            }
            return create_response(command_envelope, self.name, payload=payload)
        except Exception as e:
            raw_err = str(e)
            redacted_err = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err
            return create_error_response(
                command_envelope,
                self.name,
                error_code=type(e).__name__,
                error_message=redacted_err,
            )

    def handle_audit_query(self, query_envelope: MessageEnvelope) -> MessageEnvelope:
        if self._audit_store is None:
            return create_error_response(
                query_envelope,
                self.name,
                error_code="STORE_UNINITIALIZED",
                error_message="Audit store uninitialized",
            )
        records = [
            {
                "audit_id": r.audit_id,
                "epoch_id": r.epoch_id,
                "timestamp_utc": r.timestamp_utc,
                "audit_passed": r.audit_passed,
                "findings_count": len(r.findings),
            }
            for r in self._audit_store.records
        ]
        return create_response(query_envelope, self.name, payload={"audit_records": records})

    def _emit_event(self, topic: str, payload: Mapping[str, Any]) -> None:
        """Emit protocol event over dispatcher and record in event sink."""
        env = create_event(
            message_name=topic,
            source=self.name,
            payload=dict(payload),
        )

        if self._event_sink is not None:
            try:
                self._event_sink.record_event(env)
            except PersistenceCapacityError:
                pass

        if self._dispatcher is not None and getattr(self._dispatcher, "state", None) == DispatcherState.STARTED:
            self._dispatcher.dispatch(env)
