# -*- coding: utf-8 -*-
"""Phase F3.2-D4.0 Deterministic Cutter Provenance & Bounded Patch Partition Foundation."""

from dataclasses import dataclass
from typing import Tuple, List, Dict, Set

from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.mesh import AnatomicalMesh, AnatomicalPiece
from holomed.anatomy.swept_surface_generator import SweptCutMesh
from holomed.anatomy.boundary_graph import BoundarySegment
from holomed.anatomy.freeform_intersection import intersect_triangles, TriangleIntersectionResult, IntersectionClassification, CoplanarAmbiguityError
from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.mesh_partition import _split_triangle

class CutterPartitionError(AnatomyValidationError):
    pass

@dataclass(frozen=True)
class ProvenancedIntersectionSegment:
    target_triangle_index: int
    cutter_triangle_index: int
    start: Point3D
    end: Point3D

def compute_intersections_with_provenance(
    target: AnatomicalMesh,
    cutter: SweptCutMesh
) -> Tuple[ProvenancedIntersectionSegment, ...]:
    """
    Deterministically computes all triangle-triangle intersections between target and cutter meshes.
    Returns the provenanced segment records for all valid SEGMENT intersections.
    Fails closed on coplanar ambiguities.
    """
    segments = []
    
    num_target_tris = len(target.indices) // 3
    num_cutter_tris = len(cutter.indices) // 3
    
    # Extract triangles to avoid repeated indexing
    target_tris = []
    for i in range(num_target_tris):
        v0 = target.vertices[target.indices[i*3]]
        v1 = target.vertices[target.indices[i*3 + 1]]
        v2 = target.vertices[target.indices[i*3 + 2]]
        target_tris.append((v0, v1, v2))
        
    cutter_tris = []
    for j in range(num_cutter_tris):
        v0 = cutter.vertices[cutter.indices[j*3]]
        v1 = cutter.vertices[cutter.indices[j*3 + 1]]
        v2 = cutter.vertices[cutter.indices[j*3 + 2]]
        cutter_tris.append((v0, v1, v2))

    for i, t_tri in enumerate(target_tris):
        for j, c_tri in enumerate(cutter_tris):
            try:
                res = intersect_triangles(t_tri, c_tri)
                if res.classification == IntersectionClassification.SEGMENT:
                    assert res.start is not None and res.end is not None
                    segments.append(ProvenancedIntersectionSegment(
                        target_triangle_index=i,
                        cutter_triangle_index=j,
                        start=res.start,
                        end=res.end
                    ))
            except CoplanarAmbiguityError as e:
                raise e # Bubble up
                
    # Sort deterministically
    segments.sort(key=lambda s: (
        s.target_triangle_index,
        s.cutter_triangle_index,
        s.start.x, s.start.y, s.start.z,
        s.end.x, s.end.y, s.end.z
    ))
    
    return tuple(segments)

def get_segment_views(
    provenanced_segments: Tuple[ProvenancedIntersectionSegment, ...]
) -> Tuple[Dict[int, List[BoundarySegment]], Dict[int, List[BoundarySegment]]]:
    """
    Derives the indexed dictionary views for Target and Cutter triangles.
    """
    target_view: Dict[int, List[BoundarySegment]] = {}
    cutter_view: Dict[int, List[BoundarySegment]] = {}
    
    for ps in provenanced_segments:
        bs = BoundarySegment(ps.start, ps.end)
        target_view.setdefault(ps.target_triangle_index, []).append(bs)
        cutter_view.setdefault(ps.cutter_triangle_index, []).append(bs)
        
    # Sort the lists to guarantee determinism
    def sort_key(s: BoundarySegment):
        return (s.start.x, s.start.y, s.start.z, s.end.x, s.end.y, s.end.z)
        
    for k in target_view:
        target_view[k].sort(key=sort_key)
    for k in cutter_view:
        cutter_view[k].sort(key=sort_key)
        
    return target_view, cutter_view

