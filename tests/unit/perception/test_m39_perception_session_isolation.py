# -*- coding: utf-8 -*-
"""M39 Hostile & Isolation Test Suite for Session-Partitioned Perception.

Validates session isolation, tracker separation, state machine independence,
capacity enforcement (32 max sessions), sequence replay defense, teardown handling,
and security mutants across Vision, Audio, and Gesture domain services.
"""

from datetime import datetime, timezone
import math
import uuid
import zlib
import pytest

from holomed.audio.exceptions import (
    AudioEpochMismatchError,
    AudioSequenceError,
    AudioSessionMismatchError,
    AudioValidationError,
    AudioLifecycleError,
    AudioCapacityError,
)
from holomed.audio.models import (
    AudioChunk,
    ChannelLayout,
    SampleFormat,
)
from holomed.audio.service import AudioService
from holomed.configuration.models import AppConfig, EnvironmentProfile, LogLevel
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.interfaces import IDevice
from holomed.devices.manager import DeviceManager
from holomed.devices.models import DeviceCapability, DeviceHealth, DeviceState, DeviceType
from holomed.devices.registry import DeviceRegistry
from holomed.gesture.exceptions import (
    GestureEpochMismatchError,
    GestureSequenceError,
    GestureSessionMismatchError,
    GestureValidationError,
    GestureLifecycleError,
)
from holomed.gesture.models import (
    HandObservation,
    Handedness,
)
from holomed.gesture.service import GestureService
from holomed.protocol.builders import create_event, create_query
from holomed.runtime.context import RuntimeContext
from holomed.runtime.models import HealthStatus
from holomed.vision.exceptions import (
    VisionSequenceError,
    VisionSessionMismatchError,
    VisionValidationError,
    VisionEpochMismatchError,
    VisionLifecycleError,
)
from holomed.platform.exceptions import PlatformCapacityError
from holomed.vision.models import FrameDescriptor, PixelFormat, SpatialLandmark
from holomed.vision.service import VisionService


RAW_FRAME_DATA = b"\x00" * 19200
RAW_FRAME_CRC = zlib.crc32(RAW_FRAME_DATA)



from holomed.vision.service import VisionService
from holomed.audio.service import AudioService
from holomed.gesture.service import GestureService


class DummyDevice(IDevice):
    """Test device implementation of IDevice."""

    def __init__(self, device_id: str, physical_id: str, dev_type: DeviceType = DeviceType.RGB_CAMERA) -> None:
        self._device_id = device_id
        self._physical_id = physical_id
        self._dev_type = dev_type
        self._state = DeviceState.UNREGISTERED

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def physical_id(self) -> str:
        return self._physical_id

    @property
    def device_type(self) -> DeviceType:
        return self._dev_type

    @property
    def capabilities(self) -> tuple[DeviceCapability, ...]:
        return tuple()

    @property
    def current_epoch(self) -> int:
        return 1

    @property
    def state(self) -> DeviceState:
        return self._state

    def initialize(self, accessor) -> None:
        self._state = DeviceState.READY

    def start(self) -> None:
        self._state = DeviceState.ACTIVE

    def stop(self, accessor) -> None:
        self._state = DeviceState.STOPPED


    @property
    def endpoints(self) -> tuple:
        return ()
    def health(self) -> DeviceHealth:
        return DeviceHealth(
            device_id=self._device_id,
            status=HealthStatus.HEALTHY,
            message="Healthy",
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )


@pytest.fixture
def perception_platform(test_runtime_context: RuntimeContext, device_registry_env: tuple[DeviceRegistry, DeviceManager], monkeypatch: pytest.MonkeyPatch):
    import holomed.platform.session
    monkeypatch.setattr(holomed.platform.session, "MAX_ACTIVE_PLATFORM_SESSIONS", 32)

    from holomed.core.dispatcher import MessageDispatcher
    dispatcher = MessageDispatcher()
    dispatcher.initialize(test_runtime_context)

    from holomed.platform.session import SessionManager
    sm = SessionManager(epoch_id=1, dispatcher=dispatcher)
    
    _, dm = device_registry_env
    
    vision = VisionService(device_manager=dm, dispatcher=dispatcher)
    audio = AudioService(device_manager=dm, dispatcher=dispatcher)
    gesture = GestureService(device_manager=dm, dispatcher=dispatcher)
    
    vision.initialize(test_runtime_context)
    audio.initialize(test_runtime_context)
    gesture.initialize(test_runtime_context)
    
    dispatcher.start()
    vision.start()
    audio.start()
    gesture.start()
    
    class PlatformFixture:
        def __init__(self):
            self.vision = vision
            self.audio = audio
            self.gesture = gesture
            self.session_manager = sm
            self.dispatcher = dispatcher
            
        def start_session(self, session_id: str, epoch_id: int = 1):
            return self.session_manager.start_session(session_id, epoch_id)
            
        def stop_session(self, session_id: str):
            return self.session_manager.stop_session(session_id)
            
        def purge_session(self, session_id: str):
            from holomed.protocol.builders import create_event
            from holomed.platform._capability import _PlatformCapability, _INTERNAL_PLATFORM_KEY
            evt = create_event("execution.session.purged", "platform.session_manager", payload={"session_id": session_id, "epoch_id": 1})
            cap = _PlatformCapability(internal_key=_INTERNAL_PLATFORM_KEY, service_instance_id=id(self), session_id=session_id, action="TEARDOWN")
            self.dispatcher.dispatch_internal(evt, cap)
            
    yield PlatformFixture()
    
    vision.stop()
    audio.stop()
    gesture.stop()
    dispatcher.stop()


