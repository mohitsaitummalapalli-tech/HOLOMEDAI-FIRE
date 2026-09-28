import time
from typing import Any, Tuple, Optional

import pytest

from holomed.input.camera import CameraInputNode, CameraFrame, ICameraSource
from holomed.input.perception import MediaPipePerceptionPipeline, IMediaPipeAdapter


class MockCameraSource(ICameraSource):
    def __init__(self, frames: list[CameraFrame]):
        self._frames = frames
        self._index = 0
        self._is_opened = True

    def read(self) -> Tuple[bool, Any]:
        if not self._is_opened or self._index >= len(self._frames):
            time.sleep(0.01)
            return True, "STALL"
        frame = self._frames[self._index]
        self._index += 1
        return True, frame.image_data

    def release(self) -> None:
        self._is_opened = False

    def is_opened(self) -> bool:
        return self._is_opened


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
    class DirectMockNode:
        def __init__(self):
            self.frames = []
        def get_latest_frame(self):
            if self.frames:
                return self.frames.pop(0)
            return None
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
    
    # We use a trick to bypass the camera thread since we injected a DirectMockNode
    # actually, CameraInputNode has start/stop. The pipeline expects a type compatible with CameraInputNode.
    # Our direct mock has get_latest_frame.
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


def test_stale_frame_rejection(mock_camera_node):
    # Pass seq 2, then seq 1
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
    
    # It should process seq 2 and ignore seq 1
    assert obs is not None
    assert obs.frame_sequence == 2
    assert obs.landmarks[0] == (0.1, 0.2, 0.3)


def test_duplicate_frame_rejection(mock_camera_node):
    # Same sequence number multiple times
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
    # Wait, the first frame is processed. 
    # Since adapter consumes responses in order, if it processed the second frame, 
    # the second response would overwrite.
    # Because it rejected the duplicate seq=3, the second frame wasn't processed!
    # Therefore the adapter's index is only 1.
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
    
    # Thread should still be alive
    assert pipeline._thread is not None and pipeline._thread.is_alive()
    
    # Recover adapter
    adapter.exception_to_raise = None
    
    # Append the next frame so it can be successfully processed
    mock_camera_node.frames.append(CameraFrame(2, 2000, 2001, "img2"))
    
    time.sleep(0.05)
    
    obs = pipeline.get_latest_observation()
    pipeline.stop()
    
    assert obs is not None
    assert obs.frame_sequence == 2

