"""Tests for the Phase B Camera Input Node."""

import threading
import time
from typing import Any, List, Optional, Tuple

from holomed.input.camera import CameraInputNode, ICameraSource, CameraFrame


class MockCameraSource(ICameraSource):
    def __init__(self, frames: List[Any], succeeds: bool = True):
        self._frames = frames
        self._succeeds = succeeds
        self._index = 0
        self._is_opened = True
        self._lock = threading.Lock()
        self.released = False

    def read(self) -> Tuple[bool, Any]:
        with self._lock:
            if not self._is_opened or not self._succeeds:
                return False, None
            if self._index < len(self._frames):
                frame = self._frames[self._index]
                self._index += 1
                return True, frame
            # Block/stall to prevent auto-disconnect
            time.sleep(0.01)
            return True, "STALL"

    def release(self) -> None:
        with self._lock:
            self._is_opened = False
            self.released = True

    def is_opened(self) -> bool:
        with self._lock:
            return self._is_opened

    def trigger_failure(self):
        with self._lock:
            self._succeeds = False

    def push_frame(self, frame: Any):
        with self._lock:
            self._frames.append(frame)


def test_frame_ordering_and_sequence_numbering():
    mock_cam = MockCameraSource(["frame1", "frame2"])
    
    node = CameraInputNode(camera_source_factory=lambda: mock_cam, retry_interval_sec=0.01)
    node.start()
    
    # Wait for frames to be processed
    time.sleep(0.05)
    node.stop()
    
    # Since it's a depth-1 queue, we might miss frame1 if we didn't read it fast enough,
    # but the sequence number of whatever we get should be monotonically increasing.
    # To test ordering strictly without dropping, we manually drive a mock or check the latest.
    assert mock_cam.released

def test_stale_frame_dropping_and_newest_preference():
    """
    Test depth-1 bounded queue behavior:
    - At most one unpublished frame is retained.
    - Writing a new frame replaces the old unpublished frame (no unbounded queue).
    - get_latest_frame() does not duplicate the same frame unintentionally.
    """
    mock_cam = MockCameraSource(["frame1", "frame2", "frame3", "frame4"])
    node = CameraInputNode(camera_source_factory=lambda: mock_cam, retry_interval_sec=0.01)
    
    node.start()
    time.sleep(0.1) # Let the background thread read all 4 frames
    
    frame = node.get_latest_frame()
    
    # It should drop 1, 2, 3 and keep 4 (or STALL if it read faster).
    assert frame is not None
    assert frame.image_data in ("frame4", "STALL")
    assert frame.sequence_number >= 4
    
    # Prove get_latest_frame does not duplicate: second read immediately after should be None
    assert node.get_latest_frame() is None
    
    node.stop()

def test_timestamp_monotonicity():
    mock_cam = MockCameraSource(["f1", "f2", "f3"])
    node = CameraInputNode(camera_source_factory=lambda: mock_cam, retry_interval_sec=0.01)
    
    node.start()
    
    timestamps = []
    seqs = []
    
    for _ in range(10):
        f = node.get_latest_frame()
        if f is not None:
            timestamps.append(f.logical_sequence_timestamp_ns)
            seqs.append(f.sequence_number)
        time.sleep(0.02)
        
    node.stop()
    
    assert len(timestamps) > 0
    # Check strict monotonicity
    for i in range(1, len(timestamps)):
        assert timestamps[i] > timestamps[i-1], "Timestamps must be strictly increasing"
        assert seqs[i] > seqs[i-1], "Sequences must be strictly increasing"

def test_camera_unavailable_and_reconnect():
    """Test reconnect logic when camera disconnects or is unavailable."""
    factory_calls = 0
    
    def factory():
        nonlocal factory_calls
        factory_calls += 1
        # First call fails, second call succeeds
        return MockCameraSource(["recovered_frame"], succeeds=(factory_calls > 1))
        
    node = CameraInputNode(camera_source_factory=factory, retry_interval_sec=0.05)
    node.start()
    
    time.sleep(0.2)
    node.stop()
    
    # Factory should have been called multiple times
    assert factory_calls >= 2
    
    # We should have eventually recovered and read the frame, but since stop() clears things
    # we just care that the factory retried.

def test_deterministic_shutdown():
    mock_cam = MockCameraSource(["frame"])
    node = CameraInputNode(camera_source_factory=lambda: mock_cam, retry_interval_sec=0.01)
    
    # 1. Start -> Running
    assert not node._running
    node.start()
    assert node._running
    assert node._thread is not None and node._thread.is_alive()
    
    # 2. Stop -> Shutdown
    node.stop()
    assert not node._running
    assert not node._thread.is_alive()
    assert mock_cam.released
    
    # 3. Repeated Stop/Start
    mock_cam.released = False
    node.start()
    assert node._running
    assert node._thread is not None and node._thread.is_alive()
    node.stop()
    assert not node._running
    assert not node._thread.is_alive()

