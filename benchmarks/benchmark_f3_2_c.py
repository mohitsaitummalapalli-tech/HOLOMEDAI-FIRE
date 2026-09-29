# -*- coding: utf-8 -*-
"""Phase F3.2-C Swept Surface Generator Benchmark."""

import time
import statistics
import platform
import math

import time
import statistics
import platform
import math

from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.trajectory import ProcessedCutTrajectory, TrajectorySample, TrajectoryState
from holomed.anatomy.frame_transport import generate_transport_frames
from holomed.anatomy.swept_surface_generator import generate_swept_surface, generate_swept_mesh

def create_circular_trajectory(num_samples: int) -> ProcessedCutTrajectory:
    samples = []
    radius = 0.05
    for i in range(num_samples):
        t = (i / max(1, num_samples - 1)) * 2 * math.pi
        p = Point3D(radius * math.cos(t), radius * math.sin(t), i * 0.001)
        state = TrajectoryState.START if i == 0 else (TrajectoryState.END if i == num_samples-1 else TrajectoryState.ACTIVE)
        samples.append(TrajectorySample(
            position=p,
            timestamp=i * 0.01,
            state=state
        ))
    
    tangents = []
    for i in range(num_samples):
        if i < num_samples - 1:
            dx = samples[i+1].position.x - samples[i].position.x
            dy = samples[i+1].position.y - samples[i].position.y
            dz = samples[i+1].position.z - samples[i].position.z
            mag = math.sqrt(dx*dx + dy*dy + dz*dz)
            tangents.append(Vector3D(dx/mag, dy/mag, dz/mag))
        else:
            tangents.append(tangents[-1])

    return ProcessedCutTrajectory(
        trajectory_id="bench",
        correlation_id="bench",
        samples=tuple(samples),
        tangents=tuple(tangents),
        sampling_metadata={},
        coordinate_space="canonical_anatomical"
    )

def run_benchmark(num_samples: int, warmups: int = 10, reps: int = 100):
    traj = create_circular_trajectory(num_samples)
    frames = generate_transport_frames(traj)
    
    # Warmup
    for _ in range(warmups):
        surf = generate_swept_surface(traj, frames, 0.01, 0.005)
        mesh = generate_swept_mesh(surf, u_samples=3)
        
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        surf = generate_swept_surface(traj, frames, 0.01, 0.005)
        mesh = generate_swept_mesh(surf, u_samples=3)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000.0) # ms
        
    median = statistics.median(times)
    p95 = statistics.quantiles(times, n=20)[18]
    
    print(f"--- Benchmark: {num_samples} samples ---")
    print(f"Median: {median:.3f} ms")
    print(f"P95:    {p95:.3f} ms")
    print()

def main():
    print(f"Environment: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Python: {platform.python_version()}")
    print()
    
    run_benchmark(100)
    run_benchmark(500)
    run_benchmark(1000)

if __name__ == "__main__":
    main()
