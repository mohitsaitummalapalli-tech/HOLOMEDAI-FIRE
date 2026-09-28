import pytest
import time
from types import MappingProxyType
from holomed.input.models import (
    RawVideoFrame,
    PerceptionObservation,
    UltronIntent,
    generate_correlation_id
)

def test_generate_correlation_id_format():
    """Verify generated UUID string matches typical UUID length and format."""
    cid = generate_correlation_id()
    assert isinstance(cid, str)
    assert len(cid) == 36
    assert cid.count('-') == 4

def test_generate_correlation_id_uniqueness():
    """Verify consecutive correlation IDs are unique."""
    id1 = generate_correlation_id()
    id2 = generate_correlation_id()
    assert id1 != id2

def test_raw_video_frame_contract():
    frame = RawVideoFrame(
        capture_timestamp_ns=time.time_ns(),
        frame_sequence=1,
        data=b"mockdata",
        width=1920,
        height=1080
    )
    assert frame.width == 1920
    assert frame.height == 1080
    assert frame.frame_sequence == 1

def test_deep_immutability_raw_video_frame():
    """Test F: Nested/payload structures cannot mutate an immutable contract after construction."""
    with pytest.raises(ValueError, match="data must be immutable bytes"):
        RawVideoFrame(
            capture_timestamp_ns=time.time_ns(),
            frame_sequence=1,
            data=bytearray(b"mutable"),
            width=1920,
            height=1080
        )

def test_frame_sequence_and_correlation_id_semantics():
    """Test D: frame_sequence and correlation_id have distinct semantics."""
    cid = generate_correlation_id()
    obs = PerceptionObservation(
        capture_timestamp_ns=time.time_ns(),
        perception_timestamp_ns=time.time_ns(),
        frame_sequence=42,  # Represents the physical capture sequential order
        correlation_id=cid, # Represents the logical interaction tracking lineage
        landmarks=((0.1, 0.2, 0.3),),
        confidence=0.95
    )
    assert obs.frame_sequence == 42
    assert obs.correlation_id == cid
    assert isinstance(obs.correlation_id, str)
    assert isinstance(obs.frame_sequence, int)
    
    # Prove landmarks are deeply immutable
    assert isinstance(obs.landmarks, tuple)

def test_perception_observation_deep_immutability_and_aliasing():
    """Test F: nested landmark structures cannot mutate an immutable contract after construction."""
    cid = generate_correlation_id()
    
    # Original mutable list containing another list
    mutable_landmark = [0.1, 0.2, 0.3]
    mutable_landmarks_list = [mutable_landmark]
    
    obs = PerceptionObservation(
        capture_timestamp_ns=time.time_ns(),
        perception_timestamp_ns=time.time_ns(),
        frame_sequence=1,
        correlation_id=cid,
        landmarks=mutable_landmarks_list, # Passed as mutable list
        confidence=0.95
    )

    # Prove stored value is deeply frozen to tuples
    assert isinstance(obs.landmarks, tuple)
    assert isinstance(obs.landmarks[0], tuple)
    
    # Prove mutating the original alias has no effect on the stored contract
    mutable_landmarks_list.append([0.4, 0.5, 0.6])
    mutable_landmark[0] = 99.9
    
    assert len(obs.landmarks) == 1
    assert obs.landmarks[0] == (0.1, 0.2, 0.3)

def test_perception_observation_validation():
    with pytest.raises(ValueError, match="correlation_id must not be empty"):
        PerceptionObservation(
            capture_timestamp_ns=1,
            perception_timestamp_ns=2,
            frame_sequence=1,
            correlation_id="",
            landmarks=((0.1,0.2,0.3),),
            confidence=1.0
        )
    with pytest.raises(ValueError, match="confidence must be between 0.0 and 1.0"):
        PerceptionObservation(
            capture_timestamp_ns=1,
            perception_timestamp_ns=2,
            frame_sequence=1,
            correlation_id="cid",
            landmarks=((0.1,0.2,0.3),),
            confidence=1.5
        )

def test_ultron_intent_contract():
    cid = generate_correlation_id()
    intent = UltronIntent(
        correlation_id=cid,
        intent_timestamp_ns=time.time_ns(),
        action="GRASP",
        value=1.0,
        confidence=0.9
    )
    assert intent.action == "GRASP"
    assert intent.value == 1.0

def test_ultron_intent_validation():
    with pytest.raises(ValueError):
        UltronIntent(
            correlation_id="",
            intent_timestamp_ns=1,
            action="GRASP",
            value=1.0,
            confidence=0.9
        )
    with pytest.raises(ValueError):
        UltronIntent(
            correlation_id="cid",
            intent_timestamp_ns=1,
            action="GRASP",
            value=1.0,
            confidence=-0.1
        )
