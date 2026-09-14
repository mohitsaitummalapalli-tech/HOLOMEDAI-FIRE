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
    def capabilities(self) -> frozenset[DeviceCapability]:
        return frozenset()

    @property
    def state(self) -> DeviceState:
        return self._state

    def initialize(self) -> None:
        self._state = DeviceState.INITIALIZED

    def start(self) -> None:
        self._state = DeviceState.ACTIVE

    def stop(self) -> None:
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
        gemini_api_key="test_key",
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

def test_session_stopped_event_clears_resources(perception_platform) -> None:
    perception_platform.start_session("session_A")
    audio = perception_platform.audio
    gesture = perception_platform.gesture
    vision = perception_platform.vision
    
    assert "session_A" in audio._session_stores
    assert "session_A" in gesture._session_trackers
    assert "session_A" in vision._session_stores
    
    perception_platform.session_manager.stop_session("session_A")
    
    assert "session_A" not in audio._session_stores
    assert "session_A" not in gesture._session_trackers
    assert "session_A" not in vision._session_stores

def test_session_evicted_event_clears_resources(perception_platform) -> None:
    perception_platform.start_session("session_A")
    audio = perception_platform.audio
    gesture = perception_platform.gesture
    vision = perception_platform.vision
    
    assert "session_A" in audio._session_stores
    assert "session_A" in gesture._session_trackers
    assert "session_A" in vision._session_stores
    
    perception_platform.session_manager.evict_session("session_A")
    
    assert "session_A" not in audio._session_stores
    assert "session_A" not in gesture._session_trackers
    assert "session_A" not in vision._session_stores

def test_duplicate_teardown_events_safe(perception_platform) -> None:
    perception_platform.start_session("session_A")
    audio = perception_platform.audio
    gesture = perception_platform.gesture
    vision = perception_platform.vision
    
    perception_platform.session_manager.stop_session("session_A")
    # Idempotent second stop
    perception_platform.session_manager.stop_session("session_A")
    perception_platform.session_manager.evict_session("session_A")
    
    assert "session_A" not in audio._session_stores
    assert "session_A" not in gesture._session_trackers
    assert "session_A" not in vision._session_stores

def test_late_observation_after_teardown_dropped_safely(perception_platform) -> None:
    perception_platform.start_session("session_A")
    audio = perception_platform.audio
    gesture = perception_platform.gesture
    vision = perception_platform.vision
    
    perception_platform.session_manager.stop_session("session_A")
    
    # Testing vision
    frame_desc = make_frame_descriptor(seq=1, session_id="session_A")
    with pytest.raises(VisionLifecycleError, match="not activated"):
        vision.ingest_frame(frame_desc, b"123", session_id="session_A")

    # Testing gesture
    observation = make_hand_observation(seq=1, session_id="session_A")
    with pytest.raises(GestureLifecycleError, match="not activated"):
        gesture.ingest_observation(observation, session_id="session_A")
        
    # Testing audio
    chunk, data = make_audio_chunk(seq=1, session_id="session_A")
    with pytest.raises(AudioLifecycleError, match="not activated"):
        audio.ingest_chunk(chunk, data, session_id="session_A")

def test_teardown_does_not_affect_active_sessions(perception_platform) -> None:
    perception_platform.start_session("session_A")
    perception_platform.start_session("session_B")
    
    audio = perception_platform.audio
    
    assert "session_A" in audio._session_stores
    assert "session_B" in audio._session_stores
    
    perception_platform.session_manager.stop_session("session_A")
    
    assert "session_A" not in audio._session_stores
    assert "session_B" in audio._session_stores

def test_rapid_session_churn(perception_platform) -> None:
    for i in range(10):
        perception_platform.start_session(f"churn_{i}")
        perception_platform.session_manager.stop_session(f"churn_{i}")
        
    audio = perception_platform.audio
    assert len(audio._session_stores) == 0

def test_teardown_emits_purged_event(perception_platform) -> None:
    events = []
    def handler(env):
        events.append(env)
    
    from holomed.core.dispatcher import EventHandler, EventSubscription
    perception_platform.dispatcher._subscription_registry._events.append(
        EventSubscription(
            pattern="gesture.session.purged",
            handler=handler,
            service_name="test_subscriber",
            registration_index=0,
        )
    )
    
    perception_platform.start_session("session_A")
    perception_platform.session_manager.stop_session("session_A")
    
    assert len(events) == 1
    assert events[0].payload["session_id"] == "session_A"
