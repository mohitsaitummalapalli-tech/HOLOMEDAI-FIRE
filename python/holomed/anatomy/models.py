# -*- coding: utf-8 -*-
"""M05 Anatomy & Simulation Core Models, Geometric Primitives, and Enums."""

from __future__ import annotations

from dataclasses import dataclass
import enum
import math
from types import MappingProxyType
from typing import Any, Mapping, Optional

from holomed.anatomy.exceptions import AnatomyValidationError

# Canonical Constants
MAX_ANATOMY_COORDINATE_METERS: float = 10.0
MAX_DISPLACEMENT_METERS: float = 0.10
COORDINATE_EPSILON: float = 1e-6

MAX_ANATOMICAL_ENTITIES: int = 256
MAX_ANATOMY_HIERARCHY_DEPTH: int = 8
MAX_LANDMARKS_PER_ENTITY: int = 32

FIXED_SIMULATION_TIMESTEP_S: float = 0.01  # 10 ms fixed timestep
MAX_SIMULATION_STEPS_PER_CYCLE: int = 100
MAX_SOLVER_ITERATIONS: int = 16
SOLVER_CONVERGENCE_TOLERANCE: float = 1e-4

MAX_RECORDED_ANATOMY_EVENTS: int = 1000
MAX_ANATOMY_PAYLOAD_BYTES: int = 65536  # 64 KiB
MAX_SIMULATION_PROCESSING_BUDGET_MS: float = 20.0


class AnatomicalClassification(str, enum.Enum):
    """Categorical classification of anatomical entities."""

    ORGAN = "ORGAN"
    REGION = "REGION"
    TISSUE_LAYER = "TISSUE_LAYER"
    BONE = "BONE"
    VESSEL = "VESSEL"
    LANDMARK = "LANDMARK"


class TissueType(str, enum.Enum):
    """Biological tissue material classification for physiological modeling."""

    PARENCHYMAL = "PARENCHYMAL"
    EPITHELIAL = "EPITHELIAL"
    CONNECTIVE = "CONNECTIVE"
    MUSCULAR = "MUSCULAR"
    NERVOUS = "NERVOUS"
    BONY = "BONY"


class ConvergenceStatus(str, enum.Enum):
    """Outcome status of bounded iterative constraint solver."""

    CONVERGED = "CONVERGED"
    MAX_ITERATIONS_REACHED = "MAX_ITERATIONS_REACHED"
    DIVERGED = "DIVERGED"
    DEGENERATE = "DEGENERATE"


@dataclass(frozen=True)
class Point3D:
    """Rigid 3D metric coordinate point quantized to 6 decimal places."""

    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        for val, name in ((self.x, "x"), (self.y, "y"), (self.z, "z")):
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise AnatomyValidationError(f"Point3D element '{name}' must be finite, got {val!r}")
            if abs(val) > MAX_ANATOMY_COORDINATE_METERS:
                raise AnatomyValidationError(
                    f"Point3D coordinate '{name}' ({val}m) exceeds {MAX_ANATOMY_COORDINATE_METERS}m bound"
                )

        norm_x = 0.0 if float(self.x) == 0.0 else round(float(self.x), 6)
        norm_y = 0.0 if float(self.y) == 0.0 else round(float(self.y), 6)
        norm_z = 0.0 if float(self.z) == 0.0 else round(float(self.z), 6)
        object.__setattr__(self, "x", norm_x)
        object.__setattr__(self, "y", norm_y)
        object.__setattr__(self, "z", norm_z)

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass(frozen=True)
class Vector3D:
    """3D direction/displacement vector in metric space."""

    dx: float
    dy: float
    dz: float

    def __post_init__(self) -> None:
        for val, name in ((self.dx, "dx"), (self.dy, "dy"), (self.dz, "dz")):
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise AnatomyValidationError(f"Vector3D element '{name}' must be finite, got {val!r}")

        norm_dx = 0.0 if float(self.dx) == 0.0 else round(float(self.dx), 6)
        norm_dy = 0.0 if float(self.dy) == 0.0 else round(float(self.dy), 6)
        norm_dz = 0.0 if float(self.dz) == 0.0 else round(float(self.dz), 6)
        object.__setattr__(self, "dx", norm_dx)
        object.__setattr__(self, "dy", norm_dy)
        object.__setattr__(self, "dz", norm_dz)

    @property
    def magnitude(self) -> float:
        return math.sqrt(self.dx * self.dx + self.dy * self.dy + self.dz * self.dz)

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.dx, self.dy, self.dz)


