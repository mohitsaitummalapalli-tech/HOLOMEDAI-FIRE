# Phase F3.2-D5.1 Architecture: Corner-Safe Freeform Cutter Geometry

## 1. Objective and Scope
The current `SweptCutSurface` and its mesh generator `generate_swept_mesh` assume a continuously differentiable trajectory. At sharp tangent discontinuities (e.g., a 90-degree L-shape), the naive evaluation of the transport frame on the "incoming" and "outgoing" segments causes the swept geometry to fold over itself on the inside of the turn, producing a self-intersecting mesh. This invalidates downstream manifold guarantees.

This architecture defines a deterministic corner join contract to produce valid, non-self-intersecting cutter geometry for finite-width (and finite-thickness) cuts.

## 2. Corner Detection & Angle Semantics
*   **Detection**: A corner is detected at any trajectory sample $P_i$ where the angle $\theta$ between the incoming tangent $T_{in}$ and outgoing tangent $T_{out}$ exceeds a strict collinearity tolerance (e.g., $10^{-4}$ radians).
*   **Frame Handling**: At a corner, the trajectory possesses two distinct frames: $F_{in}$ (approaching the corner) and $F_{out}$ (leaving the corner).

## 3. Join Construction: The Miter Join
To prevent self-intersection while maintaining semantic alignment with the user's trajectory, the system will implement a **Miter Join** with an acute-angle fallback.

*   **Mechanism**: At the corner vertex $P_i$, we define a **Miter Plane**. The normal of this plane is the angle bisector of $-T_{in}$ and $T_{out}$. 
*   **Vertex Generation**: Instead of generating two overlapping cross-sections, the mesh generator computes a single cross-section of vertices that are explicitly projected along their respective sweep paths until they intersect the Miter Plane.
*   **Why Miter?**: 
    1. It eliminates the "inner fold" overlap by truncating the inner vertices exactly at the bisection plane.
    2. It closes the "outer gap" by extending the outer vertices to meet at the bisection plane.
    3. It is deterministically computable and maintains a continuous manifold surface.
*   **Fallback (Bevel Join)**: For extremely acute angles where the miter spike extends beyond a safety threshold (e.g., `miter_length > 2 * width`), the miter is truncated into a Bevel Join to preserve bounded geometry.

## 4. Manifold Guarantees & Topology
The resulting `SweptCutMesh` guarantees:
*   Finite vertices and deterministic right-handed winding.
*   No zero-area faces (unless the original trajectory segment is zero-length, which is already rejected).
*   No self-intersections (the miter plane acts as a strict geometric boundary between the incoming and outgoing segments).
*   Translation and rotation invariance (the miter plane is derived purely from local tangent vectors).

## 5. D2/D3/D4 INCOMPATIBILITY & STOP CONDITION

**STOP EVENT:** The architecture reveals that even with a mathematically perfect, non-self-intersecting Miter Join, the L-shaped cutter **cannot guarantee successful passage through D4**.

### The Contract Mismatch
D4 extracts the bounded cutter patch by executing the D3 target-partitioning methodology (`TargetMeshPartitioner.partition` / `_split_triangle`) directly on the **cutter mesh**.

D3's `_split_triangle` enforces a strict topological invariant: **a triangle may only be split by a single, simple, connected chain of segments**. If a triangle contains multiple disjoint chains, it explicitly fails with `MeshPartitionError("Multiple disjoint chains or loops inside target triangle")`.

### The Failure Mode
1.  **Asymmetry of Scale**: Cutter triangles are generated based on trajectory samples and are typically large (e.g., spanning a length of 0.4 and a width of 8.0). Target mesh triangles are comparatively small.
2.  **Boundary Weaving**: When a large cutter triangle intersects a complex target surface (such as the 90-degree corner of the target box, or a face offset from the exact sample coordinates), the intersection curve frequently exits and re-enters the large cutter triangle.
3.  **Disjoint Segments**: This weaving produces multiple disconnected intersection segments inside a single cutter triangle.
4.  **Crash**: When D4 invokes `_split_triangle` on this cutter triangle, it immediately crashes.

*(Note: In targeted tests, displacing the target box by a small epsilon `0.123` causes the cutter mesh to intersect the left face and bottom face of the target box, generating two completely disjoint segments inside Cutter Triangle 809, unconditionally crashing D4).*

### Conclusion
We **cannot** fulfill the requirement: *"The actual L-shaped cutter must proceed through... D4 and produce exactly two target children... Do not change D2/D3/D4 contracts to make this pass."*

D4's reliance on `_split_triangle` for patch extraction is fundamentally incompatible with the topological realities of arbitrary cutter mesh intersections. Changing the join geometry to Miter/Bevel will **not** resolve this D4 contract violation. Implementation is HALTED pending architectural review of D4's patch extraction invariants.
