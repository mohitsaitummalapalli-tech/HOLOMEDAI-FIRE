# -*- coding: utf-8 -*-
"""Phase F3.2-B Benchmark: Deterministic Bishop Frame Transport."""

import time
import math
import statistics
import platform
import sys
from typing import List, Tuple

from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.trajectory import ProcessedCutTrajectory, TrajectorySample, TrajectoryState
from holomed.anatomy.frame_transport import generate_transport_frames

def _normalize_vec(dx: float, dy: float, dz: float) -> Vector3D:
    mag = math.sqrt(dx*dx + dy*dy + dz*dz)
    return Vector3D(dx/mag, dy/mag, dz/mag)

def generate_3d_curve_trajectory(num_samples: int) -> ProcessedCutTrajectory:
    samples = []
    tangents = []
    for i in range(num_samples):
        t = i * 0.1
        # Position is not strictly used by transport but required by contract
        pt = Point3D(math.cos(t), math.sin(t), t * 0.1)
        samples.append(TrajectorySample(position=pt, timestamp=t, state=TrajectoryState.ACTIVE))
        
        # Derivative for tangent: (-sin(t), cos(t), 0.1)
        tangents.append(_normalize_vec(-math.sin(t), math.cos(t), 0.1))
        
    return ProcessedCutTrajectory(
        trajectory_id="bench_t",
        correlation_id="bench_c",
        samples=tuple(samples),
        tangents=tuple(tangents)
    )

def run_benchmark(num_samples: int, iterations: int = 100, warmup: int = 10):
    pct = generate_3d_curve_trajectory(num_samples)
    
    # Warmup
    for _ in range(warmup):
        _ = generate_transport_frames(pct)
        
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        _ = generate_transport_frames(pct)
        end = time.perf_counter()
        times.append((end - start) * 1000.0) # ms
        
    times.sort()
    median = statistics.median(times)
    p95 = times[int(0.95 * len(times))]
    
    print(f"| {num_samples:<12} | {median:<10.3f} | {p95:<10.3f} | {iterations:<10} |")

def main():
    print(f"Environment: Python {platform.python_version()} on {platform.system()} {platform.release()}")
    print("-" * 60)
    print(f"| {'Samples':<12} | {'Median (ms)':<10} | {'P95 (ms)':<10} | {'Iterations':<10} |")
    print("-" * 60)
    
    run_benchmark(100)
    run_benchmark(500)
    run_benchmark(1000)
    print("-" * 60)

if __name__ == "__main__":
    main()
