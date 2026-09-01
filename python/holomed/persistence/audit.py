# -*- coding: utf-8 -*-
"""Durable Consistency Audit Store for M09."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional
import uuid

from holomed.persistence.exceptions import (
    PersistenceCapacityError,
    PersistenceValidationError,
)
from holomed.persistence.journal import JournalWriter
from holomed.persistence.models import (
    MAX_AUDIT_RECORDS,
    PERSISTENCE_SCHEMA_VERSION,
    DurableAuditRecord,
    JournalEntryType,
)
from holomed.runtime.logging import SecretFilter


class DurableAuditStore:
    """Manages durable audit recording and secret redaction."""

    def __init__(self, secret_filter: Optional[SecretFilter] = None) -> None:
        self._secret_filter = secret_filter
        self._records: list[DurableAuditRecord] = []

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def records(self) -> tuple[DurableAuditRecord, ...]:
        return tuple(self._records)

    def record_audit(
        self,
        audit_report: Mapping[str, Any],
        epoch_id: int,
        journal_writer: Optional[JournalWriter] = None,
    ) -> DurableAuditRecord:
        """Persist a sanitized whole-system consistency audit record."""
        if len(self._records) >= MAX_AUDIT_RECORDS:
            raise PersistenceCapacityError(f"Audit record capacity exceeded ({MAX_AUDIT_RECORDS} max)")

        raw_findings = audit_report.get("findings", [])
        sanitized_findings = tuple(
            self._secret_filter.redact(str(f)) if self._secret_filter else str(f)
            for f in raw_findings
        )

        audit_id = str(uuid.uuid4())
        now_utc = datetime.now(timezone.utc).isoformat()

        rec = DurableAuditRecord(
            audit_id=audit_id,
            epoch_id=epoch_id,
            timestamp_utc=now_utc,
            audit_passed=bool(audit_report.get("audit_passed", False)),
            required_services_count=int(audit_report.get("required_services_count", 0)),
            present_services_count=int(audit_report.get("present_services_count", 0)),
            active_sessions_count=int(audit_report.get("active_sessions_count", 0)),
            cycle_history_count=int(audit_report.get("cycle_history_count", 0)),
            findings=sanitized_findings,
            schema_version=PERSISTENCE_SCHEMA_VERSION,
        )

        if journal_writer is not None:
            journal_writer.append_entry(
                entry_type=JournalEntryType.AUDIT_SNAPSHOT,
                sequence_number=journal_writer.last_sequence,
                timestamp_utc=now_utc,
                payload={
                    "audit_id": rec.audit_id,
                    "audit_passed": rec.audit_passed,
                    "findings_count": len(rec.findings),
                    "findings": list(rec.findings),
                },
            )

        self._records.append(rec)
        return rec

    def clear(self) -> None:
        self._records.clear()
