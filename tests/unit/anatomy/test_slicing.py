import pytest
import math
from holomed.anatomy.geometry import Point3D, Vector3D
from holomed.anatomy.mesh import AnatomicalMesh, AnatomicalPiece
from holomed.anatomy.slicing import PlanarCutSurface, SliceOperation, SliceEngine

def create_cube_mesh() -> AnatomicalMesh:
    # A simple 1x1x1 cube from -0.5 to 0.5
    v = [
        Point3D(-0.5, -0.5, -0.5), Point3D(0.5, -0.5, -0.5),
        Point3D(0.5, 0.5, -0.5), Point3D(-0.5, 0.5, -0.5),
        Point3D(-0.5, -0.5, 0.5), Point3D(0.5, -0.5, 0.5),
        Point3D(0.5, 0.5, 0.5), Point3D(-0.5, 0.5, 0.5)
    ]
    # 12 triangles
    idx = [
        0, 2, 1, 0, 3, 2, # back
        4, 5, 6, 4, 6, 7, # front
        0, 1, 5, 0, 5, 4, # bottom
        2, 3, 7, 2, 7, 6, # top
        0, 4, 7, 0, 7, 3, # left
        1, 2, 6, 1, 6, 5  # right
    ]
    return AnatomicalMesh(vertices=tuple(v), indices=tuple(idx))

def test_slice_engine_cube_middle():
    mesh = create_cube_mesh()
    piece = AnatomicalPiece(
        piece_id="cube_1",
        mesh=mesh,
        parent_id=None,
        lineage_metadata={}
    )
    # Cut through middle of Z axis
    cut = PlanarCutSurface(origin=Point3D(0, 0, 0), normal=Vector3D(0, 0, 1))
    op = SliceOperation(
        operation_id="cut_1",
        cut_surface=cut,
        geometry_version=1
    )

    result = SliceEngine.slice_piece(piece, op)
    child_pieces = result.child_pieces
    assert child_pieces is not None
    assert len(child_pieces) == 2

    f, b = child_pieces[0], child_pieces[1] # type: ignore
    assert f.piece_id == "cube_1_cut_1_f"
    assert b.piece_id == "cube_1_cut_1_b"
    assert f.lineage_metadata is not None and b.lineage_metadata is not None
    assert f.lineage_metadata["side"] == "front"
    assert b.lineage_metadata["side"] == "back"

    for pt in f.mesh.vertices:
        assert pt.z >= -1e-6
    for pt in b.mesh.vertices:
        assert pt.z <= 1e-6

def test_slice_engine_miss():
    mesh = create_cube_mesh()
    piece = AnatomicalPiece(
        piece_id="cube_1",
        mesh=mesh,
        parent_id=None,
        lineage_metadata={}
    )
    # Cut way outside
    cut = PlanarCutSurface(origin=Point3D(0, 0, 2.0), normal=Vector3D(0, 0, 1))
    op = SliceOperation(
        operation_id="cut_2",
        cut_surface=cut,
        geometry_version=1
    )

    result = SliceEngine.slice_piece(piece, op)
    child_pieces = result.child_pieces
    assert child_pieces is not None
    assert len(child_pieces) == 1

    b = child_pieces[0] # type: ignore
    assert b.lineage_metadata is not None
    assert b.lineage_metadata["side"] == "back"
    # Same number of triangles
    assert len(b.mesh.indices) == len(mesh.indices)

def test_slice_engine_diagonal():
    mesh = create_cube_mesh()
    piece = AnatomicalPiece(
        piece_id="cube_1",
        mesh=mesh,
        parent_id=None,
        lineage_metadata={}
    )
    # Diagonal cut
    nx = 1 / math.sqrt(2)
    ny = 1 / math.sqrt(2)
    cut = PlanarCutSurface(origin=Point3D(0, 0, 0), normal=Vector3D(nx, ny, 0))
    op = SliceOperation(
        operation_id="cut_diag",
        cut_surface=cut,
        geometry_version=1
    )

    result = SliceEngine.slice_piece(piece, op)
    child_pieces = result.child_pieces
    assert child_pieces is not None
    assert len(child_pieces) == 2
    f, b = child_pieces[0], child_pieces[1] # type: ignore

    for pt in f.mesh.vertices:
        d = pt.x * nx + pt.y * ny
        assert d >= -1e-6

    for pt in b.mesh.vertices:
        d = pt.x * nx + pt.y * ny
        assert d <= 1e-6

