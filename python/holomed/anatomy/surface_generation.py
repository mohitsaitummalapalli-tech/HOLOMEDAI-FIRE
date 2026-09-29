# -*- coding: utf-8 -*-
import math
from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.trajectory import CutTrajectory, ProcessedCutTrajectory, TrajectorySample, TrajectoryState, ValidityState, TrajectoryValidationError
from holomed.anatomy.slicing import PlanarCutSurface

class SurfaceGenerationError(Exception):
    pass

def resample_trajectory(traj: CutTrajectory) -> CutTrajectory:
    # First pass: collapse micro movements (< 0.001)
    collapsed = [traj.samples[0]]
    for s in traj.samples[1:]:
        p1 = collapsed[-1].position
        p2 = s.position
        dx = p2.x - p1.x
        dy = p2.y - p1.y
        dz = p2.z - p1.z
        if math.sqrt(dx*dx + dy*dy + dz*dz) >= 0.001:
            collapsed.append(s)
            
    resampled = [collapsed[0]]
    next_target = 0.005
    current_length = 0.0
    
    for i in range(len(collapsed) - 1):
        s1 = collapsed[i]
        s2 = collapsed[i+1]
        dx = s2.position.x - s1.position.x
        dy = s2.position.y - s1.position.y
        dz = s2.position.z - s1.position.z
        segment_len = math.sqrt(dx*dx + dy*dy + dz*dz)
        
        while current_length + segment_len >= next_target:
            t = (next_target - current_length) / segment_len
            ix = s1.position.x + t * dx
            iy = s1.position.y + t * dy
            iz = s1.position.z + t * dz
            it = s1.timestamp + t * (s2.timestamp - s1.timestamp)
            resampled.append(TrajectorySample(Point3D(ix, iy, iz), it, TrajectoryState.ACTIVE))
            next_target += 0.005
            
        current_length += segment_len
        
    if len(resampled) > 1:
        lx = resampled[-1].position.x - collapsed[-1].position.x
        ly = resampled[-1].position.y - collapsed[-1].position.y
        lz = resampled[-1].position.z - collapsed[-1].position.z
        if math.sqrt(lx*lx + ly*ly + lz*lz) < 1e-4:
            resampled[-1] = collapsed[-1]
        else:
            resampled.append(collapsed[-1])
    else:
        resampled.append(collapsed[-1])
        
    resampled[0] = TrajectorySample(resampled[0].position, resampled[0].timestamp, TrajectoryState.START)
    resampled[-1] = TrajectorySample(resampled[-1].position, resampled[-1].timestamp, TrajectoryState.END)
    
    return CutTrajectory(
        trajectory_id=traj.trajectory_id,
        correlation_id=traj.correlation_id,
        samples=tuple(resampled),
        coordinate_space=traj.coordinate_space,
        validity_state=ValidityState.PROCESSED,
        sampling_metadata=traj.sampling_metadata
    )

