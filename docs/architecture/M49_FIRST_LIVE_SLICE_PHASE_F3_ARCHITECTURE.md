# Phase F3 Architecture: Freeform Trajectory Processing

## Objective
Implement the trajectory-processing layer that converts a validated 3D user cutting trajectory into the generic `CutSurface` abstraction consumed by the sealed F2 SliceEngine.

**Final Architecture Boundary:**
- `Raw CutTrajectory` → `Validator` → `Resampler` → `Smoother` → `ProcessedCutTrajectory` → `PlanarSurfaceGenerator` → `CutSurface` → `F2 SliceEngine`

**Future Expansion Boundary:**
- `ProcessedCutTrajectory` → `SweptSurfaceGenerator` → `SweptCutSurface` → `F2 SliceEngine`

**Non-Negotiable Product Requirement:**
The user's actual trajectory determines the resulting cut geometry. The implementation MUST NOT silently snap to axial/coronal/sagittal planes, a fixed plane, or classify the gesture into a preset cut. Different valid trajectories MUST produce mathematically distinct `CutSurface`s.

---

## 1. CutTrajectory Contract
The `CutTrajectory` is an immutable, purely mathematical representation of the user's cutting path in 3D space.

**Structure:**
- `trajectory_id` (str): Unique identifier for the raw trajectory input.
- `correlation_id` (str): Associates the trajectory with a broader interaction or session (but NOT a physical operation authority).
- `samples` (Tuple[TrajectorySample, ...]): An ordered immutable sequence of coordinate samples.
- `coordinate_space` (str): Explicit identifier of the coordinate frame (e.g., `"canonical_anatomical"`).
- `validity_state` (Enum): `RAW`, `VALIDATED`, `REJECTED`, `PROCESSED`.
- `sampling_metadata` (Dict): Sensor metadata (e.g., device type), purely for analytics, not logic.

**TrajectorySample Structure:**
- `position` (Point3D): (x, y, z) coordinate.
- `timestamp` (float): Monotonic time (in seconds) of the sample.
- `state` (Enum): e.g., `START`, `ACTIVE`, `END`, representing the interaction state.

*Note:* Authority metadata (device_epoch, physical_operation_id, command_nonce) are strictly EXCLUDED from this mathematical abstraction.

## 2. Coordinate Contract
The trajectory operates strictly in the stable canonical anatomical coordinate system defined in Phase F.

**Transformations:**
- Camera/world coordinates MUST be explicitly transformed to canonical anatomical coordinates before forming a `CutTrajectory`.
- Origin: (0.0, 0.0, 0.0) aligned to the canonical isocenter.
- Handedness: Right-handed.
- Units: Meters (float).

## 3. Resampling Semantics
Resampling transforms an unevenly sampled raw sequence into a deterministically spaced sequence, making it independent of camera sampling frequency.

**Exact Rules:**
- **Spatial Spacing:** Exactly `0.005` meters (5 mm) between interpolated points.
- **Minimum Threshold:** Micro-movements $< 0.001$ meters (1 mm) from the previous retained point are collapsed (ignored) during resampling traversal.
- **Maximum Sample Gap:** Any two consecutive raw points $> 0.05$ meters (5 cm) apart trigger an immediate REJECTION of the trajectory.
- **Interpolation Method:** Linear 3D spatial interpolation along the piecewise segment.
- **Timestamp Interpolation:** Linear interpolation strictly proportional to the spatial distance along the segment.
- **Ordering:** Strictly sequential from `START` to `END`.
- **Fidelity Guarantee:** Processed geometry sampled sparsely versus densely across the same mathematical path must produce equivalent interpolated paths within a mathematical tolerance of $10^{-4}$ meters (exact bit-for-bit equality is not required due to floating-point interpolation).

## 4. Smoothing and Corner Preservation
Smoothing removes sensor jitter without erasing meaningful user intent. It runs **after** micro-movement collapse and resampling.

