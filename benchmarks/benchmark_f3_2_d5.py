# -*- coding: utf-8 -*-
"""Phase F3.2-D5 End-to-End Freeform SliceEngine Benchmark."""

import sys
import time
import platform
import statistics

from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.mesh import AnatomicalMesh, AnatomicalPiece
from holomed.anatomy.swept_surface import SweptCutSurface, TransportedFrame
from holomed.anatomy.swept_surface_generator import generate_swept_surface, generate_swept_mesh
from holomed.anatomy.trajectory import ProcessedCutTrajectory, TrajectorySample, TrajectoryState
from holomed.anatomy.slicing import SliceEngine, SliceOperation
from holomed.anatomy.freeform_capping import (
    compute_intersections_with_provenance,
    get_segment_views,
    extract_cutter_patch,
    cap_partitioned_mesh,
)
from holomed.anatomy.mesh_partition import TargetMeshPartitioner


def create_box_mesh(size: float = 2.0) -> AnatomicalMesh:
    v = [
        Point3D(-size, -size, -size), Point3D(size, -size, -size),
        Point3D(size, size, -size), Point3D(-size, size, -size),
        Point3D(-size, -size, size), Point3D(size, -size, size),
        Point3D(size, size, size), Point3D(-size, size, size),
    ]
    indices = [
        0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7,
        0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5,
        2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7,
    ]
    return AnatomicalMesh(tuple(v), tuple(indices))


def create_surface(width: float = 8.0, length: float = 8.0) -> SweptCutSurface:
    samples = [
        TrajectorySample(Point3D(-length / 2, 0, 0), 0.0, TrajectoryState.START),
        TrajectorySample(Point3D(0, 0, 0), 1.0, TrajectoryState.ACTIVE),
        TrajectorySample(Point3D(length / 2, 0, 0), 2.0, TrajectoryState.END),
    ]
    traj = ProcessedCutTrajectory("bench_traj", "corr", tuple(samples), tuple([Vector3D(1, 0, 0)] * 3))
    frames = tuple([TransportedFrame(Vector3D(1, 0, 0), Vector3D(0, 0, 1), Vector3D(0, -1, 0))] * 3)
    return generate_swept_surface(traj, frames, width, 1.0)


def benchmark():
    WARMUPS = 10
    REPS = 100

    mesh = create_box_mesh(size=2.0)
    piece = AnatomicalPiece(piece_id="box", mesh=mesh)
    surface = create_surface()

    t_cutter_times = []
    t_d2_times = []
    t_d3_times = []
    t_d4_patch_times = []
    t_d4_graft_times = []
    t_total_times = []

    for i in range(WARMUPS + REPS):
        t0 = time.perf_counter()

        # 1. Cutter generation
        cutter_mesh = generate_swept_mesh(surface)
        t1 = time.perf_counter()

        # 2. D2 intersection
        intersections = compute_intersections_with_provenance(mesh, cutter_mesh)
        t2 = time.perf_counter()

        target_view, cutter_view = get_segment_views(intersections)

        # 3. D3 partition
        partition_result = TargetMeshPartitioner.partition(mesh, "box", f"op_{i}", target_view)
        t3 = time.perf_counter()

        # 4. D4 patch extraction
        cutter_patch = extract_cutter_patch(cutter_mesh, cutter_view)
        t4 = time.perf_counter()

        # 5. D4 grafting
        child_A = cap_partitioned_mesh(partition_result.child_pieces[0], cutter_patch)
        child_B = cap_partitioned_mesh(partition_result.child_pieces[1], cutter_patch)
        t5 = time.perf_counter()

        if i >= WARMUPS:
            t_cutter_times.append((t1 - t0) * 1000)
            t_d2_times.append((t2 - t1) * 1000)
            t_d3_times.append((t3 - t2) * 1000)
            t_d4_patch_times.append((t4 - t3) * 1000)
            t_d4_graft_times.append((t5 - t4) * 1000)
            t_total_times.append((t5 - t0) * 1000)

    # Also measure the full SliceEngine path
    t_engine_times = []
    for i in range(WARMUPS + REPS):
        op = SliceOperation(operation_id=f"bench_{i}", cut_surface=surface, geometry_version=1)
        t0 = time.perf_counter()
        result = SliceEngine.slice_piece(piece, op)
        t1 = time.perf_counter()
        if i >= WARMUPS:
            t_engine_times.append((t1 - t0) * 1000)

    num_target_tris = len(mesh.indices) // 3
    cutter = generate_swept_mesh(surface)
    num_cutter_tris = len(cutter.indices) // 3

    print(f"F3.2-D5 End-to-End Benchmark")
    print(f"Python version: {sys.version.split()[0]}")
    print(f"OS/Environment: {platform.system()} {platform.release()}")
    print(f"Target Tris: {num_target_tris}, Cutter Tris: {num_cutter_tris}")
    print(f"Warmups: {WARMUPS}, Repetitions: {REPS}")
    print("-" * 60)

    def report(name: str, times: list[float]):
        med = statistics.median(times)
        p95 = sorted(times)[int(len(times) * 0.95)]
        print(f"{name:<30s} | Median: {med:>10.3f} ms | P95: {p95:>10.3f} ms")

    print("\nComponent Breakdown:")
    report("Cutter Generation", t_cutter_times)
    report("D2 Intersections", t_d2_times)
    report("D3 Partition", t_d3_times)
    report("D4 Patch Extraction", t_d4_patch_times)
    report("D4 Grafting", t_d4_graft_times)
    report("Total (components)", t_total_times)

    print("\nSliceEngine E2E:")
    report("SliceEngine.slice_piece", t_engine_times)


if __name__ == "__main__":
    benchmark()
