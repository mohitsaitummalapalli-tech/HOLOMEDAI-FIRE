import pytest
import math
from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.trajectory import CutTrajectory, TrajectorySample, TrajectoryState
from holomed.anatomy.surface_generation import process_trajectory, generate_planar_surface, SurfaceGenerationError
from holomed.anatomy.slicing import SliceEngine
from holomed.anatomy.mesh import AnatomicalMesh

def create_trajectory(points) -> CutTrajectory:
    timestamps = [float(i) for i in range(len(points))]
    samples = []
    for i, p in enumerate(points):
        state = TrajectoryState.ACTIVE
        if i == 0: state = TrajectoryState.START
        elif i == len(points) - 1: state = TrajectoryState.END
        samples.append(TrajectorySample(Point3D(*p), timestamps[i], state))
    return CutTrajectory(
        trajectory_id="test",
        correlation_id="corr",
        samples=tuple(samples)
    )

def test_valid_planar_surface():
    # A valid planar trajectory (a simple circle in XY plane)
    pts = []
    for i in range(100):
        theta = i * 0.05
        pts.append((0.1 * math.cos(theta), 0.1 * math.sin(theta), 0))
    traj = create_trajectory(pts)
    p = process_trajectory(traj)
    surf = generate_planar_surface(p)
    # The normal should be (0, 0, 1) or (0, 0, -1). 
    # Canonical sign rule: nz < 0 flips it. So nz should be > 0.
    assert abs(surf.normal.dz - 1.0) < 1e-4
    assert abs(surf.normal.dx) < 1e-4
    assert abs(surf.normal.dy) < 1e-4

def test_straight_collinear_rejection():
    # B straight/collinear rejection
    pts = [(0, 0, 0), (0.04, 0, 0), (0.08, 0, 0), (0.12, 0, 0), (0.16, 0, 0), (0.2, 0, 0)]
    traj = create_trajectory(pts)
    p = process_trajectory(traj)
    with pytest.raises(SurfaceGenerationError, match="Nearly collinear"):
        generate_planar_surface(p)

def test_nearly_collinear_rejection():
    # N nearly collinear
    pts = [(0, 0, 0), (0.04, 1e-4, 0), (0.08, 0, 0), (0.12, 1e-4, 0), (0.16, 0, 0), (0.2, 0, 0)]
    traj = create_trajectory(pts)
    p = process_trajectory(traj)
    with pytest.raises(SurfaceGenerationError, match="Nearly collinear"):
        generate_planar_surface(p)

def test_diagonal_orientation():
    # C diagonal trajectory
    # D arbitrary orientation
    # A circle in a plane rotated 45 deg around X
    pts = []
    for i in range(100):
        theta = i * 0.05
        y = 0.1 * math.cos(theta)
        z = 0.1 * math.sin(theta)
        y_rot = y * 0.707106 - z * 0.707106
        z_rot = y * 0.707106 + z * 0.707106
        pts.append((0, y_rot, z_rot))
    traj = create_trajectory(pts)
    p = process_trajectory(traj)
    surf = generate_planar_surface(p)
    assert abs(surf.normal.dx) > 0.99 or abs(surf.normal.dy) > 0.5

def test_arbitrary_position():
    # E arbitrary position
    pts = []
    for i in range(100):
        theta = i * 2 * math.pi / 100
        pts.append((1.5 + 0.1 * math.cos(theta), -0.8 + 0.1 * math.sin(theta), 0.2))
    traj = create_trajectory(pts)
    p = process_trajectory(traj)
    surf = generate_planar_surface(p)
    assert abs(surf.origin.x - 1.5) < 1e-2
    assert abs(surf.origin.y + 0.8) < 1e-2
    assert abs(surf.origin.z - 0.2) < 1e-2

def test_noisy_non_planar():
    # O noisy/non-planar
    # A trajectory that goes wildly out of plane
    pts = []
    for i in range(100):
        theta = i * 0.05
        pts.append((0.1 * math.cos(theta), 0.1 * math.sin(theta), 0.1 * math.sin(theta*3)))
    traj = create_trajectory(pts)
    p = process_trajectory(traj)
    with pytest.raises(SurfaceGenerationError, match="Non-planar"):
        generate_planar_surface(p)

