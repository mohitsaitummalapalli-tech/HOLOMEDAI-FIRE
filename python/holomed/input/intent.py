"""HoloMed AI - Validated Intent Detection Layer (Phase D).

Converts PerceptionObservation → UltronIntent using deterministic
mathematical and temporal logic.

Intent is NOT authority. Intent is NOT a command.
Intent must never directly control Unity.

Architecture:
    PerceptionObservation
    → GraspDetector (landmark geometry)
    → IntentStateMachine (temporal stability / hysteresis)
    → UltronIntent (immutable validated semantic intent)
"""

import enum
import logging
import math
import time
import threading
from typing import Optional

from holomed.input.models import PerceptionObservation, UltronIntent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MediaPipe Hand Landmark Indices (21 landmarks total)
# ---------------------------------------------------------------------------
class HandLandmark(enum.IntEnum):
    """MediaPipe hand landmark indices."""
    WRIST = 0
    THUMB_CMC = 1
    THUMB_MCP = 2
    THUMB_IP = 3
    THUMB_TIP = 4
    INDEX_FINGER_MCP = 5
    INDEX_FINGER_PIP = 6
    INDEX_FINGER_DIP = 7
    INDEX_FINGER_TIP = 8
    MIDDLE_FINGER_MCP = 9
    MIDDLE_FINGER_PIP = 10
    MIDDLE_FINGER_DIP = 11
    MIDDLE_FINGER_TIP = 12
    RING_FINGER_MCP = 13
    RING_FINGER_PIP = 14
    RING_FINGER_DIP = 15
    RING_FINGER_TIP = 16
    PINKY_MCP = 17
    PINKY_PIP = 18
    PINKY_DIP = 19
    PINKY_TIP = 20


EXPECTED_LANDMARK_COUNT = 21

# Fingertip indices used for grasp geometry
_FINGERTIP_INDICES = (
    HandLandmark.THUMB_TIP,
    HandLandmark.INDEX_FINGER_TIP,
    HandLandmark.MIDDLE_FINGER_TIP,
    HandLandmark.RING_FINGER_TIP,
    HandLandmark.PINKY_TIP,
)

# MCP (knuckle base) indices for open-hand reference distances
_MCP_INDICES = (
    HandLandmark.INDEX_FINGER_MCP,
    HandLandmark.MIDDLE_FINGER_MCP,
    HandLandmark.RING_FINGER_MCP,
    HandLandmark.PINKY_MCP,
)


# ---------------------------------------------------------------------------
# Interaction State
# ---------------------------------------------------------------------------
class InteractionState(str, enum.Enum):
    """Observable interaction lifecycle states."""
    IDLE = "IDLE"                       # No interaction detected
    FORMING = "FORMING"                 # Evidence accumulating, not yet stable
    STABLE_GRASP = "STABLE_GRASP"       # Grasp is confirmed / temporally stable
    RELEASED = "RELEASED"               # Grasp was released


# Intent action constants
ACTION_GRASP = "GRASP"
ACTION_RELEASE = "RELEASE"
ACTION_NO_COMMAND = "NO_COMMAND"


