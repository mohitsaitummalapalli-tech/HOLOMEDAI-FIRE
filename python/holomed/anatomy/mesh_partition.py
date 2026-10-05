import math
from dataclasses import dataclass
from typing import Tuple, List, Dict, Set, Optional

from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.mesh import AnatomicalMesh, AnatomicalPiece
from holomed.anatomy.boundary_graph import BoundarySegment
from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.freeform_intersection import INTERSECTION_TOLERANCE, AREA_TOLERANCE
from holomed.anatomy.slicing import _triangulate_polygon, MeshBuilder

class MeshPartitionError(AnatomyValidationError):
    pass

@dataclass(frozen=True)
class PartitionResult:
    child_pieces: Tuple[AnatomicalPiece, ...]

def _point_on_segment(p: Point3D, a: Point3D, b: Point3D) -> bool:
    dist = math.sqrt((a.x-p.x)**2 + (a.y-p.y)**2 + (a.z-p.z)**2) + \
           math.sqrt((b.x-p.x)**2 + (b.y-p.y)**2 + (b.z-p.z)**2) - \
           math.sqrt((a.x-b.x)**2 + (a.y-b.y)**2 + (a.z-b.z)**2)
    return dist < INTERSECTION_TOLERANCE

def _get_perimeter_t(p: Point3D, v0: Point3D, v1: Point3D, v2: Point3D) -> float:
    if _point_on_segment(p, v0, v1):
        l = math.sqrt((v1.x-v0.x)**2 + (v1.y-v0.y)**2 + (v1.z-v0.z)**2)
        if l < 1e-12: return 0.0
        return 0.0 + math.sqrt((p.x-v0.x)**2 + (p.y-v0.y)**2 + (p.z-v0.z)**2) / l
    if _point_on_segment(p, v1, v2):
        l = math.sqrt((v2.x-v1.x)**2 + (v2.y-v1.y)**2 + (v2.z-v1.z)**2)
        if l < 1e-12: return 1.0
        return 1.0 + math.sqrt((p.x-v1.x)**2 + (p.y-v1.y)**2 + (p.z-v1.z)**2) / l
    if _point_on_segment(p, v2, v0):
        l = math.sqrt((v0.x-v2.x)**2 + (v0.y-v2.y)**2 + (v0.z-v2.z)**2)
        if l < 1e-12: return 2.0
        return 2.0 + math.sqrt((p.x-v2.x)**2 + (p.y-v2.y)**2 + (p.z-v2.z)**2) / l
    raise MeshPartitionError(f"Point {p} is not on the perimeter of {v0}, {v1}, {v2}")

def _weld_to_triangle(p: Point3D, v0: Point3D, v1: Point3D, v2: Point3D) -> Point3D:
    for vp in (v0, v1, v2):
        if math.sqrt((p.x-vp.x)**2 + (p.y-vp.y)**2 + (p.z-vp.z)**2) < INTERSECTION_TOLERANCE:
            return vp
    return p

def _calculate_normal(v0: Point3D, v1: Point3D, v2: Point3D) -> Vector3D:
    dx1, dy1, dz1 = v1.x - v0.x, v1.y - v0.y, v1.z - v0.z
    dx2, dy2, dz2 = v2.x - v0.x, v2.y - v0.y, v2.z - v0.z
    nx = dy1*dz2 - dz1*dy2
    ny = dz1*dx2 - dx1*dz2
    nz = dx1*dy2 - dy1*dx2
    mag = math.sqrt(nx*nx + ny*ny + nz*nz)
    if mag < 1e-12:
        return Vector3D(1.0, 0.0, 0.0)
    return Vector3D(nx/mag, ny/mag, nz/mag)