from holomed.anatomy.exceptions import AnatomyValidationError

def test_volume_conservation():
    mesh = create_cube_mesh()
    piece = AnatomicalPiece(piece_id="c1", mesh=mesh, parent_id=None, lineage_metadata={})
    cut = PlanarCutSurface(origin=Point3D(0, 0, 0), normal=Vector3D(1, 1, 1))
    op = SliceOperation("cut_1", cut, 1)

    def volume(m: AnatomicalMesh) -> float:
        v = 0.0
        for i in range(0, len(m.indices), 3):
            p0 = m.vertices[m.indices[i]]
            p1 = m.vertices[m.indices[i+1]]
            p2 = m.vertices[m.indices[i+2]]
            v += p0.x * (p1.y*p2.z - p2.y*p1.z) + p0.y * (p1.z*p2.x - p2.z*p1.x) + p0.z * (p1.x*p2.y - p2.x*p1.y)
        return abs(v / 6.0)

    orig_vol = volume(mesh)
    res = SliceEngine.slice_piece(piece, op)
    assert len(res.child_pieces) == 2
    f_vol = volume(res.child_pieces[0].mesh)
    b_vol = volume(res.child_pieces[1].mesh)
    assert math.isclose(orig_vol, f_vol + b_vol, rel_tol=1e-5)

def assert_watertight_boundary_edges(mesh: AnatomicalMesh):
    edges = {}
    for i in range(0, len(mesh.indices), 3):
        i0, i1, i2 = mesh.indices[i], mesh.indices[i+1], mesh.indices[i+2]
        for e in [(i0, i1), (i1, i2), (i2, i0)]:
            edges[e] = edges.get(e, 0) + 1

    for (u, v), count in edges.items():
        assert count == 1, f"Edge ({u}, {v}) seen {count} times, expected 1"
        assert (v, u) in edges, f"Opposite edge ({v}, {u}) not found for ({u}, {v})"
        assert edges[(v, u)] == 1, f"Opposite edge ({v}, {u}) seen {edges[(v, u)]} times, expected 1"

def test_watertight_topology_validation():
    # Tests that the output meshes are watertight boundary-edge wise, and genus-0 Euler wise.
    # Note: F2 only currently supports single connected closed genus-0 input meshes.
    mesh = create_cube_mesh()
    piece = AnatomicalPiece(piece_id="c1", mesh=mesh, parent_id=None, lineage_metadata={})
    cut = PlanarCutSurface(origin=Point3D(0, 0, 0), normal=Vector3D(0.5, 0.5, 1.0))
    res = SliceEngine.slice_piece(piece, SliceOperation("cut_1", cut, 1))

    assert len(res.child_pieces) == 2
    for child in res.child_pieces:
        # 1. Boundary Edge Analysis
        assert_watertight_boundary_edges(child.mesh)

        # 2. Euler characteristic for single connected closed genus-0 mesh: V - E + F = 2
        V = len(child.mesh.vertices)
        F = len(child.mesh.indices) // 3
        # Unique undirected edges
        edges = set()
        for i in range(0, len(child.mesh.indices), 3):
            i0, i1, i2 = child.mesh.indices[i], child.mesh.indices[i+1], child.mesh.indices[i+2]
            edges.add(tuple(sorted((i0, i1))))
            edges.add(tuple(sorted((i1, i2))))
            edges.add(tuple(sorted((i2, i0))))
        E = len(edges)

        assert V - E + F == 2, f"Euler characteristic failed for piece {child.piece_id}. V={V}, E={E}, F={F}"

