import pytest
import math
from holomed.anatomy.models import Point3D, Vector3D
from holomed.anatomy.trajectory import (
    TrajectorySample, CutTrajectory, ProcessedCutTrajectory, 
    TrajectoryState, ValidityState, TrajectoryValidationError,
    validate_trajectory
)
from holomed.anatomy.surface_generation import process_trajectory

def create_trajectory(points, timestamps=None) -> CutTrajectory:
    if timestamps is None:
        timestamps = [float(i) for i in range(len(points))]
    samples = []
    for i, p in enumerate(points):
        state = TrajectoryState.ACTIVE
        if i == 0: state = TrajectoryState.START
        elif i == len(points) - 1: state = TrajectoryState.END
        samples.append(TrajectorySample(Point3D(*p), timestamps[i], state))
    return CutTrajectory(
        trajectory_id="test",
        correlation_id="corr",
        samples=tuple(samples),
        sampling_metadata={"test_key": "test_value"}
    )

def test_valid_planar_trajectory():
    # A valid planar trajectory
    pts = [(0, 0, 0), (0.04, 0, 0), (0.08, 0, 0), (0.1, 0, 0), (0.1, 0.05, 0), (0.1, 0.1, 0)]
    traj = create_trajectory(pts)
    processed = process_trajectory(traj)
    assert len(processed.samples) >= 3

def _sample_curve(t_vals):
    # A deterministic nonlinear mathematical reference curve (e.g., a parabola)
    # y = 0.5 * x^2
    pts = []
    for t in t_vals:
        x = t * 0.1 # x from 0 to 0.1
        y = 0.5 * (x ** 2)
        pts.append((x, y, 0))
    return pts

def _get_arc_lengths(samples):
    # Calculate cumulative arc lengths for correspondence
    lengths = [0.0]
    for i in range(1, len(samples)):
        dx = samples[i].position.x - samples[i-1].position.x
        dy = samples[i].position.y - samples[i-1].position.y
        dz = samples[i].position.z - samples[i-1].position.z
        l = math.sqrt(dx*dx + dy*dy + dz*dz)
        lengths.append(lengths[-1] + l)
    return lengths

def _compare_by_arc_length(samples1, lengths1, samples2, lengths2):
    # For every sample in 1, find the linearly interpolated position in 2 at the same arc length
    max_dev = 0.0
    for s1, l1 in zip(samples1, lengths1):
        # find segment in 2
        idx = 0
        while idx < len(lengths2) - 2 and lengths2[idx+1] < l1:
            idx += 1
            
        l_start = lengths2[idx]
        l_end = lengths2[idx+1] if idx + 1 < len(lengths2) else l_start
        s_start = samples2[idx]
        s_end = samples2[idx+1] if idx + 1 < len(samples2) else s_start
        
        if l_end == l_start:
            px, py, pz = s_start.position.x, s_start.position.y, s_start.position.z
        else:
            t = (l1 - l_start) / (l_end - l_start)
            px = s_start.position.x + t * (s_end.position.x - s_start.position.x)
            py = s_start.position.y + t * (s_end.position.y - s_start.position.y)
            pz = s_start.position.z + t * (s_end.position.z - s_start.position.z)
            
        dx = s1.position.x - px
        dy = s1.position.y - py
        dz = s1.position.z - pz
        dev = math.sqrt(dx*dx + dy*dy + dz*dz)
        if dev > max_dev:
            max_dev = dev
    return max_dev

def test_sparse_vs_dense_equivalence():
    # Replace straight-line proof with deterministic nonlinear curve.
    # Curve: y = 0.5 * x^2 for x in [0, 0.1].
    # Generate sparse, dense, and uneven samplings of the exact same mathematical path.
    dense_t = [i / 100.0 for i in range(101)] # 101 points
    sparse_t = [i / 10.0 for i in range(11)]   # 11 points
    uneven_t = [0.0, 0.01, 0.02, 0.2, 0.4, 0.6, 0.8, 0.98, 0.99, 1.0]
    
    traj_dense = create_trajectory(_sample_curve(dense_t))
    traj_sparse = create_trajectory(_sample_curve(sparse_t))
    traj_uneven = create_trajectory(_sample_curve(uneven_t))
    
    p_dense = process_trajectory(traj_dense)
    p_sparse = process_trajectory(traj_sparse)
    p_uneven = process_trajectory(traj_uneven)
    
    # Explain correspondence method:
    # Since resampling produces uniform arc-length spacing, the resulting points 
    # might not perfectly align in index. We use cumulative arc-length to establish
    # a continuous parameterization of each processed trajectory, and then 
    # linearly interpolate the coordinates at corresponding arc-lengths to measure deviation.
    l_dense = _get_arc_lengths(p_dense.samples)
    l_sparse = _get_arc_lengths(p_sparse.samples)
    l_uneven = _get_arc_lengths(p_uneven.samples)
    
    dev_sparse = _compare_by_arc_length(p_dense.samples, l_dense, p_sparse.samples, l_sparse)
    dev_uneven = _compare_by_arc_length(p_dense.samples, l_dense, p_uneven.samples, l_uneven)
    
    # Prove that sampling density does not materially change the processed geometric path
    # Maximum deviation <= 1e-4 m
    assert dev_sparse <= 1e-4
    assert dev_uneven <= 1e-4

