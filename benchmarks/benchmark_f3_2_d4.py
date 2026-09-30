import time
import math
import sys
import platform
import statistics
from dataclasses import dataclass
from typing import Tuple, List

from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.mesh import AnatomicalMesh
from holomed.anatomy.swept_surface_generator import SweptCutMesh, generate_swept_surface, generate_swept_mesh, TransportedFrame
from holomed.anatomy.trajectory import ProcessedCutTrajectory, TrajectorySample, TrajectoryState
from holomed.anatomy.freeform_capping import (
    compute_intersections_with_provenance,
    get_segment_views,
    extract_cutter_patch
)

def create_cylinder_mesh(radius: float, height: float, radial_segments: int, height_segments: int) -> AnatomicalMesh:
    vertices = []
    
    for h in range(height_segments + 1):
        z = -height/2 + h * (height / height_segments)
        for r in range(radial_segments):
            angle = 2 * math.pi * r / radial_segments
            x = radius * math.cos(angle)
            y = radius * math.sin(angle)
            vertices.append(Point3D(x, y, z))
            
    # Add top and bottom center vertices
    bottom_center = Point3D(0, 0, -height/2)
    top_center = Point3D(0, 0, height/2)
    bottom_idx = len(vertices)
    vertices.append(bottom_center)
    top_idx = len(vertices)
    vertices.append(top_center)
    
    indices = []
    
    # Side quads
    for h in range(height_segments):
        for r in range(radial_segments):
            v0 = h * radial_segments + r
            v1 = h * radial_segments + (r + 1) % radial_segments
            v2 = (h + 1) * radial_segments + (r + 1) % radial_segments
            v3 = (h + 1) * radial_segments + r
            
            indices.extend([v0, v1, v2])
            indices.extend([v0, v2, v3])
            
    # Bottom caps
    for r in range(radial_segments):
        v1 = r
        v2 = (r + 1) % radial_segments
        indices.extend([bottom_idx, v2, v1])
        
    # Top caps
    for r in range(radial_segments):
        v1 = height_segments * radial_segments + r
        v2 = height_segments * radial_segments + (r + 1) % radial_segments
        indices.extend([top_idx, v1, v2])
        
    return AnatomicalMesh(tuple(vertices), tuple(indices))

def create_straight_cutter(width: float, length: float, length_segments: int, u_samples: int) -> SweptCutMesh:
    samples = []
    frames = []
    for i in range(length_segments):
        t = i / (length_segments - 1)
        x = -length/2 + t * length
        state = TrajectoryState.START if i == 0 else (TrajectoryState.END if i == length_segments - 1 else TrajectoryState.ACTIVE)
        samples.append(TrajectorySample(Point3D(x, 0, 0.123), float(i), state))
        frames.append(TransportedFrame(Vector3D(1, 0, 0), Vector3D(0, 0, 1), Vector3D(0, -1, 0)))
        
    traj = ProcessedCutTrajectory("test_traj", "correl", tuple(samples), tuple([Vector3D(1,0,0)]*length_segments))
    surf = generate_swept_surface(traj, tuple(frames), width, 1.0)
    return generate_swept_mesh(surf, u_samples=u_samples)

def run_benchmark():
    print("F3.2-D4.0 Benchmark")
    print(f"Python version: {platform.python_version()}")
    print(f"OS/Environment: {platform.system()} {platform.release()}")
    print("-" * 60)
    
    configs = [
        ("Small", 20, 5, 5, 3),      # Target tris ~200, Cutter tris ~16
        ("Medium", 40, 10, 10, 5),   # Target tris ~800, Cutter tris ~72
        ("Large", 80, 20, 20, 9)     # Target tris ~3200, Cutter tris ~304
    ]
    
    warmup = 10
    reps = 100
    
    for name, radial, height_seg, c_len_seg, c_u_seg in configs:
        target = create_cylinder_mesh(4.0, 8.0, radial, height_seg)
        cutter = create_straight_cutter(12.0, 12.0, c_len_seg, c_u_seg)
        
        target_tris = len(target.indices) // 3
        cutter_tris = len(cutter.indices) // 3
        print(f"\\nMesh Size: {name} (Target Tris={target_tris}, Cutter Tris={cutter_tris})")
        
        t_intersection = []
        t_provenance = []
        t_partition = []
        t_total = []
        
        for i in range(warmup + reps):
            t0 = time.perf_counter()
            
            # Intersection
            prov = compute_intersections_with_provenance(target, cutter)
            t1 = time.perf_counter()
            
            # Provenance construction
            t_view, c_view = get_segment_views(prov)
            t2 = time.perf_counter()
            
            # Graph partition
            patch = extract_cutter_patch(cutter, c_view)
            t3 = time.perf_counter()
            
            if i >= warmup:
                t_intersection.append((t1 - t0) * 1000)
                t_provenance.append((t2 - t1) * 1000)
                t_partition.append((t3 - t2) * 1000)
                t_total.append((t3 - t0) * 1000)
                
        for metric_name, data in [
            ("Intersection Stage", t_intersection),
            ("Provenance Construction", t_provenance),
            ("Cutter Graph Partition", t_partition),
            ("Total D4.0 Time", t_total)
        ]:
            med = statistics.median(data)
            p95 = sorted(data)[int(0.95 * len(data))]
            print(f"{metric_name:.<30} | Median: {med:.3f} ms | P95: {p95:.3f} ms")

if __name__ == "__main__":
    run_benchmark()
