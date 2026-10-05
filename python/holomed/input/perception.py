"""HoloMed AI - MediaPipe Perception Layer."""

import abc
import logging
import threading
import time
from typing import Any, Optional, Tuple

try:
    import mediapipe as mp # type: ignore
    from mediapipe.tasks import python as mp_python # type: ignore
    from mediapipe.tasks.python import vision as mp_vision # type: ignore
    import numpy as np # type: ignore
    HAS_MEDIAPIPE = True
except ImportError:
    mp = None # type: ignore
    mp_python = None # type: ignore
    mp_vision = None # type: ignore
    np = None # type: ignore
    HAS_MEDIAPIPE = False

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

class ConcreteMediaPipeAdapter(IMediaPipeAdapter):
    """
    Concrete implementation wrapping the real mediapipe Hands solution.
    Expects image_data to be an RGB numpy array.
    """
    def __init__(self, min_detection_confidence: float = 0.5, min_tracking_confidence: float = 0.5):
        self._landmarker = None
        if not HAS_MEDIAPIPE:
            logger.warning("mediapipe is not installed. ConcreteMediaPipeAdapter will fail safely.")
            return
        
        import os
        model_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "tests", "assets", "hand_landmarker.task")
        
        if not os.path.exists(model_path):
            logger.warning(f"Missing hand_landmarker.task at {model_path}. ConcreteMediaPipeAdapter will fail safely.")
            return
            
        base_options = mp_python.BaseOptions(model_asset_path=model_path) # type: ignore
        options = mp_vision.HandLandmarkerOptions( # type: ignore
            base_options=base_options,
            num_hands=1,
            min_hand_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence
        )
        self._landmarker = mp_vision.HandLandmarker.create_from_options(options) # type: ignore

    def process_frame(self, image_data: Any) -> Tuple[bool, float, tuple[tuple[float, float, float], ...]]:
        if self._landmarker is None:
            return False, 0.0, ()
        if not HAS_MEDIAPIPE or np is None or not isinstance(image_data, np.ndarray): # type: ignore
            return False, 0.0, ()

        if image_data.ndim != 3 or image_data.shape[2] != 3:
            return False, 0.0, ()

        # MediaPipe expects contiguous numpy arrays
        img = image_data if image_data.flags['C_CONTIGUOUS'] else np.ascontiguousarray(image_data) # type: ignore
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img) # type: ignore

        results = self._landmarker.detect(mp_image)

        if not results.hand_landmarks or not results.handedness:
            return False, 0.0, ()

        # Get the first hand
        hand_landmarks = results.hand_landmarks[0]
        handedness = results.handedness[0]
        confidence = float(handedness[0].score)

        # Convert landmarks
        landmarks = tuple((float(lm.x), float(lm.y), float(lm.z)) for lm in hand_landmarks)
        return True, confidence, landmarks


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
        self._is_healthy = True

        # Correlation lineage tracking
        self._current_correlation_id: Optional[str] = None

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @property
    def is_healthy(self) -> bool:
        with self._lock:
            return self._is_healthy

    def start(self) -> None:
        """Starts the perception processing thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._is_healthy = True
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
                    with self._lock:
                        self._is_healthy = False
                        self._current_correlation_id = None
                    self._stop_event.wait(0.05)
                    continue

                t_end = time.perf_counter()
                latency_ms = (t_end - t_start) * 1000.0

                # Performance instrumentation
                if latency_ms > 100.0:
                    logger.debug(f"Perception latency high: {latency_ms:.2f}ms")

                if not success or conf < self._confidence_threshold:
                    # No hand detected or low confidence: reset correlation lineage
                    with self._lock:
                        self._current_correlation_id = None
                    continue

                # We have a valid detection, so we ensure a correlation_id exists
                with self._lock:
                    if self._current_correlation_id is None:
                        self._current_correlation_id = generate_correlation_id()
                    current_correlation_id = self._current_correlation_id

                # Create immutable observation
                try:
                    obs = PerceptionObservation(
                        capture_timestamp_ns=frame.capture_timestamp_ns,
                        perception_timestamp_ns=time.monotonic_ns(),
                        frame_sequence=frame.sequence_number,
                        correlation_id=current_correlation_id,
                        landmarks=landmarks,
                        confidence=conf
                    )
                except ValueError as e:
                    # Malformed landmark data rejection
                    logger.error(f"Malformed observation rejected: {e}")
                    with self._lock:
                        self._current_correlation_id = None
                    continue

                with self._lock:
                    # Only overwrite if the new observation is strictly newer
                    if self._latest_observation is None or obs.frame_sequence > self._latest_observation.frame_sequence:
                        self._latest_observation = obs

            except Exception as e:
                logger.error(f"Unexpected error in perception loop: {e}")
                with self._lock:
                    self._is_healthy = False
                self._stop_event.wait(0.05)