def test_spacing_invariant():
    # Verify explicitly that resampling produces exactly 5mm Euclidean spacing 
    # for a straight line, and smoothing alters it when bent.
    from holomed.anatomy.surface_generation import resample_trajectory
    pts = []
    # Create a straight line first
    for i in range(20):
        pts.append((i * 0.004, 0, 0))
    traj = create_trajectory(pts)
    resampled = resample_trajectory(traj)
    
    for i in range(len(resampled.samples) - 1):
        d = math.sqrt((resampled.samples[i].position.x - resampled.samples[i+1].position.x)**2 + 
                      (resampled.samples[i].position.y - resampled.samples[i+1].position.y)**2 + 
                      (resampled.samples[i].position.z - resampled.samples[i+1].position.z)**2)
        if i < len(resampled.samples) - 2:
            assert abs(d - 0.005) < 1e-6 # Exactly 5mm

    # Now make it bent
    pts[10] = (pts[10][0], 0.01, 0)
    traj_bent = create_trajectory(pts)
    p = process_trajectory(traj_bent)
    # After smoothing, it is approximately 5mm but not exactly
    d0 = math.sqrt((p.samples[9].position.x - p.samples[10].position.x)**2 + 
                   (p.samples[9].position.y - p.samples[10].position.y)**2 + 
                   (p.samples[9].position.z - p.samples[10].position.z)**2)
    assert abs(d0 - 0.005) > 1e-6

def test_gap_rejection():
    # H >5cm gap rejection
    pts = [(0, 0, 0), (0.06, 0, 0), (0.1, 0, 0)]
    traj = create_trajectory(pts)
    with pytest.raises(TrajectoryValidationError, match="Excessive gap"):
        process_trajectory(traj)

def test_micro_movement_collapse():
    # I micro-movement collapse
    pts = [(0, 0, 0), (0.0005, 0, 0), (0.0008, 0, 0), (0.01, 0, 0)]
    traj = create_trajectory(pts)
    p = process_trajectory(traj)
    # Should ignore the micro movements and still reach 0.01 properly
    assert p.samples[-1].position.x == 0.01

def test_duplicate_points():
    # J duplicate points
    pts = [(0, 0, 0), (0, 0, 0), (0.02, 0, 0), (0.02, 0, 0), (0.04, 0, 0)]
    timestamps = [0.1, 0.2, 0.3, 0.4, 0.5]
    traj = create_trajectory(pts, timestamps)
    p = process_trajectory(traj)
    assert len(p.samples) >= 3

def test_duplicate_timestamp_semantics():
    # same spatial position + multiple timestamps -> first temporal timestamp survives.
    pts = [(0, 0, 0), (0, 0, 0), (0, 0, 0), (0.04, 0, 0), (0.08, 0, 0)]
    timestamps = [0.1, 0.2, 0.3, 0.4, 0.5]
    traj = create_trajectory(pts, timestamps)
    validated = validate_trajectory(traj)
    
    # The first sample (0,0,0) should have survived, retaining timestamp 0.1
    assert validated.samples[0].timestamp == 0.1
    # The next unique sample is at 0.4
    assert validated.samples[1].timestamp == 0.4
    
    # confirm duplicate removal doesn't hide excessive gap
    pts_gap = [(0, 0, 0), (0, 0, 0), (0.06, 0, 0)]
    timestamps_gap = [0.1, 0.2, 0.3]
    traj_gap = create_trajectory(pts_gap, timestamps_gap)
    with pytest.raises(TrajectoryValidationError, match="Excessive gap"):
        validate_trajectory(traj_gap)

def test_timestamp_reversal():
    # K timestamp reversal
    pts = [(0, 0, 0), (0.01, 0, 0), (0.02, 0, 0)]
    timestamps = [0.0, 1.0, 0.5]
    traj = create_trajectory(pts, timestamps)
    with pytest.raises(TrajectoryValidationError, match="Non-monotonic"):
        process_trajectory(traj)

def test_nan_inf():
    # L NaN/Inf
    from holomed.anatomy.exceptions import AnatomyValidationError
    with pytest.raises(AnatomyValidationError):
        pts = [(0, 0, 0), (float('nan'), 0, 0), (0.02, 0, 0)]
        create_trajectory(pts)

def test_zero_length():
    # M zero-length
    pts = [(0, 0, 0), (0.002, 0, 0), (0.004, 0, 0)]
    traj = create_trajectory(pts)
    with pytest.raises(TrajectoryValidationError, match="Zero-length"):
        process_trajectory(traj)