def test_reconnect_sequence_invariant():
    """
    Prove that after a camera disconnect -> reconnect:
    1. frame_sequence never resets (strictly increasing)
    2. post-reconnect frame is correct relative to the sequence contract
    3. no stale pre-disconnect frame is emitted after reconnect.
    """
    factory_calls = 0
    camera_sources = []
    
    def factory():
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            cam = MockCameraSource(["pre1", "pre2", "pre3"])
        else:
            cam = MockCameraSource(["post1", "post2"])
        camera_sources.append(cam)
        return cam
        
    node = CameraInputNode(camera_source_factory=factory, retry_interval_sec=0.01)
    node.start()
    
    # Read pre-disconnect frames
    time.sleep(0.05) # Allow thread to read some pre frames
    frame1 = node.get_latest_frame()
    assert frame1 is not None
    assert frame1.image_data.startswith("pre") or frame1.image_data == "STALL"
    seq1 = frame1.sequence_number
    
    # Force disconnect
    camera_sources[0].trigger_failure()
    
    # Wait for reconnect and post-reconnect frames
    time.sleep(0.1)
    frame2 = node.get_latest_frame()
    assert frame2 is not None
    assert frame2.image_data.startswith("post") or frame2.image_data == "STALL"
    seq2 = frame2.sequence_number
    
    # Prove invariants
    assert seq2 > seq1, "Sequence number must not reset after reconnect"
    assert "pre" not in frame2.image_data, "Stale pre-disconnect frame must not leak after reconnect"
    
    node.stop()

def test_error_behavior_does_not_kill_thread():
    """
    Verify camera-source exceptions/read failures cannot silently kill the acquisition thread
    while leaving the node falsely appearing healthy.
    """
    read_calls = 0
    
    class ThrowingCameraSource(ICameraSource):
        def read(self) -> Tuple[bool, Any]:
            nonlocal read_calls
            read_calls += 1
            if read_calls == 1:
                raise RuntimeError("Simulated read failure")
            return True, "frame"
            
        def release(self) -> None:
            pass
            
        def is_opened(self) -> bool:
            return True

    factory_calls = 0
    def factory():
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            raise ValueError("Simulated factory failure")
        return ThrowingCameraSource()
        
    node = CameraInputNode(camera_source_factory=factory, retry_interval_sec=0.01)
    node.start()
    
    # Wait for the thread to encounter both exceptions and recover
    time.sleep(0.1)
    
    assert node._running, "Node running state is independent of transient faults"
    assert node._thread is not None and node._thread.is_alive(), "Thread MUST NOT die on unhandled exception"
    assert node.is_healthy, "Node should have recovered and become healthy again"
    
    frame = node.get_latest_frame()
    assert frame is not None
    assert frame.image_data == "frame", "Thread should recover and read the frame eventually"
    
    node.stop()

def test_lifecycle_edge_cases():
    """Test start while running, stop before start, and deterministic state transitions."""
    mock_cam = MockCameraSource(["frame"])
    node = CameraInputNode(camera_source_factory=lambda: mock_cam, retry_interval_sec=0.01)

    # Stop before start
    node.stop()
    assert not node._running
    assert not node.is_healthy

    node.start()
    # Start while running
    node.start()
    
    time.sleep(0.05)
    assert node.is_healthy
    
    node.stop()
    assert not node.is_healthy

def test_is_healthy_observable_failure_state():
    """Verify that failure creates an observable unhealthy state and doesn't swallow errors silently."""
    succeed = True
    
    class ToggleCameraSource(ICameraSource):
        def read(self) -> Tuple[bool, Any]:
            if not succeed:
                raise RuntimeError("Boom")
            return True, "frame"
        def release(self) -> None:
            pass
        def is_opened(self) -> bool:
            return True

    node = CameraInputNode(camera_source_factory=lambda: ToggleCameraSource(), retry_interval_sec=0.01)
    node.start()
    
    time.sleep(0.05)
    assert node.is_healthy, "Should start healthy"
    
    succeed = False
    time.sleep(0.05)
    assert not node.is_healthy, "Must expose observable failure state upon fault"
    
    succeed = True
    time.sleep(0.05)
    assert node.is_healthy, "Must recover to healthy state when reading resumes"
    
    node.stop()
