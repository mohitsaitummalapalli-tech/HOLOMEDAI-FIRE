# -*- coding: utf-8 -*-
import pytest
import math
import copy

from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.trajectory import ProcessedCutTrajectory, TrajectorySample, TrajectoryState
from holomed.anatomy.frame_transport import generate_transport_frames, PARALLEL_EPSILON, ANTIPARALLEL_EPSILON, _dot, _cross
from holomed.anatomy.exceptions import AnatomyValidationError
from holomed.anatomy.swept_surface import TransportedFrame, UNIT_VECTOR_TOLERANCE

def _make_pct(tangents: list[Vector3D]) -> ProcessedCutTrajectory:
    # Dummy samples
    samples = tuple([
        TrajectorySample(position=Point3D(i*0.1, 0, 0), timestamp=i*0.1, state=TrajectoryState.ACTIVE)
        for i in range(len(tangents))
    ])
    return ProcessedCutTrajectory(
        trajectory_id="t1",
        correlation_id="c1",
        samples=samples,
        tangents=tuple(tangents)
    )

def _normalize_vec(dx, dy, dz) -> Vector3D:
    mag = math.sqrt(dx*dx + dy*dy + dz*dz)
    return Vector3D(dx/mag, dy/mag, dz/mag)

def test_insufficient_tangents():
    pct = _make_pct([Vector3D(1.0, 0.0, 0.0)] * 2)
    with pytest.raises(AnatomyValidationError, match="Insufficient"):
        generate_transport_frames(pct)

def test_invalid_tangents():
    # zero
    pct = _make_pct([Vector3D(0.0, 0.0, 0.0)] * 3)
    with pytest.raises(AnatomyValidationError):
        generate_transport_frames(pct)

    # non-unit
    pct = _make_pct([Vector3D(2.0, 0.0, 0.0)] * 3)
    with pytest.raises(AnatomyValidationError):
        generate_transport_frames(pct)

    # non-finite
    with pytest.raises(AnatomyValidationError):
        pct = _make_pct([Vector3D(math.inf, 0.0, 0.0)] * 3)
        generate_transport_frames(pct)

def _check_frame_invariants(frames: tuple[TransportedFrame, ...]):
    for f in frames:
        assert abs(f.tangent.magnitude - 1.0) < UNIT_VECTOR_TOLERANCE
        assert abs(f.normal.magnitude - 1.0) < UNIT_VECTOR_TOLERANCE
        assert abs(f.binormal.magnitude - 1.0) < UNIT_VECTOR_TOLERANCE

        assert abs(_dot(f.tangent, f.normal)) < 1e-4
        assert abs(_dot(f.tangent, f.binormal)) < 1e-4
        assert abs(_dot(f.normal, f.binormal)) < 1e-4

        cross_b = _cross(f.tangent, f.normal)
        assert abs(cross_b.dx - f.binormal.dx) < 1e-4
        assert abs(cross_b.dy - f.binormal.dy) < 1e-4
        assert abs(cross_b.dz - f.binormal.dz) < 1e-4

def test_straight_trajectory():
    tangents = [_normalize_vec(1.0, 0.0, 0.0)] * 10
    pct = _make_pct(tangents)
    frames = generate_transport_frames(pct)
    _check_frame_invariants(frames)

    # Frames should remain identical
    for i in range(1, len(frames)):
        assert frames[i].normal == frames[0].normal
        assert frames[i].binormal == frames[0].binormal

def test_first_tangent_parallel_reference():
    # First tangent +Y
    tangents = [_normalize_vec(0.0, 1.0, 0.0)] * 3
    pct = _make_pct(tangents)
    frames = generate_transport_frames(pct)
    _check_frame_invariants(frames)

    # First tangent -Y
    tangents2 = [_normalize_vec(0.0, -1.0, 0.0)] * 3
    pct2 = _make_pct(tangents2)
    frames2 = generate_transport_frames(pct2)
    _check_frame_invariants(frames2)

    # In both cases, the fallback reference is +X
    # Normal = +X
    assert abs(frames[0].normal.dx - 1.0) < 1e-4
    assert abs(frames2[0].normal.dx - 1.0) < 1e-4

def test_180_reversal():
    tangents = [
        _normalize_vec(1.0, 0.0, 0.0),
        _normalize_vec(1.0, 0.0, 0.0),
        _normalize_vec(-1.0, 0.0, 0.0) # Exact 180
    ]
    pct = _make_pct(tangents)
    frames = generate_transport_frames(pct)
    _check_frame_invariants(frames)

    assert frames[-1].tangent.dx == -1.0

    # Repeated should be deterministic
    frames2 = generate_transport_frames(pct)
    assert frames[-1].normal == frames2[-1].normal

