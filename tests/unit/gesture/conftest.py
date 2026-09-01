# -*- coding: utf-8 -*-
"""Deterministic fixtures for M03 Gesture subsystem unit tests."""

from __future__ import annotations

import math
from typing import Mapping
import uuid

import pytest

from holomed.configuration.models import AppConfig
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.manager import DeviceManager
from holomed.devices.models import (
    DeviceDescriptor,
    DeviceType,
)
from holomed.devices.registry import DeviceRegistry
from holomed.gesture.models import (
    CANONICAL_LANDMARK_NAMES,
    HandObservation,
    Handedness,
)
from holomed.runtime.context import RuntimeContext
from holomed.runtime.logging import SecretFilter
from holomed.vision.models import SpatialLandmark


@pytest.fixture
def test_epoch() -> int:
    return 1


@pytest.fixture
def runtime_context(test_epoch: int) -> RuntimeContext:
    config = AppConfig(
        app_name="HoloMed-Test",
        environment="TESTING",
        log_level="DEBUG",
        host="127.0.0.1",
        port=8000,
    )
    return RuntimeContext(app_config=config, epoch_id=test_epoch)


@pytest.fixture
def secret_filter() -> SecretFilter:
    return SecretFilter(secrets=["SECRET_TOKEN_XYZ_12345"])


@pytest.fixture
def message_dispatcher(runtime_context: RuntimeContext) -> MessageDispatcher:
    disp = MessageDispatcher()
    disp.initialize(runtime_context)
    return disp


from holomed.devices.interfaces import IDevice
from holomed.devices.models import DeviceCapability, DeviceHealth, DeviceState


class DummyOpticalCamera(IDevice):
    def __init__(self, device_id: str = "cam_optical_0", physical_id: str = "phys_cam_0") -> None:
        self._device_id = device_id
        self._physical_id = physical_id
        self._state = DeviceState.UNREGISTERED

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def device_type(self) -> DeviceType:
        return DeviceType.RGB_CAMERA

    @property
    def physical_id(self) -> str:
        return self._physical_id

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def capabilities(self) -> frozenset[DeviceCapability]:
        return frozenset({DeviceCapability.DATA_STREAM})

    def initialize(self) -> None:
        pass

    def start(self) -> None:
        self._state = DeviceState.ACTIVE

    def stop(self) -> None:
        self._state = DeviceState.STOPPED

    def teardown(self) -> None:
        self._state = DeviceState.STOPPED

    def health(self) -> DeviceHealth:
        from holomed.runtime.models import HealthStatus
        return DeviceHealth(
            device_id=self._device_id,
            status=HealthStatus.HEALTHY,
            message="OK",
            timestamp_utc="2026-09-01T12:00:00.000000Z",
        )


@pytest.fixture
def device_manager(runtime_context: RuntimeContext) -> DeviceManager:
    dm = DeviceManager()
    dm.initialize(runtime_context)
    dm.start()
    reg = getattr(dm, "registry", None) or getattr(dm, "_registry", None)
    token = getattr(dm, "registry_token", None) or getattr(dm, "_registry_token", None)
    cam = DummyOpticalCamera("cam_optical_0", "phys_cam_0")
    reg.register(cam, token)
    cam._state = DeviceState.ACTIVE
    return dm


