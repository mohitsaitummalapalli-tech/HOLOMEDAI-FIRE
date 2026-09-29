# Phase F Architecture: First Live Slice (Freeform Anatomical Slicing)

## 1. Introduction & Scope
Phase F implements the First Live Slice: a deterministic, freeform anatomical cutting engine driven by user-defined geometry. Execution occurs across a Python authoritative core maintaining canonical geometric state and a Unity rendering endpoint.

### 1.1 Goals
- Accept arbitrary user-drawn 3D cut trajectories.
- Generate valid `CutSurface` geometries from trajectories via a formal pipeline.
- Perform deterministic mesh intersection and splitting in Python.
- Generate and cap internal cut surfaces.
- Support repeated cuts on resulting pieces with a stable coordinate frame.
- Maintain the strict capability-based authority boundary established in Phase E.

### 1.2 Explicit Non-Goals
- Real surgical hardware or robotic physical actuation.
- Clinical claims or patient data integration.
- Hardware physical termination evidence.
- Restricting cuts to predefined anatomical planes (e.g., purely axial/coronal/sagittal).
- Implicitly normalizing arbitrary cuts to presets.
- UI bloating; the focus is on a seamless "draw and slice" interaction.
- Transferring geometric or command authority to Unity.

---

## 2. Core Architecture & Authority

### 2.1 Single Canonical Anatomical State
**Python is the single canonical owner of anatomical geometry.**
- Unity and Python do NOT maintain independent uncontrolled versions of anatomy.
- **Unity's Responsibility:** Unity is strictly a consumer of geometric state updates and a renderer. It possesses no authority and does not calculate the canonical mesh split.
- **State Synchronization:** Python calculates the `SliceResult`, maintaining the definitive mesh structures. Python sends resulting geometry updates to Unity.
- **Mismatch Detection & Recovery:** Every piece has a deterministic `geometry_version`. Unity MUST reject stale/mismatched geometry versions. If Unity receives a capability for a stale version, Python rejects the operation. If IPC restarts, Unity requests a full state synchronization.

### 2.2 Authority Boundary (Preserved from Phase E)
The `DeviceControlManager` (Python) remains the absolute root of authority.
`Ultron` → untrusted proposal → `DeviceControlManager` → authoritative admission → `capability` → Unity endpoint → `evidence` → reconciliation.
Object identity is not authority. Geometric authority and command authority are NOT moved into Unity.

---

## 3. Geometry Contracts

### 3.1 Stable Coordinate Frame
To guarantee reproducibility, a stable anatomical/model coordinate frame is maintained across all cuts. The coordinate origin is NOT redefined dynamically after each cut (e.g., no dynamic bounding-box origins).

**Mathematical Transformation:**
- **Spaces:** Camera Space → Perception/World Space → Unity World Space → Anatomical Local Space.
- **Units:** Meters (MKS standard).
- **Origin:** Fixed at the 3D model's canonical origin (e.g., model centroid at `t=0`).
- **Transform:** 
  - Python Canonical Space: Right-handed (X right, Y up, Z forward).
  - Unity World Space: Left-handed (X right, Y up, Z backward).
  - Transform Rule: `Unity_X = Python_X`, `Unity_Y = Python_Y`, `Unity_Z = -Python_Z`. Normal vectors and winding orders must be inverted accordingly across the boundary.

### 3.2 Freeform Trajectory → Cut Surface
The core slicing abstraction does NOT implicitly reduce trajectories to predefined planes. The mathematical pipeline is explicit:
`3D CutTrajectory` → `resampling` → `smoothing/validation` → `CutSurface generation` → `SliceEngine`

The `CutSurface` abstraction serves as the base for concrete implementations:
- **`PlanarCutSurface`**: (Implemented first) Supports arbitrary and non-axis-aligned planar surfaces.
- **`SweptCutSurface`**: (Future) Supports arbitrary swept/freeform surfaces generated from full 3D trajectories.

The `SliceEngine` API remains unchanged regardless of whether a `PlanarCutSurface` or `SweptCutSurface` is provided.

### 3.3 Cutter Parameter Contract
- **Sampling/Resampling Rules:** Resampled at fixed spatial intervals (e.g., 1mm) decoupled from framerates.
- **Minimum Sample Count:** 2 samples (defines a line for a planar cut).
- **Maximum Sample Gap:** 5cm (larger gaps reject the trajectory as discontinuous).
- **Smoothing Rules:** B-spline or moving average smoothing applied before surface generation.
- **Cut Width/Thickness:** 0 for an ideal plane; >0 if simulating blade thickness.