def extract_cutter_patch(
    cutter: SweptCutMesh,
    cutter_triangle_segments: Dict[int, List[BoundarySegment]]
) -> AnatomicalMesh:
    """
    Extracts the unique bounded patch from the cutter mesh using dual-graph BFS.
    Fails closed if the patch touches the outer boundary of the cutter or if != 1 patch is found.
    """
    if not cutter_triangle_segments:
        raise CutterPartitionError("No cutter triangle segments provided, cannot extract patch")
        
    # 1. Identify outer boundary of the original cutter mesh
    # Edge -> count. Outer boundaries have count == 1.
    edge_counts: dict[tuple[Point3D, Point3D], int] = {}
    num_cutter_tris = len(cutter.indices) // 3
    for j in range(num_cutter_tris):
        v0 = cutter.vertices[cutter.indices[j*3]]
        v1 = cutter.vertices[cutter.indices[j*3 + 1]]
        v2 = cutter.vertices[cutter.indices[j*3 + 2]]
        for edge in [(v0, v1), (v1, v2), (v2, v0)]:
            # Correct lexicographical sorting for 3D points
            pA, pB = edge[0], edge[1]
            if (pA.x < pB.x) or \
               (pA.x == pB.x and pA.y < pB.y) or \
               (pA.x == pB.x and pA.y == pB.y and pA.z < pB.z):
                edge_norm = (pA, pB)
            else:
                edge_norm = (pB, pA)
            edge_counts[edge_norm] = edge_counts.get(edge_norm, 0) + 1
            
    outer_boundary_edges = set()
    for edge, count in edge_counts.items():
        if count == 1:
            outer_boundary_edges.add(edge)
            outer_boundary_edges.add((edge[1], edge[0]))
            
    outer_boundary_vertices = set()
    for e in outer_boundary_edges:
        outer_boundary_vertices.add(e[0])
        outer_boundary_vertices.add(e[1])
        
    # 2. Split cutter triangles and collect boundaries
    modified_mesh_tris = []
    barrier_edges = set()
    
    # We must preserve the mapping from new triangles to their original vertices 
    # to test against the outer boundary
    
    for t_idx in range(num_cutter_tris):
        v0 = cutter.vertices[cutter.indices[t_idx*3]]
        v1 = cutter.vertices[cutter.indices[t_idx*3 + 1]]
        v2 = cutter.vertices[cutter.indices[t_idx*3 + 2]]
        
        if t_idx in cutter_triangle_segments:
            try:
                from holomed.anatomy.local_arrangement import subdivide_cutter_triangle
                new_tris, new_segs = subdivide_cutter_triangle(v0, v1, v2, cutter_triangle_segments[t_idx])
                modified_mesh_tris.extend(new_tris)
                for seg in new_segs:
                    barrier_edges.add((seg.start, seg.end))
                    barrier_edges.add((seg.end, seg.start))
            except Exception as e:
                raise CutterPartitionError(f"Failed to split cutter triangle {t_idx}: {e}")
        else:
            modified_mesh_tris.append((v0, v1, v2))

    # 2.5 Deduplicate vertices across all cutter triangles to heal floating-point cracks
    global_verts: list[Point3D] = []
    def get_global(pt: Point3D) -> Point3D:
        for gv in global_verts:
            if abs(pt.x - gv.x) + abs(pt.y - gv.y) + abs(pt.z - gv.z) < 1e-5:
                return gv
        global_verts.append(pt)
        return pt
        
    deduped_tris = []
    for (va, vb, vc) in modified_mesh_tris:
        deduped_tris.append((get_global(va), get_global(vb), get_global(vc)))
    modified_mesh_tris = deduped_tris
    
    deduped_barrier = set()
    for edge in barrier_edges:
        deduped_barrier.add((get_global(edge[0]), get_global(edge[1])))
    barrier_edges = deduped_barrier
            
    # 3. Build adjacency graph of new triangles
    edge_to_tris: dict[tuple[Point3D, Point3D], list[int]] = {}
    tri_to_edges = []
    for i, (va, vb, vc) in enumerate(modified_mesh_tris):
        edges = [(va, vb), (vb, vc), (vc, va)]
        tri_to_edges.append(edges)
        for edge in edges:
            edge_to_tris.setdefault(edge, []).append(i)
            edge_to_tris.setdefault((edge[1], edge[0]), []).append(i)
            
    # 4. BFS to find components
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
                    if edge in barrier_edges or (edge[1], edge[0]) in barrier_edges:
                        continue # Wall
                    neighbors = edge_to_tris.get((edge[1], edge[0]), [])
                    for n in neighbors:
                        if n not in visited and n != curr:
                            visited.add(n)
                            queue.append(n)
            components.append(comp)
            
    # 5. Filter components touching outer boundary
    interior_components = []
    for comp in components:
        touches_outer = False
        for t_idx in comp:
            va, vb, vc = modified_mesh_tris[t_idx]
            if va in outer_boundary_vertices or vb in outer_boundary_vertices or vc in outer_boundary_vertices:
                touches_outer = True
                break
        if not touches_outer:
            interior_components.append(comp)

    # 6. Require exactly one interior component
    if len(interior_components) == 0:
        raise CutterPartitionError("No valid bounded patch found (all components touch outer boundary or none exist)")
    if len(interior_components) > 1:
        raise CutterPartitionError(f"Ambiguous cutter partitioning: found {len(interior_components)} bounded patches, expected exactly 1")
        
    patch_comp = interior_components[0]
    
    # Generate the AnatomicalMesh for the patch
    patch_verts: list[Point3D] = []
    vert_map = {}
    patch_indices = []
    
    for t_idx in patch_comp:
        for v in modified_mesh_tris[t_idx]:
            if v not in vert_map:
                vert_map[v] = len(patch_verts)
                patch_verts.append(v)
            patch_indices.append(vert_map[v])
            
    return AnatomicalMesh(vertices=tuple(patch_verts), indices=tuple(patch_indices))