# ---------------------------------------------------------------------------
# Grasp Geometry Detection (pure function, no state)
# ---------------------------------------------------------------------------
def _euclidean_3d(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    """Euclidean distance between two 3D points."""
    return math.sqrt(
        (a[0] - b[0]) ** 2 +
        (a[1] - b[1]) ** 2 +
        (a[2] - b[2]) ** 2
    )


def compute_grasp_ratio(landmarks: tuple[tuple[float, float, float], ...]) -> Optional[float]:
    """Compute a normalised grasp ratio from hand landmarks.

    Returns a value in [0.0, 1.0] where:
        ~0.0 = hand fully open
        ~1.0 = hand fully closed (grasp)

    Returns None if landmarks are malformed or insufficient.

    The ratio is computed as:
        mean(fingertip-to-wrist distances) / mean(MCP-to-wrist distances)

    A closed hand brings fingertips close to the wrist relative to the
    MCP joints. We invert this so that a smaller ratio → higher grasp value.
    """
    if len(landmarks) != EXPECTED_LANDMARK_COUNT:
        return None

    wrist = landmarks[HandLandmark.WRIST]

    # Validate all values are finite
    for lm in landmarks:
        for v in lm:
            if not math.isfinite(v):
                return None

    # Average fingertip-to-wrist distance
    tip_distances = [_euclidean_3d(landmarks[i], wrist) for i in _FINGERTIP_INDICES]
    mean_tip_dist = sum(tip_distances) / len(tip_distances)

    # Average MCP-to-wrist distance (normalisation reference)
    mcp_distances = [_euclidean_3d(landmarks[i], wrist) for i in _MCP_INDICES]
    mean_mcp_dist = sum(mcp_distances) / len(mcp_distances)

    if mean_mcp_dist < 1e-6:
        # Degenerate hand geometry
        return None

    # Ratio: when tips are close to wrist relative to MCP, ratio is small → grasp
    raw_ratio = mean_tip_dist / mean_mcp_dist

    # Clamp to [0, 1] where 1 = grasp, 0 = open
    # Empirically: open hand ratio ≈ 1.5–2.0, closed ≈ 0.5–0.8
    # Map: ratio ≤ 0.8 → grasp_value=1.0; ratio ≥ 1.6 → grasp_value=0.0
    GRASP_THRESHOLD_LOW = 0.8
    GRASP_THRESHOLD_HIGH = 1.6

    if raw_ratio <= GRASP_THRESHOLD_LOW:
        return 1.0
    if raw_ratio >= GRASP_THRESHOLD_HIGH:
        return 0.0

    # Linear interpolation between thresholds
    grasp_value = 1.0 - (raw_ratio - GRASP_THRESHOLD_LOW) / (GRASP_THRESHOLD_HIGH - GRASP_THRESHOLD_LOW)
    return max(0.0, min(1.0, grasp_value))


# ---------------------------------------------------------------------------
# Temporal Stability Configuration
# ---------------------------------------------------------------------------
class IntentConfig:
    """Deterministic configuration for intent detection parameters."""

    def __init__(
        self,
        *,
        min_stable_observations: int = 3,
        temporal_window_ns: int = 500_000_000,    # 500ms
        grasp_threshold: float = 0.6,
        release_threshold: float = 0.3,
        min_confidence: float = 0.5,
    ):
        if min_stable_observations < 1:
            raise ValueError("min_stable_observations must be >= 1")
        if temporal_window_ns <= 0:
            raise ValueError("temporal_window_ns must be positive")
        if not (0.0 < grasp_threshold <= 1.0):
            raise ValueError("grasp_threshold must be in (0.0, 1.0]")
        if not (0.0 <= release_threshold < grasp_threshold):
            raise ValueError("release_threshold must be in [0.0, grasp_threshold)")
        if not (0.0 <= min_confidence <= 1.0):
            raise ValueError("min_confidence must be in [0.0, 1.0]")

        self.min_stable_observations = min_stable_observations
        self.temporal_window_ns = temporal_window_ns
        self.grasp_threshold = grasp_threshold
        self.release_threshold = release_threshold
        self.min_confidence = min_confidence


# ---------------------------------------------------------------------------
# Intent State Machine
# ---------------------------------------------------------------------------
class IntentDetector:
    """Deterministic, temporally-stable intent detector.

    Consumes PerceptionObservation objects and produces UltronIntent
    when sufficient stable evidence is accumulated.

    Thread-safe: all mutable state is guarded by a lock.

    State machine:
        IDLE → FORMING       (first observation with grasp evidence)
        FORMING → STABLE_GRASP (min_stable_observations within temporal_window)
        FORMING → IDLE       (evidence fails or window expires)
        STABLE_GRASP → RELEASED (grasp ratio drops below release_threshold)
        RELEASED → IDLE      (immediate transition, emits RELEASE intent)

    Hysteresis: grasp_threshold > release_threshold prevents rapid toggling.
    """

    def __init__(self, config: Optional[IntentConfig] = None):
        self._config = config or IntentConfig()
        self._lock = threading.Lock()

        # State
        self._state = InteractionState.IDLE
        self._evidence_buffer: list[tuple[int, float]] = []   # (perception_timestamp_ns, grasp_ratio)
        self._current_correlation_id: Optional[str] = None
        self._last_processed_sequence: int = -1
        self._last_intent_sequence: int = -1
        self._grasp_intent_emitted: bool = False

    @property
    def state(self) -> InteractionState:
        """Current interaction state (thread-safe read)."""
        with self._lock:
            return self._state

    def process_observation(self, obs: PerceptionObservation) -> Optional[UltronIntent]:
        """Process a single PerceptionObservation and optionally produce a UltronIntent.

        Returns:
            UltronIntent if a new validated intent is produced.
            None if the observation does not result in a new intent
            (no command / evidence accumulating / duplicate).
        """
        with self._lock:
            return self._process_locked(obs)

    def _process_locked(self, obs: PerceptionObservation) -> Optional[UltronIntent]:
        """Core processing logic under lock."""
        t_start = time.perf_counter()

        # 1. STALE / DUPLICATE REJECTION
        if obs.frame_sequence <= self._last_processed_sequence:
            return None
        self._last_processed_sequence = obs.frame_sequence

        # 2. CONFIDENCE GATE
        if obs.confidence < self._config.min_confidence:
            self._reset_forming()
            return None

        # 3. CORRELATION TRACKING
        # If correlation_id changed, a new logical interaction started upstream.
        if self._current_correlation_id is not None and obs.correlation_id != self._current_correlation_id:
            # New interaction lineage — reset state
            self._full_reset()
        self._current_correlation_id = obs.correlation_id

        # 4. LANDMARK GEOMETRY
        grasp_ratio = compute_grasp_ratio(obs.landmarks)
        if grasp_ratio is None:
            # Malformed / inconsistent landmarks
            self._reset_forming()
            return None

        # 5. STATE MACHINE TRANSITIONS
        intent = self._transition(obs, grasp_ratio)

        t_end = time.perf_counter()
        latency_ms = (t_end - t_start) * 1000.0
        if latency_ms > 5.0:
            logger.debug(f"Intent detection latency: {latency_ms:.2f}ms")

        return intent

    def _transition(self, obs: PerceptionObservation, grasp_ratio: float) -> Optional[UltronIntent]:
        """Execute state machine transition based on current state and grasp ratio."""
        now_ns = obs.perception_timestamp_ns

        if self._state == InteractionState.IDLE:
            if grasp_ratio >= self._config.grasp_threshold:
                self._state = InteractionState.FORMING
                self._evidence_buffer = [(now_ns, grasp_ratio)]
            return None

        elif self._state == InteractionState.FORMING:
            if grasp_ratio < self._config.grasp_threshold:
                # Evidence broke — reset to IDLE
                self._reset_forming()
                return None

            # Add evidence
            self._evidence_buffer.append((now_ns, grasp_ratio))

            # Prune observations outside the temporal window
            window_start = now_ns - self._config.temporal_window_ns
            self._evidence_buffer = [
                (ts, gr) for ts, gr in self._evidence_buffer
                if ts >= window_start
            ]

            # Check stability: enough observations within the window?
            if len(self._evidence_buffer) >= self._config.min_stable_observations:
                self._state = InteractionState.STABLE_GRASP
                self._grasp_intent_emitted = False
                # Emit GRASP intent
                return self._emit_grasp_intent(obs, grasp_ratio)

            return None

        elif self._state == InteractionState.STABLE_GRASP:
            if grasp_ratio < self._config.release_threshold:
                # Grasp released
                self._state = InteractionState.RELEASED
                intent = self._emit_release_intent(obs)
                # Immediately transition to IDLE
                self._full_reset()
                return intent

            # Still grasping — do NOT emit duplicate GRASP intent
            # But continue accumulating evidence to maintain stability
            return None

        elif self._state == InteractionState.RELEASED:
            # Should not normally reach here (immediate reset above)
            self._full_reset()
            return None

        return None

    def _emit_grasp_intent(self, obs: PerceptionObservation, grasp_ratio: float) -> UltronIntent:
        """Create an immutable GRASP UltronIntent."""
        assert self._current_correlation_id is not None
        self._grasp_intent_emitted = True
        self._last_intent_sequence = obs.frame_sequence
        return UltronIntent(
            correlation_id=self._current_correlation_id,
            intent_timestamp_ns=time.monotonic_ns(),
            action=ACTION_GRASP,
            value=grasp_ratio,
            confidence=obs.confidence,
        )

    def _emit_release_intent(self, obs: PerceptionObservation) -> UltronIntent:
        """Create an immutable RELEASE UltronIntent."""
        assert self._current_correlation_id is not None
        self._last_intent_sequence = obs.frame_sequence
        return UltronIntent(
            correlation_id=self._current_correlation_id,
            intent_timestamp_ns=time.monotonic_ns(),
            action=ACTION_RELEASE,
            value=0.0,
            confidence=obs.confidence,
        )

    def _reset_forming(self) -> None:
        """Reset forming state back to IDLE without clearing correlation."""
        self._state = InteractionState.IDLE
        self._evidence_buffer = []
        self._grasp_intent_emitted = False

    def _full_reset(self) -> None:
        """Full state reset for new interaction lineage."""
        self._state = InteractionState.IDLE
        self._evidence_buffer = []
        self._current_correlation_id = None
        self._grasp_intent_emitted = False