def test_watertight_boundary_edges():
    # Dedicated test alias to fulfill the requested boundary-edge analysis.
    test_watertight_topology_validation()

def test_intersection_vertex_welding():
    # Two neighboring triangles sharing an edge crossed by the plane
    v = [
        Point3D(0, 0, 1), Point3D(1, 0, -1),
        Point3D(0, 1, 1), Point3D(-1, 0, -1)
    ]
    idx = [0, 1, 2, 0, 2, 3] # (0, 2) is the shared edge, crosses z=0
    mesh = AnatomicalMesh(tuple(v), tuple(idx))
    piece = AnatomicalPiece("p1", mesh, None, {})
    cut = PlanarCutSurface(origin=Point3D(0, 0, 0), normal=Vector3D(0, 0, 1))
    res = SliceEngine.slice_piece(piece, SliceOperation("op1", cut, 1))

    # We should have one exact intersection on edge (0,2) at (0, 0.5, 0)
    # The front pieces should share this exact vertex
    assert res.child_pieces[0].lineage_metadata is not None
    f_mesh = res.child_pieces[0].mesh if res.child_pieces[0].lineage_metadata["side"] == "front" else res.child_pieces[1].mesh
    # Verify no duplicate vertices
    unique_verts = set((round(pt.x, 6), round(pt.y, 6), round(pt.z, 6)) for pt in f_mesh.vertices)
    assert len(unique_verts) == len(f_mesh.vertices)

def test_intersection_vertex_welding_index():
    # Explicit adversarial shared-edge test
    # triangle A and triangle B share the same original edge that crosses the cutting surface.
    # v0 = (0, 0, 1), v1 = (1, 0, 1), v2 = (0, 0, -1), v3 = (-1, 0, 1)
    # Triangles: (0, 1, 2) and (0, 3, 2). Shared edge: (0, 2) which crosses z=0 at (0,0,0).
    v = [
        Point3D(0, 0, 1), Point3D(1, 0, 1),
        Point3D(0, 0, -1), Point3D(-1, 0, 1)
    ]
    idx = [0, 1, 2, 0, 3, 2]
    mesh = AnatomicalMesh(tuple(v), tuple(idx))
    piece = AnatomicalPiece("p1", mesh, None, {})
    cut = PlanarCutSurface(origin=Point3D(0, 0, 0), normal=Vector3D(0, 0, 1))
    res = SliceEngine.slice_piece(piece, SliceOperation("op1", cut, 1))

    assert res.child_pieces[0].lineage_metadata is not None
    front = res.child_pieces[0].mesh if res.child_pieces[0].lineage_metadata["side"] == "front" else res.child_pieces[1].mesh

    # We expect front piece to contain the intersection vertex (0, 0, 0).
    # Since triangles A and B share edge (0,2), they should both reference the EXACT SAME index
    # for the intersection vertex.
    intersection_idx = None
    intersection_count = 0
    for i, pt in enumerate(front.vertices):
        if math.isclose(pt.x, 0, abs_tol=1e-5) and math.isclose(pt.y, 0, abs_tol=1e-5) and math.isclose(pt.z, 0, abs_tol=1e-5):
            intersection_idx = i
            intersection_count += 1

    assert intersection_count == 1, "There should be EXACTLY ONE generated intersection vertex for the edge."

    # Check that this index is used by triangles in the front mesh.
    # Specifically, front mesh will have cut triangles corresponding to the front portions of original A and B.
    # Original A (0,1,2) front portion connects v0, intersection(0,1), intersection(1,2).
    # Wait, (0,2) is the shared edge, so intersection(0,2) is the shared vertex!
    # Yes, both new front triangles must use `intersection_idx`.
    usage_count = front.indices.count(intersection_idx)
    assert usage_count >= 2, "Both child triangle sets must reference the same vertex index."

