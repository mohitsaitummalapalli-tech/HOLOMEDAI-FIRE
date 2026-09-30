# HOLOMEDAI — PHASE F3.2-D5 FREEFORM SLICEENGINE INTEGRATION ARCHITECTURE

## 1. STRATEGY SELECTION
`SliceEngine` is the orchestration layer. It remains unchanged in its authority model. Dispatching is handled deterministically via strict type introspection of the `cut_surface` attached to the `SliceOperation`:
- `isinstance(operation.cut_surface, PlanarCutSurface)` -> `PlanarSliceStrategy`
- `isinstance(operation.cut_surface, SweptCutSurface)` -> `FreeformSliceStrategy` (New)

No implicit planarization or fallback is permitted. If `FreeformSliceStrategy` fails, the exception propagates up and the operation halts.

## 2. API CHANGES
The existing `SliceEngine.slice_piece(piece: AnatomicalPiece, operation: SliceOperation)` signature remains completely intact. No API arguments are modified.
A new internal class `FreeformSliceStrategy` will be added to `slicing.py` implementing the `slice()` method. It returns standard `SliceResult` objects, maintaining complete transparency to downstream consumers. A new exception `FreeformSliceError` may be introduced to wrap orchestration-level assertions.

## 3. CALL GRAPH
The deterministic sequence within `FreeformSliceStrategy.slice()`:
1. **Cutter Generation**: `cutter_mesh = operation.cut_surface.generate_mesh()`
2. **Intersection**: `intersections = compute_intersections_with_provenance(piece.mesh, cutter_mesh)`
3. **Partitioning**: `children = TargetMeshPartitioner.partition(piece.mesh, piece.piece_id, operation.operation_id, intersections)`
4. **Patch Extraction**: `cutter_patch = extract_cutter_patch(cutter_mesh, intersections)`
5. **Grafting A**: `child_A_capped = cap_partitioned_mesh(children[0], cutter_patch)`
6. **Grafting B**: `child_B_capped = cap_partitioned_mesh(children[1], cutter_patch)`
7. **Assertion**: Verify `volume(child_A_capped) + volume(child_B_capped) ≈ volume(piece.mesh)`
8. **Serialization**: Return `SliceResult(child_pieces=(child_A_capped, child_B_capped), ...)`

## 4. FAILURE SEMANTICS
- **Fail Closed**: Any failure halts the pipeline immediately and raises an exception.
- **D2 Failures**: Coplanar overlaps, degenerate vertices are handled natively by D2 tolerances.
- **D3 Failures**: If the boundary loop does not fully partition the mesh (e.g. cut is a surface scratch or incomplete), `MeshPartitionError` is raised.
- **D4 Failures**: If the intersection graph reaches the boundary of the `SweptCutSurface`, or patch extraction is ambiguous, `CutterPartitionError` is raised.
- **D5 Orchestration Failures**: If the final combined volume deviates from the parent volume beyond numeric tolerance, `FreeformSliceError` is raised.

## 5. UNSUPPORTED CASES
Unsupported topologies explicitly reject processing (fail closed):
- Target meshes with multiple disconnected components.
- Self-intersecting cutting trajectories.
- Trajectories that do not fully span the target volume (resulting in non-separating cuts).
- Partial cuts that strike the outer rim of the cutter mesh.
- Degenerate intersection topologies (e.g., matching edges exact overlap) beyond the mathematical handling currently present in D2.

## 6. RESULT CONTRACT
- **Output Type**: `SliceResult`
- **Children**: Exactly two `AnatomicalPiece` elements.
- **Topological Integrity**: Both returned children are CLOSED, WATERTIGHT, and MANIFOLD.
- **Volume**: Both children have positive volume.
- **Metadata**: `lineage_metadata` reflects `{"operation_id": <op_id>, "capped": "true", "side": "A|B"}`. The operation identity remains faithfully preserved.

## 7. DETERMINISM
All steps are strictly deterministic:
- Cutter generation builds vertex lists and triangle indices sequentially.
- Intersection dictionary sorting ensures provenance ordering.
- BFS traversal in D3 and D4 follows sorted triangle/edge neighbors.
- Child piece IDs are deterministically seeded from the parent ID + operation ID.
- Child ordering in the `SliceResult` tuple is predictably assigned (e.g., A then B).

## 8. IMMUTABILITY
The `FreeformSliceStrategy` treats the following as strictly read-only:
- `SliceOperation` (including trajectory and sweeping profile).
- `SweptCutSurface`.
- `AnatomicalPiece` (source target mesh).
- D2, D3, and D4 outputs (intersections, target partitions, patches). 
No modifications are made in-place. All processing creates new, independent data structures.

## 9. REGRESSION STRATEGY
- **Planar Preservation**: The original `PlanarSliceStrategy` path remains completely untouched.
- **Historical Suite**: The entire historical test suite must run and remain green without modification.
- **New Coverage**: `test_freeform_slicing.py` will introduce explicit unit tests targeting the new call graph:
  - Freeform straight trajectory
  - Freeform L trajectory
  - Curved planar trajectory
  - Curved non-planar trajectory
  - Explicit rejection conditions (incomplete cut, degenerate boundaries, boundary exhaustion).
  - Translation and non-trivial 3D rotation invariance of the full orchestrated pipeline.

## 10. BENCHMARK PLAN
Create `benchmarks/benchmark_f3_2_d5.py` to test end-to-end performance.
- **Test Assets**: 3,000-triangle cylindrical target + 300-triangle swept cutter.
- **Metrics Tracked**:
  - `t_cutter`: `SweptCutSurface` mesh generation time.
  - `t_d2`: Intersection compute time.
  - `t_d3`: Target partition time.
  - `t_d4`: Patch extraction and capping time.
  - `t_total`: Total orchestration time.
- **Execution**: 10 warmup cycles, 100 timed repetitions. 

## 11. EXACT INTEGRATION BOUNDARIES
The boundary is purely at `SliceEngine.slice_piece`. No external callers of `SliceEngine` are modified. Internal implementations within D2/D3/D4 are completely opaque to the `SliceEngine`, which relies strictly on their public API methods.
