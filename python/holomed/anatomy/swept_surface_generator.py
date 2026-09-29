# -*- coding: utf-8 -*-
"""Phase F3.2-C Deterministic Freeform Swept Surface Generator."""

import math
from dataclasses import dataclass
from typing import Tuple

from holomed.anatomy.models import Point3D
from holomed.anatomy.trajectory import ProcessedCutTrajectory
from holomed.anatomy.swept_surface import SweptCutSurface, TransportedFrame, UNIT_VECTOR_TOLERANCE, ORTHOGONALITY_TOLERANCE
from holomed.anatomy.exceptions import AnatomyValidationError

@dataclass(frozen=True)
class SweptCutMesh:
    """
    Deterministic connected surface mesh generated from a SweptCutSurface.
    Identity and topology are strictly ordered and deterministic.
    """
    vertices: Tuple[Point3D, ...]
    indices: Tuple[int, ...]
    # For future extension, normals could be added if needed, but not required yet.

def generate_swept_surface(
    trajectory: ProcessedCutTrajectory,
    frames: Tuple[TransportedFrame, ...],
    width: float,
    thickness: float
) -> SweptCutSurface:
    """
    Creates the mathematically verified SweptCutSurface from a trajectory and frames.
    Fails closed on any invalid input.
    """
    if len(trajectory.samples) < 3:
        raise AnatomyValidationError("Insufficient centerline samples")
    
    if len(trajectory.samples) != len(frames):
        raise AnatomyValidationError("Frame count must exactly match centerline count")
        
    if not isinstance(width, (int, float)) or not math.isfinite(width) or width <= 0.0:
        raise AnatomyValidationError("Width must be finite and strictly positive")
        
    if not isinstance(thickness, (int, float)) or not math.isfinite(thickness) or thickness <= 0.0:
        raise AnatomyValidationError("Thickness must be finite and strictly positive")

    # Validate coordinate space
    if trajectory.coordinate_space != "canonical_anatomical":
        raise AnatomyValidationError("Coordinate space must be canonical_anatomical")
        
    # Validate positions are finite
    for s in trajectory.samples:
        if not math.isfinite(s.position.x) or not math.isfinite(s.position.y) or not math.isfinite(s.position.z):
            raise AnatomyValidationError("Non-finite position in trajectory")

    # Validate frames
    for f in frames:
        if not math.isfinite(f.tangent.dx) or not math.isfinite(f.normal.dx) or not math.isfinite(f.binormal.dx):
            raise AnatomyValidationError("Non-finite vector in frame")
            
        if abs(f.tangent.magnitude - 1.0) > UNIT_VECTOR_TOLERANCE:
            raise AnatomyValidationError("Frame vector not unit")
            
        cross_x = f.tangent.dy * f.normal.dz - f.tangent.dz * f.normal.dy
        cross_y = f.tangent.dz * f.normal.dx - f.tangent.dx * f.normal.dz
        cross_z = f.tangent.dx * f.normal.dy - f.tangent.dy * f.normal.dx
        if abs(cross_x - f.binormal.dx) > ORTHOGONALITY_TOLERANCE or \
           abs(cross_y - f.binormal.dy) > ORTHOGONALITY_TOLERANCE or \
           abs(cross_z - f.binormal.dz) > ORTHOGONALITY_TOLERANCE:
            raise AnatomyValidationError("Frame is not right-handed or orthogonal")

    # The surface id is derived from trajectory deterministically
    surface_id = f"{trajectory.trajectory_id}_surface"
    
    pts = tuple(s.position for s in trajectory.samples)
    
    # Check zero length segment
    for i in range(1, len(pts)):
        p1 = pts[i-1]
        p2 = pts[i]
        dist2 = (p2.x-p1.x)**2 + (p2.y-p1.y)**2 + (p2.z-p1.z)**2
        if dist2 == 0.0:
            raise AnatomyValidationError("Zero-length segment in trajectory")

    return SweptCutSurface(
        surface_id=surface_id,
        centerline_samples=pts,
        transport_frames=frames,
        width=float(width),
        thickness=float(thickness),
        coordinate_space="canonical_anatomical"
    )

def generate_swept_mesh(surface: SweptCutSurface, u_samples: int = 3) -> SweptCutMesh:
    """
    Generates a deterministic connected surface mesh from a SweptCutSurface.
    
    Sampling Strategy:
    - Longitudinal samples match the exact input centerline samples (no interpolation).
    - Cross-section samples are uniformly distributed from u=-0.5 (left) to u=0.5 (right).
    - By default, u_samples = 3 means: u=-0.5, u=0.0, u=0.5.
    
    Mesh Topology:
    - Vertices are ordered longitudinally first, then cross-sectionally.
      For N longitudinal samples and M cross-section samples, vertex index is i*M + j.
    - Winding order: right-handed (counter-clockwise) when viewed from the +Normal direction.
      Triangle (V_{i,j}, V_{i+1,j}, V_{i,j+1})
      Triangle (V_{i,j+1}, V_{i+1,j}, V_{i+1,j+1})
    """
    if u_samples < 2:
        raise AnatomyValidationError("Mesh generation requires at least 2 cross-section samples")

    vertices = []
    L = surface.total_arc_length
    
    # Generate vertices
    u_values = [-0.5 + i * (1.0 / (u_samples - 1)) for i in range(u_samples)]
    
    # We evaluate at exact s corresponding to centerline samples to avoid interpolation discrepancies
    for i in range(len(surface.centerline_samples)):
        s_val = surface._arc_lengths[i]
        for u in u_values:
            pt = surface.evaluate_surface(s_val, u)
            if not math.isfinite(pt.x) or not math.isfinite(pt.y) or not math.isfinite(pt.z):
                raise AnatomyValidationError("Non-finite vertex generated")
            vertices.append(pt)

    # Generate indices
    indices = []
    num_longitudinal = len(surface.centerline_samples)
    
    for i in range(num_longitudinal - 1):
        for j in range(u_samples - 1):
            v0 = i * u_samples + j
            v1 = (i + 1) * u_samples + j
            v2 = i * u_samples + (j + 1)
            v3 = (i + 1) * u_samples + (j + 1)
            
            # Triangle 1
            indices.extend([v0, v1, v2])
            # Triangle 2
            indices.extend([v2, v1, v3])

    return SweptCutMesh(vertices=tuple(vertices), indices=tuple(indices))