**Exact Deterministic Algorithm:**
- **Method:** 3-point weighted moving average.
- **Kernel Weights:** `[0.25, 0.5, 0.25]` applied to `[p_{i-1}, p_i, p_{i+1}]`.
- **Edge Handling:** The first (`0`) and last (`N-1`) samples are preserved EXACTLY. Smoothing is not applied to trajectory endpoints.
- **Corner Preservation (Turning Angle Rule):** At each vertex `i`, the angle between the incoming vector (`p_i - p_{i-1}`) and the outgoing vector (`p_{i+1} - p_i`) is calculated. If this turning angle $> 30^\circ$ (approx. `0.523` radians), it is classified as a deliberate sharp corner. Smoothing is **bypassed** for vertex `i`, preserving its raw position exactly.
- **Deviation Limit:** No smoothed point may deviate from its pre-smoothed position by more than `0.02` meters (2 cm). If the weighted average yields a displacement $>0.02$m, it is clamped mathematically along the displacement vector.
- **Fidelity Test:** An intentional sharp corner must be demonstrably retained exactly, verified by checking coordinate equality with the pre-smoothed vertex.

## 5. Trajectory Validation & Interaction Volume
Validation guarantees geometric safety before surface generation.

**Fail-Closed Rejection Rules:**
- **Empty/Insufficient Points:** Fewer than 3 valid points.
- **Duplicate Consecutive Points:** Filtered out. If the resulting sequence is $<3$ points, rejected.
- **Zero-Length Path:** Total accumulated path length $< 0.01$ meters (1 cm).
- **Excessive Sample Gap:** Any consecutive raw gap $> 0.05$ meters.
- **NaN / Inf:** Any non-finite float value.
- **Interaction Volume Validity:** Any point outside the fixed global anatomical interaction bounding box (e.g., strictly within $[-2.0, 2.0]$ meters on all axes) is rejected. This is independent of any dynamic mesh bounding box.
- **Invalid Timestamps:** Any non-monotonic (`t[i] >= t[i+1]`) timestamps.

## 6. Planar Generation Contract
Converts the processed `CutTrajectory` into a `PlanarCutSurface` deterministically.

**Exact Mathematical Equations:**
1. **Centroid ($p_0$):** Calculated as the exact arithmetic mean of all 3D points in the processed trajectory. This is the exact `plane_origin`.
2. **Covariance Matrix:** Compute the $3 \times 3$ covariance matrix ($X^T X$) of the mean-centered points.
3. **Eigen Decomposition:** Extract eigenvalues such that $\lambda_1 \ge \lambda_2 \ge \lambda_3$ and eigenvectors $v_1, v_2, v_3$.
4. **Collinearity Rejection:** Evaluate $\lambda_2 / \lambda_1$. If $\lambda_2 / \lambda_1 < 10^{-3}$, the trajectory is nearly collinear (a 1D line) and lacks sufficient 2D spread to uniquely define a plane. REJECT.
5. **Normal Selection ($n$):** The eigenvector $v_3$ corresponding to $\lambda_3$.
6. **Canonical Sign Rule:** To ensure deterministic serialization (since $n$ and $-n$ define the same plane), enforce:
   - If $n_z < 0$, flip $n = -n$.
   - If $n_z == 0$ and $n_y < 0$, flip $n = -n$.
   - If $n_z == 0$ and $n_y == 0$ and $n_x < 0$, flip $n = -n$.
7. **Plane Equation:** $n \cdot (x - p_0) = 0$.
8. **Residual Rejection:** If $\max(|(p_i - p_0) \cdot n|) > 0.05$ meters (5 cm), the trajectory is distinctly non-planar. REJECT.

**Straight Trajectory Semantics (Important UX Distinction):**
While the F3 mathematical planar generator rejects nearly collinear trajectories, this does NOT mean the final HoloMedAI UX forbids "straight cuts". In future phases, live straight user strokes will be valid either because the tool provides explicit orientation vectors, or because they map to the Swept Surface pipeline.

## 7. Plane Normal Sign Equivalence & Synthetic vs Live Tolerances
- **Angular Comparison:** All internal plane normal comparisons must use orientation-invariant evaluation: $|n_1 \cdot n_2|$.
- **Synthetic Tolerance:** For controlled algorithmic verification (e.g., ensuring no-snap behavior), angular error must be $\le 10^{-4}$ radians.
- **Live Tolerance:** The future physical camera/MediaPipe integration will use significantly wider tolerance bands (to be defined in Phase G) accounting for biological and sensor noise. Synthetic math test bounds must not be conflated with live UX acceptance criteria.

## 8. No "Exactly 5 Degrees" Claim (No Preset Snapping)
- The pipeline MUST NOT contain references to anatomical axes (e.g., `[0,1,0]`) as snapping targets.
- **Measurable Tolerance Rule:** A synthetic trajectory constructed to represent a plane precisely 5 degrees off-axis must produce a generated `CutSurface` whose normal matches the expected mathematical normal within the synthetic angular error $\le 10^{-4}$ radians.