def _split_triangle(v0: Point3D, v1: Point3D, v2: Point3D, segments: List[BoundarySegment]) -> Tuple[List[Tuple[Point3D, Point3D, Point3D]], List[Tuple[Point3D, Point3D, Point3D]], List[BoundarySegment]]:
    adj: dict[Point3D, list[Point3D]] = {}
    for seg in segments:
        adj.setdefault(seg.start, []).append(seg.end)
        adj.setdefault(seg.end, []).append(seg.start)
        
    if any(len(n) > 2 for n in adj.values()):
        raise MeshPartitionError("Branched boundary chain inside target triangle")
        
    if len(adj) != len(segments) + 1:
        raise MeshPartitionError("Multiple disjoint chains or loops inside target triangle")
        
    ends = [k for k, v in adj.items() if len(v) == 1]
    if len(ends) != 2:
        raise MeshPartitionError("Invalid chain topology in target triangle")
        
    s_start, s_end = ends[0], ends[1]
    
    chain = [s_start]
    curr = s_start
    prev = None
    while curr != s_end:
        neighbors = adj[curr]
        nxt = neighbors[0] if neighbors[0] != prev else neighbors[1]
        chain.append(nxt)
        prev = curr
        curr = nxt
        
    welded_chain = [_weld_to_triangle(p, v0, v1, v2) for p in chain]
    ws_start, ws_end = welded_chain[0], welded_chain[-1]
    
    pts = [
        (_get_perimeter_t(v0, v0, v1, v2), v0),
        (_get_perimeter_t(v1, v0, v1, v2), v1),
        (_get_perimeter_t(v2, v0, v1, v2), v2),
        (_get_perimeter_t(ws_start, v0, v1, v2), ws_start),
        (_get_perimeter_t(ws_end, v0, v1, v2), ws_end)
    ]
    
    pts.sort(key=lambda x: x[0])
    
    unique_pts: list[tuple[float, Point3D]] = []
    for p in pts:
        if not unique_pts:
            unique_pts.append(p)
        else:
            dist = math.sqrt((p[1].x-unique_pts[-1][1].x)**2 + (p[1].y-unique_pts[-1][1].y)**2 + (p[1].z-unique_pts[-1][1].z)**2)
            if dist > INTERSECTION_TOLERANCE:
                unique_pts.append(p)
                
    # Handle wrap-around duplicate if start and end are same
    dist = math.sqrt((unique_pts[0][1].x-unique_pts[-1][1].x)**2 + (unique_pts[0][1].y-unique_pts[-1][1].y)**2 + (unique_pts[0][1].z-unique_pts[-1][1].z)**2)
    if dist < INTERSECTION_TOLERANCE and len(unique_pts) > 1:
        unique_pts.pop()
                
    perimeter = [p for t, p in unique_pts]
    
    idx_start = perimeter.index(ws_start)
    idx_end = perimeter.index(ws_end)
    
    path1 = []
    i = idx_start
    while True:
        path1.append(perimeter[i])
        if i == idx_end: break
        i = (i + 1) % len(perimeter)
        
    path2 = []
    i = idx_end
    while True:
        path2.append(perimeter[i])
        if i == idx_start: break
        i = (i + 1) % len(perimeter)
        
    poly1 = path1 + list(reversed(welded_chain))[1:-1]
    poly2 = path2 + welded_chain[1:-1]
    
    normal = _calculate_normal(v0, v1, v2)
    
    tris1, tris2 = [], []
    if len(poly1) >= 3:
        idx1 = _triangulate_polygon(poly1, normal)
        tris1 = [(poly1[i0], poly1[i1], poly1[i2]) for i0, i1, i2 in idx1]
    if len(poly2) >= 3:
        idx2 = _triangulate_polygon(poly2, normal)
        tris2 = [(poly2[i0], poly2[i1], poly2[i2]) for i0, i1, i2 in idx2]
        
    new_segments = []
    for i in range(len(welded_chain)-1):
        new_segments.append(BoundarySegment(welded_chain[i], welded_chain[i+1]))
        
    return tris1, tris2, new_segments