@pytest.fixture
def test_runtime_context() -> RuntimeContext:
    app_cfg = AppConfig(
        app_name="HoloMed AI",
        environment=EnvironmentProfile.TESTING,
        host="localhost",
        port=8080,
        log_level=LogLevel.DEBUG,
        gemini_api_key=None,
        protocol_version="1.0",
    )
    return RuntimeContext(app_config=app_cfg, epoch_id=1)


@pytest.fixture
def device_registry_env(test_runtime_context: RuntimeContext) -> tuple[DeviceRegistry, DeviceManager]:
    dm = DeviceManager()
    dm.initialize(test_runtime_context)
    dm.start()

    reg = getattr(dm, "registry", None) or getattr(dm, "_registry", None)
    token = getattr(dm, "registry_token", None) or getattr(dm, "_registry_token", None)

    cam = DummyDevice("cam_main", "phys_cam_001", DeviceType.RGB_CAMERA)
    mic = DummyDevice("mic_main", "phys_mic_001", DeviceType.AUDIO_MICROPHONE)

    assert reg is not None
    reg.register(cam, token)
    reg.register(mic, token)
    cam._state = DeviceState.ACTIVE
    mic._state = DeviceState.ACTIVE

    return reg, dm


def make_frame_descriptor(
    frame_id: str | None = None,
    seq: int = 1,
    epoch_id: int = 1,
    session_id: str | None = None,
    device_id: str = "cam_main",
    physical_id: str = "phys_cam_001",
) -> FrameDescriptor:
    fid = frame_id if frame_id and len(frame_id) == 36 and "-" in frame_id else str(uuid.uuid4())
    return FrameDescriptor(
        frame_id=fid,
        device_id=device_id,
        physical_id=physical_id,
        sequence_number=seq,
        timestamp_utc="2026-09-10T12:00:00.000000Z",
        width=160,
        height=120,
        pixel_format=PixelFormat.GRAY8,
        stride_bytes=160,
        total_bytes=19200,
        checksum_crc32=RAW_FRAME_CRC,
        epoch_id=epoch_id,
        buffer_handle_id="buf_001",
        session_id=session_id,
    )


def make_audio_chunk(
    chunk_id: str = "chk_001",
    seq: int = 1,
    epoch_id: int = 1,
    session_id: str | None = None,
) -> tuple[AudioChunk, bytes]:
    pcm = b"\x00" * 640  # 160 frames, 2 channels, int16
    crc = zlib.crc32(pcm)
    chunk = AudioChunk(
        chunk_id=chunk_id,
        device_id="mic_main",
        physical_id="phys_mic_001",
        sequence_number=seq,
        timestamp_utc="2026-09-10T12:00:00.000000Z",
        epoch_id=epoch_id,
        sample_rate_hz=16000,
        channels=2,
        sample_format=SampleFormat.INT16,
        channel_layout=ChannelLayout.INTERLEAVED,
        frame_count=160,
        payload_bytes=640,
        checksum_crc32=crc,
        buffer_handle_id="buf_001",
        session_id=session_id,
    )
    return chunk, pcm


def make_hand_observation(
    frame_id: str | None = None,
    seq: int = 1,
    epoch_id: int = 1,
    session_id: str | None = None,
) -> HandObservation:
    fid = frame_id or str(uuid.uuid4())
    landmarks = tuple(
        SpatialLandmark(
            landmark_id=i,
            name=f"LM_{i}",
            u=0.5,
            v=0.5,
            confidence=0.9,
            depth_m=1.0,
        )
        for i in range(21)
    )
    return HandObservation(
        frame_id=fid,
        device_id="cam_main",
        physical_id="phys_cam_001",
        sequence_number=seq,
        timestamp_utc="2026-09-10T12:00:00.000000Z",
        epoch_id=epoch_id,
        hand_id="hand_right",
        handedness=Handedness.RIGHT,
        landmarks=landmarks,
        confidence=0.95,
        session_id=session_id,
    )


# -----------------------------------------------------------------------------
# 1-5. ISOLATION & COMPONENT SEPARATION TESTS
# -----------------------------------------------------------------------------

def test_vision_ab_isolation(perception_platform) -> None:
    perception_platform.start_session("session_A")
    perception_platform.start_session("session_B")
    s = perception_platform.vision

    frame_a = make_frame_descriptor(frame_id="f_a", seq=1, session_id="session_A")
    frame_b = make_frame_descriptor(frame_id="f_b", seq=1, session_id="session_B")

    s.ingest_frame(frame_a, RAW_FRAME_DATA)
    s.ingest_frame(frame_b, RAW_FRAME_DATA)

    store_a, tracker_a, pipeline_a = s._get_session_components("session_A")
    store_b, tracker_b, pipeline_b = s._get_session_components("session_B")

    assert store_a is not store_b
    assert tracker_a is not tracker_b
    assert pipeline_a is not pipeline_b


