from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Tuple, List, Dict, Set, Optional

from holomed.anatomy.models import Point3D
from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.freeform_intersection import TriangleIntersectionResult, IntersectionClassification, INTERSECTION_TOLERANCE, _point_lt

class BoundaryTopologyClassification(Enum):
    EMPTY = auto()
    ONE_CLOSED_LOOP = auto()
    MULTIPLE_DISJOINT_LOOPS = auto()
    OPEN_CHAIN = auto()
    BRANCHED = auto()
    SELF_INTERSECTING = auto()
    AMBIGUOUS = auto()

class BoundaryValidationError(AnatomyValidationError):
    """Raised when boundary topology is invalid or unsupported."""
    pass

@dataclass(frozen=True)
class BoundarySegment:
    start: Point3D
    end: Point3D

    def __post_init__(self):
        # Enforce lexicographic canonical ordering
        if _point_lt(self.end, self.start):
            orig_start = self.start
            object.__setattr__(self, 'start', self.end)
            object.__setattr__(self, 'end', orig_start)

def _weld_points(points: List[Point3D]) -> Dict[int, Point3D]:
    """
    Deterministically welds points within INTERSECTION_TOLERANCE.
    Returns a mapping from the original point's id() to the canonical representative Point3D.
    Note: The output must be deterministic based purely on geometry, independent of id() or insertion order.
    """
    # To be perfectly deterministic, we must sort points geometrically first.
    # We will tag them with their original index to preserve mapping.
    tagged_points = [(p, i) for i, p in enumerate(points)]
    
    # Sort geometrically
    def sort_key(item: Tuple[Point3D, int]):
        p = item[0]
        return (p.x, p.y, p.z, item[1])
        
    tagged_points.sort(key=sort_key)
    
    canonical_pts: List[Point3D] = []
    weld_map: Dict[int, Point3D] = {} # id(original_pt) -> canonical_pt
    
    for p, original_idx in tagged_points:
        # Find if it matches any existing canonical point
        matched_canonical = None
        for cp in canonical_pts:
            dist_sq = (p.x - cp.x)**2 + (p.y - cp.y)**2 + (p.z - cp.z)**2
            if dist_sq <= INTERSECTION_TOLERANCE**2:
                matched_canonical = cp
                break
                
        if matched_canonical is None:
            canonical_pts.append(p)
            matched_canonical = p
            
        weld_map[id(points[original_idx])] = matched_canonical
        
    return weld_map

@dataclass(frozen=True)
class BoundaryGraph:
    vertices: Tuple[Point3D, ...]
    segments: Tuple[BoundarySegment, ...]
    adjacency: Dict[Point3D, Tuple[Point3D, ...]] = field(default_factory=dict)

    @classmethod
    def build(cls, raw_results: List[TriangleIntersectionResult]) -> 'BoundaryGraph':
        # Extract segments
        raw_pts = []
        valid_results = []
        for r in raw_results:
            if r.classification == IntersectionClassification.SEGMENT:
                if r.start is None or r.end is None:
                    raise BoundaryValidationError("Segment intersection missing endpoints")
                raw_pts.extend([r.start, r.end])
                valid_results.append(r)
                
        if not valid_results:
            return cls(vertices=(), segments=(), adjacency={})
            
        weld_map = _weld_points(raw_pts)
        
        canonical_segments = set()
        for r in valid_results:
            p1 = weld_map[id(r.start)]
            p2 = weld_map[id(r.end)]
            
            # Check for zero-length edge after welding
            dist_sq = (p1.x - p2.x)**2 + (p1.y - p2.y)**2 + (p1.z - p2.z)**2
            if dist_sq <= INTERSECTION_TOLERANCE**2:
                raise BoundaryValidationError("Zero-length boundary edge detected after welding")
                
            seg = BoundarySegment(p1, p2)
            if seg in canonical_segments:
                raise BoundaryValidationError(f"Duplicate boundary segment detected: {seg}")
            canonical_segments.add(seg)
            
        # Build adjacency deterministically
        adj: Dict[Point3D, List[Point3D]] = {}
        sorted_segments = sorted(list(canonical_segments), key=lambda s: (s.start.x, s.start.y, s.start.z, s.end.x, s.end.y, s.end.z))
        
        for seg in sorted_segments:
            adj.setdefault(seg.start, []).append(seg.end)
            adj.setdefault(seg.end, []).append(seg.start)
            
        # Make deterministic immutable adjacency
        frozen_adj: Dict[Point3D, Tuple[Point3D, ...]] = {}
        for v in sorted(adj.keys(), key=lambda p: (p.x, p.y, p.z)):
            neighbors = sorted(adj[v], key=lambda p: (p.x, p.y, p.z))
            frozen_adj[v] = tuple(neighbors)
            
        vertices = tuple(frozen_adj.keys())
        segments = tuple(sorted_segments)
        return cls(vertices=vertices, segments=segments, adjacency=frozen_adj)

    def classify_topology(self) -> BoundaryTopologyClassification:
        if not self.vertices:
            return BoundaryTopologyClassification.EMPTY
            
        degrees = [len(neighbors) for neighbors in self.adjacency.values()]
        
        if any(d > 2 for d in degrees):
            return BoundaryTopologyClassification.BRANCHED
            
        if any(d == 1 for d in degrees):
            return BoundaryTopologyClassification.OPEN_CHAIN
            
        if any(d == 0 for d in degrees):
            return BoundaryTopologyClassification.AMBIGUOUS
            
        # Check connected components
        visited: Set[Point3D] = set()
        components = 0
        
        for v in self.vertices: # Iterates in deterministic order
            if v not in visited:
                components += 1
                # BFS/DFS
                stack = [v]
                while stack:
                    curr = stack.pop()
                    if curr not in visited:
                        visited.add(curr)
                        for neighbor in self.adjacency[curr]:
                            if neighbor not in visited:
                                stack.append(neighbor)
                                
        if components == 1:
            return BoundaryTopologyClassification.ONE_CLOSED_LOOP
        else:
            return BoundaryTopologyClassification.MULTIPLE_DISJOINT_LOOPS