def test_cap_failure_fails_closed(monkeypatch):
    mesh = create_cube_mesh()
    piece = AnatomicalPiece("c1", mesh, None, {})
    cut = PlanarCutSurface(origin=Point3D(0, 0, 0), normal=Vector3D(0, 0, 1))

    def fake_triangulate(*args, **kwargs):
        raise AnatomyValidationError("Forced failure")

    import holomed.anatomy.slicing
    monkeypatch.setattr(holomed.anatomy.slicing, "_triangulate_polygon", fake_triangulate)

    with pytest.raises(AnatomyValidationError, match="Forced failure"):
        SliceEngine.slice_piece(piece, SliceOperation("op1", cut, 1))

def test_edge_case_plane_through_center():
    mesh = create_cube_mesh()
    cut = PlanarCutSurface(Point3D(0,0,0), Vector3D(1,0,0))
    res = SliceEngine.slice_piece(AnatomicalPiece("c", mesh, None, {}), SliceOperation("op", cut, 1))
    assert len(res.child_pieces) == 2

def test_edge_case_plane_near_boundary():
    mesh = create_cube_mesh()
    cut = PlanarCutSurface(Point3D(0.499,0,0), Vector3D(1,0,0))
    res = SliceEngine.slice_piece(AnatomicalPiece("c", mesh, None, {}), SliceOperation("op", cut, 1))
    assert len(res.child_pieces) == 2

def test_edge_case_plane_outside_mesh_front():
    mesh = create_cube_mesh()
    cut = PlanarCutSurface(Point3D(2,0,0), Vector3D(1,0,0))
    res = SliceEngine.slice_piece(AnatomicalPiece("c", mesh, None, {}), SliceOperation("op", cut, 1))
    assert len(res.child_pieces) == 1
    assert res.child_pieces[0].lineage_metadata is not None
    assert res.child_pieces[0].lineage_metadata["side"] == "back"

def test_edge_case_plane_outside_mesh_back():
    mesh = create_cube_mesh()
    cut = PlanarCutSurface(Point3D(-2,0,0), Vector3D(1,0,0))
    res = SliceEngine.slice_piece(AnatomicalPiece("c", mesh, None, {}), SliceOperation("op", cut, 1))
    assert len(res.child_pieces) == 1
    assert res.child_pieces[0].lineage_metadata is not None
    assert res.child_pieces[0].lineage_metadata["side"] == "front"

def test_edge_case_tangent():
    mesh = create_cube_mesh()
    cut = PlanarCutSurface(Point3D(0.5,0,0), Vector3D(1,0,0))
    res = SliceEngine.slice_piece(AnatomicalPiece("c", mesh, None, {}), SliceOperation("op", cut, 1))
    assert len(res.child_pieces) == 1
    assert res.child_pieces[0].lineage_metadata is not None
    assert res.child_pieces[0].lineage_metadata["side"] == "back"

def test_edge_case_vertex_exactly_on_plane():
    mesh = create_cube_mesh()
    # Cube corners are at +/- 0.5. Plane through (0.5, 0.5, 0.5) with normal (1, 1, 1) touches exactly one vertex
    cut = PlanarCutSurface(Point3D(0.5, 0.5, 0.5), Vector3D(1,1,1))
    res = SliceEngine.slice_piece(AnatomicalPiece("c", mesh, None, {}), SliceOperation("op", cut, 1))
    assert len(res.child_pieces) == 1
    assert res.child_pieces[0].lineage_metadata is not None
    assert res.child_pieces[0].lineage_metadata["side"] == "back"

def test_edge_case_edge_exactly_on_plane():
    mesh = create_cube_mesh()
    # Edge is along z-axis at x=0.5, y=0.5. Plane with normal (1,1,0) at (0.5, 0.5, 0)
    cut = PlanarCutSurface(Point3D(0.5, 0.5, 0), Vector3D(1,1,0))
    res = SliceEngine.slice_piece(AnatomicalPiece("c", mesh, None, {}), SliceOperation("op", cut, 1))
    assert len(res.child_pieces) == 1
    assert res.child_pieces[0].lineage_metadata is not None
    assert res.child_pieces[0].lineage_metadata["side"] == "back"

