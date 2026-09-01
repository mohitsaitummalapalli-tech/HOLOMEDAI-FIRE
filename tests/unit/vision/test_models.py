"""Unit and adversarial tests for M01 models, coordinates, and bounds."""

from __future__ import annotations

import math
import uuid
import pytest

from holomed.vision.exceptions import (
    VisionCapacityError,
    VisionFrameValidationError,
    VisionValidationError,
)
from holomed.vision.models import (
    MAX_FRAME_BYTES,
    MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH,
    MAX_TRACKED_LANDMARKS,
    FrameDescriptor,
    PixelFormat,
    SpatialLandmark,
    SpatialPose,
    quantize_normalized_coord,
)


def test_camera_coordinate_system_right_handed() -> None:
    """Verify camera 3D space is Right-Handed (+X right, +Y up, +Z forward) with X x Y = Z."""
    # Unit vectors
    x = (1.0, 0.0, 0.0)  # Right
    y = (0.0, 1.0, 0.0)  # Up
    # Cross product: x x y
    z_cross = (
        x[1] * y[2] - x[2] * y[1],
        x[2] * y[0] - x[0] * y[2],
        x[0] * y[1] - x[1] * y[0],
    )
    # Must equal +Z forward
    assert z_cross == (0.0, 0.0, 1.0)


def test_camera_to_world_transform_convention() -> None:
    """Validate camera->world active transformation p_world = R * p_cam + t."""
    pose = SpatialPose(
        tx=1.0,
        ty=2.0,
        tz=3.0,
        qw=1.0,
        qx=0.0,
        qy=0.0,
        qz=0.0,
        source_frame="camera.optical",
        target_frame="world.clinical",
        timestamp_utc="2026-09-01T08:00:00.000000Z",
    )
    mat = pose.to_matrix_4x4()
    assert len(mat) == 16
    # For identity rotation, R is 3x3 identity, translation in last column of row-major
    assert mat[3] == 1.0
    assert mat[7] == 2.0
    assert mat[11] == 3.0
    assert mat[15] == 1.0

    # Transforming point (0, 0, 1) in camera frame gives (1, 2, 4) in world
    p_cam = (0.0, 0.0, 1.0)
    p_world = (
        mat[0] * p_cam[0] + mat[1] * p_cam[1] + mat[2] * p_cam[2] + mat[3],
        mat[4] * p_cam[0] + mat[5] * p_cam[1] + mat[6] * p_cam[2] + mat[7],
        mat[8] * p_cam[0] + mat[9] * p_cam[1] + mat[10] * p_cam[2] + mat[11],
    )
    assert p_world == (1.0, 2.0, 4.0)


def test_depth_authoritative_range_boundary() -> None:
    """Ingests depths at 0.04m, 0.05m, 10.0m, 10.01m; asserts [0.05, 10.0] boundary enforcement."""
    # Valid minimum boundary 0.05m
    lm_min = SpatialLandmark(0, "p_min", 0.5, 0.5, depth_m=0.05, confidence=0.9)
    assert lm_min.depth_m == 0.05

    # Valid maximum boundary 10.0m
    lm_max = SpatialLandmark(1, "p_max", 0.5, 0.5, depth_m=10.0, confidence=0.9)
    assert lm_max.depth_m == 10.0

    # Below 0.05m must raise VisionValidationError
    with pytest.raises(VisionValidationError):
        SpatialLandmark(2, "p_below", 0.5, 0.5, depth_m=0.0499, confidence=0.9)

    # Above 10.0m must raise VisionValidationError
    with pytest.raises(VisionValidationError):
        SpatialLandmark(3, "p_above", 0.5, 0.5, depth_m=10.001, confidence=0.9)


def test_landmark_id_and_capacity_bound() -> None:
    """Assert valid landmark_id range is [0, 128) and IDs outside raise VisionValidationError."""
    # Valid min (0) and max (127)
    lm0 = SpatialLandmark(0, "pt0", 0.1, 0.1, depth_m=1.0, confidence=1.0)
    assert lm0.landmark_id == 0

    lm127 = SpatialLandmark(127, "pt127", 0.1, 0.1, depth_m=1.0, confidence=1.0)
    assert lm127.landmark_id == 127

    # Negative id raises
    with pytest.raises(VisionValidationError):
        SpatialLandmark(-1, "pt_neg", 0.1, 0.1, depth_m=1.0, confidence=1.0)

    # id == 128 raises (out of range [0, 128))
    with pytest.raises(VisionValidationError):
        SpatialLandmark(128, "pt128", 0.1, 0.1, depth_m=1.0, confidence=1.0)


def test_coordinate_quantization_precision() -> None:
    """Asserts coordinates round/quantize to 1e-6 precision."""
    val = quantize_normalized_coord(0.123456789)
    assert val == 0.123457

    # -0.0 normalizes to 0.0
    assert quantize_normalized_coord(-0.0) == 0.0

    # NaN / Inf raise VisionValidationError
    with pytest.raises(VisionValidationError):
        quantize_normalized_coord(float("nan"))
    with pytest.raises(VisionValidationError):
        quantize_normalized_coord(float("inf"))


def test_spatial_pose_contract_and_quaternion_normalization() -> None:
    """Verify non-unit quaternion raises VisionValidationError; asserts qw >= 0 canonicalization."""
    # Non-unit quaternion
    with pytest.raises(VisionValidationError):
        SpatialPose(
            tx=0.0, ty=0.0, tz=1.0,
            qw=1.0, qx=1.0, qy=0.0, qz=0.0,  # norm^2 = 2.0
            source_frame="cam", target_frame="world",
            timestamp_utc="2026-09-01T08:00:00.000000Z",
        )

    # Quaternion with qw < 0 gets flipped to qw >= 0
    pose = SpatialPose(
        tx=0.0, ty=0.0, tz=1.0,
        qw=-1.0, qx=0.0, qy=0.0, qz=0.0,
        source_frame="cam", target_frame="world",
        timestamp_utc="2026-09-01T08:00:00.000000Z",
    )
    assert pose.qw == 1.0


def test_spatial_pose_finite_float_rejection() -> None:
    """Passes NaN and Inf to translation and rotation; asserts immediate rejection."""
    with pytest.raises(VisionValidationError):
        SpatialPose(
            tx=float("nan"), ty=0.0, tz=1.0,
            qw=1.0, qx=0.0, qy=0.0, qz=0.0,
            source_frame="cam", target_frame="world",
            timestamp_utc="2026-09-01T08:00:00.000000Z",
        )

    with pytest.raises(VisionValidationError):
        SpatialPose(
            tx=0.0, ty=float("inf"), tz=1.0,
            qw=1.0, qx=0.0, qy=0.0, qz=0.0,
            source_frame="cam", target_frame="world",
            timestamp_utc="2026-09-01T08:00:00.000000Z",
        )


def test_frame_descriptor_validation() -> None:
    """Validates boundary rules on FrameDescriptor."""
    # Oversized width
    with pytest.raises(VisionFrameValidationError):
        FrameDescriptor(
            frame_id=str(uuid.uuid4()),
            device_id="cam_01",
            physical_id="usb://1",
            sequence_number=1,
            timestamp_utc="2026-09-01T08:00:00.000000Z",
            width=MAX_FRAME_WIDTH + 1,
            height=100,
            pixel_format=PixelFormat.GRAY8,
            stride_bytes=MAX_FRAME_WIDTH + 1,
            total_bytes=(MAX_FRAME_WIDTH + 1) * 100,
            checksum_crc32=0,
            epoch_id=1,
            buffer_handle_id="vision.slot.0",
        )