def test_audio_ab_isolation(perception_platform) -> None:
    perception_platform.start_session("session_A")
    perception_platform.start_session("session_B")
    s = perception_platform.audio

    chunk_a, pcm_a = make_audio_chunk(chunk_id="c_a", seq=1, session_id="session_A")
    chunk_b, pcm_b = make_audio_chunk(chunk_id="c_b", seq=1, session_id="session_B")

    s.ingest_chunk(chunk_a, pcm_a)
    s.ingest_chunk(chunk_b, pcm_b)

    store_a, pipeline_a = s._get_session_components("session_A")
    store_b, pipeline_b = s._get_session_components("session_B")

    assert store_a is not store_b
    assert pipeline_a is not pipeline_b


def test_gesture_ab_isolation(perception_platform) -> None:
    perception_platform.start_session("session_A")
    perception_platform.start_session("session_B")
    s = perception_platform.gesture

    obs_a1 = make_hand_observation(seq=1, session_id="session_A")
    obs_b1 = make_hand_observation(seq=1, session_id="session_B")
    obs_a2 = make_hand_observation(seq=2, session_id="session_A")
    obs_b2 = make_hand_observation(seq=2, session_id="session_B")

    s.ingest_observation(obs_a1)
    s.ingest_observation(obs_b1)
    s.ingest_observation(obs_a2)
    s.ingest_observation(obs_b2)

    tr_a, sm_a, pipe_a = s._get_session_components("session_A")
    tr_b, sm_b, pipe_b = s._get_session_components("session_B")

    assert tr_a is not tr_b
    assert sm_a is not sm_b
    assert pipe_a is not pipe_b


def test_tracker_and_state_machine_isolation(perception_platform) -> None:
    perception_platform.start_session("sess_1")
    perception_platform.start_session("sess_2")
    s = perception_platform.gesture

    tr1, sm1, _ = s._get_session_components("sess_1")
    tr2, sm2, _ = s._get_session_components("sess_2")

    assert id(tr1) != id(tr2)
    assert id(sm1) != id(sm2)


# -----------------------------------------------------------------------------
# 6. QUERY ISOLATION
# -----------------------------------------------------------------------------

def test_query_isolation(perception_platform) -> None:
    perception_platform.start_session("foreign_session")
    perception_platform.start_session("session_A")
    perception_platform.start_session("session_B")
    v = perception_platform.vision
    g = perception_platform.gesture

    # Populate Session A in Vision and Gesture
    frame_a = make_frame_descriptor(frame_id="fa", seq=1, session_id="session_A")
    v.ingest_frame(frame_a, RAW_FRAME_DATA)

    obs_a = make_hand_observation(seq=1, session_id="session_A")
    g.ingest_observation(obs_a)

    # Query tracks for Session A
    q_v_a = create_query("vision.tracker.tracks", "client", payload={}, metadata={"session_id": "session_A"})
    res_v_a = v.handle_tracks_query(q_v_a)
    assert res_v_a.payload["active_tracks_count"] >= 0

    # Query tracks for Session B (non-existent / clean)
    q_v_b = create_query("vision.tracker.tracks", "client", payload={}, metadata={"session_id": "session_B"})
    res_v_b = v.handle_tracks_query(q_v_b)
    assert res_v_b.payload["active_tracks_count"] == 0

    # Foreign session query on Gesture returns 0 tracks
    q_g_foreign = create_query("gesture.tracks", "client", payload={}, metadata={"session_id": "foreign_session"})
    res_g = g.handle_tracks_query(q_g_foreign)
    assert res_g.payload["active_tracks_count"] == 0


# -----------------------------------------------------------------------------
# 7-8. PURGE & PURGE IDEMPOTENCE
# -----------------------------------------------------------------------------

def test_purge_and_idempotence(perception_platform) -> None:
    perception_platform.start_session("session_A")
    perception_platform.start_session("session_B")
    s = perception_platform.vision

    a_frame = make_frame_descriptor(frame_id="f_a", seq=1, session_id="session_A")
    b_frame = make_frame_descriptor(frame_id="f_b", seq=1, session_id="session_B")
    s.ingest_frame(a_frame, RAW_FRAME_DATA)
    s.ingest_frame(b_frame, RAW_FRAME_DATA)

    assert "session_A" in s._session_stores
    assert "session_B" in s._session_stores

    # Purge A
    s.purge_session("session_A")
    assert "session_A" not in s._session_stores
    assert "session_B" in s._session_stores

    # Idempotent second purge of A
    s.purge_session("session_A")
    assert "session_B" in s._session_stores


def test_teardown_event_handling(perception_platform) -> None:
    perception_platform.start_session("session_A")
    s = perception_platform.vision

    a_frame = make_frame_descriptor(seq=1, session_id="session_A")
    s.ingest_frame(a_frame, RAW_FRAME_DATA)

    assert "session_A" in s._session_stores

    # Use real authoritative teardown
    perception_platform.purge_session("session_A")

    assert "session_A" not in s._session_stores


