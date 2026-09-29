import math
from dataclasses import dataclass
from typing import Sequence, List, Tuple, Dict, Set
import time
import uuid

from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.models import Point3D, Vector3D, COORDINATE_EPSILON
from holomed.anatomy.mesh import AnatomicalMesh, AnatomicalPiece
from holomed.anatomy.swept_surface import SweptCutSurface
from abc import ABC, abstractmethod

class CutSurface:
    """Abstract base class for cut surfaces."""
    pass

@dataclass(frozen=True)
class PlanarCutSurface(CutSurface):
    origin: Point3D
    normal: Vector3D

    def __post_init__(self):
        mag = self.normal.magnitude
        if mag < COORDINATE_EPSILON:
            raise AnatomyValidationError("CutSurface normal must have non-zero length")

from typing import Union

AnyCutSurface = Union[CutSurface, SweptCutSurface]

@dataclass(frozen=True)
class SliceOperation:
    operation_id: str
    cut_surface: AnyCutSurface
    geometry_version: int

@dataclass(frozen=True)
class SliceResult:
    operation_id: str
    parent_piece_id: str
    child_pieces: tuple[AnatomicalPiece, ...]
    geometry_version: int

def _distance_to_plane(pt: Point3D, origin: Point3D, normal: Vector3D) -> float:
    mag = normal.magnitude
    nx, ny, nz = normal.dx / mag, normal.dy / mag, normal.dz / mag
    return (pt.x - origin.x) * nx + (pt.y - origin.y) * ny + (pt.z - origin.z) * nz

def _interpolate(p1: Point3D, p2: Point3D, d1: float, d2: float) -> Point3D:
    if abs(d1 - d2) < 1e-12:
        return Point3D(p1.x, p1.y, p1.z)
    t = d1 / (d1 - d2)
    return Point3D(
        p1.x + t * (p2.x - p1.x),
        p1.y + t * (p2.y - p1.y),
        p1.z + t * (p2.z - p1.z)
    )

class MeshBuilder:
    def __init__(self):
        self.vertices: List[Point3D] = []
        self.indices: List[int] = []
        self.vert_map: Dict[int, int] = {}

    def add_vertex(self, p: Point3D) -> int:
        key = id(p)
        if key in self.vert_map:
            return self.vert_map[key]
        idx = len(self.vertices)
        self.vertices.append(p)
        self.vert_map[key] = idx
        return idx

    def add_triangle(self, i0: int, i1: int, i2: int):
        if i0 == i1 or i1 == i2 or i2 == i0:
            return
        self.indices.extend([i0, i1, i2])

def _triangulate_polygon(vertices: List[Point3D], normal: Vector3D) -> List[Tuple[int, int, int]]:
    if len(vertices) < 3:
        return []

    if abs(normal.dx) > 0.5 or abs(normal.dy) > 0.5:
        u = Vector3D(-normal.dy, normal.dx, 0)
    else:
        u = Vector3D(0, -normal.dz, normal.dy)
    umag = u.magnitude
    u = Vector3D(u.dx/umag, u.dy/umag, u.dz/umag)
    v = Vector3D(normal.dy*u.dz - normal.dz*u.dy, normal.dz*u.dx - normal.dx*u.dz, normal.dx*u.dy - normal.dy*u.dx)

    verts_2d = []
    for pt in vertices:
        x2d = pt.x * u.dx + pt.y * u.dy + pt.z * u.dz
        y2d = pt.x * v.dx + pt.y * v.dy + pt.z * v.dz
        verts_2d.append((x2d, y2d))

    def area2(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])

    def is_ear(prev, curr, nxt, poly):
        a, b, c = poly[prev], poly[curr], poly[nxt]
        if area2(a, b, c) <= 0:
            return False
        for i, p in enumerate(poly):
            if i in (prev, curr, nxt):
                continue
            if area2(a, b, p) >= 0 and area2(b, c, p) >= 0 and area2(c, a, p) >= 0:
                return False
        return True

    area = sum(verts_2d[i][0] * verts_2d[(i+1)%len(verts_2d)][1] - verts_2d[(i+1)%len(verts_2d)][0] * verts_2d[i][1] for i in range(len(verts_2d)))
    indices = list(range(len(verts_2d)))
    if area < 0:
        indices.reverse()

    triangles = []
    while len(indices) > 3:
        n = len(indices)
        ear_found = False
        for i in range(n):
            prev, curr, nxt = indices[(i - 1) % n], indices[i], indices[(i + 1) % n]
            if is_ear(prev, curr, nxt, verts_2d):
                triangles.append((prev, curr, nxt))
                indices.pop(i)
                ear_found = True
                break
        if not ear_found:
            raise AnatomyValidationError("Cap triangulation failed: no ear found in remaining polygon")

    if len(indices) == 3:
        triangles.append((indices[0], indices[1], indices[2]))

    return triangles

