import time
import statistics
import sys
import os
import platform

from holomed.anatomy.models import Point3D
from holomed.anatomy.freeform_intersection import intersect_triangles, TriangleIntersectionResult

def run_benchmark():
    # Deterministic test cases
    # 1. Non-coplanar ordinary
    t1_ord = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0))
    t2_ord = (Point3D(1, -1, -1), Point3D(1, 3, -1), Point3D(1, 1, 1))

    # 2. Near-degenerate (tiny area)
    t1_nd = (Point3D(0, 0, 0), Point3D(1, 0, 0), Point3D(1, 1e-7, 0))
    t2_nd = (Point3D(0.5, -1, -1), Point3D(0.5, 1, -1), Point3D(0.5, 0, 1))
    
    # 3. Boundary/adversarial (exact edge intersection)
    t1_adv = (Point3D(0, 0, 0), Point3D(2, 0, 0), Point3D(0, 2, 0))
    t2_adv = (Point3D(1, 0, 0), Point3D(3, 0, 0), Point3D(2, 0, 1))

    # Warmup
    for _ in range(10):
        intersect_triangles(t1_ord, t2_ord)
        try: intersect_triangles(t1_nd, t2_nd)
        except Exception: pass
        intersect_triangles(t1_adv, t2_adv)

    # Benchmark
    reps = 100
    times = []
    
    for _ in range(reps):
        start = time.perf_counter_ns()
        
        intersect_triangles(t1_ord, t2_ord)
        try: intersect_triangles(t1_nd, t2_nd)
        except Exception: pass
        intersect_triangles(t1_adv, t2_adv)
        
        end = time.perf_counter_ns()
        times.append(end - start)

    times.sort()
    median_ns = statistics.median(times)
    p95_ns = times[int(0.95 * reps)]

    print(f"--- F3.2-D2 Benchmark ---")
    print(f"OS/Environment: {platform.system()} {platform.release()} ({platform.architecture()[0]})")
    print(f"Python Version: {sys.version.split()[0]}")
    print(f"Command: {' '.join(sys.argv)}")
    print(f"Repetitions: {reps} (after 10 warmups)")
    print(f"Cases per rep: 1 ordinary, 1 near-degenerate, 1 adversarial")
    print(f"Median Time (per rep): {median_ns / 1_000_000:.4f} ms")
    print(f"P95 Time (per rep): {p95_ns / 1_000_000:.4f} ms")

if __name__ == "__main__":
    run_benchmark()
