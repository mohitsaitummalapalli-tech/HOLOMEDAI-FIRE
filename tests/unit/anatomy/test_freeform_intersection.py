import pytest
import math

from holomed.anatomy.models import Point3D
from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.freeform_intersection import (
    TriangleIntersectionResult,
    IntersectionClassification,
    intersect_triangles,
    CoplanarAmbiguityError,
    DegenerateTriangleError,
    INTERSECTION_TOLERANCE
)

# ==============================================================================
# DEFINITIVE CLASSIFICATION MATRIX CONTRACT TESTS
# ==============================================================================
# Cases A-J:
# A. vertex <-> vertex
# B. vertex <-> edge
# C. vertex <-> face
# D. edge <-> edge
# E. edge <-> face crossing
# F. coplanar disjoint
# G. coplanar shared vertex
# H. coplanar shared edge
# I. coplanar partial-area overlap
# J. identical triangles
# ==============================================================================

def test_case_A_vertex_vertex():
    # T1 and T2 touch exactly at one vertex
    t1 = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0))
    t2 = (Point3D(0, 0, 0), Point3D(-2, 0, 1), Point3D(0, -2, 1))
    res = intersect_triangles(t1, t2)
    assert res.classification == IntersectionClassification.POINT
    assert res.start == Point3D(0, 0, 0)
    assert res.end == Point3D(0, 0, 0)

def test_case_B_vertex_edge():
    # T1's vertex touches T2's edge
    t1 = (Point3D(1, 0, 0), Point3D(1, 2, 1), Point3D(2, 0, 1))
    t2 = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0)) # Edge is (0,0,0)->(2,0,0)
    res = intersect_triangles(t1, t2)
    assert res.classification == IntersectionClassification.POINT
    assert res.start == Point3D(1, 0, 0)
    assert res.end == Point3D(1, 0, 0)

def test_case_C_vertex_face():
    # T1's vertex touches the interior of T2's face
    t1 = (Point3D(0.5, 0.5, 0), Point3D(1, 1, 1), Point3D(0, 1, 1))
    t2 = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0))
    res = intersect_triangles(t1, t2)
    assert res.classification == IntersectionClassification.POINT
    assert res.start == Point3D(0.5, 0.5, 0)

def test_case_D_edge_edge():
    # T1 in XY plane, intersecting Y axis at [0,1]
    t1 = (Point3D(-1, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0))
    # T2 in YZ plane, intersecting Y axis at [-1,0]
    t2 = (Point3D(0, 0, -1), Point3D(0, 0, 1), Point3D(0, -1, 0))
    # Intersection is exactly the origin point
    res = intersect_triangles(t1, t2)
    assert res.classification == IntersectionClassification.POINT
    assert res.start == Point3D(0, 0, 0)

def test_case_E_edge_face_crossing():
    # T1 crosses T2 completely, creating a segment
    t1 = (Point3D(0.5, -1, -1), Point3D(0.5, 3, -1), Point3D(0.5, 1, 1))
    t2 = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0))
    res = intersect_triangles(t1, t2)
    assert res.classification == IntersectionClassification.SEGMENT
    assert res.start == Point3D(0.5, 0, 0)
    assert res.end == Point3D(0.5, 1.5, 0)

def test_case_F_coplanar_disjoint():
    t1 = (Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0))
    t2 = (Point3D(3, 3, 0), Point3D(4, 3, 0), Point3D(3, 4, 0))
    with pytest.raises(CoplanarAmbiguityError):
        intersect_triangles(t1, t2)

def test_case_G_coplanar_shared_vertex():
    t1 = (Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0))
    t2 = (Point3D(1, 0, 0), Point3D(2, 0, 0), Point3D(2, 1, 0))
    with pytest.raises(CoplanarAmbiguityError):
        intersect_triangles(t1, t2)

def test_case_H_coplanar_shared_edge():
    t1 = (Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0))
    t2 = (Point3D(1, 0, 0), Point3D(0, 1, 0), Point3D(1, 1, 0))
    with pytest.raises(CoplanarAmbiguityError):
        intersect_triangles(t1, t2)