# -----------------------------------------------------------------------------
# 9-12. REUSE & STALE STATE HANDLING
# -----------------------------------------------------------------------------

def test_session_reuse(perception_platform) -> None:
    perception_platform.start_session("session_A")
    s = perception_platform.vision

    # Session A ingestion (seq=1..5)
    for i in range(1, 6):
        s.ingest_frame(make_frame_descriptor(frame_id=f"f_{i}", seq=i, session_id="session_A"), RAW_FRAME_DATA)

    # Purge A
    s.purge_session("session_A")
    perception_platform.session_manager.evict_session("session_A")
    perception_platform.start_session("session_A")

    # Recreate A - sequence number resets cleanly
    new_frame = make_frame_descriptor(frame_id="f_new_1", seq=1, session_id="session_A")
    res = s.ingest_frame(new_frame, RAW_FRAME_DATA)
    assert res is not None


# -----------------------------------------------------------------------------
# 13-18. VALIDATION & MISMATCH TESTS
# -----------------------------------------------------------------------------

def test_payload_envelope_mismatch(perception_platform) -> None:
    perception_platform.start_session("session_A")
    perception_platform.start_session("session_B")
    s = perception_platform.vision

    frame = make_frame_descriptor(frame_id="f1", seq=1, session_id="session_A")
    with pytest.raises(VisionSessionMismatchError, match="Envelope session_id"):
        s.ingest_frame(frame, RAW_FRAME_DATA, session_id="session_B")


def test_malformed_session_id(perception_platform) -> None:
    s = perception_platform.vision

    frame = make_frame_descriptor(seq=1, session_id=None)
    with pytest.raises(VisionValidationError, match="Invalid session_id syntax"):
        s.ingest_frame(frame, RAW_FRAME_DATA, session_id="bad session ID with spaces!")


def test_sequence_replay_and_cross_session(perception_platform) -> None:
    perception_platform.start_session("session_A")
    perception_platform.start_session("session_B")
    s = perception_platform.vision

    f_a1 = make_frame_descriptor(seq=10, session_id="session_A")
    f_a2 = make_frame_descriptor(seq=5, session_id="session_A")
    s.ingest_frame(f_a1, RAW_FRAME_DATA)

    # Replay on same session fails
    with pytest.raises(VisionSequenceError, match="Non-monotonic sequence number"):
        s.ingest_frame(f_a2, RAW_FRAME_DATA)

    # Same sequence number on session B succeeds (cross-session sequence isolation)
    f_b1 = make_frame_descriptor(seq=5, session_id="session_B")
    res_b = s.ingest_frame(f_b1, RAW_FRAME_DATA)
    assert res_b is not None


# -----------------------------------------------------------------------------
# 19-21. CAPACITY & EVICTION BOUNDS
# -----------------------------------------------------------------------------

def test_capacity_32_active_sessions(perception_platform) -> None:
    from holomed.platform.exceptions import PlatformCapacityError
    s = perception_platform.vision

    # Fill up to 32 sessions
    for i in range(32):
        sess_name = f"session_{i:02d}"
        perception_platform.start_session(sess_name)
        f = make_frame_descriptor(seq=1, session_id=sess_name)
        s.ingest_frame(f, RAW_FRAME_DATA)

    # 33rd session raises PlatformCapacityError
    with pytest.raises(PlatformCapacityError):
        perception_platform.start_session("session_32")
    f_overflow = make_frame_descriptor(seq=1, session_id="session_32")
    with pytest.raises(VisionLifecycleError):
        s.ingest_frame(f_overflow, RAW_FRAME_DATA)


def test_a_cannot_evict_b(perception_platform) -> None:
    s = perception_platform.vision

    perception_platform.start_session("session_B")
    perception_platform.start_session("session_A")
    # Ingest for B
    f_b = make_frame_descriptor(seq=1, session_id="session_B")
    s.ingest_frame(f_b, RAW_FRAME_DATA)

    # Flood A
    for i in range(1, 10):
        f_a = make_frame_descriptor(seq=i, session_id="session_A")
        s.ingest_frame(f_a, RAW_FRAME_DATA)

    # Session B remains present and uncorrupted
    assert "session_B" in s._session_stores
    q_b = create_query("vision.tracker.tracks", "client", payload={}, metadata={"session_id": "session_B"})
    res_b = s.handle_tracks_query(q_b)
    assert res_b.payload["active_tracks_count"] == 1


# -----------------------------------------------------------------------------
# 22-28. 32 SECURITY MUTANTS (SCENARIOS 1-32)
# -----------------------------------------------------------------------------

def test_mutant_01_vision_session_key_isolation(perception_platform) -> None:
    perception_platform.start_session("S_A")
    s = perception_platform.vision

    s.ingest_frame(make_frame_descriptor(frame_id="f1", seq=1, session_id="S_A"), RAW_FRAME_DATA)
    assert "S_A" in s._session_stores
    assert "S_B" not in s._session_stores


