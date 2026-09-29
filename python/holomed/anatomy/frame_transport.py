# -*- coding: utf-8 -*-
"""Phase F3.2-B Deterministic Bishop / Rotation-Minimizing Frame Transport."""

import math
from typing import Tuple

from holomed.anatomy.models import Vector3D
from holomed.anatomy.trajectory import ProcessedCutTrajectory
from holomed.anatomy.swept_surface import TransportedFrame, UNIT_VECTOR_TOLERANCE
from holomed.anatomy.exceptions import AnatomyValidationError

PARALLEL_EPSILON = 1e-6
ANTIPARALLEL_EPSILON = 1e-6
REORTHOGONALIZE_TOLERANCE = 1e-4

def _cross(v1: Vector3D, v2: Vector3D) -> Vector3D:
    return Vector3D(
        dx=v1.dy * v2.dz - v1.dz * v2.dy,
        dy=v1.dz * v2.dx - v1.dx * v2.dz,
        dz=v1.dx * v2.dy - v1.dy * v2.dx
    )

def _dot(v1: Vector3D, v2: Vector3D) -> float:
    return v1.dx * v2.dx + v1.dy * v2.dy + v1.dz * v2.dz

def _normalize(v: Vector3D) -> Vector3D:
    mag = v.magnitude
    if mag == 0.0:
        raise AnatomyValidationError("Attempted to normalize zero vector")
    return Vector3D(dx=v.dx / mag, dy=v.dy / mag, dz=v.dz / mag)

def _reorthogonalize(tangent: Vector3D, normal: Vector3D) -> Vector3D:
    """Gram-Schmidt re-orthogonalization: N' = N - (N.T)T"""
    d = _dot(normal, tangent)
    nx = normal.dx - d * tangent.dx
    ny = normal.dy - d * tangent.dy
    nz = normal.dz - d * tangent.dz
    return _normalize(Vector3D(nx, ny, nz))

def _rotate_180(v: Vector3D, w: Vector3D) -> Vector3D:
    """Rotate v by 180 degrees around unit axis w using Rodrigues formula: v' = -v + 2(w.v)w"""
    d = _dot(w, v)
    return Vector3D(
        dx=-v.dx + 2.0 * d * w.dx,
        dy=-v.dy + 2.0 * d * w.dy,
        dz=-v.dz + 2.0 * d * w.dz
    )

def generate_transport_frames(trajectory: ProcessedCutTrajectory) -> Tuple[TransportedFrame, ...]:
    """
    Deterministic Bishop frame generator.
    """
    tangents = trajectory.tangents
    if not tangents or len(tangents) < 3:
        raise AnatomyValidationError("Insufficient tangents (<3) for frame transport")

    # Validate all tangents are finite unit vectors
    for idx, t in enumerate(tangents):
        if abs(t.magnitude - 1.0) > UNIT_VECTOR_TOLERANCE:
            raise AnatomyValidationError(f"Tangent at index {idx} is not a unit vector: mag={t.magnitude}")

    frames = []

    # ---------------------------------------------------------
    # Initial Frame Selection (Deterministic)
    # ---------------------------------------------------------
    t0 = tangents[0]
    
    # Preferred global reference axis is +Y
    # Fallback reference is +X if T0 is parallel/anti-parallel to +Y
    if abs(t0.dy) > 1.0 - PARALLEL_EPSILON:
        r = Vector3D(1.0, 0.0, 0.0)
    else:
        r = Vector3D(0.0, 1.0, 0.0)

    b0_raw = _cross(t0, r)
    b0 = _normalize(b0_raw)
    n0_raw = _cross(b0, t0)
    n0 = _normalize(n0_raw)

    frames.append(TransportedFrame(tangent=t0, normal=n0, binormal=b0))

    # ---------------------------------------------------------
    # Bishop Transport
    # ---------------------------------------------------------
    for i in range(1, len(tangents)):
        t_prev = tangents[i-1]
        t_curr = tangents[i]
        n_prev = frames[-1].normal
        b_prev = frames[-1].binormal

        c = _dot(t_prev, t_curr)

        if c > 1.0 - PARALLEL_EPSILON:
            # Case A: near-identical direction
            # Preserve transverse frame, re-orthogonalize
            n_curr = _reorthogonalize(t_curr, n_prev)
            b_curr = _normalize(_cross(t_curr, n_curr))

        elif c < -1.0 + ANTIPARALLEL_EPSILON:
            # Case C: near-opposite / 180 reversal
            # Deterministic least-aligned axis rule to find rotation axis
            abs_x, abs_y, abs_z = abs(t_prev.dx), abs(t_prev.dy), abs(t_prev.dz)
            if abs_x <= abs_y and abs_x <= abs_z:
                a = Vector3D(1.0, 0.0, 0.0)
            elif abs_y <= abs_x and abs_y <= abs_z:
                a = Vector3D(0.0, 1.0, 0.0)
            else:
                a = Vector3D(0.0, 0.0, 1.0)
                
            w_raw = _cross(t_prev, a)
            w = _normalize(w_raw)
            
            n_rot = _rotate_180(n_prev, w)
            n_curr = _reorthogonalize(t_curr, n_rot)
            b_curr = _normalize(_cross(t_curr, n_curr))

        else:
            # Case B: general rotation
            v = _cross(t_prev, t_curr)
            
            # Rodrigues exact rotational update (no trig)
            n_cross = _cross(v, n_prev)
            v_dot_n = _dot(v, n_prev)
            
            factor = v_dot_n / (1.0 + c)
            n_curr_x = n_prev.dx * c + n_cross.dx + v.dx * factor
            n_curr_y = n_prev.dy * c + n_cross.dy + v.dy * factor
            n_curr_z = n_prev.dz * c + n_cross.dz + v.dz * factor
            
            n_rot = _normalize(Vector3D(n_curr_x, n_curr_y, n_curr_z))
            
            n_curr = _reorthogonalize(t_curr, n_rot)
            b_curr = _normalize(_cross(t_curr, n_curr))

        frames.append(TransportedFrame(tangent=t_curr, normal=n_curr, binormal=b_curr))

    return tuple(frames)