def test_surface_determinism():
    # Q deterministic replay
    pts = []
    for i in range(100):
        theta = i * 0.05
        pts.append((0.1 * math.cos(theta), 0.1 * math.sin(theta), 0.02 * math.cos(theta)))
    traj = create_trajectory(pts)
    p1 = process_trajectory(traj)
    p2 = process_trajectory(traj)
    
    surf1 = generate_planar_surface(p1)
    surf2 = generate_planar_surface(p2)
    
    assert surf1.origin.x == surf2.origin.x
    assert surf1.origin.y == surf2.origin.y
    assert surf1.origin.z == surf2.origin.z
    assert surf1.normal.dx == surf2.normal.dx
    assert surf1.normal.dy == surf2.normal.dy
    assert surf1.normal.dz == surf2.normal.dz

def test_different_trajectory():
    # S different trajectory -> different surface
    pts1 = []
    pts2 = []
    for i in range(100):
        theta = i * 0.05
        pts1.append((0.1 * math.cos(theta), 0.1 * math.sin(theta), 0))
        pts2.append((0.1 * math.cos(theta), 0, 0.1 * math.sin(theta)))
    traj1 = create_trajectory(pts1)
    traj2 = create_trajectory(pts2)
    p1 = process_trajectory(traj1)
    p2 = process_trajectory(traj2)
    surf1 = generate_planar_surface(p1)
    surf2 = generate_planar_surface(p2)
    
    # normals should be orthogonal
    dot = surf1.normal.dx * surf2.normal.dx + surf1.normal.dy * surf2.normal.dy + surf1.normal.dz * surf2.normal.dz
    assert abs(dot) < 1e-4

def test_normal_sign_equivalence():
    # T normal sign equivalence
    # Clockwise vs Counter-Clockwise should yield same normal due to canonical sign check
    pts_cw = []
    pts_ccw = []
    for i in range(100):
        theta = i * 0.05
        pts_cw.append((0.1 * math.cos(-theta), 0.1 * math.sin(-theta), 0))
        pts_ccw.append((0.1 * math.cos(theta), 0.1 * math.sin(theta), 0))
    traj_cw = create_trajectory(pts_cw)
    traj_ccw = create_trajectory(pts_ccw)
    
    surf_cw = generate_planar_surface(process_trajectory(traj_cw))
    surf_ccw = generate_planar_surface(process_trajectory(traj_ccw))
    
    assert abs(surf_cw.normal.dx - surf_ccw.normal.dx) < 1e-6
    assert abs(surf_cw.normal.dy - surf_ccw.normal.dy) < 1e-6
    assert abs(surf_cw.normal.dz - surf_ccw.normal.dz) < 1e-6

def test_f2_integration():
    # V F2 integration
    # Create simple box mesh
    pts = []
    for i in range(100):
        theta = i * 0.1
        pts.append((0.1 * math.cos(theta), 0.1 * math.sin(theta), 0))
    traj = create_trajectory(pts)
    p = process_trajectory(traj)
    surf = generate_planar_surface(p)
    
    mesh = AnatomicalMesh(
        vertices=(
            Point3D(-1, -1, -1),
            Point3D(1, -1, -1),
            Point3D(1, 1, -1),
            Point3D(-1, 1, -1),
            Point3D(-1, -1, 1),
            Point3D(1, -1, 1),
            Point3D(1, 1, 1),
            Point3D(-1, 1, 1),
        ),
        indices=(
            0, 1, 2, 0, 2, 3, # front
            1, 5, 6, 1, 6, 2, # right
            5, 4, 7, 5, 7, 6, # back
            4, 0, 3, 4, 3, 7, # left
            3, 2, 6, 3, 6, 7, # top
            4, 5, 1, 4, 1, 0, # bottom
        )
    )
    from holomed.anatomy.mesh import AnatomicalPiece
    from holomed.anatomy.slicing import SliceOperation
    piece = AnatomicalPiece(piece_id="test", mesh=mesh)
    operation = SliceOperation(operation_id="op", cut_surface=surf, geometry_version=1)
    
    result = SliceEngine.slice_piece(piece, operation)
    assert result.parent_piece_id == "test"
    assert len(result.child_pieces) == 2

