import math
from typing import List, Tuple, Set, Dict

from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.freeform_capping import CutterPartitionError, ProvenancedIntersectionSegment
from holomed.anatomy.boundary_graph import BoundarySegment

def _det2d(p1: Tuple[float, float], p2: Tuple[float, float], p3: Tuple[float, float]) -> float:
    return (p2[0] - p1[0]) * (p3[1] - p1[1]) - (p2[1] - p1[1]) * (p3[0] - p1[0])

def _lines_intersect(p1: Tuple[float, float], p2: Tuple[float, float], p3: Tuple[float, float], p4: Tuple[float, float]) -> bool:
    d1 = _det2d(p3, p4, p1)
    d2 = _det2d(p3, p4, p2)
    d3 = _det2d(p1, p2, p3)
    d4 = _det2d(p1, p2, p4)
    if d1 * d2 < -1e-8 and d3 * d4 < -1e-8:
        return True
    return False

def subdivide_cutter_triangle(
    v0: Point3D, v1: Point3D, v2: Point3D, 
    segments: List[BoundarySegment]
) -> Tuple[List[Tuple[Point3D, Point3D, Point3D]], List[ProvenancedIntersectionSegment]]:
    """
    Subdivides a cutter triangle using a deterministic local planar arrangement.
    Replaces D3's _split_triangle to properly handle multiple disjoint intersection segments.
    """
    if not segments:
        return [(v0, v1, v2)], []

    o = v0
    u_vec = Vector3D(v1.x - v0.x, v1.y - v0.y, v1.z - v0.z)
    u_len = math.sqrt(u_vec.dx**2 + u_vec.dy**2 + u_vec.dz**2)
    if u_len < 1e-10:
        raise CutterPartitionError("Degenerate cutter triangle (zero edge length)")
    u = Vector3D(u_vec.dx / u_len, u_vec.dy / u_len, u_vec.dz / u_len)
    
    v2_vec = Vector3D(v2.x - v0.x, v2.y - v0.y, v2.z - v0.z)
    wx = u_vec.dy * v2_vec.dz - u_vec.dz * v2_vec.dy
    wy = u_vec.dz * v2_vec.dx - u_vec.dx * v2_vec.dz
    wz = u_vec.dx * v2_vec.dy - u_vec.dy * v2_vec.dx
    w_len = math.sqrt(wx**2 + wy**2 + wz**2)
    if w_len < 1e-10:
        raise CutterPartitionError("Degenerate cutter triangle (zero area)")
    w = Vector3D(wx / w_len, wy / w_len, wz / w_len)
    
    vx = w.dy * u.dz - w.dz * u.dy
    vy = w.dz * u.dx - w.dx * u.dz
    vz = w.dx * u.dy - w.dy * u.dx
    v = Vector3D(vx, vy, vz)
    
    def project(p: Point3D) -> Tuple[float, float]:
        dx = p.x - o.x
        dy = p.y - o.y
        dz = p.z - o.z
        pu = dx * u.dx + dy * u.dy + dz * u.dz
        pv = dx * v.dx + dy * v.dy + dz * v.dz
        return (pu, pv)

    canon_points: list[tuple[float, float]] = []
    canon_3d = []
    
    def get_canon(pt_3d: Point3D) -> int:
        pt_2d = project(pt_3d)
        for i, cp in enumerate(canon_points):
            if math.hypot(pt_2d[0] - cp[0], pt_2d[1] - cp[1]) < 1e-6:
                return i
        canon_points.append(pt_2d)
        canon_3d.append(pt_3d)
        return len(canon_points) - 1

    c0 = get_canon(v0)
    c1 = get_canon(v1)
    c2 = get_canon(v2)
    
    edges = set()
    def add_edge(ua, va):
        if ua != va:
            edges.add((min(ua, va), max(ua, va)))
            
    add_edge(c0, c1)
    add_edge(c1, c2)
    add_edge(c2, c0)
    
    barrier_edges = set()
    for seg in segments:
        if math.hypot(seg.start.x - seg.end.x, seg.start.y - seg.end.y) + abs(seg.start.z - seg.end.z) < 1e-8:
            continue
        cs = get_canon(seg.start)
        ce = get_canon(seg.end)
        if cs != ce:
            add_edge(cs, ce)
            barrier_edges.add((min(cs, ce), max(cs, ce)))
            
    while True:
        split_occurred = False
        edge_list = list(edges)
        for e in edge_list:
            for i in range(len(canon_points)):
                if i == e[0] or i == e[1]:
                    continue
                ept1 = canon_points[e[0]]
                ept2 = canon_points[e[1]]
                pt = canon_points[i]
                d = _det2d(ept1, ept2, pt)
                if abs(d) < 1e-6:
                    dot = (pt[0] - ept1[0]) * (ept2[0] - ept1[0]) + (pt[1] - ept1[1]) * (ept2[1] - ept1[1])
                    length_sq = (ept2[0] - ept1[0])**2 + (ept2[1] - ept1[1])**2
                    if 1e-6 < dot < length_sq - 1e-6:
                        edges.remove(e)
                        add_edge(e[0], i)
                        add_edge(i, e[1])
                        if e in barrier_edges:
                            barrier_edges.remove(e)
                            barrier_edges.add((min(e[0], i), max(e[0], i)))
                            barrier_edges.add((min(i, e[1]), max(i, e[1])))
                        split_occurred = True
                        break
            if split_occurred:
                break
        if not split_occurred:
            break

    edge_list = list(edges)
    for i in range(len(edge_list)):
        for j in range(i + 1, len(edge_list)):
            e1 = edge_list[i]
            e2 = edge_list[j]
            if e1[0] in e2 or e1[1] in e2:
                continue
            if _lines_intersect(canon_points[e1[0]], canon_points[e1[1]], canon_points[e2[0]], canon_points[e2[1]]):
                raise CutterPartitionError("Proper crossing of segments detected in cutter arrangement")

    print(f"DEBUG: canon_points: {canon_points}")
    print(f"DEBUG: edges: {edges}")

    adj: dict[int, list[tuple[int, float]]] = {i: [] for i in range(len(canon_points))}
    for u_idx, v_idx in edges:
        dx1, dy1 = canon_points[v_idx][0] - canon_points[u_idx][0], canon_points[v_idx][1] - canon_points[u_idx][1]
        adj[u_idx].append((v_idx, math.atan2(dy1, dx1)))
        dx2, dy2 = canon_points[u_idx][0] - canon_points[v_idx][0], canon_points[u_idx][1] - canon_points[v_idx][1]
        adj[v_idx].append((u_idx, math.atan2(dy2, dx2)))
        
    for k in adj:
        adj[k].sort(key=lambda x: x[1])

    visited_half_edges = set()
    faces = []
    
    for u_idx in adj:
        for i in range(len(adj[u_idx])):
            v_idx = adj[u_idx][i][0]
            if (u_idx, v_idx) not in visited_half_edges:
                face = []
                curr_u = u_idx
                curr_v = v_idx
                while True:
                    face.append(curr_u)
                    visited_half_edges.add((curr_u, curr_v))
                    
                    next_idx = 0
                    for j, (out_v, out_angle) in enumerate(adj[curr_v]):
                        if out_v == curr_u:
                            next_idx = (j + 1) % len(adj[curr_v])
                            break
                            
                    next_u = curr_v
                    next_v = adj[curr_v][next_idx][0]
                    
                    if next_u == u_idx and next_v == v_idx:
                        break
                    curr_u = next_u
                    curr_v = next_v
                    
                    if len(face) > len(edges) * 2:
                        raise CutterPartitionError("Non-manifold local graph (infinite loop in face traversal)")
                        
                area = 0.0
                for j in range(len(face)):
                    p_a = canon_points[face[j]]
                    p_b = canon_points[face[(j+1)%len(face)]]
                    area += p_a[0] * p_b[1] - p_b[0] * p_a[1]
                    
                if area < -1e-8:
                    face.reverse()
                    faces.append(face)

    if not faces:
        raise CutterPartitionError("No bounded faces reconstructed")
        
    new_tris_3d = []
    for face in faces:
        poly = list(face)
        while len(poly) >= 3:
            if len(poly) == 3:
                new_tris_3d.append((
                    canon_3d[poly[0]],
                    canon_3d[poly[1]],
                    canon_3d[poly[2]]
                ))
                break
                
            ear_found = False
            for i in range(len(poly)):
                prev_i = poly[(i-1) % len(poly)]
                curr_i = poly[i]
                next_i = poly[(i+1) % len(poly)]
                
                p_prev = canon_points[prev_i]
                p_curr = canon_points[curr_i]
                p_next = canon_points[next_i]
                
                cross = (p_curr[0] - p_prev[0]) * (p_next[1] - p_curr[1]) - (p_curr[1] - p_prev[1]) * (p_next[0] - p_curr[0])
                if cross > 1e-8:
                    is_ear = True
                    for j in range(len(poly)):
                        if j == (i-1)%len(poly) or j == i or j == (i+1)%len(poly):
                            continue
                        p_test = canon_points[poly[j]]
                        d1 = _det2d(p_prev, p_curr, p_test)
                        d2 = _det2d(p_curr, p_next, p_test)
                        d3 = _det2d(p_next, p_prev, p_test)
                        if (d1 > -1e-8 and d2 > -1e-8 and d3 > -1e-8) or (d1 < 1e-8 and d2 < 1e-8 and d3 < 1e-8):
                            is_ear = False
                            break
                    if is_ear:
                        new_tris_3d.append((
                            canon_3d[prev_i],
                            canon_3d[curr_i],
                            canon_3d[next_i]
                        ))
                        poly.pop(i)
                        ear_found = True
                        break
            if not ear_found:
                raise CutterPartitionError("Failed to triangulate face (no ear found)")

    new_barrier_segs = []
    for u_idx, v_idx in barrier_edges:
        new_barrier_segs.append(ProvenancedIntersectionSegment(
            target_triangle_index=0,
            cutter_triangle_index=0,
            start=canon_3d[u_idx],
            end=canon_3d[v_idx]
        ))

    return new_tris_3d, new_barrier_segs