def make_canonical_landmarks(
    u_v_depth_map: Mapping[int, tuple[float, float, float]],
    confidence: float = 0.95,
) -> tuple[SpatialLandmark, ...]:
    """Helper to construct canonical 21 SpatialLandmarks with specific positions."""
    lms = []
    for idx in range(21):
        name = CANONICAL_LANDMARK_NAMES[idx]
        if idx in u_v_depth_map:
            u, v, depth = u_v_depth_map[idx]
        else:
            # Neutral default position (at depth 1.0m)
            u = 0.5 + 0.01 * (idx % 5 - 2)
            v = 0.5 - 0.02 * (idx // 5)
            depth = 1.0
        lms.append(
            SpatialLandmark(
                landmark_id=idx,
                name=name,
                u=round(u, 6),
                v=round(v, 6),
                depth_m=round(depth, 4),
                confidence=confidence,
            )
        )
    return tuple(lms)


@pytest.fixture
def open_hand_landmarks() -> tuple[SpatialLandmark, ...]:
    """Open flat hand: all fingers extended away from wrist (v decreases upwards)."""
    # Optical space: u [0..1] right, v [0..1] down. Lower v means higher up (+Y).
    # Wrist: u=0.5, v=0.7, depth=1.0m
    mapping: dict[int, tuple[float, float, float]] = {
        0: (0.5, 0.7, 1.0),       # wrist
        # Thumb: extended outwards to the left/up
        1: (0.45, 0.65, 1.0),     # cmc
        2: (0.40, 0.60, 1.0),     # mcp
        3: (0.35, 0.55, 1.0),     # ip
        4: (0.30, 0.50, 1.0),     # tip
        # Index: extended upwards
        5: (0.45, 0.55, 1.0),     # mcp
        6: (0.45, 0.45, 1.0),     # pip
        7: (0.45, 0.38, 1.0),     # dip
        8: (0.45, 0.30, 1.0),     # tip
        # Middle: extended upwards
        9: (0.50, 0.54, 1.0),     # mcp
        10: (0.50, 0.43, 1.0),    # pip
        11: (0.50, 0.35, 1.0),    # dip
        12: (0.50, 0.27, 1.0),    # tip
        # Ring: extended upwards
        13: (0.55, 0.55, 1.0),    # mcp
        14: (0.55, 0.45, 1.0),    # pip
        15: (0.55, 0.38, 1.0),    # dip
        16: (0.55, 0.31, 1.0),    # tip
        # Pinky: extended upwards
        17: (0.60, 0.58, 1.0),    # mcp
        18: (0.60, 0.49, 1.0),    # pip
        19: (0.60, 0.43, 1.0),    # dip
        20: (0.60, 0.37, 1.0),    # tip
    }
    return make_canonical_landmarks(mapping)


@pytest.fixture
def closed_fist_landmarks() -> tuple[SpatialLandmark, ...]:
    """Closed fist: finger tips curled in towards wrist (tips closer to wrist than PIPs)."""
    mapping: dict[int, tuple[float, float, float]] = {
        0: (0.5, 0.7, 1.0),       # wrist
        # Thumb: folded across palm
        1: (0.48, 0.68, 1.0),
        2: (0.49, 0.65, 1.0),
        3: (0.51, 0.65, 1.0),
        4: (0.52, 0.67, 1.0),     # tip folded near palm
        # Index: folded
        5: (0.45, 0.55, 1.0),
        6: (0.45, 0.48, 1.0),     # pip further out
        7: (0.46, 0.54, 1.0),
        8: (0.46, 0.60, 1.0),     # tip curled close to wrist
        # Middle: folded
        9: (0.50, 0.54, 1.0),
        10: (0.50, 0.47, 1.0),
        11: (0.50, 0.53, 1.0),
        12: (0.50, 0.60, 1.0),
        # Ring: folded
        13: (0.55, 0.55, 1.0),
        14: (0.55, 0.48, 1.0),
        15: (0.55, 0.54, 1.0),
        16: (0.55, 0.61, 1.0),
        # Pinky: folded
        17: (0.60, 0.58, 1.0),
        18: (0.60, 0.51, 1.0),
        19: (0.60, 0.57, 1.0),
        20: (0.60, 0.63, 1.0),
    }
    return make_canonical_landmarks(mapping)


@pytest.fixture
def pointing_landmarks(closed_fist_landmarks: tuple[SpatialLandmark, ...]) -> tuple[SpatialLandmark, ...]:
    """Pointing hand: index extended upwards, other 3 fingers folded."""
    fist_dict = {lm.landmark_id: (lm.u, lm.v, lm.depth_m) for lm in closed_fist_landmarks}
    # Extend index finger:
    fist_dict[6] = (0.45, 0.45, 1.0)
    fist_dict[7] = (0.45, 0.38, 1.0)
    fist_dict[8] = (0.45, 0.30, 1.0)  # extended
    return make_canonical_landmarks(fist_dict)


@pytest.fixture
def two_finger_landmarks(pointing_landmarks: tuple[SpatialLandmark, ...]) -> tuple[SpatialLandmark, ...]:
    """Two-finger pose: index and middle extended upwards, ring and pinky folded."""
    pt_dict = {lm.landmark_id: (lm.u, lm.v, lm.depth_m) for lm in pointing_landmarks}
    # Extend middle finger as well:
    pt_dict[10] = (0.50, 0.43, 1.0)
    pt_dict[11] = (0.50, 0.35, 1.0)
    pt_dict[12] = (0.50, 0.27, 1.0)  # extended
    return make_canonical_landmarks(pt_dict)


@pytest.fixture
def pinch_landmarks(open_hand_landmarks: tuple[SpatialLandmark, ...]) -> tuple[SpatialLandmark, ...]:
    """Pinch gesture: thumb tip (4) and index tip (8) touching/close (<= 0.035m)."""
    oh_dict = {lm.landmark_id: (lm.u, lm.v, lm.depth_m) for lm in open_hand_landmarks}
    # Position thumb tip and index tip at nearly identical 3D positions:
    # (u=0.45, v=0.40, depth=1.0m)
    oh_dict[4] = (0.450, 0.400, 1.0)
    oh_dict[8] = (0.452, 0.401, 1.0)  # Distance ~ 0.002m (< 0.035m)
    return make_canonical_landmarks(oh_dict)


@pytest.fixture
def thumb_up_landmarks(closed_fist_landmarks: tuple[SpatialLandmark, ...]) -> tuple[SpatialLandmark, ...]:
    """Thumbs up: thumb extended pointing straight UP (+Y), fingers folded."""
    f_dict = {lm.landmark_id: (lm.u, lm.v, lm.depth_m) for lm in closed_fist_landmarks}
    # Extend thumb upwards (+Y optical: v lower than mcp v)
    f_dict[1] = (0.40, 0.65, 1.0)
    f_dict[2] = (0.38, 0.60, 1.0)
    f_dict[3] = (0.38, 0.52, 1.0)
    f_dict[4] = (0.38, 0.42, 1.0)  # dy in optical space = (0.5 - 0.42) - (0.5 - 0.60) = +0.18m > 0.02
    return make_canonical_landmarks(f_dict)


@pytest.fixture
def thumb_down_landmarks(closed_fist_landmarks: tuple[SpatialLandmark, ...]) -> tuple[SpatialLandmark, ...]:
    """Thumbs down: thumb extended pointing straight DOWN (-Y), fingers folded."""
    f_dict = {lm.landmark_id: (lm.u, lm.v, lm.depth_m) for lm in closed_fist_landmarks}
    # Extend thumb downwards (+Y optical: v higher than mcp v)
    f_dict[1] = (0.40, 0.60, 1.0)
    f_dict[2] = (0.38, 0.62, 1.0)
    f_dict[3] = (0.38, 0.70, 1.0)
    f_dict[4] = (0.38, 0.80, 1.0)  # dy in optical space = (0.5 - 0.80) - (0.5 - 0.62) = -0.18m < -0.02
    return make_canonical_landmarks(f_dict)


def make_observation(
    landmarks: tuple[SpatialLandmark, ...],
    frame_id: str | None = None,
    sequence_number: int = 1,
    epoch_id: int = 1,
    hand_id: str = "hand_0",
    handedness: Handedness = Handedness.RIGHT,
    confidence: float = 0.95,
    is_partial: bool = False,
) -> HandObservation:
    """Helper to build a valid HandObservation."""
    return HandObservation(
        frame_id=frame_id or str(uuid.uuid4()),
        device_id="cam_optical_0",
        physical_id="phys_cam_0",
        sequence_number=sequence_number,
        timestamp_utc="2026-09-01T12:00:00.000000Z",
        epoch_id=epoch_id,
        hand_id=hand_id,
        handedness=handedness,
        landmarks=landmarks,
        confidence=confidence,
        is_partial=is_partial,
    )
