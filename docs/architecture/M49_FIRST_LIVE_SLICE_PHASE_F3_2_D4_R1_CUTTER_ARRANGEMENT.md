# Phase F3.2-D4.R1 Architecture: Cutter Local Planar Arrangement Remediation

## 1. Objective and Scope
This architecture defines a D4-specific deterministic local planar arrangement algorithm to replace the current D4 cutter-triangle splitting dependency (which incorrectly reused D3's `_split_triangle` invariant). The objective is to safely and correctly support multiple disjoint, provenanced intersection segments inside a single cutter triangle without losing global topology or manifold properties.

## 2. Cutter-Triangle Local 2D Coordinate Construction
For each cutter triangle, we extract its 3D vertices ($V_0, V_1, V_2$). We construct a local orthogonal 2D coordinate system (basis) strictly derived from these vertices. 

## 3. Deterministic 3D-to-2D Mapping
The local basis $(U, V)$ is constructed as:
- Origin $O = V_0$
- $U$-axis is the normalized vector from $V_0$ to $V_1$.
- $W$-axis (normal) is the normalized cross product of $(V_1 - V_0)$ and $(V_2 - V_0)$.
- $V$-axis is the normalized cross product of $W$ and $U$.
All 3D vertices and 3D intersection segments belonging to this triangle are projected into this local 2D plane via dot products with $U$ and $V$. This guarantees exact topological preservation without arbitrary world-axis projection.

## 4. Provenanced Segment Ownership
Segment ownership remains strictly bound to D2 provenance. Each `ProvenancedIntersectionSegment` explicitly specifies a `cutter_triangle_index` and a `target_triangle_index`. Segments are assigned to their respective local planar arrangements based *exclusively* on this `cutter_triangle_index`.

## 5. Triangle Boundary Representation
The original cutter triangle boundary is represented as three directed 2D line segments: $(V_0 \to V_1)$, $(V_1 \to V_2)$, and $(V_2 \to V_0)$. These segments are added to the local planar arrangement along with the provenanced intersection segments.

## 6. Multiple Local Intersection Segments
The arrangement natively accepts zero, one, two disjoint, or many intersection segments within a single cutter triangle, recognizing that target mesh geometry (like corners) routinely snakes through large cutter triangles. The strict D3 "single chain" invariant is lifted for D4 local arrangements.

## 7. Shared Endpoint Canonicalization
All projected 2D endpoints (from boundaries and intersection segments) are subjected to deterministic canonicalization based on a tight geometric tolerance ($10^{-8}$). Endpoints that are geometrically coincident are merged into a single unique canonical vertex to ensure the resulting graph is perfectly connected.

## 8. Segment/Segment Relationship Classification
During construction, all segments (boundary and intersection) are tested against each other. The relationship is classified deterministically:
- Disjoint
- Shared endpoint (vertex)
- Endpoint-on-segment
- Proper crossing
- Collinear overlap

## 9. Local Planar Graph Representation
The canonicalized vertices and segments are used to build a planar graph. For every segment, two directed half-edges are created. The half-edges are sorted rotationally around each vertex (using `math.atan2` on the 2D coordinates) to enable deterministic face traversal.

## 10. Directed Edge / Half-Edge Traversal
Face reconstruction utilizes the classic half-edge traversal algorithm. Starting from an unvisited half-edge, the algorithm traverses to the destination vertex and takes the "next" half-edge in counter-clockwise (or clockwise) rotational order, continuing until the loop is closed.

## 11. Bounded Face Reconstruction
The half-edge traversal generates a set of closed loops (faces). The unbounded outer face (representing the infinite plane outside the triangle) is identified by its signed area (e.g., negative area under counter-clockwise convention) and discarded. The remaining faces represent valid, bounded, non-overlapping polygonal regions inside the original cutter triangle.

## 12. Deterministic Triangulation of Reconstructed Faces
Each bounded reconstructed polygonal face is triangulated deterministically. A standard ear-clipping or fan triangulation is applied, ensuring no zero-area faces are generated and valid indices are maintained.

## 13. Local Face Orientation
The newly triangulated sub-faces are guaranteed to have consistent winding with the original cutter triangle. Their 3D coordinates are restored using the inverse mapping: $P_{3D} = O + u \cdot U + v \cdot V$, retaining the original normal $W$.

## 14. Global Cutter Adjacency Construction
After all cutter triangles are locally subdivided and triangulated, the global cutter mesh is assembled. The faces are merged, and global topological adjacency (dual graph) is computed across the entire subdivided cutter mesh.

## 15. Intersection Barriers
The original provenanced intersection segments represent the physical cut boundaries. These segments are marked as "barriers" in the global dual graph, preventing adjacency traversal across the cut line.

## 16. Unique Bounded Patch Selection
The global dual graph is traversed to find connected components (patches). Because the original cut trajectory must form a closed loop (enforced in earlier phases), the barrier edges partition the cutter mesh into components. The algorithm identifies and selects the unique valid bounded patch by rejecting the component that touches the infinite/outermost original boundary of the `SweptCutSurface`.

## 17. Local-vs-Global Topology Distinction
This architecture establishes a crucial separation of concerns:
- **Local Topology**: A single cutter triangle may contain multiple disjoint cut segments. This is a local geometric reality and is not an error.
- **Global Topology**: The union of all intersection segments must form a globally valid, non-ambiguous loop across the entire cutter mesh. Global topology determines the validity of the patch, not local segment count.

## 18. Fail-Closed Conditions
The arrangement algorithm will strictly fail closed (`FreeformSliceError` or `CutterPartitionError`) under the following conditions:
- Non-finite coordinates or degenerate triangles.
- Zero-length intersection segments.
- Ambiguous collinear overlaps.
- Proper crossings that create unsupported topology (cut lines intersecting each other).
- Non-manifold local graphs.
- Ambiguous face reconstruction (e.g., dangling segments not reaching the boundary).
- Self-intersecting cutter geometry.
- Multiple ambiguous bounded global components.
- Ambiguity in identifying the cutter outer-boundary.

## 19. Transform Invariance
The local 2D basis ensures that applying rigid 3D transformations (translation, rotation) to the cutter mesh and segments prior to execution will yield the exact same topological arrangement and patch extraction.

## 20. Immutability & Determinism
Input meshes and trajectory segments are never mutated. All operations, classifications, sorting steps, and triangulations are executed with absolute mathematical determinism without reliance on random seeds, nearest-neighbor heuristics, or non-deterministic data structures (like unordered sets).
