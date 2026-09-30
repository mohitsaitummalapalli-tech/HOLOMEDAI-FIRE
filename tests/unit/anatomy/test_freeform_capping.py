import math
import pytest

from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.mesh import AnatomicalMesh
from holomed.anatomy.swept_surface_generator import SweptCutMesh, generate_swept_surface, generate_swept_mesh, TransportedFrame
from holomed.anatomy.trajectory import ProcessedCutTrajectory, TrajectorySample, TrajectoryState
from holomed.anatomy.freeform_capping import (
    compute_intersections_with_provenance,
    get_segment_views,
    extract_cutter_patch,
    cap_partitioned_mesh,
    ProvenancedIntersectionSegment,
    CutterPartitionError
)
from holomed.anatomy.mesh_partition import TargetMeshPartitioner

def create_box_mesh(size=10.0, offset=Point3D(0, 0, 0)) -> AnatomicalMesh:
    v = [
        Point3D(-size+offset.x, -size+offset.y, -size+offset.z),
        Point3D(size+offset.x, -size+offset.y, -size+offset.z),
        Point3D(size+offset.x, size+offset.y, -size+offset.z),
        Point3D(-size+offset.x, size+offset.y, -size+offset.z),
        Point3D(-size+offset.x, -size+offset.y, size+offset.z),
        Point3D(size+offset.x, -size+offset.y, size+offset.z),
        Point3D(size+offset.x, size+offset.y, size+offset.z),
        Point3D(-size+offset.x, size+offset.y, size+offset.z),
    ]
    # 12 triangles for 6 faces
    indices = [
        0, 2, 1, 0, 3, 2, # Bottom
        4, 5, 6, 4, 6, 7, # Top
        0, 1, 5, 0, 5, 4, # Front
        1, 2, 6, 1, 6, 5, # Right
        2, 3, 7, 2, 7, 6, # Back
        3, 0, 4, 3, 4, 7  # Left
    ]
    return AnatomicalMesh(tuple(v), tuple(indices))

def create_straight_cutter(width=30.0, length=30.0, offset=Point3D(0, 0, 0)) -> SweptCutMesh:
    samples = [
        TrajectorySample(Point3D(-length/2 + offset.x, 0 + offset.y, 0 + offset.z), 0.0, TrajectoryState.START),
        TrajectorySample(Point3D(0 + offset.x, 0 + offset.y, 0 + offset.z), 1.0, TrajectoryState.ACTIVE),
        TrajectorySample(Point3D(length/2 + offset.x, 0 + offset.y, 0 + offset.z), 2.0, TrajectoryState.END)
    ]
    traj = ProcessedCutTrajectory("test_traj", "correl", tuple(samples), tuple([Vector3D(1,0,0)]*3))
    
    frames = [
        TransportedFrame(Vector3D(1, 0, 0), Vector3D(0, 0, 1), Vector3D(0, -1, 0)),
        TransportedFrame(Vector3D(1, 0, 0), Vector3D(0, 0, 1), Vector3D(0, -1, 0)),
        TransportedFrame(Vector3D(1, 0, 0), Vector3D(0, 0, 1), Vector3D(0, -1, 0)),
    ]
    
    surf = generate_swept_surface(traj, tuple(frames), width, 1.0)
    return generate_swept_mesh(surf, u_samples=3)

def test_provenance_and_patch_extraction_straight():
    target = create_box_mesh(size=2.0)
    cutter = create_straight_cutter(width=8.0, length=8.0)
    
    prov = compute_intersections_with_provenance(target, cutter)
    assert len(prov) > 0, "Should have intersected"
    
    for p in prov:
        assert 0 <= p.target_triangle_index < len(target.indices) // 3
        assert 0 <= p.cutter_triangle_index < len(cutter.indices) // 3
        
    t_view, c_view = get_segment_views(prov)
    
    patch = extract_cutter_patch(cutter, c_view)
    
    assert len(patch.vertices) > 0
    assert len(patch.indices) % 3 == 0

def test_grafted_capping_watertight():
    target = create_box_mesh(size=2.0)
    cutter = create_straight_cutter(width=8.0, length=8.0)
    
    prov = compute_intersections_with_provenance(target, cutter)
    t_view, c_view = get_segment_views(prov)
    
    patch = extract_cutter_patch(cutter, c_view)
    
    partition_res = TargetMeshPartitioner.partition(target, "box", "op1", t_view)
    assert len(partition_res.child_pieces) == 2
    
    child_a = partition_res.child_pieces[0]
    child_b = partition_res.child_pieces[1]
    
    capped_a = cap_partitioned_mesh(child_a, patch)
    capped_b = cap_partitioned_mesh(child_b, patch)
    
    def is_watertight(mesh):
        edges = {}
        for i in range(len(mesh.indices) // 3):
            i0, i1, i2 = mesh.indices[i*3:i*3+3]
            for e in [(i0, i1), (i1, i2), (i2, i0)]:
                edges[e] = edges.get(e, 0) + 1
        
        # In a watertight manifold, every edge must have a perfectly matched opposite edge
        for e in edges:
            if edges[e] != 1: return False
            if edges.get((e[1], e[0]), 0) != 1: return False
        return True
        
    assert is_watertight(capped_a.mesh)
    assert is_watertight(capped_b.mesh)
    
    # Volume check? Optional but good. The original box size=2.0 means volume is (4)^3 = 64.
    # The sum of volumes should be 64.
    def compute_volume(mesh):
        vol = 0.0
        for i in range(len(mesh.indices) // 3):
            v0 = mesh.vertices[mesh.indices[i*3]]
            v1 = mesh.vertices[mesh.indices[i*3+1]]
            v2 = mesh.vertices[mesh.indices[i*3+2]]
            vol += v0.x*v1.y*v2.z - v0.x*v2.y*v1.z - v1.x*v0.y*v2.z + v1.x*v2.y*v0.z + v2.x*v0.y*v1.z - v2.x*v1.y*v0.z
        return abs(vol) / 6.0
        
    vol_a = compute_volume(capped_a.mesh)
    vol_b = compute_volume(capped_b.mesh)
    assert abs(vol_a + vol_b - 64.0) < 1e-4

def test_cutter_outer_boundary_rejection():
    # Make cutter too small, so the intersection touches the cutter's edge
    target = create_box_mesh(size=4.0)
    cutter = create_straight_cutter(width=2.0, length=2.0) # completely inside target
    
    prov = compute_intersections_with_provenance(target, cutter)
    t_view, c_view = get_segment_views(prov)
    
    with pytest.raises(CutterPartitionError):
        extract_cutter_patch(cutter, c_view)
