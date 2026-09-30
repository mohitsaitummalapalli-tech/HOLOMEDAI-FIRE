import time
from typing import Any, Tuple, Optional
import pytest
import numpy as np

from holomed.input.camera import CameraInputNode, CameraFrame, ICameraSource
from holomed.input.perception import (
    MediaPipePerceptionPipeline,
    IMediaPipeAdapter,
    ConcreteMediaPipeAdapter,
    HAS_MEDIAPIPE
)


class MockMediaPipeAdapter(IMediaPipeAdapter):
    def __init__(self, responses: list[Tuple[bool, float, tuple]]):
        self._responses = responses
        self._index = 0
        self.exception_to_raise: Optional[Exception] = None

    def process_frame(self, image_data: Any) -> Tuple[bool, float, tuple[tuple[float, float, float], ...]]:
        if self.exception_to_raise:
            raise self.exception_to_raise
        if self._index >= len(self._responses):
            return False, 0.0, ()
        resp = self._responses[self._index]
        self._index += 1
        return resp


@pytest.fixture
def mock_camera_node():
    class DirectMockNode(CameraInputNode):
        def __init__(self):
            # Not calling super since we are mocking methods directly for this test
            self.frames = []
        def get_latest_frame(self):
            if self.frames:
                return self.frames.pop(0)
            return None
        def start(self): pass
        def stop(self): pass
        def is_opened(self): return True
    return DirectMockNode()


def test_valid_landmark_extraction(mock_camera_node):
    frame = CameraFrame(
        sequence_number=1,
        capture_timestamp_ns=1000,
        logical_sequence_timestamp_ns=1001,
        image_data="img1"
    )
    mock_camera_node.frames.append(frame)

    adapter = MockMediaPipeAdapter([
        (True, 0.9, ((0.1, 0.2, 0.3), (0.4, 0.5, 0.6)))
    ])

    pipeline = MediaPipePerceptionPipeline(mock_camera_node, adapter, confidence_threshold=0.5)
    pipeline.start()

    time.sleep(0.05)
    obs = pipeline.get_latest_observation()
    pipeline.stop()

    assert obs is not None
    assert obs.confidence == 0.9
    assert obs.frame_sequence == 1
    assert obs.capture_timestamp_ns == 1000
    assert obs.perception_timestamp_ns > 1000
    assert len(obs.landmarks) == 2
    assert obs.landmarks[0] == (0.1, 0.2, 0.3)
    assert obs.correlation_id is not None
    assert pipeline.is_healthy


def test_no_hand_result(mock_camera_node):
    frame = CameraFrame(1, 1000, 1001, "img1")
    mock_camera_node.frames.append(frame)

    adapter = MockMediaPipeAdapter([
        (False, 0.0, ())
    ])

    pipeline = MediaPipePerceptionPipeline(mock_camera_node, adapter, confidence_threshold=0.5)
    pipeline.start()
    time.sleep(0.05)
    obs = pipeline.get_latest_observation()
    pipeline.stop()

    assert obs is None
    assert pipeline.is_healthy


def test_low_confidence_rejection(mock_camera_node):
    frame = CameraFrame(1, 1000, 1001, "img1")
    mock_camera_node.frames.append(frame)

    adapter = MockMediaPipeAdapter([
        (True, 0.4, ((0.1, 0.2, 0.3),))
    ])

    pipeline = MediaPipePerceptionPipeline(mock_camera_node, adapter, confidence_threshold=0.5)
    pipeline.start()
    time.sleep(0.05)
    obs = pipeline.get_latest_observation()
    pipeline.stop()

    assert obs is None
    assert pipeline.is_healthy


def test_malformed_result_rejection(mock_camera_node):
    frame = CameraFrame(1, 1000, 1001, "img1")
    mock_camera_node.frames.append(frame)

    adapter = MockMediaPipeAdapter([
        (True, 0.9, ((0.1, 0.2),))  # Malformed: only 2 dimensions
    ])

    pipeline = MediaPipePerceptionPipeline(mock_camera_node, adapter, confidence_threshold=0.5)
    pipeline.start()
    time.sleep(0.05)
    obs = pipeline.get_latest_observation()
    pipeline.stop()

    assert obs is None
    assert pipeline.is_healthy


def test_stale_frame_rejection(mock_camera_node):
    mock_camera_node.frames.append(CameraFrame(2, 2000, 2001, "img2"))
    mock_camera_node.frames.append(CameraFrame(1, 1000, 1001, "img1"))

    adapter = MockMediaPipeAdapter([
        (True, 0.9, ((0.1, 0.2, 0.3),)),
        (True, 0.9, ((0.4, 0.5, 0.6),))
    ])

    pipeline = MediaPipePerceptionPipeline(mock_camera_node, adapter, confidence_threshold=0.5)
    pipeline.start()
    time.sleep(0.05)
    obs = pipeline.get_latest_observation()
    pipeline.stop()

    assert obs is not None
    assert obs.frame_sequence == 2
    assert obs.landmarks[0] == (0.1, 0.2, 0.3)


def test_duplicate_frame_rejection(mock_camera_node):
    mock_camera_node.frames.append(CameraFrame(3, 3000, 3001, "img3"))
    mock_camera_node.frames.append(CameraFrame(3, 3000, 3001, "img3"))

    adapter = MockMediaPipeAdapter([
        (True, 0.9, ((0.1, 0.2, 0.3),)),
        (True, 0.9, ((0.4, 0.5, 0.6),))
    ])

    pipeline = MediaPipePerceptionPipeline(mock_camera_node, adapter, confidence_threshold=0.5)
    pipeline.start()
    time.sleep(0.05)

    obs = pipeline.get_latest_observation()
    pipeline.stop()

    assert obs is not None
    assert obs.frame_sequence == 3
    assert adapter._index == 1
    assert obs.landmarks[0] == (0.1, 0.2, 0.3)


