import time
import math
import statistics
from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.mesh import AnatomicalMesh, AnatomicalPiece
from holomed.anatomy.trajectory import ProcessedCutTrajectory, TrajectorySample, TrajectoryState
from holomed.anatomy.swept_surface_generator import generate_swept_surface, generate_swept_mesh
from holomed.anatomy.frame_transport import generate_transport_frames
from holomed.anatomy.slicing import SliceEngine, FreeformSliceStrategy, SliceOperation

def create_target_mesh():
    vertices = (
        Point3D(-1, -1, -1), Point3D(1, -1, -1), Point3D(1, 1, -1), Point3D(-1, 1, -1),
        Point3D(-1, -1, 1), Point3D(1, -1, 1), Point3D(1, 1, 1), Point3D(-1, 1, 1)
    )
    indices = (
        0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6, 0, 4, 5, 0, 5, 1,
        1, 5, 6, 1, 6, 2, 2, 6, 7, 2, 7, 3, 3, 7, 4, 3, 4, 0
    )
    return AnatomicalMesh(vertices, indices)

def create_l_shape_trajectory():
    samples = [
        TrajectorySample(Point3D(-4.0, 0.0, 0.123), 0.0, TrajectoryState.START),
        TrajectorySample(Point3D(0.0, 0.0, -0.123), 1.0, TrajectoryState.ACTIVE),
        TrajectorySample(Point3D(4.0, 0.0, 0.123), 2.0, TrajectoryState.END),
    ]
    tangents = [Vector3D(1, 0, 0)] * 3
    return ProcessedCutTrajectory(
        trajectory_id="traj_l", correlation_id="corr_l",
        samples=tuple(samples), tangents=tuple(tangents),
        sampling_metadata={}, coordinate_space="canonical_anatomical"
    )

def run_benchmark():
    target = create_target_mesh()
    traj = create_l_shape_trajectory()
    engine = SliceEngine()
    
    # Custom frames for L-surface
    from holomed.anatomy.frame_transport import TransportedFrame
    frames = tuple([TransportedFrame(Vector3D(1, 0, 0), Vector3D(0, 0, 1), Vector3D(0, -1, 0))] * 3)
    
    surf = generate_swept_surface(traj, frames, width=8.0, thickness=1.0)
    mesh = generate_swept_mesh(surf)
    piece = AnatomicalPiece("p1", target, None)
    op = SliceOperation("op1", surf, 1)
    
    cutter_times = []
    freeform_total_times = []
    engine_times = []
    WARMUP = 10
    REPS = 100
    
    for i in range(WARMUP + REPS):
        t0 = time.perf_counter()
        surf_run = generate_swept_surface(traj, frames, width=8.0, thickness=1.0)
        mesh_run = generate_swept_mesh(surf_run)
        t1 = time.perf_counter()
        
        t2 = time.perf_counter()
        strat = FreeformSliceStrategy()
        res = strat.slice(piece, op)
        t3 = time.perf_counter()
        
        t4 = time.perf_counter()
        res_total = engine.slice_piece(piece, op)
        t5 = time.perf_counter()
        
        if i >= WARMUP:
            cutter_times.append((t1 - t0) * 1000.0)
            freeform_total_times.append((t3 - t2) * 1000.0)
            engine_times.append((t5 - t4) * 1000.0)
            
    def p95(arr):
        return sorted(arr)[int(len(arr)*0.95)]
        
    print("=== D5.1 BENCHMARK RESULTS ===")
    print(f"Target Mesh Size: {len(target.vertices)} vertices, {len(target.indices)//3} triangles")
    print(f"Cutter Mesh Size: {len(mesh.vertices)} vertices, {len(mesh.indices)//3} triangles")
    print("")
    print(f"Cutter Generation     : Median {statistics.median(cutter_times):.3f} ms, P95 {p95(cutter_times):.3f} ms")
    print(f"Total Freeform (Strat): Median {statistics.median(freeform_total_times):.3f} ms, P95 {p95(freeform_total_times):.3f} ms")
    print(f"Total SliceEngine     : Median {statistics.median(engine_times):.3f} ms, P95 {p95(engine_times):.3f} ms")

if __name__ == '__main__':
    run_benchmark()