@dataclass(frozen=True)
class BoundingBox3D:
    """Axis-aligned 3D bounding box with min_point <= max_point."""

    min_point: Point3D
    max_point: Point3D

    def __post_init__(self) -> None:
        if not isinstance(self.min_point, Point3D):
            raise AnatomyValidationError(f"min_point must be Point3D, got {type(self.min_point).__name__}")
        if not isinstance(self.max_point, Point3D):
            raise AnatomyValidationError(f"max_point must be Point3D, got {type(self.max_point).__name__}")

        if (
            self.min_point.x > self.max_point.x + COORDINATE_EPSILON
            or self.min_point.y > self.max_point.y + COORDINATE_EPSILON
            or self.min_point.z > self.max_point.z + COORDINATE_EPSILON
        ):
            raise AnatomyValidationError(
                f"BoundingBox3D min_point ({self.min_point}) must be <= max_point ({self.max_point})"
            )

    def contains_point(self, pt: Point3D) -> bool:
        """Check if 3D point lies within this bounding box."""
        return (
            self.min_point.x - COORDINATE_EPSILON <= pt.x <= self.max_point.x + COORDINATE_EPSILON
            and self.min_point.y - COORDINATE_EPSILON <= pt.y <= self.max_point.y + COORDINATE_EPSILON
            and self.min_point.z - COORDINATE_EPSILON <= pt.z <= self.max_point.z + COORDINATE_EPSILON
        )

    def intersects(self, other: BoundingBox3D) -> bool:
        """Check if this bounding box intersects another bounding box."""
        return (
            self.min_point.x <= other.max_point.x + COORDINATE_EPSILON
            and self.max_point.x >= other.min_point.x - COORDINATE_EPSILON
            and self.min_point.y <= other.max_point.y + COORDINATE_EPSILON
            and self.max_point.y >= other.min_point.y - COORDINATE_EPSILON
            and self.min_point.z <= other.max_point.z + COORDINATE_EPSILON
            and self.max_point.z >= other.min_point.z - COORDINATE_EPSILON
        )


@dataclass(frozen=True)
class RigidTransform3D:
    """Rigid 6-DOF spatial transform adhering to M01 SpatialPose convention.
    
    Transforms vector from source_frame to target_frame:
        p_target = R * p_source + t
    Quaternion convention: unit norm, qw >= 0.0.
    """

    translation: Point3D
    rotation_quaternion: tuple[float, float, float, float]  # (qw, qx, qy, qz)
    source_frame: str
    target_frame: str

    def __post_init__(self) -> None:
        if not isinstance(self.translation, Point3D):
            raise AnatomyValidationError(f"translation must be Point3D, got {type(self.translation).__name__}")

        if len(self.rotation_quaternion) != 4:
            raise AnatomyValidationError("rotation_quaternion must be a 4-tuple of floats")

        qw, qx, qy, qz = self.rotation_quaternion
        for val, name in ((qw, "qw"), (qx, "qx"), (qy, "qy"), (qz, "qz")):
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise AnatomyValidationError(f"Quaternion element '{name}' must be finite")

        # Quaternion Unit Norm Invariant (||q|| = 1.0 +/- 1e-4)
        norm_sq = qw * qw + qx * qx + qy * qy + qz * qz
        if abs(norm_sq - 1.0) > 1e-4:
            raise AnatomyValidationError(f"Quaternion is not unit-normalized (norm^2 = {norm_sq})")

        # Canonical Hemisphere Convention (qw >= 0.0)
        qw_f, qx_f, qy_f, qz_f = float(qw), float(qx), float(qy), float(qz)
        if qw_f < 0.0:
            qw_f, qx_f, qy_f, qz_f = -qw_f, -qx_f, -qy_f, -qz_f

        object.__setattr__(
            self,
            "rotation_quaternion",
            (round(qw_f, 6), round(qx_f, 6), round(qy_f, 6), round(qz_f, 6)),
        )

        if not isinstance(self.source_frame, str) or not self.source_frame.strip():
            raise AnatomyValidationError("source_frame cannot be empty")
        if not isinstance(self.target_frame, str) or not self.target_frame.strip():
            raise AnatomyValidationError("target_frame cannot be empty")


