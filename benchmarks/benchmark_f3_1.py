import time
import math
from statistics import median
from holomed.anatomy.models import Point3D
from holomed.anatomy.trajectory import CutTrajectory, TrajectorySample, TrajectoryState, validate_trajectory
from holomed.anatomy.surface_generation import resample_trajectory, smooth_trajectory, generate_planar_surface, process_trajectory

def create_synthetic_trajectory(num_points: int) -> CutTrajectory:
    samples = []
    for i in range(num_points):
        theta = i * 0.01
        p = Point3D(0.1 * math.cos(theta), 0.1 * math.sin(theta), 0)
        state = TrajectoryState.ACTIVE
        if i == 0: state = TrajectoryState.START
        elif i == num_points - 1: state = TrajectoryState.END
        samples.append(TrajectorySample(p, float(i), state))
    from types import MappingProxyType
    return CutTrajectory(
        trajectory_id="bench",
        correlation_id="bench",
        samples=tuple(samples),
        sampling_metadata=MappingProxyType({})
    )

def benchmark_function(func, *args, iterations=1000):
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        func(*args)
        t1 = time.perf_counter()
        times.append(t1 - t0)
    times.sort()
    return min(times), median(times), times[int(0.95 * len(times))], sum(times)

def run_benchmarks():
    print("F3.1 PERFORMANCE BENCHMARK")
    print("==========================")
    
    # Representative synthetic trajectory: 500 samples
    num_samples = 500
    print(f"Sample count: {num_samples}")
    traj = create_synthetic_trajectory(num_samples)
    
    val_min, val_med, val_p95, val_tot = benchmark_function(validate_trajectory, traj, iterations=100)
    print(f"A. Validation  : min={val_min*1000:.3f}ms, median={val_med*1000:.3f}ms, P95={val_p95*1000:.3f}ms, total(100x)={val_tot*1000:.1f}ms")
    
    validated = validate_trajectory(traj)
    res_min, res_med, res_p95, res_tot = benchmark_function(resample_trajectory, validated, iterations=100)
    print(f"B. Resampling  : min={res_min*1000:.3f}ms, median={res_med*1000:.3f}ms, P95={res_p95*1000:.3f}ms, total(100x)={res_tot*1000:.1f}ms")
    
    resampled = resample_trajectory(validated)
    smt_min, smt_med, smt_p95, smt_tot = benchmark_function(smooth_trajectory, resampled, iterations=100)
    print(f"C. Smoothing   : min={smt_min*1000:.3f}ms, median={smt_med*1000:.3f}ms, P95={smt_p95*1000:.3f}ms, total(100x)={smt_tot*1000:.1f}ms")
    
    processed = process_trajectory(traj)
    surf_min, surf_med, surf_p95, surf_tot = benchmark_function(generate_planar_surface, processed, iterations=100)
    print(f"D. PCA Surface : min={surf_min*1000:.3f}ms, median={surf_med*1000:.3f}ms, P95={surf_p95*1000:.3f}ms, total(100x)={surf_tot*1000:.1f}ms")
    
    full_min, full_med, full_p95, full_tot = benchmark_function(
        lambda t: generate_planar_surface(process_trajectory(t)), traj, iterations=100
    )
    print(f"E. Full F3.1   : min={full_min*1000:.3f}ms, median={full_med*1000:.3f}ms, P95={full_p95*1000:.3f}ms, total(100x)={full_tot*1000:.1f}ms")

if __name__ == "__main__":
    run_benchmarks()
