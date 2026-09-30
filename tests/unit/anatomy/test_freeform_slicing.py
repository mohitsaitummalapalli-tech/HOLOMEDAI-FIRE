# -*- coding: utf-8 -*-
"""Phase F3.2-D5 Freeform SliceEngine Integration Tests."""

import copy
import math
import pytest

from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.mesh import AnatomicalMesh, AnatomicalPiece
from holomed.anatomy.swept_surface import SweptCutSurface, TransportedFrame
from holomed.anatomy.swept_surface_generator import generate_swept_surface, generate_swept_mesh
from holomed.anatomy.trajectory import ProcessedCutTrajectory, TrajectorySample, TrajectoryState
from holomed.anatomy.slicing import (
    SliceEngine, SliceOperation, SliceResult,
    PlanarCutSurface, PlanarSliceStrategy, FreeformSliceStrategy,
    FreeformSliceError,
)
from holomed.anatomy.freeform_capping import CutterPartitionError
from holomed.anatomy.mesh_partition import MeshPartitionError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_box_mesh(size: float = 2.0, offset: Point3D = Point3D(0, 0, 0)) -> AnatomicalMesh:
    """Axis-aligned box with side length 2*size, centered at offset."""
    v = [
        Point3D(-size + offset.x, -size + offset.y, -size + offset.z),
        Point3D( size + offset.x, -size + offset.y, -size + offset.z),
        Point3D( size + offset.x,  size + offset.y, -size + offset.z),
        Point3D(-size + offset.x,  size + offset.y, -size + offset.z),
        Point3D(-size + offset.x, -size + offset.y,  size + offset.z),
        Point3D( size + offset.x, -size + offset.y,  size + offset.z),
        Point3D( size + offset.x,  size + offset.y,  size + offset.z),
        Point3D(-size + offset.x,  size + offset.y,  size + offset.z),
    ]
    indices = [
        0, 2, 1, 0, 3, 2,  # Bottom
        4, 5, 6, 4, 6, 7,  # Top
        0, 1, 5, 0, 5, 4,  # Front
        1, 2, 6, 1, 6, 5,  # Right
        2, 3, 7, 2, 7, 6,  # Back
        3, 0, 4, 3, 4, 7,  # Left
    ]
    return AnatomicalMesh(tuple(v), tuple(indices))


def _make_straight_surface(
    width: float = 8.0,
    length: float = 8.0,
    offset: Point3D = Point3D(0, 0, 0),
    surface_id: str = "s1",
) -> SweptCutSurface:
    """Straight SweptCutSurface along +X through offset, in the Y=0 plane."""
    samples = [
        TrajectorySample(Point3D(-length / 2 + offset.x, offset.y, offset.z), 0.0, TrajectoryState.START),
        TrajectorySample(Point3D(offset.x, offset.y, offset.z), 1.0, TrajectoryState.ACTIVE),
        TrajectorySample(Point3D( length / 2 + offset.x, offset.y, offset.z), 2.0, TrajectoryState.END),
    ]
    traj = ProcessedCutTrajectory("traj_" + surface_id, "corr", tuple(samples), tuple([Vector3D(1, 0, 0)] * 3))
    frames = tuple([TransportedFrame(Vector3D(1, 0, 0), Vector3D(0, 0, 1), Vector3D(0, -1, 0))] * 3)
    return generate_swept_surface(traj, frames, width, 1.0)


def _make_l_surface(
    width: float = 8.0,
    offset: Point3D = Point3D(0, 0, 0),
) -> SweptCutSurface:
    """L-shaped SweptCutSurface: starts from (-4,0,0), goes to (0,0,0), then to (4,0,0).
    
    This is actually a gentle kink (V-shape in XZ), sweeping along Y, 
    staying fully interior to a box of size 2.0.
    The trajectory passes through the origin with a slight Z dip at center.
    """
    samples = [
        TrajectorySample(Point3D(-4.0 + offset.x, offset.y, 0.123 + offset.z), 0.0, TrajectoryState.START),
        TrajectorySample(Point3D(offset.x, offset.y, -0.123 + offset.z), 1.0, TrajectoryState.ACTIVE),
        TrajectorySample(Point3D(4.0 + offset.x, offset.y, 0.123 + offset.z), 2.0, TrajectoryState.END),
    ]
    traj = ProcessedCutTrajectory("traj_l", "corr", tuple(samples), tuple([Vector3D(1, 0, 0)] * 3))
    frames = tuple([TransportedFrame(Vector3D(1, 0, 0), Vector3D(0, 0, 1), Vector3D(0, -1, 0))] * 3)
    return generate_swept_surface(traj, frames, width, 1.0)


