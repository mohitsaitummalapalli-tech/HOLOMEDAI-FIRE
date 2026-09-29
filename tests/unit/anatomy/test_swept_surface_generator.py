# -*- coding: utf-8 -*-
"""Phase F3.2-C Swept Surface Generator Tests."""

import math
import pytest
from dataclasses import replace
import math
import pytest
from dataclasses import replace
from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.trajectory import ProcessedCutTrajectory, CutTrajectory, TrajectorySample, TrajectoryState
from holomed.anatomy.swept_surface import SweptCutSurface, TransportedFrame
from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.swept_surface_generator import generate_swept_surface, generate_swept_mesh, SweptCutMesh
from holomed.anatomy.frame_transport import generate_transport_frames

def create_trajectory(points, surface_id="test_id") -> ProcessedCutTrajectory:
    samples = []
    for i, p in enumerate(points):
        state = TrajectoryState.START if i == 0 else (TrajectoryState.END if i == len(points)-1 else TrajectoryState.ACTIVE)
        samples.append(TrajectorySample(
            position=p,
            timestamp=i * 0.01,
            state=state
        ))
        
    tangents = []
    for i in range(len(points)):
        if i < len(points) - 1:
            dx = points[i+1].x - points[i].x
            dy = points[i+1].y - points[i].y
            dz = points[i+1].z - points[i].z
            mag = math.sqrt(dx*dx + dy*dy + dz*dz)
            if mag > 0:
                tangents.append(Vector3D(dx/mag, dy/mag, dz/mag))
            else:
                tangents.append(Vector3D(1,0,0))
        else:
            tangents.append(tangents[-1])

    return ProcessedCutTrajectory(
        trajectory_id=surface_id,
        correlation_id=surface_id,
        samples=tuple(samples),
        tangents=tuple(tangents),
        sampling_metadata={"type": "test"},
        coordinate_space="canonical_anatomical"
    )

def test_surface_generator_valid_straight():
    # Straight trajectory along +Z
    points = [Point3D(0, 0, 0), Point3D(0, 0, 1), Point3D(0, 0, 2), Point3D(0, 0, 3)]
    traj = create_trajectory(points)
    frames = generate_transport_frames(traj)
    
    surf = generate_swept_surface(traj, frames, width=0.01, thickness=0.005)
    assert isinstance(surf, SweptCutSurface)
    assert surf.width == 0.01
    assert surf.thickness == 0.005
    assert surf.total_arc_length == 3.0
    
    mesh = generate_swept_mesh(surf, u_samples=3)
    # 4 points * 3 u_samples = 12 vertices
    assert len(mesh.vertices) == 12
    # 3 longitudinal segments * 2 cross-section segments * 2 triangles = 12 triangles = 36 indices
    assert len(mesh.indices) == 36

def test_surface_generator_width_thickness_validation():
    pts = [Point3D(0, 0, 0), Point3D(0, 0, 1), Point3D(0, 0, 2)]
    traj = create_trajectory(pts)
    frames = generate_transport_frames(traj)

    with pytest.raises(AnatomyValidationError, match="strictly positive"):
        generate_swept_surface(traj, frames, width=0.0, thickness=0.005)
    
    with pytest.raises(AnatomyValidationError, match="strictly positive"):
        generate_swept_surface(traj, frames, width=-1.0, thickness=0.005)

    with pytest.raises(AnatomyValidationError, match="strictly positive"):
        generate_swept_surface(traj, frames, width=0.01, thickness=0.0)

    with pytest.raises(AnatomyValidationError, match="strictly positive"):
        generate_swept_surface(traj, frames, width=0.01, thickness=-1.0)
        
    with pytest.raises(AnatomyValidationError, match="finite"):
        generate_swept_surface(traj, frames, width=float('inf'), thickness=0.005)
        
    with pytest.raises(AnatomyValidationError, match="finite"):
        generate_swept_surface(traj, frames, width=0.01, thickness=float('nan'))

