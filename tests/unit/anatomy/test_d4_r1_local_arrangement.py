import pytest
from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.mesh import AnatomicalMesh, AnatomicalPiece
from holomed.anatomy.slicing import SliceEngine
from holomed.anatomy.freeform_capping import CutterPartitionError
from holomed.anatomy.boundary_graph import BoundarySegment
from holomed.anatomy.local_arrangement import subdivide_cutter_triangle


def test_zero_segments():
    v0, v1, v2 = Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0)
    tris, segs = subdivide_cutter_triangle(v0, v1, v2, [])
    assert len(tris) == 1
    assert len(segs) == 0
    # Should equal original triangle
    tv0, tv1, tv2 = tris[0]
    assert tv0 == v0
    assert tv1 == v1
    assert tv2 == v2

def test_one_segment():
    v0, v1, v2 = Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0)
    # segment splitting the triangle in half
    seg = BoundarySegment(Point3D(0.5, 0, 0), Point3D(0, 0.5, 0))
    tris, segs = subdivide_cutter_triangle(v0, v1, v2, [seg])
    assert len(tris) == 3 # Two triangles for the quad, one for the triangle
    assert len(segs) == 1

def test_two_disjoint_segments():
    v0, v1, v2 = Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0)
    # segment 1 near v1
    seg1 = BoundarySegment(Point3D(0.8, 0, 0), Point3D(0.8, 0.2, 0))
    # segment 2 near v2
    seg2 = BoundarySegment(Point3D(0, 0.8, 0), Point3D(0.2, 0.8, 0))
    tris, segs = subdivide_cutter_triangle(v0, v1, v2, [seg1, seg2])
    assert len(tris) > 1
    assert len(segs) == 2

def test_shared_endpoint():
    v0, v1, v2 = Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0)
    seg1 = BoundarySegment(Point3D(0, 0, 0), Point3D(0.5, 0.5, 0))
    seg2 = BoundarySegment(Point3D(0.5, 0.5, 0), Point3D(1, 0, 0))
    tris, segs = subdivide_cutter_triangle(v0, v1, v2, [seg1, seg2])
    assert len(tris) > 0
    assert len(segs) == 2

def test_proper_crossing_fails():
    v0, v1, v2 = Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0)
    seg1 = BoundarySegment(Point3D(0.2, 0.2, 0), Point3D(0.8, 0.8, 0))
    seg2 = BoundarySegment(Point3D(0.2, 0.8, 0), Point3D(0.8, 0.2, 0))
    with pytest.raises(CutterPartitionError, match="Proper crossing"):
        subdivide_cutter_triangle(v0, v1, v2, [seg1, seg2])

