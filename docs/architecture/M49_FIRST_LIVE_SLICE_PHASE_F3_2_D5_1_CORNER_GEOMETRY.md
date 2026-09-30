# Phase F3.2-D5.1 Architecture: Corner-Safe Freeform Cutter Geometry

## 1. Objective and Scope
This architecture defines a deterministic corner join contract to produce valid, non-self-intersecting cutter geometry for finite-width and finite-thickness cuts when a freeform trajectory contains direction discontinuities (e.g., corners, L-shapes). 

## 2. Semantics and Core Rules

### Corner Detection
A corner is detected at any trajectory sample $P_i$ where the angle between the incoming tangent $T_{in}$ (approaching $P_i$) and outgoing tangent $T_{out}$ (leaving $P_i$) is non-zero (exceeding numerical tolerances such as $10^{-6}$ radians).

### Turn Angle Definition
The turn angle $\theta$ is the angle between $T_{in}$ and $T_{out}$. 
- **180-degree / straight**: $\theta \approx 0$ (tangents are parallel).
- **Near-180-degree**: $0 < \theta < 10^{-4}$ (handled gracefully or treated as straight).
- **Obtuse/90-degree/Acute**: Normal corners requiring explicit join geometry.

### Exact Meaning of "Preserve Trajectory"
The user-requested centerline trajectory is semantically sacred. A join construction (miter or bevel) is exclusively a mechanism for the finite-width volumetric boundary to remain manifold. The centerline of the cut *must* pass exactly through $P_i$ as requested. We do not round, smooth, or fillet the centerline.

### Immutability & Transform Invariance
All generation operations are strictly immutable, never mutating the input `ProcessedCutTrajectory`, `SweptCutSurface`, `TransportedFrame`, or source `AnatomicalMesh`. Construction relies exclusively on local frame vectors, guaranteeing exact topological and geometric equivalence regardless of 3D rigid transformations (translation or rotation).

## 3. Miter Join Construction
At a corner $P_i$, the naive extrusion of the incoming and outgoing segments causes boundary overlapping (self-intersection) on the inner corner. To prevent this, we construct a **Miter Join**.

### Tangent Semantics & Miter Plane
We define the **Miter Plane** at $P_i$. The plane normal $N_m$ is the normalized angle bisector between $-T_{in}$ and $T_{out}$. If $-T_{in}$ and $T_{out}$ are perfectly parallel (a 180-degree reversal, $\theta = \pi$), the operation is unsupported and fails closed.

### Corner Vertex Generation
When generating the boundary vertices at $P_i$, we project the vertices along their intended $T_{in}$ / $T_{out}$ trajectories until they intersect the Miter Plane. This ensures:
1. Inner fold overlaps are exactly truncated.
2. Outer gaps are exactly closed.

### Corner Topology & Deterministic Ordering
A single cross-section (parameterized by $u$) is generated at $P_i$. The indices are woven exactly as in a straight segment, guaranteeing manifold topology (no duplicate faces, no zero-area faces, no non-manifold edges). Multi-corner trajectories process corners strictly sequentially, maintaining deterministic ordering.

## 4. The Miter Limit and Bevel Fallback Contract

### The Limit
For acute angles, the miter vertex on the outer edge spikes outward. The distance $d$ from $P_i$ to the outer vertex is inversely proportional to $\cos(\theta/2)$. For highly acute angles, $d$ becomes unacceptably large, creating "spears" that violate the intended locality of the cut.

To maintain **bounded geometry**, we enforce a strict mathematical limit: `miter_limit = 2.0 * surface.width`. 

### Bevel Fallback Condition
If the projection distance along the tangent exceeds `miter_limit`, the pure miter is mathematically impossible to fulfill without violating the spatial locality contract of the cutter. 
When this condition is reached, the fallback strategy applies:
1. The geometry transitions to a **Bevel Join**.
2. We truncate the spike by capping it.
*Note:* The fallback must still preserve manifold topology, bounded generation, and no self-intersection. If the bevel fallback cannot mathematically guarantee a manifold, non-degenerate surface under extreme acute angles, the system must **fail closed** rather than emitting malformed geometry.

## 5. Self-Intersection Prevention & Degenerate Handling
The Miter/Bevel construction intrinsically prevents local self-intersection at $P_i$ by enforcing the Miter Plane as a strict boundary. 
- If a generated vertex results in non-finite coordinates (NaN/Inf), the generator raises an explicit `AnatomyValidationError` (fail closed).
- Zero-area faces are prevented because coincident vertices only occur if trajectory segments have 0 length (which is structurally rejected).

## 6. Supported vs Unsupported Geometries
- **Supported**: 3D spatial turns, 90-degree L-shapes, obtuse angles, multiple corners.
- **Unsupported**: 180-degree trajectory reversals ($\theta = \pi$). These fail closed.
