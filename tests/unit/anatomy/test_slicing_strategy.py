import pytest

from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.mesh import AnatomicalMesh, AnatomicalPiece
from holomed.anatomy.slicing import (
    SliceEngine, SliceOperation, PlanarCutSurface, CutSurface,
    PlanarSliceStrategy
)
from holomed.anatomy.swept_surface import SweptCutSurface, TransportedFrame

def _create_cube_mesh() -> AnatomicalMesh:
    v = [
        Point3D(-1, -1, -1), Point3D(1, -1, -1), Point3D(1, 1, -1), Point3D(-1, 1, -1),
        Point3D(-1, -1, 1), Point3D(1, -1, 1), Point3D(1, 1, 1), Point3D(-1, 1, 1)
    ]
    # Simple cube indices (12 triangles)
    i = [
        0, 1, 2, 0, 2, 3, # bottom
        4, 6, 5, 4, 7, 6, # top
        0, 4, 5, 0, 5, 1, # front
        1, 5, 6, 1, 6, 2, # right
        2, 6, 7, 2, 7, 3, # back
        3, 7, 4, 3, 4, 0  # left
    ]
    return AnatomicalMesh(tuple(v), tuple(i))

@pytest.fixture
def cube_piece() -> AnatomicalPiece:
    return AnatomicalPiece(
        piece_id="cube_1",
        mesh=_create_cube_mesh(),
        parent_id=None,
        lineage_metadata={}
    )

def test_planar_cut_surface_dispatch(cube_piece):
    plane = PlanarCutSurface(Point3D(0, 0, 0), Vector3D(1, 0, 0))
    op = SliceOperation("op_1", plane, 1)

    result = SliceEngine.slice_piece(cube_piece, op)
    assert len(result.child_pieces) == 2
    assert result.child_pieces[0].piece_id == "cube_1_op_1_f"
    assert result.child_pieces[1].piece_id == "cube_1_op_1_b"

def test_existing_planar_result_regression(cube_piece):
    plane = PlanarCutSurface(Point3D(0, 0, 0), Vector3D(1, 0, 0))
    op = SliceOperation("op_1", plane, 1)

    # Direct strategy invocation (old logic equivalent)
    strategy = PlanarSliceStrategy()
    strategy_result = strategy.slice(cube_piece, op)

    # Engine invocation (new dispatch)
    engine_result = SliceEngine.slice_piece(cube_piece, op)

    # Check exact equality
    assert len(strategy_result.child_pieces) == len(engine_result.child_pieces)
    assert len(strategy_result.child_pieces) == 2

    c1_strat = strategy_result.child_pieces[0]
    c1_eng = engine_result.child_pieces[0]
    assert c1_strat.piece_id == c1_eng.piece_id
    assert c1_strat.mesh.vertices == c1_eng.mesh.vertices
    assert c1_strat.mesh.indices == c1_eng.mesh.indices

    c2_strat = strategy_result.child_pieces[1]
    c2_eng = engine_result.child_pieces[1]
    assert c2_strat.piece_id == c2_eng.piece_id
    assert c2_strat.mesh.vertices == c2_eng.mesh.vertices
    assert c2_strat.mesh.indices == c2_eng.mesh.indices


class FakeCutSurface(CutSurface):
    pass

def test_unsupported_type_explicit_failure(cube_piece):
    fake_surface = FakeCutSurface()
    op = SliceOperation("op_3", fake_surface, 1)

    with pytest.raises(TypeError) as exc:
        SliceEngine.slice_piece(cube_piece, op)
    assert "Unsupported cut surface type: FakeCutSurface" in str(exc.value)

def test_deterministic_dispatch_and_immutability(cube_piece):
    plane = PlanarCutSurface(Point3D(0, 0, 0), Vector3D(1, 0, 0))
    op = SliceOperation("op_immut", plane, 1)

    v_count = len(cube_piece.mesh.vertices)
    i_count = len(cube_piece.mesh.indices)

    SliceEngine.slice_piece(cube_piece, op)

    assert len(cube_piece.mesh.vertices) == v_count
    assert len(cube_piece.mesh.indices) == i_count