def smooth_trajectory(traj: CutTrajectory) -> ProcessedCutTrajectory:
    samples = traj.samples
    if len(samples) < 3:
        raise TrajectoryValidationError("Need at least 3 points for smoothing")
    
    # Pre-calculate corners to create smoothing boundaries
    is_corner = [False] * len(samples)
    for i in range(1, len(samples) - 1):
        p_prev = samples[i-1].position
        p_curr = samples[i].position
        p_next = samples[i+1].position
        
        vx_in = p_curr.x - p_prev.x
        vy_in = p_curr.y - p_prev.y
        vz_in = p_curr.z - p_prev.z
        vx_out = p_next.x - p_curr.x
        vy_out = p_next.y - p_curr.y
        vz_out = p_next.z - p_curr.z
        
        lin = math.sqrt(vx_in**2 + vy_in**2 + vz_in**2)
        lout = math.sqrt(vx_out**2 + vy_out**2 + vz_out**2)
        
        if lin > 1e-6 and lout > 1e-6:
            dot = (vx_in*vx_out + vy_in*vy_out + vz_in*vz_out) / (lin * lout)
            dot = max(-1.0, min(1.0, dot))
            angle = math.acos(dot)
            if angle > 30.0 * math.pi / 180.0:
                is_corner[i] = True

    smoothed = [samples[0]]
    for i in range(1, len(samples) - 1):
        # A protected corner creates a smoothing boundary.
        # If this point is a corner, or adjacent to a corner, it remains unsmoothed.
        bypass = is_corner[i] or is_corner[i-1] or is_corner[i+1]
        
        if bypass:
            smoothed.append(samples[i])
        else:
            p_prev = samples[i-1].position
            p_curr = samples[i].position
            p_next = samples[i+1].position
            
            sx = 0.25 * p_prev.x + 0.5 * p_curr.x + 0.25 * p_next.x
            sy = 0.25 * p_prev.y + 0.5 * p_curr.y + 0.25 * p_next.y
            sz = 0.25 * p_prev.z + 0.5 * p_curr.z + 0.25 * p_next.z
            
            dx = sx - p_curr.x
            dy = sy - p_curr.y
            dz = sz - p_curr.z
            dist = math.sqrt(dx*dx + dy*dy + dz*dz)
            if dist > 0.02:
                scale = 0.02 / dist
                sx = p_curr.x + dx * scale
                sy = p_curr.y + dy * scale
                sz = p_curr.z + dz * scale
                
            smoothed.append(TrajectorySample(Point3D(sx, sy, sz), samples[i].timestamp, samples[i].state))
        
    smoothed.append(samples[-1])
    
    tangents = []
    for i in range(len(smoothed)):
        if i == 0:
            vx = smoothed[1].position.x - smoothed[0].position.x
            vy = smoothed[1].position.y - smoothed[0].position.y
            vz = smoothed[1].position.z - smoothed[0].position.z
        elif i == len(smoothed) - 1:
            vx = smoothed[-1].position.x - smoothed[-2].position.x
            vy = smoothed[-1].position.y - smoothed[-2].position.y
            vz = smoothed[-1].position.z - smoothed[-2].position.z
        else:
            vx = smoothed[i+1].position.x - smoothed[i-1].position.x
            vy = smoothed[i+1].position.y - smoothed[i-1].position.y
            vz = smoothed[i+1].position.z - smoothed[i-1].position.z
        ll = math.sqrt(vx*vx + vy*vy + vz*vz)
        if ll > 1e-8:
            tangents.append(Vector3D(vx/ll, vy/ll, vz/ll))
        else:
            tangents.append(Vector3D(0, 0, 1))
            
    t0 = tangents[0]
    if abs(t0.dx) > 0.9:
        v_up = Vector3D(0, 1, 0)
    else:
        v_up = Vector3D(1, 0, 0)
    cx = t0.dy * v_up.dz - t0.dz * v_up.dy
    cy = t0.dz * v_up.dx - t0.dx * v_up.dz
    cz = t0.dx * v_up.dy - t0.dy * v_up.dx
    cl = math.sqrt(cx*cx + cy*cy + cz*cz)
    n0 = Vector3D(cx/cl, cy/cl, cz/cl)
    
    bx = n0.dy * t0.dz - n0.dz * t0.dy
    by = n0.dz * t0.dx - n0.dx * t0.dz
    bz = n0.dx * t0.dy - n0.dy * t0.dx
    b0 = Vector3D(bx, by, bz)
    
    frames = [(t0, n0, b0)]
    
    for i in range(1, len(smoothed)):
        t_prev = tangents[i-1]
        t_curr = tangents[i]
        n_prev = frames[-1][1]
        
        axis_x = t_prev.dy * t_curr.dz - t_prev.dz * t_curr.dy
        axis_y = t_prev.dz * t_curr.dx - t_prev.dx * t_curr.dz
        axis_z = t_prev.dx * t_curr.dy - t_prev.dy * t_curr.dx
        
        sin_angle = math.sqrt(axis_x**2 + axis_y**2 + axis_z**2)
        dot_angle = t_prev.dx*t_curr.dx + t_prev.dy*t_curr.dy + t_prev.dz*t_curr.dz
        
        if sin_angle > 1e-8:
            axis_x /= sin_angle
            axis_y /= sin_angle
            axis_z /= sin_angle
            angle = math.acos(max(-1.0, min(1.0, dot_angle)))
            
            c = math.cos(angle)
            s = math.sin(angle)
            dot_n_axis = n_prev.dx*axis_x + n_prev.dy*axis_y + n_prev.dz*axis_z
            
            cross_ax_n_x = axis_y*n_prev.dz - axis_z*n_prev.dy
            cross_ax_n_y = axis_z*n_prev.dx - axis_x*n_prev.dz
            cross_ax_n_z = axis_x*n_prev.dy - axis_y*n_prev.dx
            
            n_curr_x = n_prev.dx*c + cross_ax_n_x*s + axis_x*dot_n_axis*(1-c)
            n_curr_y = n_prev.dy*c + cross_ax_n_y*s + axis_y*dot_n_axis*(1-c)
            n_curr_z = n_prev.dz*c + cross_ax_n_z*s + axis_z*dot_n_axis*(1-c)
            
            nl = math.sqrt(n_curr_x**2 + n_curr_y**2 + n_curr_z**2)
            n_curr = Vector3D(n_curr_x/nl, n_curr_y/nl, n_curr_z/nl)
        else:
            n_curr = n_prev
            
        b_curr_x = n_curr.dy * t_curr.dz - n_curr.dz * t_curr.dy
        b_curr_y = n_curr.dz * t_curr.dx - n_curr.dx * t_curr.dz
        b_curr_z = n_curr.dx * t_curr.dy - n_curr.dy * t_curr.dx
        b_curr = Vector3D(b_curr_x, b_curr_y, b_curr_z)
        
        frames.append((t_curr, n_curr, b_curr))
        
    return ProcessedCutTrajectory(
        trajectory_id=traj.trajectory_id,
        correlation_id=traj.correlation_id,
        samples=tuple(smoothed),
        tangents=tuple(tangents),
        transport_frames=tuple(frames),
        coordinate_space=traj.coordinate_space,
        sampling_metadata=traj.sampling_metadata
    )

