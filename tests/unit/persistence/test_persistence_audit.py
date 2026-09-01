# -*- coding: utf-8 -*-
"""Unit Tests for DurableAuditStore and Secret Redaction."""

from __future__ import annotations

from pathlib import Path
import pytest

from holomed.persistence.audit import DurableAuditStore
from holomed.persistence.exceptions import PersistenceCapacityError
from holomed.persistence.journal import JournalWriter
from holomed.persistence.models import MAX_AUDIT_RECORDS
from holomed.runtime.logging import SecretFilter


def test_secret_redaction_before_disk_write(temp_storage_root: Path, secret_filter: SecretFilter) -> None:
    """Verify secrets are redacted prior to persistence in audit records and journals."""
    store = DurableAuditStore(secret_filter=secret_filter)
    writer = JournalWriter(temp_storage_root, "sess_audit_redact", epoch_id=1)
    writer.initialize_storage()

    raw_report = {
        "audit_passed": False,
        "findings": ["Failure occurred with secret TOP_SECRET_PERSISTENCE_TOKEN in buffer"],
    }
    rec = store.record_audit(raw_report, epoch_id=1, journal_writer=writer)

    assert "TOP_SECRET_PERSISTENCE_TOKEN" not in rec.findings[0]
    assert "<redacted>" in rec.findings[0]

    # Verify journal on disk also does not contain raw secret
    with open(writer.journal_path, "r", encoding="utf-8") as f:
        disk_content = f.read()

    assert "TOP_SECRET_PERSISTENCE_TOKEN" not in disk_content
    assert "<redacted>" in disk_content


def test_audit_store_capacity() -> None:
    """Verify MAX_AUDIT_RECORDS limit."""
    store = DurableAuditStore()
    for i in range(MAX_AUDIT_RECORDS):
        store.record_audit({"audit_passed": True, "findings": []}, epoch_id=1)

    assert store.record_count == MAX_AUDIT_RECORDS

    with pytest.raises(PersistenceCapacityError):
        store.record_audit({"audit_passed": True, "findings": []}, epoch_id=1)
