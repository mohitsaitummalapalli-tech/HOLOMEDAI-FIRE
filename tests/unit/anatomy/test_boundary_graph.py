import pytest
from holomed.anatomy.models import Point3D
from holomed.anatomy.freeform_intersection import TriangleIntersectionResult, IntersectionClassification, INTERSECTION_TOLERANCE
from holomed.anatomy.boundary_graph import BoundaryGraph, BoundaryTopologyClassification, BoundaryValidationError, BoundarySegment, _weld_points

def test_welding_exact_duplicate():
    p1 = Point3D(1.0, 0.0, 0.0)
    p2 = Point3D(1.0, 0.0, 0.0)
    
    welded = _weld_points([p1, p2])
    assert welded[id(p1)] is welded[id(p2)]
    
def test_welding_near_tolerance():
    p1 = Point3D(1.0, 0.0, 0.0)
    p2 = Point3D(1.0 + (INTERSECTION_TOLERANCE * 0.9), 0.0, 0.0)
    
    welded = _weld_points([p1, p2])
    assert welded[id(p1)] is welded[id(p2)]
    
def test_welding_outside_tolerance():
    p1 = Point3D(1.0, 0.0, 0.0)
    p2 = Point3D(1.00001, 0.0, 0.0)
    
    welded = _weld_points([p1, p2])
    assert welded[id(p1)] is not welded[id(p2)]
    
def test_welding_deterministic_sorting():
    p1 = Point3D(1.0, 2.0, 3.0)
    p2 = Point3D(1.0, 2.0, 3.0 + (INTERSECTION_TOLERANCE * 0.5))
    
    w1 = _weld_points([p1, p2])
    w2 = _weld_points([p2, p1])
    
    # The canonical representative chosen should be exactly the same (the lexicographically smaller one)
    assert w1[id(p1)].z == 3.0
    assert w2[id(p1)].z == 3.0
    
def test_boundary_graph_empty():
    graph = BoundaryGraph.build([])
    assert graph.classify_topology() == BoundaryTopologyClassification.EMPTY
    
def test_boundary_graph_one_closed_loop():
    p1 = Point3D(0.0, 0.0, 0.0)
    p2 = Point3D(1.0, 0.0, 0.0)
    p3 = Point3D(0.0, 1.0, 0.0)
    
    results = [
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p1, p2),
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p2, p3),
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p3, p1),
    ]
    
    graph = BoundaryGraph.build(results)
    assert graph.classify_topology() == BoundaryTopologyClassification.ONE_CLOSED_LOOP
    assert len(graph.vertices) == 3
    assert len(graph.segments) == 3
    
def test_boundary_graph_open_chain_rejection():
    p1 = Point3D(0.0, 0.0, 0.0)
    p2 = Point3D(1.0, 0.0, 0.0)
    p3 = Point3D(0.0, 1.0, 0.0)
    
    results = [
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p1, p2),
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p2, p3),
        # Missing p3 -> p1
    ]
    
    graph = BoundaryGraph.build(results)
    assert graph.classify_topology() == BoundaryTopologyClassification.OPEN_CHAIN
    
def test_boundary_graph_branched():
    p1 = Point3D(0.0, 0.0, 0.0)
    p2 = Point3D(1.0, 0.0, 0.0)
    p3 = Point3D(0.0, 1.0, 0.0)
    p4 = Point3D(0.0, -1.0, 0.0)
    
    results = [
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p1, p2),
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p2, p3),
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p3, p1),
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p1, p4), # Branch at p1
    ]
    
    graph = BoundaryGraph.build(results)
    assert graph.classify_topology() == BoundaryTopologyClassification.BRANCHED

def test_boundary_graph_duplicate_segment():
    p1 = Point3D(0.0, 0.0, 0.0)
    p2 = Point3D(1.0, 0.0, 0.0)
    
    results = [
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p1, p2),
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p2, p1), # Exact duplicate backwards
    ]
    
    with pytest.raises(BoundaryValidationError, match="Duplicate boundary segment"):
        BoundaryGraph.build(results)

def test_zero_length_edge_rejected():
    p1 = Point3D(0.0, 0.0, 0.0)
    p2 = Point3D(0.0, 0.0, INTERSECTION_TOLERANCE * 0.5)
    
    results = [
        TriangleIntersectionResult(IntersectionClassification.SEGMENT, p1, p2)
    ]
    
    with pytest.raises(BoundaryValidationError, match="Zero-length boundary edge"):
        BoundaryGraph.build(results)