def _build_loops(segments: List[Tuple[Point3D, Point3D]]) -> List[List[Point3D]]:
    if not segments: return []
    loops = []
    unprocessed = list(segments)
    while unprocessed:
        loop = [unprocessed.pop(0)]
        while True:
            added = False
            last_pt = loop[-1][1]
            for i, seg in enumerate(unprocessed):
                if last_pt is seg[0]:
                    loop.append(seg)
                    unprocessed.pop(i)
                    added = True
                    break
                if last_pt is seg[1]:
                    loop.append((seg[1], seg[0]))
                    unprocessed.pop(i)
                    added = True
                    break
            if not added:
                break
        loops.append([s[0] for s in loop])
    return loops

def _cap_mesh(builder: MeshBuilder, segments: List[Tuple[Point3D, Point3D]], normal: Vector3D, reverse_winding: bool):
    loops = _build_loops(segments)
    for loop in loops:
        tris = _triangulate_polygon(loop, normal)
        loop_idx = [builder.add_vertex(p) for p in loop]
        for t in tris:
            i0, i1, i2 = loop_idx[t[0]], loop_idx[t[1]], loop_idx[t[2]]
            if reverse_winding:
                builder.add_triangle(i0, i2, i1)
            else:
                builder.add_triangle(i0, i1, i2)

class SliceStrategy(ABC):
    @abstractmethod
    def slice(self, piece: AnatomicalPiece, operation: SliceOperation) -> SliceResult:
        pass

