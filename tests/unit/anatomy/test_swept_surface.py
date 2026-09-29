# -*- coding: utf-8 -*-
import pytest
import math
import dataclasses
from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.swept_surface import TransportedFrame, SweptCutSurface

def create_valid_frame() -> TransportedFrame:
    return TransportedFrame(
        tangent=Vector3D(1.0, 0.0, 0.0),
        normal=Vector3D(0.0, 1.0, 0.0),
        binormal=Vector3D(0.0, 0.0, 1.0)
    )

def test_straight_path_representation():
    samples = (
        Point3D(0.0, 0.0, 0.0),
        Point3D(1.0, 0.0, 0.0),
        Point3D(2.0, 0.0, 0.0)
    )
    frames = (
        create_valid_frame(),
        create_valid_frame(),
        create_valid_frame()
    )
    
    surface = SweptCutSurface(
        surface_id="s1",
        centerline_samples=samples,
        transport_frames=frames,
        width=0.05,
        thickness=0.01
    )
    
    assert surface.total_arc_length == 2.0
    
    # Evaluate at s=1.0, u=0.5
    pt = surface.evaluate_surface(s=1.0, u=0.5)
    # C(s) + u * W * B(s)
    # C(1) = (1.0, 0.0, 0.0)
    # B = (0.0, 0.0, 1.0)
    # 0.5 * 0.05 = 0.025
    assert pt.x == 1.0
    assert pt.y == 0.0
    assert pytest.approx(pt.z) == 0.025

def test_arc_length_parameterization():
    samples = (Point3D(0.0, 0.0, 0.0), Point3D(1.0, 0.0, 0.0), Point3D(2.0, 0.0, 0.0))
    frames = (create_valid_frame(),) * 3
    surface = SweptCutSurface("s1", samples, frames, width=0.05, thickness=0.01)
    
    # Test s = 0.5 (between 0.0 and 1.0)
    pt = surface.evaluate_surface(s=0.5, u=0.0)
    assert pt.x == 0.5
    assert pt.y == 0.0
    assert pt.z == 0.0
    
    # Test out of bounds rejection
    with pytest.raises(AnatomyValidationError):
        surface.evaluate_surface(s=-0.1, u=0.0)
    with pytest.raises(AnatomyValidationError):
        surface.evaluate_surface(s=2.1, u=0.0)

def test_centerline_equality_at_u0():
    samples = (Point3D(0.0, 0.0, 0.0), Point3D(1.0, 0.0, 0.0), Point3D(2.0, 0.0, 0.0))
    frames = (create_valid_frame(),) * 3
    surface = SweptCutSurface("s1", samples, frames, width=0.05, thickness=0.01)
    
    for s in [0.0, 1.0, 2.0]:
        pt = surface.evaluate_surface(s=s, u=0.0)
        assert pt.x == s
        assert pt.y == 0.0
        assert pt.z == 0.0

def test_negative_zero_positive_u():
    samples = (Point3D(0.0, 0.0, 0.0), Point3D(1.0, 0.0, 0.0), Point3D(2.0, 0.0, 0.0))
    frames = (create_valid_frame(),) * 3
    surface = SweptCutSurface("s1", samples, frames, width=0.10, thickness=0.01)
    
    # u = -0.5
    pt = surface.evaluate_surface(s=1.0, u=-0.5)
    assert pt.z == -0.05
    # u = 0
    pt = surface.evaluate_surface(s=1.0, u=0.0)
    assert pt.z == 0.0
    # u = +0.5
    pt = surface.evaluate_surface(s=1.0, u=0.5)
    assert pt.z == 0.05

def test_endpoint_evaluation():
    samples = (Point3D(0.0, 0.0, 0.0), Point3D(1.0, 0.0, 0.0), Point3D(2.0, 0.0, 0.0))
    frames = (create_valid_frame(),) * 3
    surface = SweptCutSurface("s1", samples, frames, width=0.10, thickness=0.01)
    
    pt_start = surface.evaluate_surface(s=0.0, u=0.0)
    assert pt_start.x == 0.0
    
    pt_end = surface.evaluate_surface(s=2.0, u=0.0)
    assert pt_end.x == 2.0

def test_width_validity():
    samples = (Point3D(0.0, 0.0, 0.0), Point3D(1.0, 0.0, 0.0), Point3D(2.0, 0.0, 0.0))
    frames = (create_valid_frame(),) * 3
    
    with pytest.raises(AnatomyValidationError, match="strictly positive"):
        SweptCutSurface("s1", samples, frames, width=0.0, thickness=0.01)
        
    with pytest.raises(AnatomyValidationError, match="strictly positive"):
        SweptCutSurface("s1", samples, frames, width=-0.1, thickness=0.01)

def test_thickness_validity():
    samples = (Point3D(0.0, 0.0, 0.0), Point3D(1.0, 0.0, 0.0), Point3D(2.0, 0.0, 0.0))
    frames = (create_valid_frame(),) * 3
    
    with pytest.raises(AnatomyValidationError, match="strictly positive"):
        SweptCutSurface("s1", samples, frames, width=0.05, thickness=0.0)
        
    with pytest.raises(AnatomyValidationError, match="strictly positive"):
        SweptCutSurface("s1", samples, frames, width=0.05, thickness=-0.01)