def test_edge_case_coplanar_triangle():
    mesh = create_cube_mesh() # Face at z=0.5
    cut = PlanarCutSurface(Point3D(0, 0, 0.5), Vector3D(0,0,1))
    res = SliceEngine.slice_piece(AnatomicalPiece("c", mesh, None, {}), SliceOperation("op", cut, 1))
    assert len(res.child_pieces) == 1
    assert res.child_pieces[0].lineage_metadata is not None
    assert res.child_pieces[0].lineage_metadata["side"] == "back"

def test_edge_case_zero_length_normal():
    with pytest.raises(AnatomyValidationError):
        PlanarCutSurface(Point3D(0,0,0), Vector3D(0,0,0))

def test_edge_case_nan_inf():
    with pytest.raises(AnatomyValidationError):
        Point3D(math.nan, 0, 0)
    with pytest.raises(AnatomyValidationError):
        Vector3D(math.inf, 0, 0)

def test_preserve_input_immutability():
    mesh = create_cube_mesh()
    piece = AnatomicalPiece("c1", mesh, None, {})

    import copy
    mesh_copy = copy.deepcopy(mesh)

    cut = PlanarCutSurface(Point3D(0,0,0), Vector3D(1,1,1))
    SliceEngine.slice_piece(piece, SliceOperation("op", cut, 1))

    assert len(mesh.vertices) == len(mesh_copy.vertices)
    assert mesh.indices == mesh_copy.indices
    for i in range(len(mesh.vertices)):
        assert mesh.vertices[i].x == mesh_copy.vertices[i].x
        assert mesh.vertices[i].y == mesh_copy.vertices[i].y
        assert mesh.vertices[i].z == mesh_copy.vertices[i].z

def meshes_are_equal(m1: AnatomicalMesh, m2: AnatomicalMesh, tol=1e-5):
    if len(m1.vertices) != len(m2.vertices) or len(m1.indices) != len(m2.indices):
        return False
    for i in range(len(m1.vertices)):
        if not math.isclose(m1.vertices[i].x, m2.vertices[i].x, abs_tol=tol): return False
        if not math.isclose(m1.vertices[i].y, m2.vertices[i].y, abs_tol=tol): return False
        if not math.isclose(m1.vertices[i].z, m2.vertices[i].z, abs_tol=tol): return False
    if list(m1.indices) != list(m2.indices): return False
    return True

def test_geometric_determinism():
    mesh = create_cube_mesh()
    piece1 = AnatomicalPiece("c1", mesh, None, {})
    piece2 = AnatomicalPiece("c2", mesh, None, {})

    cut = PlanarCutSurface(Point3D(0.1, 0.2, 0.3), Vector3D(1, 2, 3))
    cut_diff = PlanarCutSurface(Point3D(0.2, -0.1, 0.3), Vector3D(1, 0, 0))

    res1 = SliceEngine.slice_piece(piece1, SliceOperation("op1", cut, 1))
    res2 = SliceEngine.slice_piece(piece2, SliceOperation("op2", cut, 1))
    res3 = SliceEngine.slice_piece(piece1, SliceOperation("op3", cut_diff, 1))

    assert len(res1.child_pieces) == 2
    assert len(res2.child_pieces) == 2

    assert meshes_are_equal(res1.child_pieces[0].mesh, res2.child_pieces[0].mesh)
    assert meshes_are_equal(res1.child_pieces[1].mesh, res2.child_pieces[1].mesh)

    # Prove that different cut surfaces result in different geometry
    assert not meshes_are_equal(res1.child_pieces[0].mesh, res3.child_pieces[0].mesh)
    assert not meshes_are_equal(res1.child_pieces[1].mesh, res3.child_pieces[1].mesh)