class TargetMeshPartitioner:
    @staticmethod
    def partition(
        target: AnatomicalMesh, 
        base_piece_id: str,
        operation_id: str,
        triangle_segments: Dict[int, List[BoundarySegment]]
    ) -> PartitionResult:
        
        if not triangle_segments:
            return PartitionResult((AnatomicalPiece(
                piece_id=f"{base_piece_id}_{operation_id}_unchanged",
                mesh=target,
                parent_id=base_piece_id,
                lineage_metadata={"operation_id": operation_id, "partition": "unchanged"}
            ),))
            
        modified_mesh_tris = []
        boundary_edges = set()
        
        # Poly tags to trace which triangles belong to "side 1" or "side 2" of the local cut
        # Actually, if we just retriangulate, we can build the dual graph of the entire new mesh to partition.
        
        for t_idx in range(len(target.indices) // 3):
            v0 = target.vertices[target.indices[t_idx * 3]]
            v1 = target.vertices[target.indices[t_idx * 3 + 1]]
            v2 = target.vertices[target.indices[t_idx * 3 + 2]]
            
            if t_idx in triangle_segments:
                try:
                    t1, t2, new_segs = _split_triangle(v0, v1, v2, triangle_segments[t_idx])
                    modified_mesh_tris.extend(t1)
                    modified_mesh_tris.extend(t2)
                    for seg in new_segs:
                        # Boundary edges are bidirectional walls
                        boundary_edges.add((seg.start, seg.end))
                        boundary_edges.add((seg.end, seg.start))
                except MeshPartitionError as e:
                    raise MeshPartitionError(f"Failed to partition triangle {t_idx}: {e}")
            else:
                modified_mesh_tris.append((v0, v1, v2))
                
        # Build global dual graph
        # Map edge -> list of triangle indices
        edge_to_tris: dict[tuple[Point3D, Point3D], list[int]] = {}
        tri_to_edges = []
        for i, (va, vb, vc) in enumerate(modified_mesh_tris):
            edges = [(va, vb), (vb, vc), (vc, va)]
            tri_to_edges.append(edges)
            for edge in edges:
                edge_to_tris.setdefault(edge, []).append(i)
                edge_to_tris.setdefault((edge[1], edge[0]), []).append(i)
                
        # BFS
        visited = set()
        components = []
        
        for i in range(len(modified_mesh_tris)):
            if i not in visited:
                comp = []
                queue = [i]
                visited.add(i)
                while queue:
                    curr = queue.pop(0)
                    comp.append(curr)
                    for edge in tri_to_edges[curr]:
                        if edge in boundary_edges or (edge[1], edge[0]) in boundary_edges:
                            continue # Wall
                        neighbors = edge_to_tris.get((edge[1], edge[0]), [])
                        for n in neighbors:
                            if n not in visited and n != curr:
                                visited.add(n)
                                queue.append(n)
                components.append(comp)
                
        if len(components) != 2:
            raise MeshPartitionError(f"Partitioning resulted in {len(components)} connected components, expected exactly 2.")
            
        # Deterministic assignment
        # Compare minimum centroid
        def get_min_centroid(comp_tris):
            cx, cy, cz = 0.0, 0.0, 0.0
            for t_idx in comp_tris:
                va, vb, vc = modified_mesh_tris[t_idx]
                cx += (va.x + vb.x + vc.x)/3
                cy += (va.y + vb.y + vc.y)/3
                cz += (va.z + vb.z + vc.z)/3
            return (cx/len(comp_tris), cy/len(comp_tris), cz/len(comp_tris))
            
        c0 = get_min_centroid(components[0])
        c1 = get_min_centroid(components[1])
        
        if c0 > c1:
            components[0], components[1] = components[1], components[0]
            
        pieces = []
        for side, comp in enumerate(components):
            builder = MeshBuilder()
            for t_idx in comp:
                va, vb, vc = modified_mesh_tris[t_idx]
                builder.add_triangle(builder.add_vertex(va), builder.add_vertex(vb), builder.add_vertex(vc))
                
            m = AnatomicalMesh(tuple(builder.vertices), tuple(builder.indices))
            side_str = "A" if side == 0 else "B"
            pieces.append(AnatomicalPiece(
                piece_id=f"{base_piece_id}_{operation_id}_{side_str}",
                mesh=m,
                parent_id=base_piece_id,
                lineage_metadata={"side": side_str, "operation_id": operation_id}
            ))
            
        return PartitionResult(tuple(pieces))