class PlanarSliceStrategy(SliceStrategy):
    def slice(self, piece: AnatomicalPiece, operation: SliceOperation) -> SliceResult:
        if not isinstance(operation.cut_surface, PlanarCutSurface):
            raise NotImplementedError("PlanarSliceStrategy only supports PlanarCutSurface")
        plane = operation.cut_surface
        mesh = piece.mesh

        distances = [_distance_to_plane(pt, plane.origin, plane.normal) for pt in mesh.vertices]

        def get_side(d: float) -> int:
            if d > COORDINATE_EPSILON: return 1
            if d < -COORDINATE_EPSILON: return -1
            return 0

        sides = [get_side(d) for d in distances]

        has_pos = any(s == 1 for s in sides)
        has_neg = any(s == -1 for s in sides)

        # If there's no intersection (strictly on one side or tangent to one side), return original mesh
        if not has_pos and has_neg:
            return SliceResult(
                operation_id=operation.operation_id,
                parent_piece_id=piece.piece_id,
                child_pieces=(AnatomicalPiece(
                    piece_id=f"{piece.piece_id}_{operation.operation_id}_b",
                    mesh=mesh,
                    parent_id=piece.piece_id,
                    lineage_metadata={"side": "back", "operation_id": operation.operation_id}
                ),),
                geometry_version=operation.geometry_version
            )
        elif not has_neg and has_pos:
            return SliceResult(
                operation_id=operation.operation_id,
                parent_piece_id=piece.piece_id,
                child_pieces=(AnatomicalPiece(
                    piece_id=f"{piece.piece_id}_{operation.operation_id}_f",
                    mesh=mesh,
                    parent_id=piece.piece_id,
                    lineage_metadata={"side": "front", "operation_id": operation.operation_id}
                ),),
                geometry_version=operation.geometry_version
            )
        elif not has_pos and not has_neg:
            # Everything is exactly on the plane (degenerate input mesh)
            return SliceResult(
                operation_id=operation.operation_id,
                parent_piece_id=piece.piece_id,
                child_pieces=(AnatomicalPiece(
                    piece_id=f"{piece.piece_id}_{operation.operation_id}_f",
                    mesh=mesh,
                    parent_id=piece.piece_id,
                    lineage_metadata={"side": "front", "operation_id": operation.operation_id}
                ),),
                geometry_version=operation.geometry_version
            )


        front_builder = MeshBuilder()
        back_builder = MeshBuilder()
        cap_segments_front = []
        cap_segments_back = []

        intersections = {}
        def get_intersection(i, j):
            key = tuple(sorted((i, j)))
            if key in intersections: return intersections[key]
            v = _interpolate(mesh.vertices[i], mesh.vertices[j], distances[i], distances[j])
            intersections[key] = v
            return v

        for t in range(0, len(mesh.indices), 3):
            i0, i1, i2 = mesh.indices[t], mesh.indices[t+1], mesh.indices[t+2]
            s0, s1, s2 = sides[i0], sides[i1], sides[i2]

            pos = (s0 == 1) + (s1 == 1) + (s2 == 1)
            neg = (s0 == -1) + (s1 == -1) + (s2 == -1)
            zero = (s0 == 0) + (s1 == 0) + (s2 == 0)

            verts = [mesh.vertices[i0], mesh.vertices[i1], mesh.vertices[i2]]

            if pos == 3 or (pos == 2 and zero == 1) or (pos == 1 and zero == 2):
                front_builder.add_triangle(front_builder.add_vertex(verts[0]), front_builder.add_vertex(verts[1]), front_builder.add_vertex(verts[2]))
            elif neg == 3 or (neg == 2 and zero == 1) or (neg == 1 and zero == 2):
                back_builder.add_triangle(back_builder.add_vertex(verts[0]), back_builder.add_vertex(verts[1]), back_builder.add_vertex(verts[2]))
            elif zero == 3:
                # Coplanar
                v01 = Vector3D(verts[1].x - verts[0].x, verts[1].y - verts[0].y, verts[1].z - verts[0].z)
                v02 = Vector3D(verts[2].x - verts[0].x, verts[2].y - verts[0].y, verts[2].z - verts[0].z)
                tn = Vector3D(v01.dy*v02.dz - v01.dz*v02.dy, v01.dz*v02.dx - v01.dx*v02.dz, v01.dx*v02.dy - v01.dy*v02.dx)
                dot = tn.dx*plane.normal.dx + tn.dy*plane.normal.dy + tn.dz*plane.normal.dz
                if dot >= 0:
                    front_builder.add_triangle(front_builder.add_vertex(verts[0]), front_builder.add_vertex(verts[1]), front_builder.add_vertex(verts[2]))
                else:
                    back_builder.add_triangle(back_builder.add_vertex(verts[0]), back_builder.add_vertex(verts[1]), back_builder.add_vertex(verts[2]))
            elif pos == 1 and neg == 2:
                idxs = [i0, i1, i2]
                while sides[idxs[0]] != 1: idxs = [idxs[1], idxs[2], idxs[0]]
                v0, v1, v2 = mesh.vertices[idxs[0]], mesh.vertices[idxs[1]], mesh.vertices[idxs[2]]
                i_01, i_02 = get_intersection(idxs[0], idxs[1]), get_intersection(idxs[0], idxs[2])

                front_builder.add_triangle(front_builder.add_vertex(v0), front_builder.add_vertex(i_01), front_builder.add_vertex(i_02))
                back_builder.add_triangle(back_builder.add_vertex(i_01), back_builder.add_vertex(v1), back_builder.add_vertex(v2))
                back_builder.add_triangle(back_builder.add_vertex(i_01), back_builder.add_vertex(v2), back_builder.add_vertex(i_02))
                cap_segments_front.append((i_02, i_01))
                cap_segments_back.append((i_01, i_02))
            elif pos == 2 and neg == 1:
                idxs = [i0, i1, i2]
                while sides[idxs[0]] != -1: idxs = [idxs[1], idxs[2], idxs[0]]
                v0, v1, v2 = mesh.vertices[idxs[0]], mesh.vertices[idxs[1]], mesh.vertices[idxs[2]]
                i_01, i_02 = get_intersection(idxs[0], idxs[1]), get_intersection(idxs[0], idxs[2])

                back_builder.add_triangle(back_builder.add_vertex(v0), back_builder.add_vertex(i_01), back_builder.add_vertex(i_02))
                front_builder.add_triangle(front_builder.add_vertex(i_01), front_builder.add_vertex(v1), front_builder.add_vertex(v2))
                front_builder.add_triangle(front_builder.add_vertex(i_01), front_builder.add_vertex(v2), front_builder.add_vertex(i_02))
                cap_segments_back.append((i_02, i_01))
                cap_segments_front.append((i_01, i_02))
            elif pos == 1 and neg == 1 and zero == 1:
                idxs = [i0, i1, i2]
                while sides[idxs[0]] != 0: idxs = [idxs[1], idxs[2], idxs[0]]
                v0 = mesh.vertices[idxs[0]]
                if sides[idxs[1]] == 1:
                    v_pos, v_neg = mesh.vertices[idxs[1]], mesh.vertices[idxs[2]]
                    i_pn = get_intersection(idxs[1], idxs[2])
                    front_builder.add_triangle(front_builder.add_vertex(v0), front_builder.add_vertex(v_pos), front_builder.add_vertex(i_pn))
                    back_builder.add_triangle(back_builder.add_vertex(v0), back_builder.add_vertex(i_pn), back_builder.add_vertex(v_neg))
                    cap_segments_front.append((i_pn, v0))
                    cap_segments_back.append((v0, i_pn))
                else:
                    v_neg, v_pos = mesh.vertices[idxs[1]], mesh.vertices[idxs[2]]
                    i_pn = get_intersection(idxs[1], idxs[2])
                    back_builder.add_triangle(back_builder.add_vertex(v0), back_builder.add_vertex(v_neg), back_builder.add_vertex(i_pn))
                    front_builder.add_triangle(front_builder.add_vertex(v0), front_builder.add_vertex(i_pn), front_builder.add_vertex(v_pos))
                    cap_segments_front.append((v0, i_pn))
                    cap_segments_back.append((i_pn, v0))

        if cap_segments_front:
            _cap_mesh(front_builder, cap_segments_front, plane.normal, reverse_winding=True)
        if cap_segments_back:
            _cap_mesh(back_builder, cap_segments_back, plane.normal, reverse_winding=False)

        child_pieces = []
        base_id = piece.piece_id
        op_id = operation.operation_id

        if len(front_builder.indices) > 0:
            m = AnatomicalMesh(tuple(front_builder.vertices), tuple(front_builder.indices))
            child_pieces.append(AnatomicalPiece(
                piece_id=f"{base_id}_{op_id}_f",
                mesh=m,
                parent_id=base_id,
                lineage_metadata={"side": "front", "operation_id": op_id}
            ))

        if len(back_builder.indices) > 0:
            m = AnatomicalMesh(tuple(back_builder.vertices), tuple(back_builder.indices))
            child_pieces.append(AnatomicalPiece(
                piece_id=f"{base_id}_{op_id}_b",
                mesh=m,
                parent_id=base_id,
                lineage_metadata={"side": "back", "operation_id": op_id}
            ))

        # Return pieces only if the cut was non-degenerate. A cut outside geometry returns 1 piece.
        return SliceResult(
            operation_id=op_id,
            parent_piece_id=base_id,
            child_pieces=tuple(child_pieces),
            geometry_version=operation.geometry_version
        )


class SliceEngine:
    @staticmethod
    def slice_piece(piece: AnatomicalPiece, operation: SliceOperation) -> SliceResult:
        if isinstance(operation.cut_surface, PlanarCutSurface):
            strategy = PlanarSliceStrategy()
            return strategy.slice(piece, operation)
        elif isinstance(operation.cut_surface, SweptCutSurface):
            raise NotImplementedError("Freeform slice strategy is not yet implemented")
        else:
            raise TypeError(f"Unsupported cut surface type: {type(operation.cut_surface).__name__}")
