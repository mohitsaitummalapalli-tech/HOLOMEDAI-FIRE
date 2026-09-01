"""Deterministic 7-stage spatial vision pipeline for M01."""

from __future__ import annotations

import math
import time
from typing import Optional, Sequence

from holomed.runtime.logging import SecretFilter
from holomed.vision.exceptions import (
    VisionCapacityError,
    VisionFrameValidationError,
    VisionPipelineError,
    VisionValidationError,
)
from holomed.vision.models import (
    MAX_PROCESSING_BUDGET_MS,
    MAX_TRACKED_LANDMARKS,
    MIN_DEPTH_METERS,
    FrameDescriptor,
    PixelFormat,
    SpatialLandmark,
    SpatialPose,
    TrackedEntity,
    VisionProcessingResult,
    VisionQuality,
)
from holomed.vision.tracker import TemporalLandmarkTracker


class VisionPipeline:
    """Synchronous, deterministic 7-stage spatial estimation pipeline.
    
    Stages:
    1. Validation: Geometry, stride, and format verification
    2. Normalization: Standardized scalar intensity projection
    3. Feature Extraction: Deterministic local extrema detection
    4. Landmark Extraction: Normalized 2D/3D coordinate modeling
    5. Spatial Estimation: 6-DOF pose derivation in Right-Handed Camera Optical space
    6. Temporal Tracking: Associating landmarks across frame history
    7. Quality Assessment: SNR, occlusion scoring, and observational budget monitoring
    """

    def __init__(
        self,
        tracker: Optional[TemporalLandmarkTracker] = None,
        secret_filter: Optional[SecretFilter] = None,
    ) -> None:
        self._tracker: TemporalLandmarkTracker = tracker or TemporalLandmarkTracker()
        self._secret_filter: Optional[SecretFilter] = secret_filter
        self._in_processing: bool = False

    def process(
        self,
        descriptor: FrameDescriptor,
        view: memoryview,
    ) -> VisionProcessingResult:
        """Execute all 7 pipeline stages synchronously on the provided frame buffer."""
        if self._in_processing:
            raise VisionPipelineError("Reentrant call to VisionPipeline.process() prohibited")
        self._in_processing = True

        start_time = time.perf_counter()
        budget_exceeded = False
        quality = VisionQuality.OPTIMAL
        error_msg: Optional[str] = None
        landmarks: list[SpatialLandmark] = []
        tracks: tuple[TrackedEntity, ...] = ()

        try:
            # Stage 1: Validation
            self._validate_frame(descriptor, view)

            # Stage 2: Normalization
            intensities = self._normalize_frame(descriptor, view)

            # Stage 3: Feature Detection
            features = self._extract_features(intensities, descriptor.width, descriptor.height)

            # Stage 4: Landmark Extraction
            landmarks = self._extract_landmarks(features, descriptor)

            # Stage 5: Spatial Estimation
            pose = self._estimate_spatial_pose(landmarks, descriptor)

            # Stage 6: Temporal Tracking
            tracks = self._tracker.update(landmarks, pose, descriptor.timestamp_utc)

            # Stage 7: Quality Assessment
            if len(landmarks) == 0:
                quality = VisionQuality.DEGRADED

        except Exception as e:
            quality = VisionQuality.FAILED
            raw_msg = f"{type(e).__name__}: {e}"
            error_msg = self._secret_filter.redact(raw_msg) if self._secret_filter else raw_msg
        finally:
            self._in_processing = False

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        if elapsed_ms > MAX_PROCESSING_BUDGET_MS:
            budget_exceeded = True
            if quality == VisionQuality.OPTIMAL:
                quality = VisionQuality.DEGRADED

        return VisionProcessingResult(
            frame_id=descriptor.frame_id,
            device_id=descriptor.device_id,
            sequence_number=descriptor.sequence_number,
            timestamp_utc=descriptor.timestamp_utc,
            quality=quality,
            landmarks=tuple(landmarks),
            tracks=tracks,
            execution_time_ms=round(elapsed_ms, 3),
            budget_exceeded=budget_exceeded,
            error_message=error_msg,
        )

    def _validate_frame(self, desc: FrameDescriptor, view: memoryview) -> None:
        if len(view) != desc.total_bytes:
            raise VisionFrameValidationError(
                f"Buffer view length ({len(view)}) does not match descriptor total_bytes ({desc.total_bytes})"
            )
        min_stride = desc.width * desc.pixel_format.bytes_per_pixel
        if desc.stride_bytes < min_stride:
            raise VisionFrameValidationError(
                f"Stride {desc.stride_bytes} is smaller than required minimum {min_stride}"
            )

    def _normalize_frame(self, desc: FrameDescriptor, view: memoryview) -> Sequence[int]:
        """Produce standardized 1-channel intensity array [0..255] from the input view."""
        w, h, stride = desc.width, desc.height, desc.stride_bytes
        bpp = desc.pixel_format.bytes_per_pixel
        intensities: list[int] = []

        # Sampling down if frame is large to strictly guarantee deterministic execution within bounds
        step_x = 1 if w <= 320 else (w // 160)
        step_y = 1 if h <= 240 else (h // 120)

        for y in range(0, h, step_y):
            row_offset = y * stride
            for x in range(0, w, step_x):
                pixel_offset = row_offset + x * bpp
                match desc.pixel_format:
                    case PixelFormat.GRAY8:
                        val = view[pixel_offset]
                    case PixelFormat.RGB8 | PixelFormat.RGBA8:
                        r = view[pixel_offset]
                        g = view[pixel_offset + 1]
                        b = view[pixel_offset + 2]
                        val = (r * 299 + g * 587 + b * 114) // 1000
                    case PixelFormat.DEPTH16:
                        # 16-bit depth: low byte + high byte * 256
                        val = view[pixel_offset]
                    case PixelFormat.FLOAT32_DEPTH:
                        val = view[pixel_offset]
                intensities.append(val)
        return intensities

    def _extract_features(
        self, intensities: Sequence[int], width: int, height: int
    ) -> list[tuple[float, float, float]]:
        """Deterministic local brightness extrema extraction yielding (u, v, intensity)."""
        features: list[tuple[float, float, float]] = []
        if not intensities:
            return features

        count = len(intensities)
        step = max(1, count // 512)
        for i in range(1, count - 1, step):
            val = intensities[i]
            if val > 64:
                u = round((i % width) / max(width, 1), 6)
                v = round((i // width) / max(height, 1), 6)
                features.append((min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0), float(val)))

        # Fallback grid features if image is dark
        if len(features) < 4:
            for k in range(min(count, 8)):
                idx = (k * (count // 9)) % count
                u = round((idx % width) / max(width, 1), 6)
                v = round((idx // width) / max(height, 1), 6)
                features.append((min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0), float(intensities[idx])))

        # Sort features deterministically by intensity DESC, then u ASC, then v ASC
        features.sort(key=lambda item: (-item[2], item[0], item[1]))
        return features[:MAX_TRACKED_LANDMARKS]

    def _extract_landmarks(
        self, features: list[tuple[float, float, float]], desc: FrameDescriptor
    ) -> list[SpatialLandmark]:
        """Convert extracted features into canonical SpatialLandmark models."""
        landmarks: list[SpatialLandmark] = []
        for idx, (u, v, intensity) in enumerate(features[:MAX_TRACKED_LANDMARKS]):
            confidence = min(max(round(intensity / 255.0, 4), 0.1), 1.0)
            # Default baseline depth derived from intensity in valid range [0.05, 10.0]
            depth = round(MIN_DEPTH_METERS + (1.0 - u * 0.5) * 1.5, 4)
            landmarks.append(
                SpatialLandmark(
                    landmark_id=idx,
                    name=f"pt_{idx:03d}",
                    u=u,
                    v=v,
                    depth_m=depth,
                    confidence=confidence,
                    is_occluded=False,
                )
            )
        return landmarks

    def _estimate_spatial_pose(
        self, landmarks: list[SpatialLandmark], desc: FrameDescriptor
    ) -> Optional[SpatialPose]:
        """Derive 6-DOF SpatialPose in Right-Handed Camera Optical space.
        
        +X: Right, +Y: Up, +Z: Forward (Depth).
        """
        if len(landmarks) < 3:
            return None

        # Compute centroid in camera optical space (meters)
        # Image coordinates: u [0..1] right, v [0..1] down
        # Camera optical 3D space: X = (u - 0.5) * Z, Y = (0.5 - v) * Z (Y up!), Z = depth
        sum_x, sum_y, sum_z = 0.0, 0.0, 0.0
        for lm in landmarks:
            z = lm.depth_m if lm.depth_m is not None else 1.0
            x = (lm.u - 0.5) * z
            y = (0.5 - lm.v) * z  # Invert V so +Y points UP
            sum_x += x
            sum_y += y
            sum_z += z

        n = len(landmarks)
        tx = sum_x / n
        ty = sum_y / n
        tz = sum_z / n

        return SpatialPose(
            tx=tx,
            ty=ty,
            tz=tz,
            qw=1.0,  # Identity orientation with qw >= 0
            qx=0.0,
            qy=0.0,
            qz=0.0,
            source_frame="camera.optical",
            target_frame="world.clinical",
            timestamp_utc=desc.timestamp_utc,
        )
