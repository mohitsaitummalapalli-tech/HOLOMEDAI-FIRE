"""HoloMed AI - MediaPipe Perception Layer."""

import abc
import logging
import threading
import time
from typing import Any, Optional, Tuple

from holomed.input.camera import CameraInputNode, CameraFrame
from holomed.input.models import PerceptionObservation, generate_correlation_id

logger = logging.getLogger(__name__)


class IMediaPipeAdapter(abc.ABC):
    """Adapter for testing MediaPipe behavior without physical inference."""
    
    @abc.abstractmethod
    def process_frame(self, image_data: Any) -> Tuple[bool, float, tuple[tuple[float, float, float], ...]]:
        """
        Process a single image frame.
        Returns: (success, confidence, landmarks)
        If success is False, the frame did not contain a valid detection (e.g., no hand).
        """


class MediaPipePerceptionPipeline:
    """
    Consumes frames from CameraInputNode, runs them through MediaPipe,
    and produces PerceptionObservation objects.
    Enforces confidence thresholds and stale frame rejection.
    """
    
    def __init__(
        self, 
        camera_node: CameraInputNode, 
        adapter: IMediaPipeAdapter, 
        confidence_threshold: float = 0.5
    ):
        self._camera_node = camera_node
        self._adapter = adapter
        self._confidence_threshold = confidence_threshold
        
        self._lock = threading.Lock()
        self._latest_observation: Optional[PerceptionObservation] = None
        self._last_processed_sequence: int = -1
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
    def start(self) -> None:
        """Starts the perception processing thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="MediaPipePerceptionPipeline",
                daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Deterministically stops the perception thread."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
        
        if self._thread and self._thread.is_alive():
            self._thread.join()

    def get_latest_observation(self) -> Optional[PerceptionObservation]:
        """
        CONSUME-ONCE pop of the latest valid observation.
        Returns None if no new observation has been produced since the last call.
        """
        with self._lock:
            obs = self._latest_observation
            self._latest_observation = None
            return obs

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                frame = self._camera_node.get_latest_frame()
                if frame is None:
                    # No new frame available, wait briefly
                    self._stop_event.wait(0.01)
                    continue
                    
                with self._lock:
                    if frame.sequence_number <= self._last_processed_sequence:
                        # Reject stale/duplicate frame
                        continue
                    self._last_processed_sequence = frame.sequence_number
                    
                # Process frame
                t_start = time.perf_counter()
                
                try:
                    success, conf, landmarks = self._adapter.process_frame(frame.image_data)
                except Exception as e:
                    logger.error(f"MediaPipe adapter raised exception: {e}")
                    # Safe outcome: no observation generated.
                    continue
                    
                t_end = time.perf_counter()
                latency_ms = (t_end - t_start) * 1000.0
                
                # Performance instrumentation
                if latency_ms > 100.0:
                    logger.debug(f"Perception latency high: {latency_ms:.2f}ms")
                    
                if not success:
                    # No hand detected or otherwise explicitly failed processing
                    continue
                    
                if conf < self._confidence_threshold:
                    # Low confidence rejection: deterministic drop
                    continue
                    
                # Create immutable observation
                try:
                    obs = PerceptionObservation(
                        capture_timestamp_ns=frame.capture_timestamp_ns,
                        perception_timestamp_ns=time.monotonic_ns(),
                        frame_sequence=frame.sequence_number,
                        correlation_id=generate_correlation_id(),
                        landmarks=landmarks,
                        confidence=conf
                    )
                except ValueError as e:
                    # Malformed landmark data rejection
                    logger.error(f"Malformed observation rejected: {e}")
                    continue
                
                with self._lock:
                    # Only overwrite if the new observation is strictly newer
                    if self._latest_observation is None or obs.frame_sequence > self._latest_observation.frame_sequence:
                        self._latest_observation = obs
                    
            except Exception as e:
                logger.error(f"Unexpected error in perception loop: {e}")
                self._stop_event.wait(0.05)
