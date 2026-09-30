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

def test_partition_transformation_invariance():
    v0 = Point3D(0.0, 0.0, 0.0)
    v1 = Point3D(1.0, 0.0, 0.0)
    v2 = Point3D(0.0, 1.0, 0.0)
    
    mesh = AnatomicalMesh(vertices=(v0, v1, v2), indices=(0, 1, 2))
    
    seg = BoundarySegment(Point3D(0.5, 0.0, 0.0), Point3D(0.0, 0.5, 0.0))
    res1 = TargetMeshPartitioner.partition(mesh, "base", "op", {0: [seg]})
    
    import math
    def tx(p: Point3D) -> Point3D:
        # Translate by (5.0, -5.0, 3.0)
        p = Point3D(p.x + 5.0, p.y - 5.0, p.z + 3.0)
        
        # Nontrivial 3D rotation
        axis = (1.0, 2.0, 3.0)
        angle = 0.456
        ax, ay, az = axis
        norm = math.sqrt(ax*ax + ay*ay + az*az)
        ax, ay, az = ax/norm, ay/norm, az/norm
        
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        
        cross_x = ay * p.z - az * p.y
        cross_y = az * p.x - ax * p.z
        cross_z = ax * p.y - ay * p.x
        
        dot = ax * p.x + ay * p.y + az * p.z
        
        return Point3D(
            p.x * cos_a + cross_x * sin_a + ax * dot * (1 - cos_a),
            p.y * cos_a + cross_y * sin_a + ay * dot * (1 - cos_a),
            p.z * cos_a + cross_z * sin_a + az * dot * (1 - cos_a)
        )
        
    mesh_t = AnatomicalMesh(
        vertices=(tx(v0), tx(v1), tx(v2)),
        indices=(0, 1, 2)
    )
    seg_t = BoundarySegment(tx(seg.start), tx(seg.end))
    res2 = TargetMeshPartitioner.partition(mesh_t, "base", "op", {0: [seg_t]})
    
    # Topology should be completely identical.
    assert len(res1.child_pieces) == len(res2.child_pieces)
    for p1, p2 in zip(res1.child_pieces, res2.child_pieces):
        assert len(p1.mesh.vertices) == len(p2.mesh.vertices)
        assert len(p1.mesh.indices) == len(p2.mesh.indices)

def test_mesh_accounting_invariants():
    v0 = Point3D(0.0, 0.0, 0.0)
    v1 = Point3D(1.0, 0.0, 0.0)
    v2 = Point3D(0.0, 1.0, 0.0)
    
    mesh = AnatomicalMesh(vertices=(v0, v1, v2), indices=(0, 1, 2))
    seg = BoundarySegment(Point3D(0.5, 0.0, 0.0), Point3D(0.0, 0.5, 0.0))
    
    res = TargetMeshPartitioner.partition(mesh, "base", "op", {0: [seg]})
    
    # Original mesh had 1 triangle.
    # Output has 1 triangle (Child A) and 2 triangles (Child B).
    total_output_tris = 0
    for child in res.child_pieces:
        total_output_tris += len(child.mesh.indices) // 3
        # Ensure all triangle areas > 0
        for i in range(len(child.mesh.indices) // 3):
            ca = child.mesh.vertices[child.mesh.indices[i*3]]
            cb = child.mesh.vertices[child.mesh.indices[i*3+1]]
            cc = child.mesh.vertices[child.mesh.indices[i*3+2]]
            
            dx1, dy1, dz1 = cb.x - ca.x, cb.y - ca.y, cb.z - ca.z
            dx2, dy2, dz2 = cc.x - ca.x, cc.y - ca.y, cc.z - ca.z
            nx = dy1*dz2 - dz1*dy2
            ny = dz1*dx2 - dx1*dz2
            nz = dx1*dy2 - dy1*dx2
            area = 0.5 * (nx*nx + ny*ny + nz*nz)**0.5
            assert area > 1e-6
            
    assert total_output_tris == 3