def test_near_180_reversal():
    # positive perturbation
    tangents_pos = [
        _normalize_vec(1.0, 0.0, 0.0),
        _normalize_vec(-1.0, 1e-7, 0.0),
        _normalize_vec(-1.0, 0.0, 0.0)
    ]
    pct_pos = _make_pct(tangents_pos)
    frames_pos = generate_transport_frames(pct_pos)
    _check_frame_invariants(frames_pos)

    # negative perturbation
    tangents_neg = [
        _normalize_vec(1.0, 0.0, 0.0),
        _normalize_vec(-1.0, -1e-7, 0.0),
        _normalize_vec(-1.0, 0.0, 0.0)
    ]
    pct_neg = _make_pct(tangents_neg)
    frames_neg = generate_transport_frames(pct_neg)
    _check_frame_invariants(frames_neg)

    # tiny perturbation around threshold
    tangents_tiny = [
        _normalize_vec(1.0, 0.0, 0.0),
        _normalize_vec(-1.0, ANTIPARALLEL_EPSILON/2, 0.0),
        _normalize_vec(-1.0, 0.0, 0.0)
    ]
    pct_tiny = _make_pct(tangents_tiny)
    frames_tiny = generate_transport_frames(pct_tiny)
    _check_frame_invariants(frames_tiny)

def test_bishop_property_no_twist():
    """Mathematical property: Bishop frame introduces no twist around the tangent."""
    tangents = []
    # Circle in XY plane
    for i in range(10):
        theta = i * (math.pi / 10)
        tangents.append(_normalize_vec(math.cos(theta), math.sin(theta), 0.0))

    pct = _make_pct(tangents)
    frames = generate_transport_frames(pct)
    _check_frame_invariants(frames)

    for i in range(1, len(frames)):
        t_prev = frames[i-1].tangent
        t_curr = frames[i].tangent
        n_prev = frames[i-1].normal
        n_curr = frames[i].normal

        # The rotation from T_prev to T_curr is around T_prev x T_curr
        # In a purely rotation-minimizing transport, the normal should only rotate around this axis.
        # This implies that the projection of N_curr onto T_prev x T_curr is preserved.
        axis = _cross(t_prev, t_curr)
        if axis.magnitude > 1e-6:
            axis = _normalize_vec(axis.dx, axis.dy, axis.dz)
            proj_prev = _dot(n_prev, axis)
            proj_curr = _dot(n_curr, axis)
            assert abs(proj_prev - proj_curr) < 1e-4

def test_arbitrary_3d_curve():
    tangents = []
    for i in range(20):
        t = i * 0.1
        tangents.append(_normalize_vec(math.sin(t), math.cos(t), t))
    pct = _make_pct(tangents)
    frames = generate_transport_frames(pct)
    _check_frame_invariants(frames)

def test_s_curve():
    tangents = []
    for i in range(40):
        t = i * 0.1
        # S-curve in planar coordinates
        tangents.append(_normalize_vec(math.cos(t), math.sin(2*t), 0.0))
    pct = _make_pct(tangents)
    frames = generate_transport_frames(pct)
    _check_frame_invariants(frames)

import dataclasses

def test_immutability():
    tangents = [_normalize_vec(1.0, 0.0, 0.0)] * 3
    pct = _make_pct(tangents)

    frames = generate_transport_frames(pct)

    # Input not modified - no fields were changed
    # We can check that the output frames are immutable
    with pytest.raises(dataclasses.FrozenInstanceError):
        frames[0].tangent = Vector3D(1.0, 0.0, 0.0) # type: ignore

    with pytest.raises(dataclasses.FrozenInstanceError):
        frames[0].normal = Vector3D(0.0, 1.0, 0.0) # type: ignore

def test_deterministic_replay():
    tangents = []
    for i in range(20):
        t = i * 0.1
        tangents.append(_normalize_vec(math.sin(t), math.cos(t), t))
    pct = _make_pct(tangents)

    frames1 = generate_transport_frames(pct)
    frames2 = generate_transport_frames(pct)

    for f1, f2 in zip(frames1, frames2):
        assert f1.tangent == f2.tangent
        assert f1.normal == f2.normal
        assert f1.binormal == f2.binormal
