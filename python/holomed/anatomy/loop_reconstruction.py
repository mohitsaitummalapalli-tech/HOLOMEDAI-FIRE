from dataclasses import dataclass
from typing import Tuple, List, Set, Optional

from holomed.anatomy.models import Point3D
from holomed.anatomy.boundary_graph import BoundaryGraph, BoundaryTopologyClassification, BoundaryValidationError
from holomed.anatomy.freeform_intersection import _point_lt

@dataclass(frozen=True)
class ReconstructedLoop:
    """
    An immutable, ordered sequence of vertices defining a closed boundary loop.
    The sequence is canonical and deterministic.
    """
    vertices: Tuple[Point3D, ...]

    def __post_init__(self):
        if len(self.vertices) < 3:
            raise BoundaryValidationError("Closed loop must contain at least 3 vertices")

class LoopReconstructor:
    @staticmethod
    def reconstruct_loops(graph: BoundaryGraph) -> Tuple[ReconstructedLoop, ...]:
        """
        Reconstructs explicit closed loops from a validated BoundaryGraph.
        Fails closed if the topology is not supported.
        """
        topology = graph.classify_topology()
        
        if topology == BoundaryTopologyClassification.EMPTY:
            return tuple()
            
        if topology not in (BoundaryTopologyClassification.ONE_CLOSED_LOOP, BoundaryTopologyClassification.MULTIPLE_DISJOINT_LOOPS):
            raise BoundaryValidationError(f"Unsupported boundary topology for loop reconstruction: {topology}")
            
        visited: Set[Point3D] = set()
        loops: List[ReconstructedLoop] = []
        
        # graph.vertices is already deterministically sorted.
        for start_v in graph.vertices:
            if start_v in visited:
                continue
                
            # Follow the loop
            loop_verts: List[Point3D] = [start_v]
            visited.add(start_v)
            
            curr = start_v
            # The start vertex has exactly 2 neighbors (since topology is closed loops).
            # To be deterministic, we pick the lexicographically smallest neighbor as the NEXT vertex.
            # graph.adjacency is already lexicographically sorted.
            neighbors = graph.adjacency[curr]
            # Since graph.adjacency[curr] is sorted, neighbors[0] is the smallest.
            prev = curr
            curr = neighbors[0]
            
            while curr != start_v:
                loop_verts.append(curr)
                visited.add(curr)
                
                curr_neighbors = graph.adjacency[curr]
                # Find the neighbor that is not `prev`
                next_v = curr_neighbors[0] if curr_neighbors[0] != prev else curr_neighbors[1]
                
                prev = curr
                curr = next_v
                
            loops.append(ReconstructedLoop(tuple(loop_verts)))
            
        # Sort multiple loops by their start vertices (which are canonical minimums of each loop)
        # Since we iterated over `graph.vertices` which is sorted, the loops are naturally 
        # appended in the sorted order of their minimum vertices. 
        # But we'll sort explicitly to be absolutely safe.
        loops.sort(key=lambda l: (l.vertices[0].x, l.vertices[0].y, l.vertices[0].z))
        
        return tuple(loops)