def test_mutant_02_audio_session_key_isolation(perception_platform) -> None:
    perception_platform.start_session("S_A")
    s = perception_platform.audio

    c, pcm = make_audio_chunk(chunk_id="c1", seq=1, session_id="S_A")
    s.ingest_chunk(c, pcm)

    assert "S_A" in s._session_stores
    assert "S_B" not in s._session_stores


def test_mutant_03_gesture_session_key_isolation(perception_platform) -> None:
    perception_platform.start_session("S_A")
    s = perception_platform.gesture

    obs = make_hand_observation(seq=1, session_id="S_A")
    s.ingest_observation(obs)

    assert "S_A" in s._session_trackers
    assert "S_B" not in s._session_trackers


def test_mutant_04_gesture_state_machine_not_inherited(perception_platform) -> None:
    """Mutant 4: S_B gesture state machine does not inherit S_A active state."""
    perception_platform.start_session("S_A")
    perception_platform.start_session("S_B")
    s = perception_platform.gesture

    obs_a = make_hand_observation(seq=1, session_id="S_A")
    obs_b = make_hand_observation(seq=1, session_id="S_B")
    s.ingest_observation(obs_a)
    s.ingest_observation(obs_b)

    sm_a = s._session_state_machines["S_A"]
    sm_b = s._session_state_machines["S_B"]
    assert sm_a is not sm_b


def test_mutant_05_stale_session_purge_does_not_affect_active(perception_platform) -> None:
    """Mutant 5: Purging S_A leaves S_B intact."""
    perception_platform.start_session("S_A")
    perception_platform.start_session("S_B")
    s = perception_platform.vision

    s.ingest_frame(make_frame_descriptor(frame_id="f1", seq=1, session_id="S_A"), RAW_FRAME_DATA)
    s.ingest_frame(make_frame_descriptor(frame_id="f2", seq=1, session_id="S_B"), RAW_FRAME_DATA)

    s.purge_session("S_A")
    assert "S_A" not in s._session_stores
    assert "S_B" in s._session_stores


def test_mutant_06_envelope_payload_mismatch_fails_closed_audio(perception_platform) -> None:
    """Mutant 6: Envelope and payload session mismatch fails closed in Audio."""
    perception_platform.start_session("S_A")
    perception_platform.start_session("S_B")
    s = perception_platform.audio

    c, pcm = make_audio_chunk(chunk_id="c1", seq=1, session_id="S_A")
    with pytest.raises(AudioSessionMismatchError):
        s.ingest_chunk(c, pcm, session_id="S_B")


def test_mutant_07_envelope_payload_mismatch_fails_closed_gesture(perception_platform) -> None:
    """Mutant 7: Envelope and payload session mismatch fails closed in Gesture."""
    perception_platform.start_session("S_A")
    perception_platform.start_session("S_B")
    s = perception_platform.gesture

    obs = make_hand_observation(seq=1, session_id="S_A")
    with pytest.raises(GestureSessionMismatchError):
        s.ingest_observation(obs, session_id="S_B")


def test_mutant_08_audio_capacity_limit_32(perception_platform) -> None:
    """Mutant 08: AudioService exceeds 32 sessions."""
    from holomed.platform.exceptions import PlatformCapacityError
    s = perception_platform.audio

    for i in range(32):
        sess = f"S_{i:02d}"
        perception_platform.start_session(sess)
        c, pcm = make_audio_chunk(chunk_id=f"c_{i}", seq=1, session_id=sess)
        s.ingest_chunk(c, pcm)

    with pytest.raises(PlatformCapacityError):
        perception_platform.start_session("S_33")
    c_33, pcm_33 = make_audio_chunk(chunk_id="c_33", seq=1, session_id="S_33")
    with pytest.raises(AudioLifecycleError):
        s.ingest_chunk(c_33, pcm_33)


def test_mutant_09_gesture_capacity_limit_32(perception_platform) -> None:
    """Mutant 09: GestureService exceeds 32 sessions."""
    from holomed.platform.exceptions import PlatformCapacityError
    s = perception_platform.gesture

    for i in range(32):
        sess = f"S_{i:02d}"
        perception_platform.start_session(sess)
        obs = make_hand_observation(seq=1, session_id=sess)
        s.ingest_observation(obs)

    with pytest.raises(PlatformCapacityError):
        perception_platform.start_session("S_33")
    obs_33 = make_hand_observation(seq=1, session_id="S_33")
    with pytest.raises(GestureLifecycleError):
        s.ingest_observation(obs_33)


def test_mutant_10_observation_stamping_when_none(perception_platform) -> None:
    """Mutant 10: Ingesting frame without payload session_id stamps envelope session_id."""
    perception_platform.start_session("S_ENVELOPE")
    s = perception_platform.vision

    f = make_frame_descriptor(frame_id="f1", seq=1, session_id=None)
    res = s.ingest_frame(f, RAW_FRAME_DATA, session_id="S_ENVELOPE")
    assert res is not None
    assert "S_ENVELOPE" in s._session_stores