def process_trajectory(traj: CutTrajectory) -> ProcessedCutTrajectory:
    from holomed.anatomy.trajectory import validate_trajectory
    validated = validate_trajectory(traj)
    resampled = resample_trajectory(validated)
    smoothed = smooth_trajectory(resampled)
    return smoothed

def jacobi_eigen(matrix, max_iter=100, tol=1e-12):
    V = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    A = [row[:] for row in matrix]
    n = 3
    for _ in range(max_iter):
        max_val = 0.0
        p, q = 0, 1
        for i in range(n):
            for j in range(i+1, n):
                if abs(A[i][j]) > max_val:
                    max_val = abs(A[i][j])
                    p, q = i, j
        if max_val < tol:
            break
        
        diff = A[q][q] - A[p][p]
        if abs(A[p][q]) < 1e-15:
            t = 0.0
        else:
            phi = diff / (2.0 * A[p][q])
            t = 1.0 / (abs(phi) + math.sqrt(phi*phi + 1.0))
            if phi < 0.0:
                t = -t
        
        c = 1.0 / math.sqrt(t*t + 1.0)
        s = t * c
        tau = s / (1.0 + c)
        
        temp_A_pp = A[p][p]
        temp_A_qq = A[q][q]
        A[p][p] = temp_A_pp - t * A[p][q]
        A[q][q] = temp_A_qq + t * A[p][q]
        A[p][q] = 0.0
        A[q][p] = 0.0
        
        for j in range(n):
            if j != p and j != q:
                temp_p = A[p][j]
                temp_q = A[q][j]
                A[p][j] = temp_p - s * (temp_q + tau * temp_p)
                A[q][j] = temp_q + s * (temp_p - tau * temp_q)
                A[j][p] = A[p][j]
                A[j][q] = A[q][j]
                
        for i in range(n):
            temp_p = V[i][p]
            temp_q = V[i][q]
            V[i][p] = temp_p - s * (temp_q + tau * temp_p)
            V[i][q] = temp_q + s * (temp_p - tau * temp_q)
            
    eigenvalues = [A[0][0], A[1][1], A[2][2]]
    idx = sorted(range(n), key=lambda i: eigenvalues[i], reverse=True)
    sorted_eigenvalues = [eigenvalues[i] for i in idx]
    sorted_eigenvectors = [[V[j][i] for j in range(n)] for i in idx]
    return sorted_eigenvalues, sorted_eigenvectors

def generate_planar_surface(traj: ProcessedCutTrajectory) -> PlanarCutSurface:
    pts = [s.position for s in traj.samples]
    n = len(pts)
    if n < 3:
        raise SurfaceGenerationError("Insufficient points for PCA")
        
    p0_x = sum(p.x for p in pts) / n
    p0_y = sum(p.y for p in pts) / n
    p0_z = sum(p.z for p in pts) / n
    p0 = Point3D(p0_x, p0_y, p0_z)
    
    cov = [[0.0]*3 for _ in range(3)]
    for p in pts:
        dx = p.x - p0_x
        dy = p.y - p0_y
        dz = p.z - p0_z
        cov[0][0] += dx * dx
        cov[0][1] += dx * dy
        cov[0][2] += dx * dz
        cov[1][0] += dy * dx
        cov[1][1] += dy * dy
        cov[1][2] += dy * dz
        cov[2][0] += dz * dx
        cov[2][1] += dz * dy
        cov[2][2] += dz * dz
        
    evals, evecs = jacobi_eigen(cov)
    l1, l2, l3 = evals
    
    if l1 < 1e-12:
        raise SurfaceGenerationError("Degenerate point cloud")
        
    if l2 / l1 < 1e-3:
        raise SurfaceGenerationError(f"Nearly collinear trajectory: l2/l1 = {l2/l1}")
        
    v3 = evecs[2]
    nx, ny, nz = v3[0], v3[1], v3[2]
    
    if nz < -1e-12:
        nx, ny, nz = -nx, -ny, -nz
    elif abs(nz) <= 1e-12 and ny < -1e-12:
        nx, ny, nz = -nx, -ny, -nz
    elif abs(nz) <= 1e-12 and abs(ny) <= 1e-12 and nx < -1e-12:
        nx, ny, nz = -nx, -ny, -nz
        
    nl = math.sqrt(nx*nx + ny*ny + nz*nz)
    nx /= nl
    ny /= nl
    nz /= nl
    
    normal = Vector3D(nx, ny, nz)
    
    max_d = 0.0
    for p in pts:
        d = abs((p.x - p0_x)*nx + (p.y - p0_y)*ny + (p.z - p0_z)*nz)
        if d > max_d:
            max_d = d
            
    if max_d > 0.05:
        raise SurfaceGenerationError(f"Non-planar trajectory, max residual: {max_d}")
        
    return PlanarCutSurface(origin=p0, normal=normal)