def test_case_I_coplanar_partial_area_overlap():
    t1 = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0))
    t2 = (Point3D(1, 0, 0), Point3D(3, 0, 0), Point3D(1, 2, 0))
    with pytest.raises(CoplanarAmbiguityError):
        intersect_triangles(t1, t2)

def test_case_J_identical_triangles():
    t1 = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0))
    with pytest.raises(CoplanarAmbiguityError):
        intersect_triangles(t1, t1)

# ==============================================================================
# SYMMETRY, IMMUTABILITY & DETERMINISM
# ==============================================================================
def test_strict_symmetry_and_immutability():
    t1 = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0))
    t2 = (Point3D(1, -1, -1), Point3D(1, 3, -1), Point3D(1, 1, 1))

    # Store references to original objects to verify immutability
    t1_ids = [id(p) for p in t1]
    t2_ids = [id(p) for p in t2]

    res1 = intersect_triangles(t1, t2)
    res2 = intersect_triangles(t2, t1)

    # Exact equivalence (geometric & canonical ordering)
    assert res1.classification == IntersectionClassification.SEGMENT
    assert res1.classification == res2.classification
    assert res1.start == res2.start
    assert res1.end == res2.end
    assert res1.start == Point3D(1, 0, 0)
    assert res1.end == Point3D(1, 1, 0)

    # Check immutability
    assert [id(p) for p in t1] == t1_ids
    assert [id(p) for p in t2] == t2_ids

# ==============================================================================
# DISJOINT & TOLERANCE BOUNDARIES
# ==============================================================================
def test_intersect_disjoint_parallel():
    t1 = (Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0))
    t2 = (Point3D(0, 0, 1), Point3D(1, 0, 1), Point3D(0, 1, 1))
    assert intersect_triangles(t1, t2).classification == IntersectionClassification.NONE

def test_intersect_disjoint_non_parallel():
    t1 = (Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0))
    t2 = (Point3D(2, 0, -1), Point3D(3, 0, -1), Point3D(2, 1, 1))
    assert intersect_triangles(t1, t2).classification == IntersectionClassification.NONE

def test_degenerate_triangle_raised():
    t1 = (Point3D(0, 0, 0), Point3D(1, 1, 1), Point3D(2, 2, 2)) # Collinear
    t2 = (Point3D(0, 0, 1), Point3D(1, 0, 1), Point3D(0, 1, 1))
    with pytest.raises(DegenerateTriangleError):
        intersect_triangles(t1, t2)

def test_tolerance_tiny_overlap():
    # Intersection overlap is just slightly above tolerance -> Point or tiny Segment
    dz = INTERSECTION_TOLERANCE * 0.5 # Below tolerance
    t1 = (Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(0, 1, 0))
    t2 = (Point3D(0.5, 0.5, dz), Point3D(1, 1, 1), Point3D(0, 1, 1))

    # If the point is within tolerance of plane 1, it should be considered touching
    res = intersect_triangles(t1, t2)
    assert res.classification == IntersectionClassification.POINT

# ==============================================================================
# TRANSFORM INVARIANCE
# ==============================================================================
def test_translation_invariance():
    t1 = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0))
    t2 = (Point3D(1, -1, -1), Point3D(1, 3, -1), Point3D(1, 1, 1))
    base_res = intersect_triangles(t1, t2)

    # Translate by +5, +5, +5
    def trans(t):
        return tuple(Point3D(p.x + 5, p.y + 5, p.z + 5) for p in t)

    t1_t = trans(t1)
    t2_t = trans(t2)
    trans_res = intersect_triangles(t1_t, t2_t)

    assert trans_res.classification == base_res.classification
    assert trans_res.start == Point3D(base_res.start.x + 5, base_res.start.y + 5, base_res.start.z + 5)
    assert trans_res.end == Point3D(base_res.end.x + 5, base_res.end.y + 5, base_res.end.z + 5)