@dataclass(frozen=True)
class AnatomicalEntity:
    """Immutable representation of an organ, region, or tissue structure."""

    entity_id: str
    name: str
    classification: AnatomicalClassification
    parent_id: Optional[str]
    reference_pose: RigidTransform3D
    bounding_volume: BoundingBox3D
    landmarks: tuple[Point3D, ...]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.entity_id, str) or not self.entity_id.strip():
            raise AnatomyValidationError("entity_id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name.strip():
            raise AnatomyValidationError("name must be a non-empty string")
        if not isinstance(self.classification, AnatomicalClassification):
            raise AnatomyValidationError(f"Invalid classification: {self.classification}")
        if self.parent_id is not None and (not isinstance(self.parent_id, str) or not self.parent_id.strip()):
            raise AnatomyValidationError("parent_id must be non-empty string or None")
        if len(self.landmarks) > MAX_LANDMARKS_PER_ENTITY:
            raise AnatomyValidationError(
                f"Landmarks count ({len(self.landmarks)}) exceeds {MAX_LANDMARKS_PER_ENTITY}"
            )
        object.__setattr__(self, "landmarks", tuple(self.landmarks))
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class TissueStateSnapshot:
    """Immutable snapshot of tissue stress, strain, displacement, and temperature."""

    entity_id: str
    simulation_time_s: float
    displacement: Vector3D
    stress_kpa: float
    strain: float
    temperature_c: float
    is_deformed: bool
    is_simulated: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.entity_id, str) or not self.entity_id.strip():
            raise AnatomyValidationError("entity_id must be non-empty")
        if not isinstance(self.displacement, Vector3D):
            raise AnatomyValidationError("displacement must be Vector3D")
        if self.displacement.magnitude > MAX_DISPLACEMENT_METERS + COORDINATE_EPSILON:
            raise AnatomyValidationError(
                f"Displacement magnitude ({self.displacement.magnitude:.4f}m) exceeds {MAX_DISPLACEMENT_METERS}m"
            )
        for val, name in (
            (self.simulation_time_s, "simulation_time_s"),
            (self.stress_kpa, "stress_kpa"),
            (self.strain, "strain"),
            (self.temperature_c, "temperature_c"),
        ):
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise AnatomyValidationError(f"{name} must be finite float, got {val!r}")

        object.__setattr__(self, "simulation_time_s", round(float(self.simulation_time_s), 4))
        object.__setattr__(self, "stress_kpa", round(float(self.stress_kpa), 4))
        object.__setattr__(self, "strain", round(float(self.strain), 6))
        object.__setattr__(self, "temperature_c", round(float(self.temperature_c), 2))


@dataclass(frozen=True)
class SimulationState:
    """Immutable aggregate simulation state for an epoch."""

    epoch_id: int
    simulation_time_s: float
    step_count: int
    tissue_states: Mapping[str, TissueStateSnapshot]
    active_constraints_count: int
    convergence_status: ConvergenceStatus
    is_simulated: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.epoch_id, int) or self.epoch_id < 0:
            raise AnatomyValidationError("epoch_id must be non-negative int")
        if not isinstance(self.convergence_status, ConvergenceStatus):
            raise AnatomyValidationError(f"Invalid convergence_status: {self.convergence_status}")
        if not isinstance(self.tissue_states, MappingProxyType):
            object.__setattr__(self, "tissue_states", MappingProxyType(dict(self.tissue_states)))