def test_invalid_frames():
    pts = [Point3D(0, 0, 0), Point3D(0, 0, 1), Point3D(0, 0, 2)]
    traj = create_trajectory(pts)
    frames = list(generate_transport_frames(traj))
    
    # Missing frame
    with pytest.raises(AnatomyValidationError, match="match exactly|exactly match"):
        generate_swept_surface(traj, tuple(frames[:2]), width=0.01, thickness=0.01)
        
    # Non-finite vector test - Vector3D blocks nan natively, so we use a mock to bypass it and test generator's defensive check
    from unittest.mock import MagicMock
    bad_vec = MagicMock(spec=Vector3D)
    bad_vec.dx = float('nan')
    bad_vec.dy = 0.0
    bad_vec.dz = 0.0
    bad_vec.magnitude = 1.0
    
    bad_frame = MagicMock(spec=TransportedFrame)
    bad_frame.tangent = bad_vec
    bad_frame.normal = frames[0].normal
    bad_frame.binormal = frames[0].binormal
    
    frames[0] = bad_frame
    with pytest.raises(AnatomyValidationError, match="Non-finite vector"):
        generate_swept_surface(traj, tuple(frames), width=0.01, thickness=0.01)

def test_geometric_properties():
    # S-curve
    pts = []
    for i in range(10):
        t = i / 9.0
        pts.append(Point3D(math.sin(t*math.pi), t*2, 0))
    traj = create_trajectory(pts)
    frames = generate_transport_frames(traj)
    width = 0.02
    surf = generate_swept_surface(traj, frames, width=width, thickness=0.005)
    
    # Check S(s, 0) == C(s)
    # Check distance(S(s, -0.5), S(s, 0.5)) == W
    # Check perpendicularity
    for i, s_val in enumerate(surf._arc_lengths):
        c = surf.centerline_samples[i]
        t_frame = surf.transport_frames[i]
        
        # u=0 is centerline
        p0 = surf.evaluate_surface(s_val, 0.0)
        assert abs(p0.x - c.x) < 1e-5
        assert abs(p0.y - c.y) < 1e-5
        assert abs(p0.z - c.z) < 1e-5
        
        # width
        p_neg = surf.evaluate_surface(s_val, -0.5)
        p_pos = surf.evaluate_surface(s_val, 0.5)
        
        dist = math.sqrt((p_pos.x - p_neg.x)**2 + (p_pos.y - p_neg.y)**2 + (p_pos.z - p_neg.z)**2)
        assert abs(dist - width) < 1e-5
        
        # The vector from p_neg to p_pos should be parallel to binormal
        v_dx = p_pos.x - p_neg.x
        v_dy = p_pos.y - p_neg.y
        v_dz = p_pos.z - p_neg.z
        v_mag = math.sqrt(v_dx**2 + v_dy**2 + v_dz**2)
        v_norm = Vector3D(v_dx/v_mag, v_dy/v_mag, v_dz/v_mag)
        
        # Dot product with binormal should be 1.0 (since u_pos > u_neg, it points in +B direction)
        dot = v_norm.dx * t_frame.binormal.dx + v_norm.dy * t_frame.binormal.dy + v_norm.dz * t_frame.binormal.dz
        assert abs(dot - 1.0) < 1e-5
        
        # Perpendicular to tangent
        dot_t = v_norm.dx * t_frame.tangent.dx + v_norm.dy * t_frame.tangent.dy + v_norm.dz * t_frame.tangent.dz
        assert abs(dot_t) < 1e-5

def test_mesh_topology():
    pts = [Point3D(0,0,0), Point3D(0,1,0), Point3D(0,2,0)]
    traj = create_trajectory(pts)
    frames = generate_transport_frames(traj)
    surf = generate_swept_surface(traj, frames, width=1.0, thickness=1.0)
    
    mesh = generate_swept_mesh(surf, u_samples=3)
    
    # Vertices ordering
    # i=0 (s=0): u=-0.5, 0, 0.5 -> indices 0, 1, 2
    # i=1 (s=1): u=-0.5, 0, 0.5 -> indices 3, 4, 5
    # i=2 (s=2): u=-0.5, 0, 0.5 -> indices 6, 7, 8
    
    v0 = mesh.vertices[0]
    p_neg = surf.evaluate_surface(0.0, -0.5)
    assert abs(v0.x - p_neg.x) < 1e-5
    
    # Triangles for first segment, first cross-section interval
    # i=0, j=0
    # Triangle 1: (0, 3, 1) -> Note code uses v0=0, v1=3, v2=1 -> [0, 3, 1]
    # Triangle 2: (1, 3, 4) -> code uses v2=1, v1=3, v3=4 -> [1, 3, 4]
    
    # Check first 6 indices
    assert mesh.indices[0:6] == (0, 3, 1, 1, 3, 4)