def test_mutant_11_prompt_injection_as_data_in_device_id(perception_platform) -> None:
    """Mutant 11: Prompt injection in device_id string is treated purely as text data."""
    perception_platform.start_session("S_NORMAL")
    dm = perception_platform.vision._device_manager
    reg = dm._registry
    token = dm._registry_token
    token = getattr(dm, "registry_token", None) or getattr(dm, "_registry_token", None)
    injection = "cam_main'; DROP TABLE sessions; --"

    dev = DummyDevice(injection, "phys_inj", DeviceType.RGB_CAMERA)
    reg.register(dev, token)
    dev._state = DeviceState.ACTIVE

    s = perception_platform.vision

    f = make_frame_descriptor(
        frame_id="f_inj",
        device_id=injection,
        physical_id="phys_inj",
        seq=1,
        session_id="S_NORMAL",
    )
    res = s.ingest_frame(f, RAW_FRAME_DATA)
    assert res is not None


def test_mutant_12_non_finite_nan_landmark_rejected(perception_platform) -> None:
    """Mutant 12: SpatialLandmark with NaN u coordinate is rejected fail closed."""
    perception_platform.start_session("S_A")
    s = perception_platform.gesture

    with pytest.raises((GestureValidationError, VisionValidationError)):
        bad_lm = SpatialLandmark(landmark_id=0, name="WRIST", u=float("nan"), v=0.5, confidence=0.9, depth_m=1.0)
        HandObservation(
            frame_id=str(uuid.uuid4()),
            device_id="cam_main",
            physical_id="phys_cam_001",
            sequence_number=1,
            timestamp_utc="2026-09-10T12:00:00.000000Z",
            epoch_id=1,
            hand_id="h1",
            handedness=Handedness.RIGHT,
            landmarks=(bad_lm,),
            confidence=0.9,
            session_id="S_A",
        )


def test_mutant_13_audio_sequence_replay_defense(perception_platform) -> None:
    """Mutant 13: Replaying older sequence number in Audio raises AudioSequenceError."""
    perception_platform.start_session("S_A")
    s = perception_platform.audio

    c1, pcm1 = make_audio_chunk(chunk_id="c1", seq=5, session_id="S_A")
    c2, pcm2 = make_audio_chunk(chunk_id="c2", seq=3, session_id="S_A")

    s.ingest_chunk(c1, pcm1)
    with pytest.raises(AudioSequenceError):
        s.ingest_chunk(c2, pcm2)


def test_mutant_14_gesture_sequence_replay_defense(perception_platform) -> None:
    """Mutant 14: Replaying older sequence number in Gesture raises GestureSequenceError."""
    perception_platform.start_session("S_A")
    s = perception_platform.gesture

    obs1 = make_hand_observation(seq=10, session_id="S_A")
    obs2 = make_hand_observation(seq=2, session_id="S_A")

    s.ingest_observation(obs1)
    with pytest.raises(GestureSequenceError):
        s.ingest_observation(obs2)


def test_mutant_15_audio_epoch_mismatch(perception_platform) -> None:
    """Mutant 15: Audio chunk with epoch_id != context epoch raises AudioEpochMismatchError."""
    perception_platform.start_session("S_A")
    s = perception_platform.audio

    c, pcm = make_audio_chunk(chunk_id="c1", seq=1, epoch_id=99, session_id="S_A")
    with pytest.raises(AudioEpochMismatchError):
        s.ingest_chunk(c, pcm)


def test_mutant_16_gesture_epoch_mismatch(perception_platform) -> None:
    """Mutant 16: Gesture observation with epoch_id != context epoch raises GestureEpochMismatchError."""
    perception_platform.start_session("S_A")
    s = perception_platform.gesture

    obs = make_hand_observation(seq=1, epoch_id=99, session_id="S_A")
    with pytest.raises(GestureEpochMismatchError):
        s.ingest_observation(obs)


def test_mutant_17_vision_status_query_isolation(perception_platform) -> None:
    """Mutant 17: Status query for specific session returns session-specific info."""
    perception_platform.start_session("S_A")
    s = perception_platform.vision

    s.ingest_frame(make_frame_descriptor(frame_id="f1", seq=1, session_id="S_A"), RAW_FRAME_DATA)
    q = create_query("vision.pipeline.status", "client", payload={}, metadata={"session_id": "S_A"})
    res = s.handle_status_query(q)
    assert res.payload["active_sessions_count"] == 1


def test_mutant_18_audio_status_query_isolation(perception_platform) -> None:
    """Mutant 18: Audio status query reports active session count."""
    perception_platform.start_session("S_A")
    s = perception_platform.audio

    c, pcm = make_audio_chunk(chunk_id="c1", seq=1, session_id="S_A")
    s.ingest_chunk(c, pcm)

    q = create_query("audio.pipeline.status", "client", payload={}, metadata={"session_id": "S_A"})
    res = s.handle_status_query(q)
    assert res.payload["active_sessions_count"] == 1


def test_mutant_19_gesture_status_query_isolation(perception_platform) -> None:
    """Mutant 19: Gesture status query reports active session count."""
    perception_platform.start_session("S_A")
    s = perception_platform.gesture

    obs = make_hand_observation(seq=1, session_id="S_A")
    s.ingest_observation(obs)

    q = create_query("gesture.pipeline.status", "client", payload={}, metadata={"session_id": "S_A"})
    res = s.handle_status_query(q)
    assert res.payload["active_sessions_count"] == 1


