# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from enum import Enum
from typing import Tuple, Mapping
import math
from types import MappingProxyType
from holomed.anatomy.models import Point3D, Vector3D

class TrajectoryState(Enum):
    START = "START"
    ACTIVE = "ACTIVE"
    END = "END"

class ValidityState(Enum):
    RAW = "RAW"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    PROCESSED = "PROCESSED"

class TrajectoryValidationError(Exception):
    pass

@dataclass(frozen=True)
class TrajectorySample:
    position: Point3D
    timestamp: float
    state: TrajectoryState

@dataclass(frozen=True)
class CutTrajectory:
    trajectory_id: str
    correlation_id: str
    samples: Tuple[TrajectorySample, ...]
    coordinate_space: str = "canonical_anatomical"
    validity_state: ValidityState = ValidityState.RAW
    sampling_metadata: Mapping = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.sampling_metadata, MappingProxyType):
            object.__setattr__(self, 'sampling_metadata', MappingProxyType(dict(self.sampling_metadata)))

@dataclass(frozen=True)
class ProcessedCutTrajectory:
    trajectory_id: str
    correlation_id: str
    samples: Tuple[TrajectorySample, ...]
    tangents: Tuple[Vector3D, ...]
    transport_frames: Tuple[Tuple[Vector3D, Vector3D, Vector3D], ...] = field(default_factory=tuple)
    width_parameter: float = 0.0
    thickness_parameter: float = 0.0
    coordinate_space: str = "canonical_anatomical"
    sampling_metadata: Mapping = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.sampling_metadata, MappingProxyType):
            object.__setattr__(self, 'sampling_metadata', MappingProxyType(dict(self.sampling_metadata)))


def validate_trajectory(traj: CutTrajectory) -> CutTrajectory:
    if len(traj.samples) < 3:
        raise TrajectoryValidationError("Insufficient samples (<3)")
    
    for i in range(len(traj.samples) - 1):
        if traj.samples[i].timestamp >= traj.samples[i+1].timestamp:
            raise TrajectoryValidationError("Non-monotonic or identical timestamps")
        
    total_length = 0.0
    filtered_samples: list = []
    
    for s in traj.samples:
        if not (math.isfinite(s.position.x) and math.isfinite(s.position.y) and math.isfinite(s.position.z)):
            raise TrajectoryValidationError("NaN/Inf coordinates")
        if not (-2.0 <= s.position.x <= 2.0 and -2.0 <= s.position.y <= 2.0 and -2.0 <= s.position.z <= 2.0):
            raise TrajectoryValidationError("Out of bounds")
        
        if len(filtered_samples) == 0:
            filtered_samples.append(s)
        else:
            prev = filtered_samples[-1]
            dx = s.position.x - prev.position.x
            dy = s.position.y - prev.position.y
            dz = s.position.z - prev.position.z
            dist = math.sqrt(dx*dx + dy*dy + dz*dz)
            if dist == 0.0:
                continue
            if dist > 0.05:
                raise TrajectoryValidationError(f"Excessive gap: {dist}")
            total_length += dist
            filtered_samples.append(s)
            
    if len(filtered_samples) < 3:
        raise TrajectoryValidationError("Insufficient samples after duplicate filtering")
    if total_length < 0.01:
        raise TrajectoryValidationError("Zero-length path")
        
    return CutTrajectory(
        trajectory_id=traj.trajectory_id,
        correlation_id=traj.correlation_id,
        samples=tuple(filtered_samples),
        coordinate_space=traj.coordinate_space,
        validity_state=ValidityState.VALIDATED,
        sampling_metadata=traj.sampling_metadata
    )
