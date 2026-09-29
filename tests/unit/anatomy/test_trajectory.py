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
    from types import MappingProxyType
    return CutTrajectory(
        trajectory_id="test",
        correlation_id="corr",
        samples=tuple(samples),
        sampling_metadata=MappingProxyType({})
    )

def test_valid_planar_trajectory():
    # A valid planar trajectory
    pts = [(0, 0, 0), (0.04, 0, 0), (0.08, 0, 0), (0.1, 0, 0), (0.1, 0.05, 0), (0.1, 0.1, 0)]
    traj = create_trajectory(pts)
    processed = process_trajectory(traj)
    assert len(processed.samples) >= 3

def test_sparse_vs_dense_equivalence():
    # F sparse vs dense equivalence
    sparse_pts = [(0, 0, 0), (0.04, 0, 0), (0.08, 0, 0), (0.12, 0, 0)]
    sparse_traj = create_trajectory(sparse_pts)
    
    dense_pts = [(i*0.001, 0, 0) for i in range(121)]
    dense_traj = create_trajectory(dense_pts)
    
    p_sparse = process_trajectory(sparse_traj)
    p_dense = process_trajectory(dense_traj)
    
    # Check length equivalent
    assert abs(len(p_sparse.samples) - len(p_dense.samples)) <= 1
    
    for s1, s2 in zip(p_sparse.samples, p_dense.samples):
        d = math.sqrt((s1.position.x - s2.position.x)**2 + (s1.position.y - s2.position.y)**2 + (s1.position.z - s2.position.z)**2)
        assert d <= 1e-4

def test_uneven_sampling():
    # G uneven sampling
    pts = [(0, 0, 0), (0.01, 0, 0), (0.011, 0, 0), (0.05, 0, 0), (0.09, 0, 0)]
    traj = create_trajectory(pts)
    p = process_trajectory(traj)
    # The resampler guarantees 5mm spacing on its output.
    # Smoothing alters this slightly. We verify the final spacing is approximately 5mm.
    for i in range(len(p.samples) - 1):
        d = math.sqrt((p.samples[i].position.x - p.samples[i+1].position.x)**2 + 
                      (p.samples[i].position.y - p.samples[i+1].position.y)**2 + 
                      (p.samples[i].position.z - p.samples[i+1].position.z)**2)
        assert abs(d - 0.005) < 1e-3 or i == len(p.samples) - 2 # last can be less

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
        # MappingProxyType prevents mutation
        traj.sampling_metadata["new_key"] = "value"