def test_nonfinite_rejection():
    samples = (Point3D(0.0, 0.0, 0.0), Point3D(1.0, 0.0, 0.0), Point3D(2.0, 0.0, 0.0))
    frames = (create_valid_frame(),) * 3
    
    with pytest.raises(AnatomyValidationError, match="finite"):
        SweptCutSurface("s1", samples, frames, width=math.inf, thickness=0.01)
        
    with pytest.raises(AnatomyValidationError, match="finite"):
        SweptCutSurface("s1", samples, frames, width=0.05, thickness=float("nan"))

def test_zero_length_rejection():
    # Identical points
    samples = (Point3D(0.0, 0.0, 0.0), Point3D(0.0, 0.0, 0.0), Point3D(0.0, 0.0, 0.0))
    frames = (create_valid_frame(),) * 3
    
    with pytest.raises(AnatomyValidationError, match="Zero-length path"):
        SweptCutSurface("s1", samples, frames, width=0.05, thickness=0.01)

def test_immutability():
    samples = (Point3D(0.0, 0.0, 0.0), Point3D(1.0, 0.0, 0.0), Point3D(2.0, 0.0, 0.0))
    frames = (create_valid_frame(),) * 3
    surface = SweptCutSurface("s1", samples, frames, width=0.05, thickness=0.01)
    
    with pytest.raises(dataclasses.FrozenInstanceError):
        surface.width = 0.1  # type: ignore

def test_distinct_trajectories_remain_distinct():
    samples1 = (Point3D(0.0, 0.0, 0.0), Point3D(1.0, 0.0, 0.0), Point3D(2.0, 0.0, 0.0))
    samples2 = (Point3D(0.0, 0.0, 0.0), Point3D(0.0, 1.0, 0.0), Point3D(0.0, 2.0, 0.0))
    
    frame1 = create_valid_frame()
    frame2 = TransportedFrame(
        tangent=Vector3D(0.0, 1.0, 0.0),
        normal=Vector3D(-1.0, 0.0, 0.0),
        binormal=Vector3D(0.0, 0.0, 1.0)
    )
    
    surface1 = SweptCutSurface("s1", samples1, (frame1, frame1, frame1), width=0.05, thickness=0.01)
    surface2 = SweptCutSurface("s2", samples2, (frame2, frame2, frame2), width=0.05, thickness=0.01)
    
    # Trajectories do not snap, they remain uniquely distinct
    assert surface1.centerline_samples[1] != surface2.centerline_samples[1]
    
    pt1 = surface1.evaluate_surface(1.0, 0.5)
    pt2 = surface2.evaluate_surface(1.0, 0.5)
    
    assert pt1.as_tuple() != pt2.as_tuple()

def test_distinct_width_produces_distinct_geometry():
    samples = (Point3D(0.0, 0.0, 0.0), Point3D(1.0, 0.0, 0.0), Point3D(2.0, 0.0, 0.0))
    frames = (create_valid_frame(),) * 3
    surface1 = SweptCutSurface("s1", samples, frames, width=0.05, thickness=0.01)
    surface2 = SweptCutSurface("s2", samples, frames, width=0.10, thickness=0.01)
    
    pt1 = surface1.evaluate_surface(1.0, 0.5)
    pt2 = surface2.evaluate_surface(1.0, 0.5)
    assert pt1.z != pt2.z

def test_distinct_frame_orientation_produces_distinct_geometry():
    samples = (Point3D(0.0, 0.0, 0.0), Point3D(1.0, 0.0, 0.0), Point3D(2.0, 0.0, 0.0))
    frame1 = create_valid_frame()
    # Rotate N and B by 90 degrees around T
    frame2 = TransportedFrame(
        tangent=Vector3D(1.0, 0.0, 0.0),
        normal=Vector3D(0.0, 0.0, -1.0),
        binormal=Vector3D(0.0, 1.0, 0.0)
    )
    surface1 = SweptCutSurface("s1", samples, (frame1,)*3, width=0.05, thickness=0.01)
    surface2 = SweptCutSurface("s2", samples, (frame2,)*3, width=0.05, thickness=0.01)
    
    pt1 = surface1.evaluate_surface(1.0, 0.5)
    pt2 = surface2.evaluate_surface(1.0, 0.5)
    assert pt1.as_tuple() != pt2.as_tuple()

def test_transport_frame_validation():
    # Not unit vectors
    with pytest.raises(AnatomyValidationError):
        TransportedFrame(Vector3D(2.0, 0.0, 0.0), Vector3D(0.0, 1.0, 0.0), Vector3D(0.0, 0.0, 1.0))
        
    # Not orthogonal
    with pytest.raises(AnatomyValidationError):
        TransportedFrame(
            Vector3D(1.0, 0.0, 0.0),
            Vector3D(0.707, 0.707, 0.0),
            Vector3D(0.0, 0.0, 1.0)
        )

def test_frame_handedness():
    # Left-handed frame (T x N = -B)
    with pytest.raises(AnatomyValidationError, match="right-handed"):
        TransportedFrame(
            tangent=Vector3D(1.0, 0.0, 0.0),
            normal=Vector3D(0.0, 1.0, 0.0),
            binormal=Vector3D(0.0, 0.0, -1.0)
        )

