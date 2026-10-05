"""HoloMed AI - Camera Input Pipeline."""

import abc
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraFrame:
    """Represents a single captured frame with strict sequence and monotonic timestamp."""
    sequence_number: int
    capture_timestamp_ns: int  # Raw physical acquisition clock timestamp
    logical_sequence_timestamp_ns: int  # Strictly increasing monotonic timestamp for logical ordering
    image_data: Any  # e.g., numpy array


class ICameraSource(abc.ABC):
    """Abstract interface for a camera device (e.g., cv2.VideoCapture or a mock)."""

    @abc.abstractmethod
    def read(self) -> Tuple[bool, Any]:
        """Read the next frame. Returns (success, frame_data)."""

    @abc.abstractmethod
    def release(self) -> None:
        """Release physical camera resources."""

    @abc.abstractmethod
    def is_opened(self) -> bool:
        """Return True if the camera is currently open and ready."""


class OpenCVCameraSource(ICameraSource):
    """
    Physical camera source using OpenCV.
    Fails safely if cv2 is not installed or camera is unavailable.
    """
    def __init__(self, camera_index: int = 0):
        self._cap = None
        try:
            import cv2  # type: ignore
            self._cap = cv2.VideoCapture(camera_index)
        except ImportError:
            logger.warning("cv2 is not installed. OpenCVCameraSource will fail safely.")
        except Exception as e:
            logger.warning(f"Failed to initialize camera {camera_index}: {e}")

    def read(self) -> Tuple[bool, Any]:
        if self._cap is None:
            return False, None
        success, frame = self._cap.read()
        if success:
            try:
                import cv2  # type: ignore
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            except Exception:
                pass
        return success, frame

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def is_opened(self) -> bool:
        return self._cap is not None and self._cap.isOpened()


class CameraInputNode:
    """
    Manages the physical camera lifecycle, reads frames in a background thread,
    and maintains a bounded depth-1 latest-frame buffer.
    """

    def __init__(self, camera_source_factory: Callable[[], ICameraSource], retry_interval_sec: float = 1.0):
        self._source_factory = camera_source_factory
        self._retry_interval_sec = retry_interval_sec
        
        self._lock = threading.Lock()
        self._latest_frame: Optional[CameraFrame] = None
        self._sequence_counter: int = 0
        self._last_timestamp_ns: int = 0
        self._is_healthy: bool = False
        self._last_error_log_time: float = 0.0
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._camera_source: Optional[ICameraSource] = None

    def start(self) -> None:
        """Starts the camera acquisition thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._is_healthy = False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._acquisition_loop,
                name="CameraInputNode_Acquisition",
                daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """Deterministically stops the camera thread and releases resources."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
        
        if self._thread and self._thread.is_alive():
            self._thread.join()
            
        with self._lock:
            if self._camera_source:
                self._camera_source.release()
                self._camera_source = None
            self._is_healthy = False

    @property
    def is_healthy(self) -> bool:
        """
        Observable health state: Returns True if the camera is actively acquired
        and reading frames without faults.
        """
        with self._lock:
            return self._is_healthy

    def _log_throttled_error(self, message: str) -> None:
        """Prevent unbounded log spam during continuous failure."""
        now = time.monotonic()
        if now - self._last_error_log_time > 5.0:
            logger.error(message)
            self._last_error_log_time = now

    def _acquisition_loop(self) -> None:
        """Background thread loop for continuous reading and reconnect logic."""
        while not self._stop_event.is_set():
            try:
                # 1. Ensure camera is open
                with self._lock:
                    camera = self._camera_source
                
                if camera is None or not camera.is_opened():
                    try:
                        new_camera = self._source_factory()
                        if new_camera.is_opened():
                            with self._lock:
                                self._camera_source = new_camera
                            camera = new_camera
                        else:
                            new_camera.release()
                            camera = None
                    except Exception as e:
                        self._log_throttled_error(f"Camera factory raised exception: {e}")
                        camera = None
                        with self._lock:
                            self._is_healthy = False

                # 2. Read frame or handle failure
                if camera is not None:
                    t0 = 0.0
                    t1 = 0.0
                    try:
                        t0 = time.perf_counter()
                        success, frame_data = camera.read()
                        t1 = time.perf_counter()
                    except Exception as e:
                        self._log_throttled_error(f"Camera source read raised exception: {e}")
                        success = False
                        frame_data = None
                        
                    if success:
                        # Successfully read a frame
                        read_time_ms = (t1 - t0) * 1000.0
                        # Basic performance instrumentation
                        if read_time_ms > 50.0:
                            logger.debug(f"Camera frame read took {read_time_ms:.2f}ms")
                            
                        raw_timestamp = time.monotonic_ns()
                        logical_timestamp = raw_timestamp
                        with self._lock:
                            self._is_healthy = True
                            
                            if logical_timestamp <= self._last_timestamp_ns:
                                logical_timestamp = self._last_timestamp_ns + 1
                            self._last_timestamp_ns = logical_timestamp
                            
                            self._sequence_counter += 1
                            seq = self._sequence_counter
                            # Bounded depth-1 buffer: implicitly drop stale frames by overwriting
                            self._latest_frame = CameraFrame(
                                sequence_number=seq,
                                capture_timestamp_ns=raw_timestamp,
                                logical_sequence_timestamp_ns=logical_timestamp,
                                image_data=frame_data
                            )
                    else:
                        # Disconnect / failure
                        with self._lock:
                            self._is_healthy = False
                            if self._camera_source:
                                self._camera_source.release()
                                self._camera_source = None
                        # Fall through to retry sleep
                
                # 3. If disconnected or unavailable, sleep before retry
                with self._lock:
                    current_camera = self._camera_source
                    
                if current_camera is None:
                    # Safe unavailable behavior: thread is alive but sleeping interruptibly
                    self._stop_event.wait(self._retry_interval_sec)
                    
            except Exception as e:
                self._log_throttled_error(f"Unexpected error in camera acquisition loop: {e}")
                with self._lock:
                    self._is_healthy = False
                # Wait briefly to prevent rapid failure loop CPU pegging
                self._stop_event.wait(self._retry_interval_sec)

    def get_latest_frame(self) -> Optional[CameraFrame]:
        """
        CONSUME-ONCE destructive pop of the latest unpublished frame (depth-1).
        - One unpublished frame -> one successful retrieval -> slot empty.
        - Repeated retrieval does not duplicate the frame, returning None until a new frame arrives.
        """
        with self._lock:
            frame = self._latest_frame
            self._latest_frame = None
            return frame
