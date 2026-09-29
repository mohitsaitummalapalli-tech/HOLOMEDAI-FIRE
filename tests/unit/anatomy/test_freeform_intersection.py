import pytest

from holomed.anatomy.models import Point3D
from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.freeform_intersection import (
    TriangleIntersectionResult,
    IntersectionClassification,
    intersect_triangles,
    CoplanarAmbiguityError,
    DegenerateTriangleError
)

def test_intersect_disjoint_parallel():
    t1 = (Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0))
    t2 = (Point3D(0, 0, 1), Point3D(1, 0, 1), Point3D(0, 1, 1))
    
    res = intersect_triangles(t1, t2)
    assert res.classification == IntersectionClassification.NONE

def test_intersect_disjoint_non_parallel():
    t1 = (Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0))
    t2 = (Point3D(2, 0, -1), Point3D(3, 0, -1), Point3D(2, 1, 1))
    
    res = intersect_triangles(t1, t2)
    assert res.classification == IntersectionClassification.NONE

def test_intersect_point_touch():
    t1 = (Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0))
    t2 = (Point3D(0.5, 0.5, 0), Point3D(0.5, 0.5, 1), Point3D(1.5, 1.5, 1))
    
    res = intersect_triangles(t1, t2)
    assert res.classification == IntersectionClassification.POINT
    assert res.start == Point3D(0.5, 0.5, 0)
    assert res.end == Point3D(0.5, 0.5, 0)

def test_intersect_segment_full_crossing():
    t1 = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0))
    t2 = (Point3D(1, -1, -1), Point3D(1, 3, -1), Point3D(1, 1, 1))
    
    res1 = intersect_triangles(t1, t2)
    res2 = intersect_triangles(t2, t1)
    
    assert res1.classification == IntersectionClassification.SEGMENT
    assert res1.start == Point3D(1, 0, 0)
    assert res1.end == Point3D(1, 1, 0)
    
    # Symmetry
    assert res1 == res2

def test_coplanar_ambiguity_raised():
    t1 = (Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0))
    t2 = (Point3D(0.5, 0.5, 0), Point3D(1.5, 0.5, 0), Point3D(0.5, 1.5, 0))
    
    with pytest.raises(CoplanarAmbiguityError):
        intersect_triangles(t1, t2)

def test_degenerate_triangle_raised():
    # Collinear triangle
    t1 = (Point3D(0, 0, 0), Point3D(1, 1, 1), Point3D(2, 2, 2))
    t2 = (Point3D(0, 0, 1), Point3D(1, 0, 1), Point3D(0, 1, 1))
    
    with pytest.raises(DegenerateTriangleError):
        intersect_triangles(t1, t2)

def test_exact_edge_overlap():
    t1 = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0))
    t2 = (Point3D(1, 0, 0), Point3D(3, 0, 0), Point3D(2, 0, 1))
    
    res = intersect_triangles(t1, t2)
    assert res.classification == IntersectionClassification.SEGMENT
    assert res.start == Point3D(1, 0, 0)
    assert res.end == Point3D(2, 0, 0)
