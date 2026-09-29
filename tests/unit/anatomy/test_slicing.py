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
