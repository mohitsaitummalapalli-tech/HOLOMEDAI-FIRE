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

Tracking loss semantics:
    transient tracking uncertainty → NO_COMMAND (stay in current state)
    sustained tracking loss        → CANCEL intent (deterministic cancellation)
    intentional release            → RELEASE intent (grasp ratio drops below threshold)
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
    """Observable interaction lifecycle states.

    IDLE           → No interaction detected.
    FORMING        → Grasp evidence is accumulating, not yet stable.
    STABLE_GRASP   → Grasp confirmed with temporal stability.
    TRACKING_LOST  → Sustained tracking loss during active grasp.
    RELEASED       → Grasp was intentionally released (ratio dropped).
    """
    IDLE = "IDLE"
    FORMING = "FORMING"
    STABLE_GRASP = "STABLE_GRASP"
    TRACKING_LOST = "TRACKING_LOST"
    RELEASED = "RELEASED"


# Intent action constants
ACTION_GRASP = "GRASP"
ACTION_RELEASE = "RELEASE"
ACTION_CANCEL = "CANCEL"
ACTION_NO_COMMAND = "NO_COMMAND"

# ---------------------------------------------------------------------------
# Latency budget (ns). Intent calculation must complete within this budget.
# 2ms is well within the ~33ms frame budget at 30fps.
# ---------------------------------------------------------------------------
INTENT_LATENCY_BUDGET_NS: int = 2_000_000  # 2ms


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
    """Deterministic configuration for intent detection parameters.

    Attributes:
        min_stable_observations: Minimum consecutive grasp-above-threshold
            observations required within the temporal window before emitting
            a GRASP intent. Default: 3.
        temporal_window_ns: Maximum time window (nanoseconds) within which
            min_stable_observations must occur. Default: 500ms.
        grasp_threshold: Grasp ratio at or above which a frame is considered
            evidence of grasping. Default: 0.6.
        release_threshold: Grasp ratio below which a stable grasp is
            considered intentionally released. Must be < grasp_threshold
            (hysteresis). Default: 0.3.
        min_confidence: Minimum perception confidence to process an
            observation at all. Default: 0.5.
        tracking_loss_threshold: Number of consecutive failed observations
            (low confidence, malformed, or no-hand) during an active grasp
            before emitting a CANCEL intent. Distinguishes transient loss
            from sustained loss. Default: 5.
    """

    def __init__(
        self,
        *,
        min_stable_observations: int = 3,
        temporal_window_ns: int = 500_000_000,    # 500ms
        grasp_threshold: float = 0.6,
        release_threshold: float = 0.3,
        min_confidence: float = 0.5,
        tracking_loss_threshold: int = 5,
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
        if tracking_loss_threshold < 1:
            raise ValueError("tracking_loss_threshold must be >= 1")

        self.min_stable_observations = min_stable_observations
        self.temporal_window_ns = temporal_window_ns
        self.grasp_threshold = grasp_threshold
        self.release_threshold = release_threshold
        self.min_confidence = min_confidence
        self.tracking_loss_threshold = tracking_loss_threshold


# ---------------------------------------------------------------------------
# Intent State Machine
# ---------------------------------------------------------------------------
class IntentDetector:
    """Deterministic, temporally-stable intent detector.

    Consumes PerceptionObservation objects and produces UltronIntent
    when sufficient stable evidence is accumulated.

    Thread-safe: all mutable state is guarded by a lock.

    State machine:
        IDLE → FORMING           (first observation with grasp evidence)
        FORMING → STABLE_GRASP   (min_stable_observations within temporal_window)
        FORMING → IDLE           (evidence fails or window expires)
        STABLE_GRASP → RELEASED  (grasp ratio drops below release_threshold
                                  → emits exactly one RELEASE intent)
        STABLE_GRASP → TRACKING_LOST (consecutive tracking failures
                                      reach tracking_loss_threshold
                                      → emits exactly one CANCEL intent)
        STABLE_GRASP (transient) (1..N-1 consecutive failures
                                  → NO_COMMAND, stays in STABLE_GRASP)
        RELEASED → IDLE          (immediate transition after emission)
        TRACKING_LOST → IDLE     (immediate transition after emission)

    Hysteresis: grasp_threshold > release_threshold prevents rapid toggling.

    Tracking loss distinction:
        - transient: < tracking_loss_threshold consecutive failures → NO_COMMAND
        - sustained: >= tracking_loss_threshold consecutive failures → CANCEL
        - intentional release: hand opens (ratio < release_threshold) → RELEASE
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
        self._consecutive_tracking_failures: int = 0

        # Latency instrumentation
        self._last_intent_latency_ns: int = 0

    @property
    def state(self) -> InteractionState:
        """Current interaction state (thread-safe read)."""
        with self._lock:
            return self._state

    @property
    def last_intent_latency_ns(self) -> int:
        """Latency of the most recent process_observation call (nanoseconds).

        Measured from the start of process_observation to the end,
        covering stale rejection, geometry, and state machine transition.
        """
        with self._lock:
            return self._last_intent_latency_ns

    def process_observation(self, obs: PerceptionObservation) -> Optional[UltronIntent]:
        """Process a single PerceptionObservation and optionally produce a UltronIntent.

        Returns:
            UltronIntent if a new validated intent is produced.
            None if the observation does not result in a new intent
            (no command / evidence accumulating / duplicate / transient loss).
        """
        with self._lock:
            return self._process_locked(obs)

    def _process_locked(self, obs: PerceptionObservation) -> Optional[UltronIntent]:
        """Core processing logic under lock."""
        t_start_ns = time.monotonic_ns()

        # 1. STALE / DUPLICATE REJECTION
        if obs.frame_sequence <= self._last_processed_sequence:
            self._last_intent_latency_ns = time.monotonic_ns() - t_start_ns
            return None
        self._last_processed_sequence = obs.frame_sequence

        # 2. CONFIDENCE GATE — distinguishes tracking failure from valid data
        if obs.confidence < self._config.min_confidence:
            intent = self._handle_tracking_failure(obs)
            self._last_intent_latency_ns = time.monotonic_ns() - t_start_ns
            return intent

        # 3. CORRELATION TRACKING
        # If correlation_id changed, a new logical interaction started upstream.
        if self._current_correlation_id is not None and obs.correlation_id != self._current_correlation_id:
            # New interaction lineage — reset state
            self._full_reset()
        self._current_correlation_id = obs.correlation_id

        # 4. LANDMARK GEOMETRY
        grasp_ratio = compute_grasp_ratio(obs.landmarks)
        if grasp_ratio is None:
            # Malformed / inconsistent landmarks — treat as tracking failure
            intent = self._handle_tracking_failure(obs)
            self._last_intent_latency_ns = time.monotonic_ns() - t_start_ns
            return intent

        # 5. Valid observation — reset tracking failure counter
        self._consecutive_tracking_failures = 0

        # 6. STATE MACHINE TRANSITIONS
        intent = self._transition(obs, grasp_ratio)

        self._last_intent_latency_ns = time.monotonic_ns() - t_start_ns
        return intent

    def _handle_tracking_failure(self, obs: PerceptionObservation) -> Optional[UltronIntent]:
        """Handle a tracking failure (low confidence or malformed landmarks).

        In FORMING state: resets to IDLE (transient).
        In STABLE_GRASP state: increments consecutive failure counter.
            If counter reaches tracking_loss_threshold → emits CANCEL.
            Otherwise → transient failure, stays in STABLE_GRASP (NO_COMMAND).
        In other states: no effect.
        """
        if self._state == InteractionState.FORMING:
            self._reset_forming()
            return None

        if self._state == InteractionState.STABLE_GRASP:
            self._consecutive_tracking_failures += 1
            if self._consecutive_tracking_failures >= self._config.tracking_loss_threshold:
                # SUSTAINED tracking loss → CANCEL
                return self._emit_cancel_intent(obs)
            # TRANSIENT tracking loss → NO_COMMAND, stay in STABLE_GRASP
            return None

        # IDLE or other states — no special handling
        return None

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
                # Emit exactly one GRASP intent at the FORMING→STABLE_GRASP edge
                return self._emit_grasp_intent(obs, grasp_ratio)

            return None

        elif self._state == InteractionState.STABLE_GRASP:
            if grasp_ratio < self._config.release_threshold:
                # INTENTIONAL RELEASE — grasp ratio dropped deliberately
                return self._emit_release_intent(obs)

            # Still grasping — do NOT emit duplicate GRASP intent
            return None

        elif self._state in (InteractionState.RELEASED, InteractionState.TRACKING_LOST):
            # Should not normally reach here (immediate reset in emission)
            self._full_reset()
            return None

        return None

    def _emit_grasp_intent(self, obs: PerceptionObservation, grasp_ratio: float) -> UltronIntent:
        """Create an immutable GRASP UltronIntent.

        Emitted exactly once at the FORMING → STABLE_GRASP transition edge.
        """
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
        """Create an immutable RELEASE UltronIntent.

        Emitted exactly once at the STABLE_GRASP → RELEASED transition edge.
        Semantics: the user intentionally opened their hand / released the grasp.
        """
        assert self._current_correlation_id is not None
        self._last_intent_sequence = obs.frame_sequence
        self._state = InteractionState.RELEASED
        intent = UltronIntent(
            correlation_id=self._current_correlation_id,
            intent_timestamp_ns=time.monotonic_ns(),
            action=ACTION_RELEASE,
            value=0.0,
            confidence=obs.confidence,
        )
        # Immediately transition to IDLE
        self._full_reset()
        return intent

    def _emit_cancel_intent(self, obs: PerceptionObservation) -> UltronIntent:
        """Create an immutable CANCEL UltronIntent.

        Emitted exactly once when sustained tracking loss is detected
        during an active STABLE_GRASP. Semantics: tracking was lost for
        too many consecutive frames — the interaction is cancelled safely.
        This does NOT imply the user intentionally released.
        """
        assert self._current_correlation_id is not None
        self._last_intent_sequence = obs.frame_sequence
        self._state = InteractionState.TRACKING_LOST
        intent = UltronIntent(
            correlation_id=self._current_correlation_id,
            intent_timestamp_ns=time.monotonic_ns(),
            action=ACTION_CANCEL,
            value=0.0,
            confidence=0.0,  # No confident observation triggered this
        )
        # Immediately transition to IDLE
        self._full_reset()
        return intent

    def _reset_forming(self) -> None:
        """Reset forming state back to IDLE without clearing correlation."""
        self._state = InteractionState.IDLE
        self._evidence_buffer = []
        self._grasp_intent_emitted = False
        self._consecutive_tracking_failures = 0

    def _full_reset(self) -> None:
        """Full state reset for new interaction lineage."""
        self._state = InteractionState.IDLE
        self._evidence_buffer = []
        self._current_correlation_id = None
        self._grasp_intent_emitted = False
        self._consecutive_tracking_failures = 0
