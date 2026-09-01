# -*- coding: utf-8 -*-
"""Unit tests for PlatformConsistencyAuditor."""

from __future__ import annotations

import pytest

from holomed.platform.auditor import PlatformConsistencyAuditor
from holomed.platform.cycle import CycleCoordinator
from holomed.platform.session import SessionManager
from holomed.runtime.logging import SecretFilter
from holomed.runtime.service import IService, ServiceState


def test_platform_consistency_auditor_clean(
    mock_domain_services: dict[str, IService],
    secret_filter: SecretFilter,
) -> None:
    """Verify auditor generates a passing audit report when all services are aligned."""
    auditor = PlatformConsistencyAuditor(secret_filter=secret_filter)
    sm = SessionManager(epoch_id=1)
    cc = CycleCoordinator(epoch_id=1)
    states = {k: ServiceState.STARTED for k in mock_domain_services}

    # Add device_manager to service list
    full_services = dict(mock_domain_services)
    full_services["device_manager"] = mock_domain_services["vision_service"]
    states["device_manager"] = ServiceState.STARTED

    report = auditor.audit_platform(
        services=full_services,
        service_states=states,
        session_manager=sm,
        cycle_coordinator=cc,
        epoch_id=1,
    )

    assert report["audit_passed"] is True
    assert report["findings_count"] == 0