def test_adapter_exception_handling(mock_camera_node):
    mock_camera_node.frames.append(CameraFrame(1, 1000, 1001, "img1"))

    adapter = MockMediaPipeAdapter([
        (True, 0.9, ((0.1, 0.2, 0.3),))
    ])
    adapter.exception_to_raise = RuntimeError("Adapter crashed")

    pipeline = MediaPipePerceptionPipeline(mock_camera_node, adapter, confidence_threshold=0.5)
    pipeline.start()
    time.sleep(0.05)

    # Observe health transitioned to false
    assert pipeline.is_healthy is False

    # Thread should not crash the python process, but we expect failure state
    assert pipeline._thread is not None and pipeline._thread.is_alive()

    # A failure to process produces no valid observation
    obs = pipeline.get_latest_observation()
    assert obs is None

    pipeline.stop()


def _wait_for_obs(pipeline, timeout=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        obs = pipeline.get_latest_observation()
        if obs is not None:
            return obs
        time.sleep(0.005)
    return None

def test_correlation_id_persists_across_frames_in_same_interaction(mock_camera_node):
    adapter = MockMediaPipeAdapter([
        (True, 0.9, ((0.1, 0.2, 0.3),)),
        (True, 0.9, ((0.1, 0.2, 0.3),)),
        (True, 0.9, ((0.1, 0.2, 0.3),))
    ])

    pipeline = MediaPipePerceptionPipeline(mock_camera_node, adapter, confidence_threshold=0.5)
    pipeline.start()

    mock_camera_node.frames.append(CameraFrame(101, 1000, 1001, "img101"))
    obs101 = _wait_for_obs(pipeline)

    mock_camera_node.frames.append(CameraFrame(102, 2000, 2001, "img102"))
    obs102 = _wait_for_obs(pipeline)

    mock_camera_node.frames.append(CameraFrame(103, 3000, 3001, "img103"))
    obs103 = _wait_for_obs(pipeline)

    pipeline.stop()

    assert obs101 is not None and obs102 is not None and obs103 is not None
    assert obs101.frame_sequence == 101
    assert obs102.frame_sequence == 102
    assert obs103.frame_sequence == 103

    # Must carry the same correlation id while they are a single logical interaction
    assert obs101.correlation_id == obs102.correlation_id
    assert obs102.correlation_id == obs103.correlation_id


def test_new_interaction_receives_new_correlation_id(mock_camera_node):
    adapter = MockMediaPipeAdapter([
        (True, 0.9, ((0.1, 0.2, 0.3),)),  # Interaction 1
        (False, 0.0, ()),                 # No hand, breaks interaction
        (True, 0.9, ((0.4, 0.5, 0.6),))   # Interaction 2
    ])

    pipeline = MediaPipePerceptionPipeline(mock_camera_node, adapter, confidence_threshold=0.5)
    pipeline.start()

    # Frame 1: Valid
    mock_camera_node.frames.append(CameraFrame(1, 1000, 1001, "img1"))
    obs1 = _wait_for_obs(pipeline)

    # Frame 2: No hand (interaction ends)
    mock_camera_node.frames.append(CameraFrame(2, 2000, 2001, "img2"))
    obs2 = _wait_for_obs(pipeline, timeout=0.1) # Will be None

    # Frame 3: Valid (new interaction begins)
    mock_camera_node.frames.append(CameraFrame(3, 3000, 3001, "img3"))
    obs3 = _wait_for_obs(pipeline)

    pipeline.stop()

    assert obs1 is not None
    assert obs2 is None
    assert obs3 is not None

    assert obs1.frame_sequence == 1
    assert obs3.frame_sequence == 3
    assert obs1.correlation_id != obs3.correlation_id


@pytest.mark.skipif(not HAS_MEDIAPIPE, reason="MediaPipe is not installed; concrete adapter cannot be verified")
def test_concrete_mediapipe_runtime_proof():
    """
    Proves the actual concrete implementation is executable against static/controlled input.
    """
    # Create the concrete adapter
    adapter = ConcreteMediaPipeAdapter(min_detection_confidence=0.1, min_tracking_confidence=0.1)

    import os
    import importlib
    cv2 = importlib.import_module("cv2")
    img_path = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "hand.jpg")
    
    # Positive test: Real hand image
    image_data = cv2.imread(img_path)
    if image_data is None:
        pytest.fail(f"Could not load hand image at {img_path}")
    
    # Convert from BGR to RGB as MediaPipe expects RGB
    image_data_rgb = cv2.cvtColor(image_data, cv2.COLOR_BGR2RGB)
    
    success, confidence, landmarks = adapter.process_frame(image_data_rgb)
    
    # A known hand image should successfully process and detect a hand.
    assert success is True, "Failed to detect hand in static image asset."
    assert confidence > 0.0
    assert len(landmarks) > 0

    # Give it an entirely black numpy array (no hands)
    black_image_data = np.zeros((480, 640, 3), dtype=np.uint8)

    success, confidence, landmarks = adapter.process_frame(black_image_data)

    # A black frame should successfully process, but yield no hands.
    assert success is False
    assert confidence == 0.0
    assert len(landmarks) == 0

    # Malformed/unusable input - should safely fail (success=False), not crash.
    # Pass a random string instead of numpy array
    success, confidence, landmarks = adapter.process_frame("not_an_image")
    assert success is False
    assert confidence == 0.0
    assert len(landmarks) == 0
