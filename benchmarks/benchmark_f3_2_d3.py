import time
import platform
import statistics
import math
from typing import List, Callable, Any
from holomed.anatomy.models import Point3D
from holomed.anatomy.mesh import AnatomicalMesh
from holomed.anatomy.freeform_intersection import TriangleIntersectionResult, IntersectionClassification
from holomed.anatomy.boundary_graph import BoundaryGraph, _weld_points, BoundarySegment
from holomed.anatomy.loop_reconstruction import LoopReconstructor
from holomed.anatomy.mesh_partition import TargetMeshPartitioner

def run_benchmark(name: str, func: Callable[[], Any], warmups: int = 10, reps: int = 100):
    for _ in range(warmups):
        func()
        
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        func()
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000.0) # in ms
        
    times.sort()
    median = statistics.median(times)
    p95 = times[int(len(times) * 0.95)]
    print(f"{name:40s} | Median: {median:.3f} ms | P95: {p95:.3f} ms")

def create_synthetic_cut(size: int):
    # Create a cylindrical grid mesh
    # x goes around the cylinder, y goes along height
    vertices = []
    indices = []
    
    # 3 layers of vertices (y=0, y=1, y=2)
    # Cylinder radius = 5.0 (fits in MAX_ANATOMY_COORDINATE_METERS)
    r = 5.0
    for y in range(3):
        for x in range(size):
            ang = 2.0 * math.pi * x / size
            vx = r * math.cos(ang)
            vy = r * math.sin(ang)
            vz = y * 2.0
            vertices.append(Point3D(vx, vy, vz))
            
    for y in range(2):
        for x in range(size):
            nx = (x + 1) % size
            i0 = y * size + x
            i1 = y * size + nx
            i2 = (y + 1) * size + x
            i3 = (y + 1) * size + nx
            
            # T0
            indices.extend([i0, i1, i2])
            # T1
            indices.extend([i1, i3, i2])
            
    mesh = AnatomicalMesh(tuple(vertices), tuple(indices))
    
    raw_results = []
    triangle_segments = {}
    
    # Cut horizontally at vz = 1.0
    # Crosses all T0 (i0-i1-i2) and T1 (i1-i3-i2) at y=0.
    for x in range(size):
        nx = (x + 1) % size
        ang0 = 2.0 * math.pi * x / size
        ang1 = 2.0 * math.pi * nx / size
        
        p0 = Point3D(r * math.cos(ang0), r * math.sin(ang0), 1.0)
        p1 = Point3D(r * math.cos(ang1), r * math.sin(ang1), 1.0)
        
        # Calculate the exact midpoint where it crosses the diagonal
        p_mid = Point3D((p0.x+p1.x)/2.0, (p0.y+p1.y)/2.0, 1.0)
        
        raw_results.append(TriangleIntersectionResult(IntersectionClassification.SEGMENT, p0, p_mid))
        raw_results.append(TriangleIntersectionResult(IntersectionClassification.SEGMENT, p_mid, p1))
        
        t0_idx = x * 2
        t1_idx = x * 2 + 1
        
        triangle_segments[t0_idx] = [BoundarySegment(p0, p_mid)]
        triangle_segments[t1_idx] = [BoundarySegment(p_mid, p1)]
        
    return mesh, raw_results, triangle_segments

def main():
    print(f"F3.2-D3 Benchmark")
    print(f"Python version: {platform.python_version()}")
    print(f"OS/Environment: {platform.system()} {platform.release()}")
    print("-" * 60)
    
    sizes = [("Small", 10), ("Medium", 50), ("Large", 100)]
    
    for name, grid_size in sizes:
        mesh, raw_results, triangle_segments = create_synthetic_cut(grid_size)
        tris = len(mesh.indices) // 3
        segs = len(raw_results)
        print(f"\\nMesh Size: {name} (tris={tris}, segments={segs})")
        
        raw_pts = []
        for r in raw_results:
            raw_pts.extend([r.start, r.end])
            
        run_benchmark("Point Welding", lambda: _weld_points(raw_pts))
        
        def build_graph():
            return BoundaryGraph.build(raw_results)
            
        run_benchmark("Graph Construction", build_graph)
        graph = build_graph()
        
        def build_loop():
            return LoopReconstructor.reconstruct_loops(graph)
            
        run_benchmark("Loop Reconstruction", build_loop)
        
        def partition_mesh():
            return TargetMeshPartitioner.partition(mesh, "base", "op", triangle_segments)
                
        run_benchmark("Triangle Partitioning", partition_mesh)

if __name__ == "__main__":
    main()
