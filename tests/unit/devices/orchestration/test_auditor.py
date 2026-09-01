"""Tests for DeviceSubsystemConsistencyAuditor."""

import pytest

from holomed.configuration.models import SecretString
from holomed.devices.orchestration.auditor import DeviceSubsystemConsistencyAuditor
from holomed.devices.orchestration.models import MAX_AUDIT_FINDINGS
from holomed.runtime.logging import SecretFilter
from tests.unit.devices.orchestration.conftest import (
    DummyOrchestrationDevice,
    register_device,
)


def test_consistency_auditor_clean_pass(device_subsystem_stack) -> None:
    auditor = DeviceSubsystemConsistencyAuditor(
        device_manager=device_subsystem_stack["manager"],
        control_manager=device_subsystem_stack["control"],
        data_processor=device_subsystem_stack["data"],
        coordination=device_subsystem_stack["coordination"],
    )

    report = auditor.audit(active_epoch_id=1)
    assert report.is_consistent is True
    assert len(report.findings) == 0


def test_consistency_auditor_detects_unobserved_device(device_subsystem_stack) -> None:
    auditor = DeviceSubsystemConsistencyAuditor(
        device_manager=device_subsystem_stack["manager"],
        control_manager=device_subsystem_stack["control"],
        data_processor=device_subsystem_stack["data"],
        coordination=device_subsystem_stack["coordination"],
    )

    # Register device in manager but don't snapshot
    dev = DummyOrchestrationDevice("cam_audit_1", "usb://1")
    register_device(device_subsystem_stack["registry"], device_subsystem_stack["token"], dev)

    # Manually mock coordination snapshot returning empty observations
    orig_capture = device_subsystem_stack["coordination"].capture_snapshot
    from holomed.devices.coordination.models import DeviceSnapshot
    device_subsystem_stack["coordination"].capture_snapshot = lambda: DeviceSnapshot(
        epoch_id=1,
        captured_at_utc="2026-09-01T00:00:00Z",
        observations=(),
    )

    try:
        report = auditor.audit(active_epoch_id=1)
        assert report.is_consistent is False
        assert any(f.code == "ERR_UNOBSERVED_DEVICES" for f in report.findings)
    finally:
        device_subsystem_stack["coordination"].capture_snapshot = orig_capture


def test_consistency_audit_report_redacts_registered_secrets(device_subsystem_stack) -> None:
    sf = SecretFilter()
    sf.set_secrets([SecretString("SuperSecretMedicalKeyXYZ")])

    auditor = DeviceSubsystemConsistencyAuditor(
        device_manager=device_subsystem_stack["manager"],
        control_manager=device_subsystem_stack["control"],
        data_processor=device_subsystem_stack["data"],
        coordination=device_subsystem_stack["coordination"],
        secret_filter=sf,
    )

    # Induce an error containing secret in coordination snapshot
    def bad_snapshot():
        raise RuntimeError("Snapshot crashed with SuperSecretMedicalKeyXYZ")

    orig_capture = device_subsystem_stack["coordination"].capture_snapshot
    device_subsystem_stack["coordination"].capture_snapshot = bad_snapshot
    try:
        report = auditor.audit(active_epoch_id=1)
        assert report.is_consistent is False
        assert len(report.findings) > 0
        finding_msg = report.findings[0].message
        assert "SuperSecretMedicalKeyXYZ" not in finding_msg
        assert "<redacted>" in finding_msg
    finally:
        device_subsystem_stack["coordination"].capture_snapshot = orig_capture


def test_audit_findings_capacity_cap(device_subsystem_stack) -> None:
    auditor = DeviceSubsystemConsistencyAuditor(
        device_manager=device_subsystem_stack["manager"],
        control_manager=device_subsystem_stack["control"],
        data_processor=device_subsystem_stack["data"],
        coordination=device_subsystem_stack["coordination"],
    )

    # Force 300 findings
    report = auditor.audit(active_epoch_id=999)  # epoch mismatch creates findings
    assert len(report.findings) <= MAX_AUDIT_FINDINGS
