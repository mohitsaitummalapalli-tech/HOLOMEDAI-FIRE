# -*- coding: utf-8 -*-
"""HoloMed AI Anatomy & Simulation subsystem (M05)."""

from __future__ import annotations

from holomed.anatomy.events import RecordingAnatomyEventSink
from holomed.anatomy.exceptions import (
    AnatomyCapacityError,
    AnatomyEpochMismatchError,
    AnatomyError,
    AnatomyHierarchyError,
    AnatomyLifecycleError,
    AnatomyResourceIntegrityError,
    AnatomyShutdownError,
    AnatomySolverError,
    AnatomyValidationError,
)
from holomed.anatomy.geometry import (
    apply_rigid_transform,
    compose_rigid_transforms,
    invert_rigid_transform,
    point_add_vector,
    point_distance,
    point_sub_point,
    quaternion_inverse,
    quaternion_multiply,
    rotate_vector_by_quaternion,
    vector_add,
    vector_cross,
    vector_dot,
    vector_scale,
    vector_sub,
)
from holomed.anatomy.hierarchy import AnatomyHierarchy
from holomed.anatomy.models import (
    COORDINATE_EPSILON,
    FIXED_SIMULATION_TIMESTEP_S,
    MAX_ANATOMICAL_ENTITIES,
    MAX_ANATOMY_COORDINATE_METERS,
    MAX_ANATOMY_HIERARCHY_DEPTH,
    MAX_ANATOMY_PAYLOAD_BYTES,
    MAX_DISPLACEMENT_METERS,
    MAX_LANDMARKS_PER_ENTITY,
    MAX_RECORDED_ANATOMY_EVENTS,
    MAX_SIMULATION_PROCESSING_BUDGET_MS,
    MAX_SIMULATION_STEPS_PER_CYCLE,
    MAX_SOLVER_ITERATIONS,
    SOLVER_CONVERGENCE_TOLERANCE,
    AnatomicalClassification,
    AnatomicalEntity,
    BoundingBox3D,
    ConvergenceStatus,
    Point3D,
    RigidTransform3D,
    SimulationState,
    TissueStateSnapshot,
    TissueType,
    Vector3D,
)
from holomed.anatomy.queries import AnatomyQueryEngine
from holomed.anatomy.serialization import (
    serialize_anatomy_bytes,
    serialize_anatomy_payload,
)
from holomed.anatomy.service import AnatomyService
from holomed.anatomy.simulation import AnatomicalSimulationEngine
from holomed.anatomy.solver import AnatomicalConstraintSolver
from holomed.anatomy.spatial import SpatialRegistrationSolver

__all__ = [
    # Exceptions
    "AnatomyError",
    "AnatomyLifecycleError",
    "AnatomyValidationError",
    "AnatomyCapacityError",
    "AnatomyEpochMismatchError",
    "AnatomyHierarchyError",
    "AnatomySolverError",
    "AnatomyResourceIntegrityError",
    "AnatomyShutdownError",
    # Constants
    "MAX_ANATOMY_COORDINATE_METERS",
    "MAX_DISPLACEMENT_METERS",
    "COORDINATE_EPSILON",
    "MAX_ANATOMICAL_ENTITIES",
    "MAX_ANATOMY_HIERARCHY_DEPTH",
    "MAX_LANDMARKS_PER_ENTITY",
    "FIXED_SIMULATION_TIMESTEP_S",
    "MAX_SIMULATION_STEPS_PER_CYCLE",
    "MAX_SOLVER_ITERATIONS",
    "SOLVER_CONVERGENCE_TOLERANCE",
    "MAX_RECORDED_ANATOMY_EVENTS",
    "MAX_ANATOMY_PAYLOAD_BYTES",
    "MAX_SIMULATION_PROCESSING_BUDGET_MS",
    # Enums
    "AnatomicalClassification",
    "TissueType",
    "ConvergenceStatus",
    # Models
    "Point3D",
    "Vector3D",
    "BoundingBox3D",
    "RigidTransform3D",
    "AnatomicalEntity",
    "TissueStateSnapshot",
    "SimulationState",
    # Geometry Math
    "point_distance",
    "vector_add",
    "vector_sub",
    "vector_scale",
    "vector_dot",
    "vector_cross",
    "point_add_vector",
    "point_sub_point",
    "quaternion_multiply",
    "quaternion_inverse",
    "rotate_vector_by_quaternion",
    "apply_rigid_transform",
    "invert_rigid_transform",
    "compose_rigid_transforms",
    # Hierarchy & Queries
    "AnatomyHierarchy",
    "AnatomyQueryEngine",
    # Simulation & Solver
    "AnatomicalSimulationEngine",
    "AnatomicalConstraintSolver",
    "SpatialRegistrationSolver",
    # Events & Serialization
    "RecordingAnatomyEventSink",
    "serialize_anatomy_payload",
    "serialize_anatomy_bytes",
    # Service
    "AnatomyService",
]
