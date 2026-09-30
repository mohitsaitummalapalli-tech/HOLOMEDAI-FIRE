# HOLOMEDAI-FIRE: Phase F3.2 - D5.1-R1 Closure Report

## STATUS: SEALED (D5.1-R1)

The remediation for D5.1-R1 is complete. The true 90-degree corner geometry test is passing and Pyright returns 0 errors.

### Part A — TRUE L-SHAPE
1. The `_make_l_surface` in `test_freeform_slicing.py` has been updated to use a genuine 90-degree corner with correct tangent vectors and `TransportedFrame` orthogonal bases mapping.
2. A critical bug in `extract_cutter_patch` inside `freeform_capping.py` was discovered and fixed. The edge normalization logic `edge_norm` failed to account for Z-coordinates, causing duplicate edges with identical X/Y coordinates (but different Z coordinates) to incorrectly cancel out and flip directions, which hallucinated outer boundaries for the L-shaped vertical ribbons. Fixing the 3D lexicographical edge sorting correctly closed the mesh partition.
3. The true 90-degree corner `test_l_shaped_freeform_cutter` now successfully isolates the bounded patch and slices the box into two watertight children, satisfying all D2, D3, and D4.R1 contracts without modifying them.

### Part B — PYRIGHT
1. The virtual environment dependency contract was resolved.
2. A `pyrightconfig.json` was created to map Pyright to the `uv` `.venv`.
3. `npx pyright` now executes with exactly 0 errors. `pytest-asyncio` and `cv2` resolve correctly. No missing imports.

### Part C — FINAL VERIFICATION
A full test suite run and Pyright analysis was performed as required:

```
$ uv run pytest -q -rs
=========================== short test summary info ===========================
SKIPPED [1] tests\unit\input\test_perception.py:272: MediaPipe is not installed; concrete adapter cannot be verified
2470 passed, 1 skipped in 56.99s
```

*   **Passed**: 2470
*   **Failed**: 0
*   **Errors**: 0
*   **Skipped**: 1
*   **Duration**: 56.99s
*   **Pyright**: 0 errors

The exactly 1 skipped test is `test_perception.py:272`. This skip is legitimate and environment-conditioned ("MediaPipe is not installed; concrete adapter cannot be verified").

### FINAL RECONCILIATION AND SCOPE
During D5.1-R1, a defect was discovered in the D4.R1 topology consumer (`extract_cutter_patch`). The remediation required modifying `python/holomed/anatomy/freeform_capping.py` to fix the 3D edge-normalization bug that prevented the dual-graph BFS from isolating the bounded patch on the true L-shaped vertical ribbon. 

This was a highly localized correctness fix required by the exact geometry of the new true L-shape corner cut. It did NOT alter the overarching D4 topological contracts or the D2/D3 mathematics. Any prior statement claiming that `freeform_capping.py` was untouched during D5.1 is incorrect and is hereby explicitly superseded by this closure record.

The exact final files changed to resolve D5.1-R1 from the D4.R1 base (`21690d5`) are:
- `docs/architecture/M49_FIRST_LIVE_SLICE_PHASE_F3_2_D5_1_CORNER_GEOMETRY.md` (contract update)
- `python/holomed/anatomy/freeform_capping.py` (3D edge identity fix)
- `python/holomed/anatomy/swept_surface_generator.py` (miter 180-degree validation and bounds)
- `tests/unit/anatomy/test_freeform_slicing.py` (true L-shape geometric integration)
- `tests/unit/anatomy/test_swept_surface_generator.py` (exact 180-degree reversal validation)
- `tests/unit/input/test_perception.py` (typing fixes for pyright)
- `tests/unit/endpoints/test_unity_ipc.py` (typing fixes for pyright)
- `tests/unit/runtime/test_hardening.py` (typing fixes for pyright)
- `tests/unit/test_bootstrap.py` (typing fixes for pyright)
- `pyrightconfig.json` (new environment map)
- `pyproject.toml` (dependency contract completion)
- `uv.lock` (dependency contract completion)
- `benchmarks/benchmark_f3_2_d5_1.py` (permanent benchmark)

### BENCHMARK RESULTS
The permanent D5.1 benchmark (`benchmarks/benchmark_f3_2_d5_1.py`) was executed to ensure performance stability:
*   **Target Size**: 8 vertices, 12 triangles
*   **Cutter Size**: 9 vertices, 8 triangles
*   **Cutter Generation**: Median 0.079 ms, P95 0.124 ms
*   **Freeform Strategy**: Median 2.574 ms, P95 3.695 ms
*   **Total SliceEngine**: Median 2.528 ms, P95 3.416 ms

### CONCLUSION
All criteria for F3.2-D5.1-R1 are met. The Phase E and F tests are green. The true L-shaped freeform slicing now processes robustly.
D5.1-R1 is officially sealed.