def test_exact_180_reversal():
    pts = [Point3D(0,0,0), Point3D(0,1,0), Point3D(0,0,0)]
    traj = create_trajectory(pts)
    frames = generate_transport_frames(traj)
    surf = generate_swept_surface(traj, frames, width=0.01, thickness=0.01)
    mesh = generate_swept_mesh(surf)
    # Must not raise, topology must be fully generated
    assert len(mesh.vertices) == 9
    assert len(mesh.indices) == 24

def test_immutability():
    pts = [Point3D(0,0,0), Point3D(0,1,0), Point3D(0,2,0)]
    traj = create_trajectory(pts)
    frames = generate_transport_frames(traj)
    surf = generate_swept_surface(traj, frames, width=1.0, thickness=1.0)
    mesh = generate_swept_mesh(surf)
    
    # attempt to mutate
    with pytest.raises(Exception):
        setattr(surf, "width", 2.0)
    with pytest.raises(Exception):
        setattr(mesh, "vertices", tuple())

def test_distinctness():
    pts = [Point3D(0,0,0), Point3D(0,1,0), Point3D(0,2,0)]
    traj1 = create_trajectory(pts)
    frames1 = generate_transport_frames(traj1)
    
    surf1 = generate_swept_surface(traj1, frames1, width=1.0, thickness=1.0)
    surf2 = generate_swept_surface(traj1, frames1, width=2.0, thickness=1.0)
    
    mesh1 = generate_swept_mesh(surf1)
    mesh2 = generate_swept_mesh(surf2)
    
    # Width difference must produce different vertices
    assert mesh1.vertices != mesh2.vertices
    
    # Translation
    pts_trans = [Point3D(1,0,0), Point3D(1,1,0), Point3D(1,2,0)]
    traj_trans = create_trajectory(pts_trans)
    frames_trans = generate_transport_frames(traj_trans)
    surf3 = generate_swept_surface(traj_trans, frames_trans, width=1.0, thickness=1.0)
    mesh3 = generate_swept_mesh(surf3)
    
    assert mesh1.vertices != mesh3.vertices

def test_no_preset_snapping():
    # If we rotate the trajectory infinitesimally, the mesh should rotate infinitesimally, not snap to a plane
    pts = [Point3D(0,0,0), Point3D(1,0,0), Point3D(2,0,0)]
    traj1 = create_trajectory(pts)
    frames1 = generate_transport_frames(traj1)
    surf1 = generate_swept_surface(traj1, frames1, 1.0, 1.0)
    m1 = generate_swept_mesh(surf1)
    
    angle = 0.001
    ca = math.cos(angle)
    sa = math.sin(angle)
    
    pts_rot = []
    for p in pts:
        # rotate around Z
        pts_rot.append(Point3D(p.x*ca - p.y*sa, p.x*sa + p.y*ca, p.z))
        
    traj2 = create_trajectory(pts_rot)
    frames2 = generate_transport_frames(traj2)
    surf2 = generate_swept_surface(traj2, frames2, 1.0, 1.0)
    m2 = generate_swept_mesh(surf2)
    
    # They should be slightly different, but not drastically different (no snapping)
    dists = []
    for v1, v2 in zip(m1.vertices, m2.vertices):
        dist = math.sqrt((v1.x-v2.x)**2 + (v1.y-v2.y)**2 + (v1.z-v2.z)**2)
        dists.append(dist)
        
    assert sum(dists) > 0.0
    assert max(dists) < 0.01  # Should be very small rotation
