# -*- coding: utf-8 -*-
"""End-to-End Integration Tests for Real PlatformService and PersistenceService."""

from __future__ import annotations

from pathlib import Path
import pytest

from holomed.anatomy.service import AnatomyService
from holomed.audio.service import AudioService
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.gesture.service import GestureService
from holomed.persistence.models import ReplayStatus
from holomed.persistence.service import PersistenceService
from holomed.platform.models import CycleStatus
from holomed.platform.service import PlatformService
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.tools.service import ToolService
from holomed.ultron.service import UltronService
from holomed.vision.service import VisionService
from holomed.xr.service import XRService


def test_real_platform_and_persistence_integration(
    temp_storage_root: Path,
    runtime_context: RuntimeContext,
    message_dispatcher: MessageDispatcher,
    secret_filter: SecretFilter,
) -> None:
    """Verify PlatformService and PersistenceService coordinate across real domain execution."""
    # 1. Setup real underlying domain services
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

    ps = PlatformService(
        services=real_services,
        dispatcher=message_dispatcher,
        secret_filter=secret_filter,
    )
    ps.initialize(runtime_context)

    # 2. Setup PersistenceService while dispatcher is in INITIALIZED state
    pers = PersistenceService(
        platform_service=ps,
        dispatcher=message_dispatcher,
        storage_root=temp_storage_root,
        secret_filter=secret_filter,
    )
    pers.initialize(runtime_context)

    # 3. Start dispatcher and services in topological order
    message_dispatcher.start()
    ps.start()
    pers.start()

    # 4. Start clinical session in both platform and persistence
    sess_id = "real_persist_session"
    ps.start_session(sess_id)
    pers.start_session(sess_id)

    # 5. Execute real cycle via PlatformService
    cycle_summary_0 = ps.tick(session_id=sess_id, sequence_number=0)
    assert cycle_summary_0.status == CycleStatus.COMPLETED

    # Record into persistence
    pers.record_cycle(session_id=sess_id, sequence_number=0, summary=cycle_summary_0)

    # Execute cycle 1
    cycle_summary_1 = ps.tick(session_id=sess_id, sequence_number=1)
    assert cycle_summary_1.status == CycleStatus.COMPLETED
    pers.record_cycle(session_id=sess_id, sequence_number=1, summary=cycle_summary_1)

    # Close session
    ps.stop_session(sess_id)
    pers.close_session(sess_id)

    # 6. Deterministic verification replay
    rep = pers.replay_session(sess_id)
    assert rep.replay_status == ReplayStatus.VERIFIED
    assert rep.hash_chain_valid is True
    assert rep.total_entries_verified == 4  # START, CYCLE_0, CYCLE_1, CLOSE
    assert rep.final_sequence == 1

    # 7. Teardown
    pers.stop()
    ps.stop()
    for s in (ts, xs, ans, us, gs, aus, vs, dm):
        s.stop()
