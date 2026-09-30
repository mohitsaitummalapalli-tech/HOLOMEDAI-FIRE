# HOLOMEDAI — PHASE F3.2-D5 FREEFORM SLICEENGINE INTEGRATION ARCHITECTURE

## 1. STRATEGY SELECTION
`SliceEngine` is the orchestration layer. It remains unchanged in its authority model. Dispatching is handled deterministically via strict type introspection of the `cut_surface` attached to the `SliceOperation`:
- `isinstance(operation.cut_surface, PlanarCutSurface)` -> `PlanarSliceStrategy`
- `isinstance(operation.cut_surface, SweptCutSurface)` -> `FreeformSliceStrategy` (New)
- Unknown/unsupported surface type -> Fail closed explicitly via `TypeError`.

No implicit planarization or fallback is permitted. Do not silently accept unsupported future subclasses/types. 

## 2. API CHANGES
The existing `SliceEngine.slice_piece(piece: AnatomicalPiece, operation: SliceOperation)` signature remains completely intact. No API arguments are modified.
A new internal class `FreeformSliceStrategy` will be added to `slicing.py` implementing the `slice()` method. It returns standard `SliceResult` objects, maintaining complete transparency to downstream consumers. A new exception `FreeformSliceError` may be introduced to wrap orchestration-level assertions.

## 3. CALL GRAPH & PROVENANCE
The deterministic sequence within `FreeformSliceStrategy.slice()` follows an explicit provenance-preserving flow. D2 remains the mathematical intersection primitive, but D4 owns provenance capture at its caller boundary. No geometric reverse-mapping is permitted.

1. **Cutter Mesh Single-Instance Contract**: `cutter_mesh = operation.cut_surface.generate_mesh()`. This generates exactly one deterministic `SweptCutMesh` for the slice operation. The same cutter mesh instance/topology and triangle indices must remain authoritative through intersection, provenance, cutter partition, patch extraction, and grafting. Prohibit regeneration with a different sampling/configuration within the same slice operation.
2. **D4 Provenance Orchestration**: Deterministic target/cutter triangle-pair enumeration.
3. **Primitive Execution**: Call the sealed D2 triangle intersection primitive.
4. **Provenance Capture**: Return `ProvenancedIntersectionSegment` which splits into `target_triangle_segments` and `cutter_triangle_segments`.
5. **Target Partitioning (D3)**: `target_triangle_segments` -> D3 `TargetMeshPartitioner.partition()`.
6. **Patch Extraction (D4)**: `cutter_triangle_segments` -> D4 cutter patch extraction.
7. **Grafting**: Graft the extracted patch into both target children.
8. **Assertion**: Verify volume conservation.
9. **Serialization**: Return final closed `SliceResult`.

## 4. FAILURE SEMANTICS
- **Documented geometry/domain failures** -> Typed domain failure (`MeshPartitionError`, `CutterPartitionError`, `FreeformSliceError`).
- **Unexpected implementation/runtime exception** -> Must not be silently converted into success.
- **Fail Closed**: Any failure before final validation must produce NO successful `SliceResult` and NO partial child geometry presented as final output. No blanket `except Exception` success/fallback behavior.

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

## 7. DETERMINISM & IMMUTABILITY
All steps are strictly deterministic:
- Cutter generation builds vertex lists and triangle indices sequentially.
- Intersection dictionary sorting ensures provenance ordering.
- BFS traversal in D3 and D4 follows sorted triangle/edge neighbors.
- Child piece IDs are deterministically seeded from the parent ID + operation ID.
- Child ordering in the `SliceResult` tuple is predictably assigned (e.g., A then B).

The `FreeformSliceStrategy` treats the following as strictly read-only:
- `SliceOperation` (including trajectory and sweeping profile).
- `SweptCutSurface`.
- `AnatomicalPiece` (source target mesh).
- D2, D3, and D4 outputs (intersections, target partitions, patches). 
No modifications are made in-place. All processing creates new, independent data structures.

## 8. TOLERANCE OWNERSHIP
D5 does not create weaker independent geometry tolerances. Watertightness, welding, degeneracy, and volume-conservation validation must either be delegated to D4, or reuse the exact canonical tolerance contracts already established by D4. No new arbitrary "close enough" thresholds are permitted.

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
  - **No-Planar-Fallback Proof**: A test where a `SweptCutSurface` that is geometrically planar is passed. The test must prove it dispatches through `FreeformSliceStrategy` and not `PlanarSliceStrategy`. Semantic surface type, not geometric coincidence, determines dispatch.

## 10. BENCHMARK PLAN
Create `benchmarks/benchmark_f3_2_d5.py` to test end-to-end performance.
- **Test Assets**: 3,000-triangle cylindrical target + 300-triangle swept cutter.
- **Metrics Tracked**: `t_cutter`, `t_d2`, `t_d3`, `t_d4`, `t_total`.
- **Execution**: 10 warmup cycles, 100 timed repetitions. 

## 11. EXACT INTEGRATION BOUNDARIES
The boundary is purely at `SliceEngine.slice_piece`. No external callers of `SliceEngine` are modified. Internal implementations within D2/D3/D4 are completely opaque to the `SliceEngine`, which relies strictly on their public API methods.
