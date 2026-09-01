# -*- coding: utf-8 -*-
"""End-to-End Multimodal Integration Tests with Real Frozen M00-M07 Services."""

from __future__ import annotations

import pytest

from holomed.anatomy.service import AnatomyService
from holomed.audio.service import AudioService
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.gesture.service import GestureService
from holomed.platform.models import CycleStatus
from holomed.platform.service import PlatformService
from holomed.protocol.builders import create_command, create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus
from holomed.tools.service import ToolService
from holomed.ultron.service import UltronService
from holomed.vision.service import VisionService
from holomed.xr.service import XRService


def test_real_frozen_services_end_to_end_integration(
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify PlatformService coordinates actual instantiated frozen M00-M07 services."""
    # 1. Instantiate and start real DeviceManager
    dm = DeviceManager()
    dm.initialize(runtime_context)
    dm.start()

    # 2. Instantiate and start real domain services
    vs = VisionService(device_manager=dm)
    vs.initialize(runtime_context)
    vs.start()

    aus = AudioService(device_manager=dm)
    aus.initialize(runtime_context)
    aus.start()

    gs = GestureService(device_manager=dm)
    gs.initialize(runtime_context)
    gs.start()

    us = UltronService(device_manager=dm)
    us.initialize(runtime_context)
    us.start()

    ans = AnatomyService(device_manager=dm)
    ans.initialize(runtime_context)
    ans.start()

    xs = XRService(device_manager=dm)
    xs.initialize(runtime_context)
    xs.start()

    ts = ToolService(device_manager=dm)
    ts.initialize(runtime_context)
    ts.start()

    real_services = {
        "device_manager": dm,
        "vision_service": vs,
        "audio_service": aus,
        "gesture_service": gs,
        "ultron_service": us,
        "anatomy_service": ans,
        "xr_service": xs,
        "tool_service": ts,
    }

    # 3. Wire into real PlatformService
    ps = PlatformService(
        services=real_services,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    ps.initialize(runtime_context)
    message_dispatcher.start()
    ps.start()

    # 4. Whole-system health check with real services
    h = ps.health()
    assert h.status == HealthStatus.HEALTHY

    # 5. Whole-system consistency audit with real services
    audit_res = ps.audit_platform()
    assert audit_res["audit_passed"] is True
    assert audit_res["present_services_count"] == 8
    assert audit_res["findings_count"] == 0

    # 6. End-to-end cycle execution with real services
    summary = ps.tick(session_id="real_clinical_session", sequence_number=0)
    assert summary.status == CycleStatus.COMPLETED
    assert "PERCEPTION" in summary.phases_completed
    assert "INTERACTION" in summary.phases_completed
    assert "REASONING" in summary.phases_completed
    assert "ANATOMY" in summary.phases_completed
    assert "TOOLS" in summary.phases_completed
    assert "PRESENTATION" in summary.phases_completed
    assert summary.confidence == 1.0
    assert summary.diagnostic_message is None

    # 7. Test dispatcher query and commands with real services
    q_status = create_query("platform.status", "client")
    resp_status = message_dispatcher.dispatch(q_status)
    assert resp_status.message_type.value == "RESPONSE"
    assert resp_status.payload["aggregate_health"] == "HEALTHY"

    cmd_cycle = create_command(
        "platform.cycle",
        "client",
        payload={"session_id": "real_clinical_session", "sequence_number": 1},
    )
    resp_cycle = message_dispatcher.dispatch(cmd_cycle)
    assert resp_cycle.message_type.value == "RESPONSE"
    assert resp_cycle.payload["status"] == "COMPLETED"

    # 8. Clean teardown of all real services
    ps.stop()
    for s in (ts, xs, ans, us, gs, aus, vs, dm):
        s.stop()