def test_corner_preservation():
    # P corner preservation (>30 degrees)
    # Move along x by 0.1, then along y by 0.1
    pts = [(0, 0, 0), (0.04, 0, 0), (0.08, 0, 0), (0.1, 0, 0), (0.1, 0.04, 0), (0.1, 0.08, 0), (0.1, 0.1, 0)]
    traj = create_trajectory(pts)
    p = process_trajectory(traj)
    
    corner_found = False
    corner_idx = -1
    for i, s in enumerate(p.samples):
        if abs(s.position.x - 0.1) < 1e-6 and abs(s.position.y - 0.0) < 1e-6 and abs(s.position.z - 0.0) < 1e-6:
            corner_found = True
            corner_idx = i
            break
    assert corner_found, "Corner sample was destroyed by smoothing!"
    
    # Neighborhood semantics: samples on side A (i < corner_idx) must be strictly on Y=0
    # Samples on side B (i > corner_idx) must be strictly on X=0.1
    for i in range(corner_idx):
        assert abs(p.samples[i].position.y - 0.0) < 1e-6, "Side A bled into Y axis"
    for i in range(corner_idx + 1, len(p.samples)):
        assert abs(p.samples[i].position.x - 0.1) < 1e-6, "Side B bled into X axis"

def test_asymmetric_non_axis_aligned_corner():
    # Asymmetric non-axis-aligned corner. 
    # Left segment: y = x, Right segment: y = -2x + 0.3
    # Corner exactly at (0.1, 0.1, 0)
    # The segments are non-axis aligned. If the corner is NOT bypassed, 
    # or if right-side samples bleed into left-side samples, they would deviate from y = x.
    pts = [(0.0, 0.0, 0), (0.025, 0.025, 0), (0.05, 0.05, 0), (0.075, 0.075, 0), (0.1, 0.1, 0), 
           (0.11, 0.08, 0), (0.12, 0.06, 0), (0.13, 0.04, 0), (0.14, 0.02, 0)]
    traj = create_trajectory(pts)
    from holomed.anatomy.surface_generation import smooth_trajectory
    p = smooth_trajectory(traj)
    
    corner_found = False
    corner_idx = -1
    for i, s in enumerate(p.samples):
        if abs(s.position.x - 0.1) < 1e-6 and abs(s.position.y - 0.1) < 1e-6 and abs(s.position.z - 0.0) < 1e-6:
            corner_found = True
            corner_idx = i
            break
    assert corner_found, "Corner sample was destroyed by smoothing!"
    
    # Left side should exactly lie on y = x
    for i in range(corner_idx):
        s = p.samples[i]
        assert abs(s.position.x - s.position.y) < 1e-6, f"Left side bled: {s.position}"
        
    # Right side should exactly lie on y = -2x + 0.3
    for i in range(corner_idx + 1, len(p.samples)):
        s = p.samples[i]
        # y = -2x + 0.3 => 2x + y - 0.3 = 0
        assert abs(2 * s.position.x + s.position.y - 0.3) < 1e-6, f"Right side bled: {s.position}"

def test_deterministic_replay():
    # Q deterministic replay
    pts = [(0, 0, 0), (0.04, 0.01, 0), (0.08, 0.03, 0.01), (0.12, 0.05, 0.02)]
    traj = create_trajectory(pts)
    p1 = process_trajectory(traj)
    p2 = process_trajectory(traj)
    
    for s1, s2 in zip(p1.samples, p2.samples):
        assert s1.position.x == s2.position.x
        assert s1.position.y == s2.position.y
        assert s1.position.z == s2.position.z

def test_curve_fidelity():
    # U curve fidelity
    pts = []
    for i in range(100):
        theta = i * 0.01
        pts.append((0.1 * math.cos(theta), 0.1 * math.sin(theta), 0))
    traj = create_trajectory(pts)
    p = process_trajectory(traj)
    
    # max deviation from the ideal circle
    max_err = 0.0
    for s in p.samples:
        r = math.sqrt(s.position.x**2 + s.position.y**2)
        err = abs(r - 0.1)
        if err > max_err: max_err = err
    assert max_err < 0.01

def test_immutability():
    # 11 immutability
    pts = [(0, 0, 0), (0.04, 0, 0), (0.08, 0, 0), (0.12, 0, 0)]
    traj = create_trajectory(pts)
    original_samples = traj.samples
    p = process_trajectory(traj)
    assert traj.samples is original_samples
    
    # Test nested immutability
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        traj.samples[0].position.x = 1.0
        
    with pytest.raises(TypeError):
        # Metadata should be immutable via MappingProxyType even though we passed a dict
        traj.sampling_metadata["new_key"] = "value"
        
    # Test a) normal CutTrajectory construction cannot mutate metadata
    mutable_meta = {"key": "val1"}
    t1 = CutTrajectory("id", "corr", tuple(), sampling_metadata=mutable_meta)
    mutable_meta["key"] = "val2"
    assert t1.sampling_metadata["key"] == "val1" # should have copied/frozen
    
    # Test b) validation does not expose mutable metadata
    t_valid = validate_trajectory(traj)
    with pytest.raises(TypeError):
        t_valid.sampling_metadata["new_key"] = "value"
        
    # Test c) ProcessedCutTrajectory does not expose mutable metadata
    with pytest.raises(TypeError):
        p.sampling_metadata["new_key"] = "value"
        
    # Test d) input trajectory metadata remains unchanged
    assert traj.sampling_metadata["test_key"] == "test_value"