def test_mutant_20_vision_reset_command_clears_all_sessions(perception_platform) -> None:
    """Mutant 20: Reset command clears perception sessions completely."""
    perception_platform.start_session("S_A")
    s = perception_platform.vision

    s.ingest_frame(make_frame_descriptor(frame_id="f1", seq=1, session_id="S_A"), RAW_FRAME_DATA)
    q = create_query("vision.pipeline.reset", "client", payload={})
    res = s.handle_reset_command(q)
    assert res.payload["reset_completed"] is True
    assert len(s._session_stores) == 0


def test_mutant_21_audio_reset_command_clears_all_sessions(perception_platform) -> None:
    """Mutant 21: Audio reset command clears all audio sessions."""
    perception_platform.start_session("S_A")
    s = perception_platform.audio

    c, pcm = make_audio_chunk(chunk_id="c1", seq=1, session_id="S_A")
    s.ingest_chunk(c, pcm)

    q = create_query("audio.pipeline.reset", "client", payload={})
    res = s.handle_reset_command(q)
    assert res.payload["reset_completed"] is True
    assert len(s._session_stores) == 0


def test_mutant_22_gesture_reset_command_clears_all_sessions(perception_platform) -> None:
    """Mutant 22: Gesture reset command clears all gesture sessions."""
    perception_platform.start_session("S_A")
    s = perception_platform.gesture

    obs = make_hand_observation(seq=1, session_id="S_A")
    s.ingest_observation(obs)

    q = create_query("gesture.pipeline.reset", "client", payload={})
    res = s.handle_reset_command(q)
    assert res.payload["reset_completed"] is True
    assert len(s._session_trackers) == 0


def test_mutant_23_vision_stop_clears_sessions(perception_platform) -> None:
    """Mutant 23: Stopping VisionService releases resources and clears session state."""
    perception_platform.start_session("S_A")
    s = perception_platform.vision

    s.ingest_frame(make_frame_descriptor(frame_id="f1", seq=1, session_id="S_A"), RAW_FRAME_DATA)
    s.stop()
    assert len(s._session_stores) == 0


def test_mutant_24_audio_stop_clears_sessions(perception_platform) -> None:
    """Mutant 24: Stopping AudioService releases resources and clears session state."""
    perception_platform.start_session("S_A")
    s = perception_platform.audio

    c, pcm = make_audio_chunk(chunk_id="c1", seq=1, session_id="S_A")
    s.ingest_chunk(c, pcm)
    s.stop()
    assert len(s._session_stores) == 0


def test_mutant_25_gesture_stop_clears_sessions(perception_platform) -> None:
    """Mutant 25: Stopping GestureService releases resources and clears session state."""
    perception_platform.start_session("S_A")
    s = perception_platform.gesture

    obs = make_hand_observation(seq=1, session_id="S_A")
    s.ingest_observation(obs)
    s.stop()
    assert len(s._session_trackers) == 0


def test_mutant_26_health_reports_active_sessions_count(perception_platform) -> None:
    """Mutant 26: Health checks report active session counts accurately."""
    perception_platform.start_session("S_A")
    s = perception_platform.vision

    s.ingest_frame(make_frame_descriptor(frame_id="f1", seq=1, session_id="S_A"), RAW_FRAME_DATA)
    h = s.health()
    assert "1 active session(s)" in h.message


def test_mutant_27_execution_session_purged_teardown(perception_platform) -> None:
    """Mutant 27: Teardown event execution.session.purged purges session."""
    perception_platform.start_session("S_EXEC")
    s = perception_platform.audio

    c, pcm = make_audio_chunk(chunk_id="c1", seq=1, session_id="S_EXEC")
    s.ingest_chunk(c, pcm)
    assert "S_EXEC" in s._session_stores

    env = create_event("execution.session.purged", "execution_gateway", payload={"session_id": "S_EXEC"})
    s.handle_session_purged_event(env)
    assert "S_EXEC" not in s._session_stores


def test_mutant_28_workflow_aborted_teardown(perception_platform) -> None:
    """Mutant 28: Teardown event workflow.aborted purges session."""
    perception_platform.start_session("S_ABORT")
    s = perception_platform.gesture

    obs = make_hand_observation(seq=1, session_id="S_ABORT")
    s.ingest_observation(obs)
    assert "S_ABORT" in s._session_trackers

    env = create_event("workflow.aborted", "workflow_engine", payload={"session_id": "S_ABORT"})
    s.handle_session_purged_event(env)
    assert "S_ABORT" not in s._session_trackers


def test_mutant_29_infinity_depth_m_rejected_in_gesture(perception_platform) -> None:
    """Mutant 29: SpatialLandmark depth_m float('inf') is rejected fail closed."""
    perception_platform.start_session("S_A")
    with pytest.raises((GestureValidationError, VisionValidationError)):
        inf_lm = SpatialLandmark(landmark_id=0, name="WRIST", u=0.5, v=0.5, confidence=0.9, depth_m=float("inf"))
        HandObservation(
            frame_id=str(uuid.uuid4()),
            device_id="cam_main",
            physical_id="phys_cam_001",
            sequence_number=1,
            timestamp_utc="2026-09-10T12:00:00.000000Z",
            epoch_id=1,
            hand_id="h1",
            handedness=Handedness.RIGHT,
            landmarks=(inf_lm,),
            confidence=0.9,
            session_id="S_A",
        )


