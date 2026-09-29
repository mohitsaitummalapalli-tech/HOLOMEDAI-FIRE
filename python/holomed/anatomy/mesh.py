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

@dataclass(frozen=True)
class AnatomicalPiece:
    """A distinct anatomical structure, optionally derived from a parent via slicing."""
    
    piece_id: str
    mesh: AnatomicalMesh
    parent_id: Optional[str] = None
    lineage_metadata: Optional[Mapping[str, str]] = None
