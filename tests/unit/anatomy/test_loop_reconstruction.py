import pytest
from holomed.anatomy.models import Point3D
from holomed.anatomy.freeform_intersection import TriangleIntersectionResult, IntersectionClassification
from holomed.anatomy.boundary_graph import BoundaryGraph, BoundaryValidationError
from holomed.anatomy.loop_reconstruction import LoopReconstructor

def test_reconstruct_single_loop():
    p1 = Point3D(0.0, 0.0, 0.0)
    p2 = Point3D(1.0, 0.0, 0.0)
    p3 = Point3D(0.0, 1.0, 0.0)
    
    results = [
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p1, p2),
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p2, p3),
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p3, p1),
    ]
    
    graph = BoundaryGraph.build(results)
    loops = LoopReconstructor.reconstruct_loops(graph)
    
    assert len(loops) == 1
    assert len(loops[0].vertices) == 3
    # Minimum point should be start
    assert loops[0].vertices[0] == p1
    # Check ordering is deterministic and lexicographical
    assert set(loops[0].vertices) == {p1, p2, p3}

def test_reconstruct_unsupported_topology():
    p1 = Point3D(0.0, 0.0, 0.0)
    p2 = Point3D(1.0, 0.0, 0.0)
    
    results = [
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p1, p2),
    ]
    
    graph = BoundaryGraph.build(results)
    with pytest.raises(BoundaryValidationError, match="Unsupported boundary topology"):
        LoopReconstructor.reconstruct_loops(graph)

def test_reconstruct_multiple_disjoint_loops():
    # Loop 1
    p1 = Point3D(0.0, 0.0, 0.0)
    p2 = Point3D(1.0, 0.0, 0.0)
    p3 = Point3D(0.0, 1.0, 0.0)
    
    # Loop 2
    p4 = Point3D(2.0, 0.0, 0.0)
    p5 = Point3D(3.0, 0.0, 0.0)
    p6 = Point3D(2.0, 1.0, 0.0)
    
    results = [
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p1, p2),
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p2, p3),
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p3, p1),
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p4, p5),
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p5, p6),
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p6, p4),
    ]
    
    graph = BoundaryGraph.build(results)
    loops = LoopReconstructor.reconstruct_loops(graph)
    
    assert len(loops) == 2
    # Ensure they are sorted by minimum vertex (Loop 1 has p1 which is smaller than p4)
    assert loops[0].vertices[0] == p1
    assert loops[1].vertices[0] == p4
    
def test_reversed_shuffled_input():
    p1 = Point3D(0.0, 0.0, 0.0)
    p2 = Point3D(1.0, 0.0, 0.0)
    p3 = Point3D(0.0, 1.0, 0.0)
    
    r1 = TriangleIntersectionResult(IntersectionClassification.SEGMENT, p1, p2)
    r2 = TriangleIntersectionResult(IntersectionClassification.SEGMENT, p2, p3)
    r3 = TriangleIntersectionResult(IntersectionClassification.SEGMENT, p3, p1)
    
    g1 = BoundaryGraph.build([r1, r2, r3])
    l1 = LoopReconstructor.reconstruct_loops(g1)
    
    # Reversed and shuffled
    r1_rev = TriangleIntersectionResult(IntersectionClassification.SEGMENT, p2, p1)
    r2_rev = TriangleIntersectionResult(IntersectionClassification.SEGMENT, p3, p2)
    r3_rev = TriangleIntersectionResult(IntersectionClassification.SEGMENT, p1, p3)
    
    g2 = BoundaryGraph.build([r2_rev, r3_rev, r1_rev])
    l2 = LoopReconstructor.reconstruct_loops(g2)
    
    assert l1[0].vertices == l2[0].vertices
