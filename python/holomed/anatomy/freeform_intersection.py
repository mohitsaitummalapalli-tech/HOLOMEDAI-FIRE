import math
from dataclasses import dataclass
from enum import Enum, auto
from typing import Tuple, List, Optional

from holomed.anatomy.models import Point3D, Vector3D, COORDINATE_EPSILON
from holomed.anatomy.exceptions import AnatomyValidationError

class IntersectionClassification(Enum):
    NONE = auto()
    POINT = auto()
    SEGMENT = auto()

@dataclass(frozen=True)
class TriangleIntersectionResult:
    classification: IntersectionClassification
    start: Optional[Point3D] = None
    end: Optional[Point3D] = None

INTERSECTION_TOLERANCE = 1e-8  # 10 nanometers, for raw float geometric math
AREA_TOLERANCE = 1e-12         # square meters

class CoplanarAmbiguityError(AnatomyValidationError):
    """Raised when coplanar triangles overlap ambiguously."""
    pass

class DegenerateTriangleError(AnatomyValidationError):
    """Raised when an input triangle is degenerate or non-finite."""
    pass

# Raw tuple math to avoid intermediate 1e-6 quantization of Point3D/Vector3D
_Vec3 = Tuple[float, float, float]

def _sub(a: Point3D, b: Point3D) -> _Vec3:
    return (a.x - b.x, a.y - b.y, a.z - b.z)

def _add(a: Point3D, v: _Vec3) -> Point3D:
    return Point3D(a.x + v[0], a.y + v[1], a.z + v[2])

def _mul(v: _Vec3, s: float) -> _Vec3:
    return (v[0] * s, v[1] * s, v[2] * s)

def _cross(v1: _Vec3, v2: _Vec3) -> _Vec3:
    return (
        v1[1] * v2[2] - v1[2] * v2[1],
        v1[2] * v2[0] - v1[0] * v2[2],
        v1[0] * v2[1] - v1[1] * v2[0]
    )

def _dot(v1: _Vec3, v2: _Vec3) -> float:
    return v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2]

def _normalize(v: _Vec3) -> _Vec3:
    mag = math.sqrt(_dot(v, v))
    return (v[0] / mag, v[1] / mag, v[2] / mag)

def _point_lt(p1: Point3D, p2: Point3D) -> bool:
    """Exact lexicographic comparison for deterministic ordering."""
    if p1.x != p2.x: return p1.x < p2.x
    if p1.y != p2.y: return p1.y < p2.y
    return p1.z < p2.z

def _validate_triangle(t: Tuple[Point3D, Point3D, Point3D]) -> Tuple[_Vec3, float]:
    for p in t:
        if not (math.isfinite(p.x) and math.isfinite(p.y) and math.isfinite(p.z)):
            raise DegenerateTriangleError("Non-finite coordinates in triangle")
    
    v01 = _sub(t[1], t[0])
    v02 = _sub(t[2], t[0])
    n = _cross(v01, v02)
    area = math.sqrt(_dot(n, n)) / 2.0
    if area < AREA_TOLERANCE:
        raise DegenerateTriangleError("Degenerate triangle (zero/near-zero area or collinear)")
    
    n = _normalize(n)
    d = _dot(n, (t[0].x, t[0].y, t[0].z))
    return n, d

def _compute_intervals(t: Tuple[Point3D, Point3D, Point3D], dists: List[float]) -> Tuple[Point3D, Point3D]:
    pts = []
    for i in range(3):
        j = (i + 1) % 3
        d1, d2 = dists[i], dists[j]
        
        # Edge crosses the plane
        if (d1 > INTERSECTION_TOLERANCE and d2 < -INTERSECTION_TOLERANCE) or \
           (d1 < -INTERSECTION_TOLERANCE and d2 > INTERSECTION_TOLERANCE):
            t_val = d1 / (d1 - d2)
            pts.append(_add(t[i], _mul(_sub(t[j], t[i]), t_val)))
        
        # Vertex exactly on plane
        if abs(d1) <= INTERSECTION_TOLERANCE:
            pts.append(t[i])
            
    # Deduplicate points in 3D
    unique_pts = []
    for p in pts:
        is_dup = False
        for up in unique_pts:
            dist = math.sqrt(_dot(_sub(p, up), _sub(p, up)))
            if dist <= INTERSECTION_TOLERANCE:
                is_dup = True
                break
        if not is_dup:
            unique_pts.append(p)
            
    if len(unique_pts) == 0:
        raise AnatomyValidationError("Logic error: Expected intersection points on plane, found none")
    elif len(unique_pts) == 1:
        return (unique_pts[0], unique_pts[0])
    else:
        # If >2 points found (due to numerical fuzziness around coplanar/collinear edges),
        # take the two most distant points.
        if len(unique_pts) > 2:
            max_dist = -1.0
            best_pair = (unique_pts[0], unique_pts[1])
            for i in range(len(unique_pts)):
                for j in range(i + 1, len(unique_pts)):
                    dist = math.sqrt(_dot(_sub(unique_pts[i], unique_pts[j]), _sub(unique_pts[i], unique_pts[j])))
                    if dist > max_dist:
                        max_dist = dist
                        best_pair = (unique_pts[i], unique_pts[j])
            return best_pair
        return (unique_pts[0], unique_pts[1])

