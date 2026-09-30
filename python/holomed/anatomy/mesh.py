from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional

from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.models import Point3D, Vector3D


@dataclass(frozen=True)
class AnatomicalMesh:
    """Immutable canonical anatomical mesh."""
    
    vertices: tuple[Point3D, ...]
    indices: tuple[int, ...]
    
    def __post_init__(self):
        if not self.vertices:
            raise AnatomyValidationError("Mesh must have at least one vertex")
        if not self.indices:
            raise AnatomyValidationError("Mesh must have at least one triangle (3 indices)")
        if len(self.indices) % 3 != 0:
            raise AnatomyValidationError("Mesh indices must be a multiple of 3")
            
        num_verts = len(self.vertices)
        for i in self.indices:
            if i < 0 or i >= num_verts:
                raise AnatomyValidationError(f"Invalid vertex index {i}")

    @property
    def volume(self) -> float:
        """Signed volume of a closed triangle mesh via divergence theorem.
        
        For a closed, consistently-wound manifold mesh, this returns the
        enclosed volume. Positive for outward-facing normals (CCW winding).
        """
        vol = 0.0
        for t in range(0, len(self.indices), 3):
            v0 = self.vertices[self.indices[t]]
            v1 = self.vertices[self.indices[t + 1]]
            v2 = self.vertices[self.indices[t + 2]]
            # Signed volume of tetrahedron formed by triangle and origin
            vol += (
                v0.x * (v1.y * v2.z - v1.z * v2.y)
                - v0.y * (v1.x * v2.z - v1.z * v2.x)
                + v0.z * (v1.x * v2.y - v1.y * v2.x)
            )
        return abs(vol) / 6.0

@dataclass(frozen=True)
class AnatomicalPiece:
    """A distinct anatomical structure, optionally derived from a parent via slicing."""
    
    piece_id: str
    mesh: AnatomicalMesh
    parent_id: Optional[str] = None
    lineage_metadata: Optional[Mapping[str, str]] = None