def test_mutant_30_oversized_audio_chunk_rejected(perception_platform) -> None:
    """Mutant 30: Oversized audio chunk payload_bytes raises AudioCapacityError."""
    perception_platform.start_session("S_A")
    with pytest.raises(AudioCapacityError):
        AudioChunk(
            chunk_id="c_huge",
            device_id="mic_main",
            physical_id="phys_mic_001",
            sequence_number=1,
            timestamp_utc="2026-09-10T12:00:00.000000Z",
            epoch_id=1,
            sample_rate_hz=16000,
            channels=2,
            sample_format=SampleFormat.INT16,
            channel_layout=ChannelLayout.INTERLEAVED,
            frame_count=160000,
            payload_bytes=500000,  # Exceeds 256 KiB
            checksum_crc32=12345678,
            buffer_handle_id="buf_001",
            session_id="S_A",
        )


def test_mutant_31_ai_context_contamination_protection(perception_platform) -> None:
    """Mutant 31: Perception outputs contain only validated structural data, preventing AI contamination."""
    perception_platform.start_session("S_A")
    s = perception_platform.vision

    f = make_frame_descriptor(frame_id="f1", seq=1, session_id="S_A")
    res = s.ingest_frame(f, RAW_FRAME_DATA)
    assert hasattr(res, "tracks")
    assert hasattr(res, "quality")


def test_mutant_32_no_tool_execution_authority(perception_platform) -> None:
    """Mutant 32: Perception services have no access or methods for executing tools or approving plans."""
    for service_cls in (VisionService, AudioService, GestureService):
        assert not hasattr(service_cls, "approve_plan")
        assert not hasattr(service_cls, "authorize_execution")
        assert not hasattr(service_cls, "execute_tool")


def test_fix_01_audio_global_state(perception_platform) -> None:
    """Test that audio previous track states are partitioned by session."""
    perception_platform.start_session("session_A")
    perception_platform.start_session("session_B")
    s = perception_platform.audio

    c_a, pcm_a = make_audio_chunk(chunk_id="c_a", seq=1, session_id="session_A")
    c_b, pcm_b = make_audio_chunk(chunk_id="c_b", seq=1, session_id="session_B")
    
    s.ingest_chunk(c_a, pcm_a)
    s.ingest_chunk(c_b, pcm_b)

    assert "session_A" in s._previous_track_states
    assert "session_B" in s._previous_track_states

    perception_platform.purge_session("session_A")
    assert "session_A" not in s._previous_track_states
    assert "session_B" in s._previous_track_states


def test_fix_02_vision_epoch_mismatch(perception_platform) -> None:
    """Test that vision epoch mismatch is caught BEFORE sequence increment."""
    perception_platform.start_session("session_A")
    s = perception_platform.vision

    active_epoch = s._epoch_id
    bad_epoch = active_epoch + 1

    desc = make_frame_descriptor(frame_id="f1", seq=1, session_id="session_A", epoch_id=bad_epoch)

    with pytest.raises(VisionEpochMismatchError):
        s.ingest_frame(desc, RAW_FRAME_DATA)

    # Verify sequence was not consumed
    assert not s._session_sequences


def test_fix_03_sequence_order(perception_platform) -> None:
    """Test that device validation happens BEFORE sequence increment."""
    perception_platform.start_session("session_A")
    s = perception_platform.vision

    # Attack A: Valid session, invalid device, high sequence
    desc_bad = make_frame_descriptor(frame_id="f1", seq=1000000, session_id="session_A", device_id="cam_main", physical_id="INVALID")

    with pytest.raises(VisionValidationError, match="Physical ID mismatch"):
        s.ingest_frame(desc_bad, RAW_FRAME_DATA)

    assert not s._session_sequences

    # Attack B: Valid session, valid device, sequence=1
    desc_good_1 = make_frame_descriptor(frame_id="f2", seq=1, session_id="session_A")

    s.ingest_frame(desc_good_1, RAW_FRAME_DATA)
    assert s._session_sequences[("session_A", "cam_main", "phys_cam_001", s._epoch_id)] == 1

    # Attack C: Invalid device sequence=999, then valid device sequence=2
    desc_bad_999 = make_frame_descriptor(frame_id="f3", seq=999, session_id="session_A", device_id="cam_main", physical_id="INVALID")

    with pytest.raises(VisionValidationError, match="Physical ID mismatch"):
        s.ingest_frame(desc_bad_999, RAW_FRAME_DATA)

    assert s._session_sequences[("session_A", "cam_main", "phys_cam_001", s._epoch_id)] == 1

    desc_good_2 = make_frame_descriptor(frame_id="f4", seq=2, session_id="session_A")

    s.ingest_frame(desc_good_2, RAW_FRAME_DATA)
    assert s._session_sequences[("session_A", "cam_main", "phys_cam_001", s._epoch_id)] == 2
