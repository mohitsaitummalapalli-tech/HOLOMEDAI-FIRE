# -*- coding: utf-8 -*-
"""End-to-End Real Integration Tests for PlatformService, PersistenceService, and WorkflowService."""

from __future__ import annotations

from pathlib import Path
import pytest

from holomed.anatomy.service import AnatomyService
from holomed.audio.service import AudioService
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.gesture.service import GestureService
from holomed.persistence.service import PersistenceService
from holomed.platform.models import CycleStatus
from holomed.platform.service import PlatformService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.tools.service import ToolService
from holomed.ultron.service import UltronService
from holomed.vision.service import VisionService
from holomed.workflow.models import ConfirmationResponse, WorkflowPhase
from holomed.workflow.service import WorkflowService
from holomed.xr.service import XRService


def test_real_platform_persistence_and_workflow_integration(
    temp_storage_root: Path,
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify end-to-end coordination across all 10 milestones (M00–M10)."""
    # 1. Setup real domain services (M00–M07)
    dm = DeviceManager()
    dm.initialize(runtime_context)
    dm.start()

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

    # 2. Setup PlatformService (M08)
    ps = PlatformService(
        services=real_services,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    ps.initialize(runtime_context)

    # 3. Setup PersistenceService (M09)
    pers = PersistenceService(
        platform_service=ps,
        dispatcher=message_dispatcher,
        storage_root=temp_storage_root,
        secret_filter=secret_filter,
    )
    pers.initialize(runtime_context)

    # 4. Setup WorkflowService (M10) while dispatcher is INITIALIZED
    wf = WorkflowService(
        platform_service=ps,
        persistence_service=pers,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    wf.initialize(runtime_context)

    # 5. Start dispatcher and apex services
    message_dispatcher.start()
    ps.start()
    pers.start()
    wf.start()

    # 6. Start clinical session
    sess_id = "real_surgical_procedure_01"
    ps.start_session(sess_id)
    pers.start_session(sess_id)
    wf_snap0 = wf.start_workflow(sess_id)
    assert wf_snap0.current_phase == WorkflowPhase.PATIENT_CONTEXT

    # Execute platform cycle 0
    c0 = ps.tick(sess_id, sequence_number=0)
    assert c0.status == CycleStatus.COMPLETED
    pers.record_cycle(sess_id, 0, c0)

    # Advance workflow: PATIENT_CONTEXT -> PRE_PROCEDURE_PLANNING
    wf.transition_phase(sess_id, WorkflowPhase.PRE_PROCEDURE_PLANNING, sequence_number=1)

    # Execute cycle 1
    c1 = ps.tick(sess_id, sequence_number=1)
    pers.record_cycle(sess_id, 1, c1)

    # Advance workflow: PRE_PROCEDURE_PLANNING -> REGISTRATION
    wf.transition_phase(sess_id, WorkflowPhase.REGISTRATION, sequence_number=2)

    # Advance workflow: REGISTRATION -> SAFETY_TIMEOUT
    wf.transition_phase(sess_id, WorkflowPhase.SAFETY_TIMEOUT, sequence_number=3)

    # Safety Timeout requires confirmation to enter NAVIGATION
    req = wf.request_confirmation(sess_id, WorkflowPhase.NAVIGATION, "Checklist verified", sequence_number=4)
    resp = ConfirmationResponse(
        confirmation_id=req.confirmation_id,
        session_id=sess_id,
        epoch_id=1,
        approved=True,
        operator_id="lead_surgeon",
        timestamp_utc="2026-09-01T20:00:00Z",
        sequence_number=5,
    )
    wf.confirm(resp)

    # Enter NAVIGATION
    wf_snap_nav = wf.transition_phase(sess_id, WorkflowPhase.NAVIGATION, sequence_number=6)
    assert wf_snap_nav.current_phase == WorkflowPhase.NAVIGATION

    # 7. Close and verify replay in persistence
    ps.stop_session(sess_id)
    pers.close_session(sess_id)
    replay_report = pers.replay_session(sess_id)
    assert replay_report.hash_chain_valid is True

    # 8. Clean Teardown
    wf.stop()
    pers.stop()
    ps.stop()
    for s in (ts, xs, ans, us, gs, aus, vs, dm):
        s.stop()
