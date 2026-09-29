import pytest
from holomed.anatomy.models import Point3D
from holomed.anatomy.mesh import AnatomicalMesh
from holomed.anatomy.boundary_graph import BoundarySegment
from holomed.anatomy.mesh_partition import TargetMeshPartitioner, MeshPartitionError

def test_partition_one_triangle_split():
    v0 = Point3D(0.0, 0.0, 0.0)
    v1 = Point3D(1.0, 0.0, 0.0)
    v2 = Point3D(0.0, 1.0, 0.0)
    
    mesh = AnatomicalMesh(
        vertices=(v0, v1, v2),
        indices=(0, 1, 2)
    )
    
    # Segment cuts from v0-v1 to v0-v2
    s_start = Point3D(0.5, 0.0, 0.0)
    s_end = Point3D(0.0, 0.5, 0.0)
    seg = BoundarySegment(s_start, s_end)
    
    triangle_segments = {0: [seg]}
    
    result = TargetMeshPartitioner.partition(mesh, "base", "op", triangle_segments)
    
    assert len(result.child_pieces) == 2
    # One piece should be the small corner triangle (3 indices)
    # The other should be the quad (6 indices)
    counts = {len(result.child_pieces[0].mesh.indices), len(result.child_pieces[1].mesh.indices)}
    assert counts == {3, 6}

def test_partition_multiple_segments():
    v0 = Point3D(0.0, 0.0, 0.0)
    v1 = Point3D(1.0, 0.0, 0.0)
    v2 = Point3D(0.0, 1.0, 0.0)
    
    mesh = AnatomicalMesh(
        vertices=(v0, v1, v2),
        indices=(0, 1, 2)
    )
    
    # Segment chain cuts from v0-v1 to v0-v2 through an interior point
    s_start = Point3D(0.5, 0.0, 0.0)
    s_mid = Point3D(0.2, 0.2, 0.0)
    s_end = Point3D(0.0, 0.5, 0.0)
    
    seg1 = BoundarySegment(s_start, s_mid)
    seg2 = BoundarySegment(s_mid, s_end)
    
    triangle_segments = {0: [seg1, seg2]}
    
    result = TargetMeshPartitioner.partition(mesh, "base", "op", triangle_segments)
    
    assert len(result.child_pieces) == 2
    counts = {len(result.child_pieces[0].mesh.indices), len(result.child_pieces[1].mesh.indices)}
    # The poly split should produce 2 polys, triangulated.
    # We don't check exact triangle count, just that it didn't fail
    assert sum(counts) >= 9 # at least 3 triangles total

def test_partition_fail_closed_on_branch():
    v0 = Point3D(0.0, 0.0, 0.0)
    v1 = Point3D(1.0, 0.0, 0.0)
    v2 = Point3D(0.0, 1.0, 0.0)
    
    mesh = AnatomicalMesh(
        vertices=(v0, v1, v2),
        indices=(0, 1, 2)
    )
    
    s_start = Point3D(0.5, 0.0, 0.0)
    s_mid = Point3D(0.2, 0.2, 0.0)
    s_end = Point3D(0.0, 0.5, 0.0)
    s_end2 = Point3D(0.4, 0.4, 0.0)
    
    seg1 = BoundarySegment(s_start, s_mid)
    seg2 = BoundarySegment(s_mid, s_end)
    seg3 = BoundarySegment(s_mid, s_end2) # Branch!
    
    triangle_segments = {0: [seg1, seg2, seg3]}
    
    with pytest.raises(MeshPartitionError, match="Branched boundary chain"):
        TargetMeshPartitioner.partition(mesh, "base", "op", triangle_segments)

def test_partition_fail_closed_on_closed_loop():
    v0 = Point3D(0.0, 0.0, 0.0)
    v1 = Point3D(1.0, 0.0, 0.0)
    v2 = Point3D(0.0, 1.0, 0.0)
    
    mesh = AnatomicalMesh(
        vertices=(v0, v1, v2),
        indices=(0, 1, 2)
    )
    
    s_start = Point3D(0.5, 0.0, 0.0)
    s_mid = Point3D(0.2, 0.2, 0.0)
    s_end = Point3D(0.0, 0.5, 0.0)
    
    seg1 = BoundarySegment(s_start, s_mid)
    seg2 = BoundarySegment(s_mid, s_end)
    seg3 = BoundarySegment(s_end, s_start) # Closed loop inside one triangle!
    
    triangle_segments = {0: [seg1, seg2, seg3]}
    
    with pytest.raises(MeshPartitionError, match="Multiple disjoint chains or loops"):
        TargetMeshPartitioner.partition(mesh, "base", "op", triangle_segments)

def test_partition_empty():
    v0 = Point3D(0.0, 0.0, 0.0)
    v1 = Point3D(1.0, 0.0, 0.0)
    v2 = Point3D(0.0, 1.0, 0.0)
    
    mesh = AnatomicalMesh(
        vertices=(v0, v1, v2),
        indices=(0, 1, 2)
    )
    
    result = TargetMeshPartitioner.partition(mesh, "base", "op", {})
    
    assert len(result.child_pieces) == 1
    assert result.child_pieces[0].piece_id == "base_op_unchanged"