def intersect_triangles(t1: Tuple[Point3D, Point3D, Point3D], t2: Tuple[Point3D, Point3D, Point3D]) -> TriangleIntersectionResult:
    """
    Computes the deterministic 3D geometric intersection of two non-coplanar triangles.
    Returns NONE, POINT, or SEGMENT.
    Endpoints are deterministically sorted lexicographically.
    Raises CoplanarAmbiguityError if triangles are coplanar.
    Raises DegenerateTriangleError if triangles are nonfinite, collinear, or zero-area.
    """
    n1, d1 = _validate_triangle(t1)
    n2, d2 = _validate_triangle(t2)
    
    d2_to_p1 = [_dot(n1, (p.x, p.y, p.z)) - d1 for p in t2]
    d1_to_p2 = [_dot(n2, (p.x, p.y, p.z)) - d2 for p in t1]
    
    if all(d > INTERSECTION_TOLERANCE for d in d2_to_p1) or all(d < -INTERSECTION_TOLERANCE for d in d2_to_p1):
        return TriangleIntersectionResult(IntersectionClassification.NONE)
    if all(d > INTERSECTION_TOLERANCE for d in d1_to_p2) or all(d < -INTERSECTION_TOLERANCE for d in d1_to_p2):
        return TriangleIntersectionResult(IntersectionClassification.NONE)
        
    coplanar_2 = all(abs(d) <= INTERSECTION_TOLERANCE for d in d2_to_p1)
    coplanar_1 = all(abs(d) <= INTERSECTION_TOLERANCE for d in d1_to_p2)
    
    if coplanar_1 and coplanar_2:
        raise CoplanarAmbiguityError("Coplanar triangles overlapping ambiguously")
        
    p1_start, p1_end = _compute_intervals(t1, d1_to_p2)
    p2_start, p2_end = _compute_intervals(t2, d2_to_p1)
    
    direction = _cross(n1, n2)
    if math.sqrt(_dot(direction, direction)) < 1e-14:
        raise CoplanarAmbiguityError("Triangles are nearly coplanar, caught during cross product")
    
    direction = _normalize(direction)
    
    t1_s = _dot(direction, (p1_start.x, p1_start.y, p1_start.z))
    t1_e = _dot(direction, (p1_end.x, p1_end.y, p1_end.z))
    if t1_s > t1_e:
        t1_s, t1_e = t1_e, t1_s
        p1_start, p1_end = p1_end, p1_start
        
    t2_s = _dot(direction, (p2_start.x, p2_start.y, p2_start.z))
    t2_e = _dot(direction, (p2_end.x, p2_end.y, p2_end.z))
    if t2_s > t2_e:
        t2_s, t2_e = t2_e, t2_s
        p2_start, p2_end = p2_end, p2_start
        
    t_start = max(t1_s, t2_s)
    t_end = min(t1_e, t2_e)
    
    if t_start > t_end + INTERSECTION_TOLERANCE:
        return TriangleIntersectionResult(IntersectionClassification.NONE)
        
    p_res_start = p1_start if t1_s >= t2_s else p2_start
    p_res_end = p1_end if t1_e <= t2_e else p2_end
    
    if abs(t_start - t_end) <= INTERSECTION_TOLERANCE:
        return TriangleIntersectionResult(IntersectionClassification.POINT, start=p_res_start, end=p_res_start)
        
    if _point_lt(p_res_end, p_res_start):
        p_res_start, p_res_end = p_res_end, p_res_start
        
    return TriangleIntersectionResult(IntersectionClassification.SEGMENT, start=p_res_start, end=p_res_end)