## 9. True Freeform Future Path (Swept Cut Contract)
The architecture must preserve all necessary context for future 3D swept surfaces. The `ProcessedCutTrajectory` is contractually required to retain sufficient information for a 3D swept construct:
- **Centerline Samples:** The processed 3D points.
- **Timestamps:** For velocity/temporal dependency.
- **Tangents:** Computed from the path traversal.
- **Deterministic Transported Frame:** An implicitly calculated parallel transport frame (e.g., Bishop Frame) ensuring stable cross-sectional orientation without requiring twist inputs.
- **Width/Thickness Parameters:** Metadatic constants defining the spatial boundary.

This guarantees mapping to an `SweptCutSurface` without redefining F3 trajectory abstractions.

## 10. Curve Fidelity & Trajectory Quality Metrics
Generated during validation/processing for analytics and rejection:
- `total_path_length`, `point_count`, `spatial_span`, `svd_fitting_residual_max`, `svd_fitting_residual_mean`.
- **Curve Fidelity Proof:** Compares the processed trajectory coordinates against the known mathematical synthetic reference curve, proving maximum absolute deviation $< 0.02$ meters (2 cm) irrespective of path length.

## 11. Failure Semantics
Fail-closed at all layers:
- Trajectory validation failure $\rightarrow$ `TrajectoryValidationError`.
- SVD degeneracy or excessive residual $\rightarrow$ `SurfaceGenerationError`.
- **Consequence:** No `CutSurface` is yielded, F2 `SliceEngine` is NEVER invoked, canonical geometry remains untouched.
- No trajectory fields become authoritative capability fields.

## 12. Test Matrix

| ID | Test Target | Description |
|---|---|---|
| A | `test_valid_planar_trajectory` | Well-defined 2D spread yielding a clean SVD plane. |
| B | `test_valid_straight_trajectory` | Rejected as collinear by SVD Planar Generator (UX note: accepted in future via explicit tool orientation). |
| C | `test_diagonal_trajectory` | Perfect 45-degree angled path proving general orientation. |
| D | `test_arbitrary_orientation` | Randomly generated valid orientation proving non-alignment. |
| E | `test_arbitrary_position` | Trajectories shifted by X yield surfaces shifted by X. |
| F | `test_sparse_vs_dense_sampling` | Sparse vs dense geometric equivalence within $10^{-4}$m tolerance. |
| G | `test_uneven_sampling` | High density middle, sparse ends -> uniform 5mm spacing. |
| H | `test_gap_rejection` | >5 cm raw gap -> validation rejection. |
| I | `test_micro_movements` | <1 mm consecutive points collapse perfectly before smoothing. |
| J | `test_duplicate_points` | Duplicate identical coordinates filtered out correctly. |
| K | `test_non_monotonic_timestamps` | Timestamps that jump backwards -> validation rejection. |
| L | `test_nan_inf` | NaN/Inf coordinates -> validation rejection. |
| M | `test_zero_length_trajectory` | Total length < 1 cm -> validation rejection. |
| N | `test_nearly_collinear_trajectory` | $\lambda_2 / \lambda_1 < 10^{-3}$ -> mathematically rejected as 1D line by Planar generator. |
| O | `test_noisy_planar_trajectory` | High variance, max deviation > 5cm -> rejected as non-planar. |
| P | `test_corner_preservation` | >30 deg angle is excluded from smoothing, preserving coordinate exactly. |
| Q | `test_deterministic_replay` | Run twice, exact float match for all generated properties. |
| R | `test_no_preset_snapping` | 5-degree off-axis input produces normal matching expectation within $10^{-4}$ rad. |
| S | `test_different_trajectory_different_surface` | Shifted/rotated input yields explicitly shifted/rotated surface. |
| T | `test_normal_sign_equivalence` | Proves `n` and `-n` evaluate to identical absolute dot product constraints. |
| U | `test_curve_fidelity` | Compares processed trajectory vs mathematical reference curve. |
| V | `test_future_swept_surface_compatibility` | Demonstrates centerline, tangent, and parallel transport frame preservation. |

## 13. Definition of Done
F3 Architecture is COMPLETE as all requirements are explicitly defined herein, establishing mathematically rigorous, deterministic, non-snapping trajectory-to-surface processing via structured SVD constraints, while perfectly preserving the F2 geometry bounds and future swept-cut expansion paths.