def cap_partitioned_mesh(
    child: AnatomicalPiece,
    patch: AnatomicalMesh
) -> AnatomicalPiece:
    """
    Stitches a partition child mesh and a generated cutter patch into a closed manifold mesh.
    Automatically determines the correct patch orientation by analyzing boundary edge winding.
    """
    merged_vertices: list[Point3D] = []
    vert_map = {}
    
    def get_vert_idx(v: Point3D) -> int:
        qx, qy, qz = round(v.x, 6), round(v.y, 6), round(v.z, 6)
        key = (qx, qy, qz)
        if key not in vert_map:
            vert_map[key] = len(merged_vertices)
            merged_vertices.append(v)
        return vert_map[key]
        
    child_mesh = child.mesh
    new_indices = []
    
    child_edges: dict[tuple[int, int], int] = {}
    num_child_tris = len(child_mesh.indices) // 3
    for i in range(num_child_tris):
        i0, i1, i2 = child_mesh.indices[i*3], child_mesh.indices[i*3+1], child_mesh.indices[i*3+2]
        new_indices.extend([
            get_vert_idx(child_mesh.vertices[i0]),
            get_vert_idx(child_mesh.vertices[i1]),
            get_vert_idx(child_mesh.vertices[i2])
        ])
        
        # Track edges to find boundary
        e1 = (new_indices[-3], new_indices[-2])
        e2 = (new_indices[-2], new_indices[-1])
        e3 = (new_indices[-1], new_indices[-3])
        for e in (e1, e2, e3):
            child_edges[e] = child_edges.get(e, 0) + 1
            
    child_boundary = {e for e, count in child_edges.items() if count == 1}
    
    # Analyze patch orientation
    patch_edges: dict[tuple[int, int], int] = {}
    patch_indices_mapped = []
    num_patch_tris = len(patch.indices) // 3
    for i in range(num_patch_tris):
        i0, i1, i2 = patch.indices[i*3], patch.indices[i*3+1], patch.indices[i*3+2]
        idx0 = get_vert_idx(patch.vertices[i0])
        idx1 = get_vert_idx(patch.vertices[i1])
        idx2 = get_vert_idx(patch.vertices[i2])
        patch_indices_mapped.append((idx0, idx1, idx2))
        
        for e in [(idx0, idx1), (idx1, idx2), (idx2, idx0)]:
            patch_edges[e] = patch_edges.get(e, 0) + 1
            
    patch_boundary = {e for e, count in patch_edges.items() if count == 1}
    
    invert_patch = False
    match_found = False
    
    for pe in patch_boundary:
        if (pe[1], pe[0]) in child_boundary:
            # Opposite winding, which is correct for manifold stitching
            invert_patch = False
            match_found = True
            break
        elif pe in child_boundary:
            # Same winding, needs inversion
            invert_patch = True
            match_found = True
            break
            
    if not match_found:
        # Failsafe: if boundaries don't intersect, it's a disjoint mesh or closed mesh
        # We fail closed.
        raise CutterPartitionError("Patch boundary does not align with child boundary (non-manifold stitch)")
        
    for idx0, idx1, idx2 in patch_indices_mapped:
        if invert_patch:
            new_indices.extend([idx0, idx2, idx1])
        else:
            new_indices.extend([idx0, idx1, idx2])
            
    capped_mesh = AnatomicalMesh(tuple(merged_vertices), tuple(new_indices))
    
    return AnatomicalPiece(
        piece_id=f"{child.piece_id}_capped",
        mesh=capped_mesh,
        parent_id=child.parent_id,
        lineage_metadata={**(child.lineage_metadata or {}), "capped": "true"}
    )
