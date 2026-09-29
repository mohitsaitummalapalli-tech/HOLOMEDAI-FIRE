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
    def trans(t: tuple[Point3D, Point3D, Point3D]) -> tuple[Point3D, Point3D, Point3D]:
        return (
            Point3D(t[0].x + 5, t[0].y + 5, t[0].z + 5),
            Point3D(t[1].x + 5, t[1].y + 5, t[1].z + 5),
            Point3D(t[2].x + 5, t[2].y + 5, t[2].z + 5)
        )

    t1_t = trans(t1)
    t2_t = trans(t2)
    trans_res = intersect_triangles(t1_t, t2_t)

    assert base_res.start is not None
    assert base_res.end is not None
    assert trans_res.classification == base_res.classification
    assert trans_res.start is not None
    assert trans_res.end is not None
    assert trans_res.start == Point3D(base_res.start.x + 5, base_res.start.y + 5, base_res.start.z + 5)
    assert trans_res.end == Point3D(base_res.end.x + 5, base_res.end.y + 5, base_res.end.z + 5)

def test_rotation_invariance():
    # Rotate by 45 deg around Z, then 45 deg around X
    import math
    angle = math.pi / 4.0
    c, s = math.cos(angle), math.sin(angle)
    
    def rotate_point(p: Point3D) -> Point3D:
        # Rz
        x1 = p.x * c - p.y * s
        y1 = p.x * s + p.y * c
        z1 = p.z
        # Rx
        x2 = x1
        y2 = y1 * c - z1 * s
        z2 = y1 * s + z1 * c
        return Point3D(x2, y2, z2)
        
    t1 = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0))
    t2 = (Point3D(1, -1, -1), Point3D(1, 3, -1), Point3D(1, 1, 1))
    base_res = intersect_triangles(t1, t2)
    
    t1_r: tuple[Point3D, Point3D, Point3D] = (rotate_point(t1[0]), rotate_point(t1[1]), rotate_point(t1[2]))
    t2_r: tuple[Point3D, Point3D, Point3D] = (rotate_point(t2[0]), rotate_point(t2[1]), rotate_point(t2[2]))
    rot_res = intersect_triangles(t1_r, t2_r)
    
    assert rot_res.classification == base_res.classification
    assert base_res.start is not None
    assert base_res.end is not None
    assert rot_res.start is not None
    assert rot_res.end is not None
    
    expected_start = rotate_point(base_res.start)
    expected_end = rotate_point(base_res.end)
    
    # Check geometrically equivalent within tolerance
    # Since intersect_triangles orders endpoints lexicographically, 
    # the rotated endpoints might swap their lexicographical order!
    d1 = math.dist((rot_res.start.x, rot_res.start.y, rot_res.start.z), (expected_start.x, expected_start.y, expected_start.z))
    d2 = math.dist((rot_res.end.x, rot_res.end.y, rot_res.end.z), (expected_end.x, expected_end.y, expected_end.z))
    match_forward = (d1 <= INTERSECTION_TOLERANCE and d2 <= INTERSECTION_TOLERANCE)
    
    d3 = math.dist((rot_res.start.x, rot_res.start.y, rot_res.start.z), (expected_end.x, expected_end.y, expected_end.z))
    d4 = math.dist((rot_res.end.x, rot_res.end.y, rot_res.end.z), (expected_start.x, expected_start.y, expected_start.z))
    match_reverse = (d3 <= INTERSECTION_TOLERANCE and d4 <= INTERSECTION_TOLERANCE)
    
    assert match_forward or match_reverse

# ==============================================================================
# MATHEMATICAL MEMBERSHIP
# ==============================================================================
def test_mathematical_membership():
    t1 = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0))
    t2 = (Point3D(1, -1, -1), Point3D(1, 3, -1), Point3D(1, 1, 1))
    res = intersect_triangles(t1, t2)
    
    assert res.classification == IntersectionClassification.SEGMENT
    assert res.start is not None
    assert res.end is not None
    
    def is_inside_triangle(pt: Point3D, tri: tuple[Point3D, Point3D, Point3D]):
        # Barycentric coordinates check using raw math
        from holomed.anatomy.freeform_intersection import _sub, _dot, _cross
        v0 = _sub(tri[1], tri[0])
        v1 = _sub(tri[2], tri[0])
        v2 = _sub(pt, tri[0])
        
        d00 = _dot(v0, v0)
        d01 = _dot(v0, v1)
        d11 = _dot(v1, v1)
        d20 = _dot(v2, v0)
        d21 = _dot(v2, v1)
        
        denom = d00 * d11 - d01 * d01
        v = (d11 * d20 - d01 * d21) / denom
        w = (d00 * d21 - d01 * d20) / denom
        u = 1.0 - v - w
        
        # Tolerate slight numerical error
        return u >= -1e-7 and v >= -1e-7 and w >= -1e-7
        
    assert is_inside_triangle(res.start, t1)
    assert is_inside_triangle(res.start, t2)
    assert is_inside_triangle(res.end, t1)
    assert is_inside_triangle(res.end, t2)
    
    # Ensure distinct points for segment
    assert math.dist((res.start.x, res.start.y, res.start.z), (res.end.x, res.end.y, res.end.z)) > INTERSECTION_TOLERANCE

# ==============================================================================
# IMMUTABILITY ATTEMPTS
# ==============================================================================
def test_immutability_enforcement():
    from dataclasses import FrozenInstanceError
    
    t1 = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0))
    t2 = (Point3D(1, -1, -1), Point3D(1, 3, -1), Point3D(1, 1, 1))
    res = intersect_triangles(t1, t2)
    
    assert res.start is not None
    
    # Attempt to mutate returned point
    with pytest.raises(FrozenInstanceError):
        setattr(res.start, 'x', 99.9)
        
    # Attempt to mutate returned segment
    with pytest.raises(FrozenInstanceError):
        setattr(res, 'classification', IntersectionClassification.NONE)