---

## 4. Slice Engine & Mesh Processing

### 4.1 Determinism vs. Identity
Explicitly separated into two concepts:

**A. Geometric Determinism**
- Same canonical mesh + same `CutSurface` + same parameters → mathematically identical `SliceResult` geometry (vertex/index arrays bit-for-bit identical).
- Deterministic testing compares raw geometry arrays independently of random or generated operation IDs.

**B. Identity Allocation & Lineage**
- Lineage tracks `Parent Piece` → `Cut Operation` → `Child Pieces`.
- Repeated cuts maintain the stable geometric coordinate frame. A piece is sliced, yielding child pieces; a user selects a child piece and cuts again without coordinate drift.

---

## 5. IPC Architecture

### 5.1 Concrete IPC Payload (WebSocket JSON Framing)
To transport large mesh buffers efficiently over JSON-framed WebSockets without losing the capability-correlation features, the payload uses base64-encoded binary blobs for geometry arrays.

**The Payload MUST include:**
- `piece_id`: UUID
- `parent_piece_id`: UUID
- `operation_id`: UUID
- `geometry_version`: integer (strict monotonic)
- `vertex_count`: integer
- `index_count`: integer
- `encoding`: string (e.g., `"base64_f32_xyz"`, `"base64_u32"`)
- `integrity_checksum`: string (SHA-256 of the unencoded binary buffers)
- `vertices`: base64 string
- `indices`: base64 string
- `normals`: base64 string
- `uvs`: base64 string

Python performs the mesh operation completely; Unity only decodes the binary blobs to update its `MeshFilter`/`MeshCollider`.

---

## 6. Resilience & Failure Taxonomy (Fail-Closed)

- **Invalid/Degenerate Trajectory:** Fails schema validation, lacks sample count, or colinear points -> Rejected.
- **Self-Intersecting Trajectory:** Trajectory crosses itself -> Rejected.
- **Invalid/Degenerate CutSurface:** Fails instantiation or zero area -> Rejected.
- **Non-Manifold Result:** Slice produces invalid topology -> Operation aborted, parent piece retained.
- **Mesh Intersection Failure / Empty Intersection:** Algorithm fails or misses target -> Operation aborted, parent retained.
- **Numerical Instability:** Floating point thresholds exceeded -> Operation aborted.

---

## 7. Performance Measurement Plan

Stated budgets are **Engineering Acceptance Targets** (not proven guarantees). Measurement methodology:
- **Trajectory Processing:** Measured from intent arrival to `CutSurface` instantiation. (Target: <15ms)
- **Mesh Slicing Latency:** Measured strictly around the Python intersection/splitting routine. (Target: <150ms per 100k vertices)
- **Result Serialization:** Measured duration to convert arrays to base64 JSON payload. (Target: <20ms)
- **IPC Transfer Latency:** Measured via round-trip ping minus processing time. (Target: <5ms localhost)
- **Unity Update Latency:** Measured via Unity Profiler from payload decoding to `mesh.SetVertices`. (Target: <16ms)

---

## 8. Testing Strategy

### 8.1 Freeform Differentiation Test (CRITICAL)
- **Test:** Same canonical mesh sliced with arbitrary `CutSurface A`, then independently sliced with different arbitrary `CutSurface B`.
- **Assertion:** `geometry(Result A) != geometry(Result B)` (ignoring piece IDs).
- **Proof:** The engine does not silently normalize arbitrary cuts.
- **Matrix:** Explicitly tests different positions, different orientations (e.g. 15-degree tilt), and completely different trajectories.

### 8.2 Additional Architecture Tests
- **Deterministic Same-Input:** Exact same trajectory repeated 10 times yields mathematically identical results.
- **Boundary & Failure:** Tests for very shallow angles, near-edge cuts, malformed/degenerate trajectories, self-intersection, and stale commands.

---

## 9. Definition of Done
- This final revised architecture document is approved.
- `SliceEngine`, `PlanarCutSurface`, and `SweptCutSurface` abstractions are defined in Python.
- Unity IPC successfully receives and decodes base64 geometry payloads.
- The CRITICAL Freeform Differentiation Test passes.
- Python remains the absolute canonical owner of geometry and authority.
- Telemetry events capture exact performance measurements against targets.