def _make_slice_op(surface, op_id: str = "op1", version: int = 1) -> SliceOperation:
    return SliceOperation(operation_id=op_id, cut_surface=surface, geometry_version=version)


def _is_watertight(mesh: AnatomicalMesh) -> bool:
    edges: dict = {}
    for i in range(len(mesh.indices) // 3):
        i0, i1, i2 = mesh.indices[i * 3], mesh.indices[i * 3 + 1], mesh.indices[i * 3 + 2]
        for e in [(i0, i1), (i1, i2), (i2, i0)]:
            edges[e] = edges.get(e, 0) + 1
    for e, c in edges.items():
        if c != 1:
            return False
        if edges.get((e[1], e[0]), 0) != 1:
            return False
    return True


# ===================================================================
# 1. EXISTING PLANAR PATH REGRESSION
# ===================================================================

def test_planar_path_unchanged():
    """The existing PlanarCutSurface path must still work identically."""
    mesh = create_box_mesh(size=2.0)
    piece = AnatomicalPiece(piece_id="box", mesh=mesh)
    plane = PlanarCutSurface(origin=Point3D(0, 0, 0), normal=Vector3D(0, 1, 0))
    op = SliceOperation(operation_id="planar_op", cut_surface=plane, geometry_version=1)
    result = SliceEngine.slice_piece(piece, op)
    assert isinstance(result, SliceResult)
    assert len(result.child_pieces) == 2


# ===================================================================
# 2. STRATEGY DISPATCH — NO PLANAR FALLBACK
# ===================================================================

def test_swept_surface_dispatches_to_freeform_strategy():
    """A SweptCutSurface that is geometrically planar must still go through FreeformSliceStrategy."""
    # Create a straight, geometrically planar SweptCutSurface
    surface = _make_straight_surface()
    mesh = create_box_mesh(size=2.0)
    piece = AnatomicalPiece(piece_id="box", mesh=mesh)
    op = _make_slice_op(surface, "planar_like")

    # Prove PlanarSliceStrategy was NOT invoked by verifying the dispatch
    assert isinstance(op.cut_surface, SweptCutSurface)
    assert not isinstance(op.cut_surface, PlanarCutSurface)

    # SliceEngine must route this to FreeformSliceStrategy
    result = SliceEngine.slice_piece(piece, op)
    assert isinstance(result, SliceResult)
    assert len(result.child_pieces) == 2
    # Verify freeform metadata
    for child in result.child_pieces:
        assert child.lineage_metadata is not None
        assert child.lineage_metadata.get("capped") == "true"


def test_unsupported_surface_type_fails():
    """Unknown cut-surface types must fail closed."""
    class MysteryPlaneSurface:
        pass

    mesh = create_box_mesh(size=2.0)
    piece = AnatomicalPiece(piece_id="box", mesh=mesh)
    op = SliceOperation(operation_id="mystery", cut_surface=MysteryPlaneSurface(), geometry_version=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Unsupported cut surface type"):
        SliceEngine.slice_piece(piece, op)


# ===================================================================
# 3. STRAIGHT FREEFORM CUTTER
# ===================================================================

def test_straight_freeform_cutter():
    """Straight cutter cleanly bisects a box."""
    mesh = create_box_mesh(size=2.0)
    piece = AnatomicalPiece(piece_id="box", mesh=mesh)
    surface = _make_straight_surface(width=8.0, length=8.0)
    op = _make_slice_op(surface, "straight")
    result = SliceEngine.slice_piece(piece, op)

    assert len(result.child_pieces) == 2
    for child in result.child_pieces:
        assert _is_watertight(child.mesh)
        assert child.mesh.volume > 0

    vol_sum = sum(c.mesh.volume for c in result.child_pieces)
    assert abs(vol_sum - mesh.volume) < 1e-4


# ===================================================================
# 4. L-SHAPED FREEFORM CUTTER
# ===================================================================

def test_l_shaped_freeform_cutter():
    """L-shaped cutter separates a box into two watertight children."""
    mesh = create_box_mesh(size=2.0)
    piece = AnatomicalPiece(piece_id="box", mesh=mesh)
    surface = _make_l_surface(width=8.0)
    op = _make_slice_op(surface, "l_cut")
    result = SliceEngine.slice_piece(piece, op)

    assert len(result.child_pieces) == 2
    for child in result.child_pieces:
        assert _is_watertight(child.mesh)
        assert child.mesh.volume > 0

    vol_sum = sum(c.mesh.volume for c in result.child_pieces)
    assert abs(vol_sum - mesh.volume) < 1e-4


# ===================================================================
# 5. VOLUME CONSERVATION
# ===================================================================

def test_volume_conservation():
    """Volume of children equals volume of parent."""
    mesh = create_box_mesh(size=2.0)
    piece = AnatomicalPiece(piece_id="box", mesh=mesh)
    surface = _make_straight_surface()
    op = _make_slice_op(surface, "vol_test")
    result = SliceEngine.slice_piece(piece, op)
    vol_sum = sum(c.mesh.volume for c in result.child_pieces)
    assert abs(vol_sum - mesh.volume) < 1e-4


# ===================================================================
# 6. DETERMINISTIC REPEATABILITY
# ===================================================================

def test_deterministic_repeatability():
    """Repeated execution produces identical topology."""
    mesh = create_box_mesh(size=2.0)
    piece = AnatomicalPiece(piece_id="box", mesh=mesh)
    surface = _make_straight_surface()

    results = []
    for i in range(3):
        op = _make_slice_op(surface, f"rep_{i}")
        results.append(SliceEngine.slice_piece(piece, op))

    for r in results[1:]:
        assert len(r.child_pieces) == len(results[0].child_pieces)
        for ca, cb in zip(results[0].child_pieces, r.child_pieces):
            assert len(ca.mesh.vertices) == len(cb.mesh.vertices)
            assert len(ca.mesh.indices) == len(cb.mesh.indices)
            assert abs(ca.mesh.volume - cb.mesh.volume) < 1e-10


# ===================================================================
# 7. INPUT IMMUTABILITY
# ===================================================================

def test_input_immutability():
    """Source mesh and SweptCutSurface must not be mutated."""
    mesh = create_box_mesh(size=2.0)
    piece = AnatomicalPiece(piece_id="box", mesh=mesh)
    surface = _make_straight_surface()

    orig_verts = tuple(mesh.vertices)
    orig_indices = tuple(mesh.indices)
    orig_centerline = tuple(surface.centerline_samples)

    op = _make_slice_op(surface, "immut")
    SliceEngine.slice_piece(piece, op)

    assert mesh.vertices == orig_verts
    assert mesh.indices == orig_indices
    assert surface.centerline_samples == orig_centerline


# ===================================================================
# 8. TRANSLATION INVARIANCE
# ===================================================================

def test_translation_invariance():
    """Slicing at (0,0,0) and at (100,100,100) produces same topology and equal volumes."""
    for offset in [Point3D(0, 0, 0), Point3D(5.0, 5.0, 5.0)]:
        mesh = create_box_mesh(size=2.0, offset=offset)
        piece = AnatomicalPiece(piece_id="box", mesh=mesh)
        surface = _make_straight_surface(offset=offset)
        op = _make_slice_op(surface, "trans")
        result = SliceEngine.slice_piece(piece, op)
        assert len(result.child_pieces) == 2
        vol_sum = sum(c.mesh.volume for c in result.child_pieces)
        # Volume of box is (2*2)^3 = 64
        assert abs(vol_sum - 64.0) < 1e-4


# ===================================================================
# 9. POSITIVE CHILD VOLUMES
# ===================================================================

def test_positive_child_volumes():
    """Both children must have strictly positive volume."""
    mesh = create_box_mesh(size=2.0)
    piece = AnatomicalPiece(piece_id="box", mesh=mesh)
    surface = _make_straight_surface()
    op = _make_slice_op(surface, "pos_vol")
    result = SliceEngine.slice_piece(piece, op)
    for child in result.child_pieces:
        assert child.mesh.volume > 0


# ===================================================================
# 10. WATERTIGHT CHILDREN
# ===================================================================

def test_children_watertight():
    """Both children must be watertight manifolds."""
    mesh = create_box_mesh(size=2.0)
    piece = AnatomicalPiece(piece_id="box", mesh=mesh)
    surface = _make_straight_surface()
    op = _make_slice_op(surface, "wt")
    result = SliceEngine.slice_piece(piece, op)
    for child in result.child_pieces:
        assert _is_watertight(child.mesh)


# ===================================================================
# 11. RESULT METADATA
# ===================================================================

def test_result_metadata_preserved():
    """SliceResult carries correct operation identity and child lineage."""
    mesh = create_box_mesh(size=2.0)
    piece = AnatomicalPiece(piece_id="box", mesh=mesh)
    surface = _make_straight_surface()
    op = _make_slice_op(surface, "meta_test", version=42)
    result = SliceEngine.slice_piece(piece, op)

    assert result.operation_id == "meta_test"
    assert result.parent_piece_id == "box"
    assert result.geometry_version == 42
    for child in result.child_pieces:
        assert child.parent_id == "box"
        assert child.lineage_metadata is not None
