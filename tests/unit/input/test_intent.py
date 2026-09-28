"""Phase D — Validated Intent Detection Tests.

Tests the deterministic pipeline:
    PerceptionObservation → IntentDetector → UltronIntent

Covers:
    * Stable grasp produces exactly one GRASP intent
    * Unstable evidence produces NO intent (NO_COMMAND safe default)
    * Repeated stable frames do not generate duplicate intents
    * Release/cancellation is deterministic
    * Correlation ID preservation through perception → intent
    * New interaction gets new lineage
    * Stale observations are rejected
    * Duplicate observations are rejected
    * Malformed / inconsistent landmarks
    * Low confidence rejection
    * Rapid gesture toggling (hysteresis)
    * Tracking interruption and recovery
    * Insufficient temporal stability
    * Performance: intent detection latency
"""

import math
import time
from typing import Optional

import pytest

from holomed.input.models import PerceptionObservation, UltronIntent
from holomed.input.intent import (
    IntentDetector,
    IntentConfig,
    InteractionState,
    HandLandmark,
    EXPECTED_LANDMARK_COUNT,
    ACTION_GRASP,
    ACTION_RELEASE,
    ACTION_CANCEL,
    INTENT_LATENCY_BUDGET_NS,
    compute_grasp_ratio,
)


# ---------------------------------------------------------------------------
# Landmark Factories
# ---------------------------------------------------------------------------

def _make_open_hand_landmarks() -> tuple[tuple[float, float, float], ...]:
    """Synthetic open-hand landmarks.

    Fingertips are far from wrist relative to MCP knuckles.
    Produces grasp_ratio ≈ 0.0 (open).
    """
    # Wrist at origin
    lm = [(0.0, 0.0, 0.0)] * EXPECTED_LANDMARK_COUNT

    # Wrist
    lm[HandLandmark.WRIST] = (0.5, 0.8, 0.0)

    # MCP knuckles at moderate distance from wrist
    lm[HandLandmark.INDEX_FINGER_MCP] = (0.4, 0.6, 0.0)
    lm[HandLandmark.MIDDLE_FINGER_MCP] = (0.5, 0.6, 0.0)
    lm[HandLandmark.RING_FINGER_MCP] = (0.6, 0.6, 0.0)
    lm[HandLandmark.PINKY_MCP] = (0.7, 0.6, 0.0)

    # Fingertips far from wrist (open hand)
    lm[HandLandmark.THUMB_TIP] = (0.2, 0.3, 0.0)
    lm[HandLandmark.INDEX_FINGER_TIP] = (0.3, 0.2, 0.0)
    lm[HandLandmark.MIDDLE_FINGER_TIP] = (0.5, 0.15, 0.0)
    lm[HandLandmark.RING_FINGER_TIP] = (0.7, 0.2, 0.0)
    lm[HandLandmark.PINKY_TIP] = (0.8, 0.3, 0.0)

    # Fill remaining with plausible mid-hand positions
    lm[HandLandmark.THUMB_CMC] = (0.35, 0.75, 0.0)
    lm[HandLandmark.THUMB_MCP] = (0.3, 0.6, 0.0)
    lm[HandLandmark.THUMB_IP] = (0.25, 0.45, 0.0)
    lm[HandLandmark.INDEX_FINGER_PIP] = (0.35, 0.45, 0.0)
    lm[HandLandmark.INDEX_FINGER_DIP] = (0.33, 0.3, 0.0)
    lm[HandLandmark.MIDDLE_FINGER_PIP] = (0.5, 0.4, 0.0)
    lm[HandLandmark.MIDDLE_FINGER_DIP] = (0.5, 0.25, 0.0)
    lm[HandLandmark.RING_FINGER_PIP] = (0.6, 0.45, 0.0)
    lm[HandLandmark.RING_FINGER_DIP] = (0.65, 0.3, 0.0)
    lm[HandLandmark.PINKY_PIP] = (0.7, 0.5, 0.0)
    lm[HandLandmark.PINKY_DIP] = (0.75, 0.4, 0.0)

    return tuple((float(v[0]), float(v[1]), float(v[2])) for v in lm)


def _make_closed_hand_landmarks() -> tuple[tuple[float, float, float], ...]:
    """Synthetic closed/grasping-hand landmarks.

    Fingertips are close to wrist relative to MCP knuckles.
    Produces grasp_ratio ≈ 1.0 (grasping).
    """
    lm = [(0.0, 0.0, 0.0)] * EXPECTED_LANDMARK_COUNT

    # Wrist
    lm[HandLandmark.WRIST] = (0.5, 0.8, 0.0)

    # MCP knuckles at normal distance
    lm[HandLandmark.INDEX_FINGER_MCP] = (0.4, 0.6, 0.0)
    lm[HandLandmark.MIDDLE_FINGER_MCP] = (0.5, 0.6, 0.0)
    lm[HandLandmark.RING_FINGER_MCP] = (0.6, 0.6, 0.0)
    lm[HandLandmark.PINKY_MCP] = (0.7, 0.6, 0.0)

    # Fingertips CLOSE to wrist (curled in, grasping)
    lm[HandLandmark.THUMB_TIP] = (0.45, 0.7, 0.0)
    lm[HandLandmark.INDEX_FINGER_TIP] = (0.42, 0.68, 0.0)
    lm[HandLandmark.MIDDLE_FINGER_TIP] = (0.5, 0.68, 0.0)
    lm[HandLandmark.RING_FINGER_TIP] = (0.58, 0.68, 0.0)
    lm[HandLandmark.PINKY_TIP] = (0.65, 0.7, 0.0)

    # Fill remaining
    lm[HandLandmark.THUMB_CMC] = (0.38, 0.75, 0.0)
    lm[HandLandmark.THUMB_MCP] = (0.4, 0.72, 0.0)
    lm[HandLandmark.THUMB_IP] = (0.43, 0.71, 0.0)
    lm[HandLandmark.INDEX_FINGER_PIP] = (0.4, 0.55, 0.0)
    lm[HandLandmark.INDEX_FINGER_DIP] = (0.41, 0.62, 0.0)
    lm[HandLandmark.MIDDLE_FINGER_PIP] = (0.5, 0.55, 0.0)
    lm[HandLandmark.MIDDLE_FINGER_DIP] = (0.5, 0.62, 0.0)
    lm[HandLandmark.RING_FINGER_PIP] = (0.6, 0.55, 0.0)
    lm[HandLandmark.RING_FINGER_DIP] = (0.59, 0.62, 0.0)
    lm[HandLandmark.PINKY_PIP] = (0.68, 0.58, 0.0)
    lm[HandLandmark.PINKY_DIP] = (0.67, 0.65, 0.0)

    return tuple((float(v[0]), float(v[1]), float(v[2])) for v in lm)


def _make_obs(
    seq: int,
    landmarks: tuple[tuple[float, float, float], ...],
    confidence: float = 0.9,
    correlation_id: str = "corr-001",
    capture_ns: int = 0,
    perception_ns: int = 0,
) -> PerceptionObservation:
    """Factory for synthetic PerceptionObservation."""
    return PerceptionObservation(
        capture_timestamp_ns=capture_ns or (seq * 33_000_000),  # ~30fps spacing
        perception_timestamp_ns=perception_ns or (seq * 33_000_000 + 1_000_000),
        frame_sequence=seq,
        correlation_id=correlation_id,
        landmarks=landmarks,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Unit Tests: Grasp Ratio Geometry
# ---------------------------------------------------------------------------

class TestGraspRatio:
    def test_open_hand_gives_low_ratio(self):
        ratio = compute_grasp_ratio(_make_open_hand_landmarks())
        assert ratio is not None
        assert ratio < 0.3, f"Open hand should have low grasp ratio, got {ratio}"

    def test_closed_hand_gives_high_ratio(self):
        ratio = compute_grasp_ratio(_make_closed_hand_landmarks())
        assert ratio is not None
        assert ratio > 0.7, f"Closed hand should have high grasp ratio, got {ratio}"

    def test_wrong_landmark_count_returns_none(self):
        # Too few landmarks
        assert compute_grasp_ratio(((0.0, 0.0, 0.0),) * 10) is None
        # Too many landmarks
        assert compute_grasp_ratio(((0.0, 0.0, 0.0),) * 30) is None

    def test_nan_landmarks_returns_none(self):
        lm = list(_make_open_hand_landmarks())
        lm[0] = (float('nan'), 0.0, 0.0)
        assert compute_grasp_ratio(tuple(lm)) is None

    def test_inf_landmarks_returns_none(self):
        lm = list(_make_open_hand_landmarks())
        lm[5] = (float('inf'), 0.0, 0.0)
        assert compute_grasp_ratio(tuple(lm)) is None

    def test_degenerate_zero_hand_returns_none(self):
        # All landmarks at exact same position
        lm = ((0.5, 0.5, 0.0),) * EXPECTED_LANDMARK_COUNT
        assert compute_grasp_ratio(lm) is None

    def test_empty_landmarks_returns_none(self):
        assert compute_grasp_ratio(()) is None


# ---------------------------------------------------------------------------
# Unit Tests: Intent Detector State Machine
# ---------------------------------------------------------------------------

class TestIntentDetector:
    """Core intent detection tests."""

    def _make_detector(self, **overrides: object) -> IntentDetector:
        kwargs: dict[str, object] = dict(
            min_stable_observations=3,
            temporal_window_ns=500_000_000,
            grasp_threshold=0.6,
            release_threshold=0.3,
            min_confidence=0.5,
            tracking_loss_threshold=5,
        )
        kwargs.update(overrides)
        config = IntentConfig(
            min_stable_observations=int(kwargs["min_stable_observations"]),  # type: ignore[arg-type]
            temporal_window_ns=int(kwargs["temporal_window_ns"]),  # type: ignore[arg-type]
            grasp_threshold=float(kwargs["grasp_threshold"]),  # type: ignore[arg-type]
            release_threshold=float(kwargs["release_threshold"]),  # type: ignore[arg-type]
            min_confidence=float(kwargs["min_confidence"]),  # type: ignore[arg-type]
            tracking_loss_threshold=int(kwargs["tracking_loss_threshold"]),  # type: ignore[arg-type]
        )
        return IntentDetector(config)

    # --- Stable Grasp Produces One Intent ---

    def test_stable_grasp_produces_one_intent(self):
        """Three consecutive closed-hand observations within temporal window → one GRASP intent."""
        detector = self._make_detector()
        closed = _make_closed_hand_landmarks()

        obs1 = _make_obs(1, closed, perception_ns=100_000_000)
        obs2 = _make_obs(2, closed, perception_ns=200_000_000)
        obs3 = _make_obs(3, closed, perception_ns=300_000_000)

        r1 = detector.process_observation(obs1)
        r2 = detector.process_observation(obs2)
        r3 = detector.process_observation(obs3)

        # First two should be None (forming), third should be GRASP
        assert r1 is None
        assert r2 is None
        assert r3 is not None
        assert r3.action == ACTION_GRASP
        assert r3.correlation_id == "corr-001"
        assert r3.confidence == 0.9
        assert r3.value > 0.6
        assert detector.state == InteractionState.STABLE_GRASP

    # --- Unstable Evidence Produces NO_COMMAND (None) ---

    def test_unstable_evidence_produces_no_intent(self):
        """Alternating open/closed hand never stabilises → no intent emitted."""
        detector = self._make_detector()
        closed = _make_closed_hand_landmarks()
        open_h = _make_open_hand_landmarks()

        results = []
        for seq in range(1, 11):
            lm = closed if seq % 2 == 1 else open_h
            obs = _make_obs(seq, lm, perception_ns=seq * 50_000_000)
            results.append(detector.process_observation(obs))

        assert all(r is None for r in results)
        assert detector.state == InteractionState.IDLE

    # --- Repeated Stable Frames Do NOT Generate Duplicate Intents ---

    def test_no_duplicate_grasp_intents(self):
        """After GRASP is emitted, continued closed-hand frames produce no more intents."""
        detector = self._make_detector()
        closed = _make_closed_hand_landmarks()

        intents = []
        for seq in range(1, 10):
            obs = _make_obs(seq, closed, perception_ns=seq * 33_000_000)
            result = detector.process_observation(obs)
            if result is not None:
                intents.append(result)

        # Exactly one GRASP intent
        assert len(intents) == 1
        assert intents[0].action == ACTION_GRASP

    def test_one_shot_emission_edge(self):
        """Proves exactly one GRASP intent is emitted at the edge."""
        detector = self._make_detector(min_stable_observations=2)
        closed = _make_closed_hand_landmarks()

        # Obs 1: FORMING (no intent)
        r1 = detector.process_observation(_make_obs(1, closed, perception_ns=100_000_000))
        assert r1 is None
        assert detector.state == InteractionState.FORMING

        # Obs 2: STABLE_GRASP (emission)
        r2 = detector.process_observation(_make_obs(2, closed, perception_ns=150_000_000))
        assert r2 is not None
        assert r2.action == ACTION_GRASP
        assert detector.state == InteractionState.STABLE_GRASP

        # Obs 3, 4: Continuing grasp (no duplicate intent)
        r3 = detector.process_observation(_make_obs(3, closed, perception_ns=200_000_000))
        r4 = detector.process_observation(_make_obs(4, closed, perception_ns=250_000_000))
        assert r3 is None
        assert r4 is None
        assert detector.state == InteractionState.STABLE_GRASP

    # --- Release/Cancellation is Deterministic ---

    def test_release_after_grasp(self):
        """Grasp → stable → open hand → deterministic RELEASE intent."""
        detector = self._make_detector()
        closed = _make_closed_hand_landmarks()
        open_h = _make_open_hand_landmarks()

        intents = []
        # Build up grasp
        for seq in range(1, 4):
            obs = _make_obs(seq, closed, perception_ns=seq * 33_000_000)
            result = detector.process_observation(obs)
            if result is not None:
                intents.append(result)

        assert len(intents) == 1
        assert intents[0].action == ACTION_GRASP

        # Release
        obs_release = _make_obs(4, open_h, perception_ns=4 * 33_000_000)
        release_result = detector.process_observation(obs_release)

        assert release_result is not None
        assert release_result.action == ACTION_RELEASE
        assert release_result.value == 0.0
        assert detector.state == InteractionState.IDLE

    # --- Correlation ID Preservation ---

    def test_correlation_id_preserved_through_intent(self):
        """The correlation_id from the observation must carry through to the intent."""
        detector = self._make_detector()
        closed = _make_closed_hand_landmarks()

        corr = "test-correlation-lineage-456"
        for seq in range(1, 4):
            obs = _make_obs(seq, closed, correlation_id=corr, perception_ns=seq * 33_000_000)
            result = detector.process_observation(obs)
            if result is not None:
                assert result.correlation_id == corr

    # --- New Interaction Gets New Lineage ---

    def test_new_correlation_id_resets_state(self):
        """When correlation_id changes, the detector resets and starts fresh."""
        detector = self._make_detector()
        closed = _make_closed_hand_landmarks()

        # Build evidence with corr-001
        for seq in range(1, 3):
            detector.process_observation(
                _make_obs(seq, closed, correlation_id="corr-001", perception_ns=seq * 33_000_000)
            )
        assert detector.state == InteractionState.FORMING

        # Switch correlation → must reset
        obs_new = _make_obs(3, closed, correlation_id="corr-002", perception_ns=3 * 33_000_000)
        result = detector.process_observation(obs_new)

        # Should have reset; this is the first observation in the new lineage
        assert result is None
        # State might be IDLE (reset) then transition to FORMING with the new obs
        # Since it's only 1 observation, it can be FORMING at most
        assert detector.state in (InteractionState.IDLE, InteractionState.FORMING)

    # --- Stale Observations Rejected ---

    def test_stale_observation_rejected(self):
        """Observation with seq ≤ last processed is silently dropped."""
        detector = self._make_detector()
        closed = _make_closed_hand_landmarks()

        obs1 = _make_obs(5, closed)
        obs2 = _make_obs(3, closed)  # Stale

        detector.process_observation(obs1)
        result = detector.process_observation(obs2)

        assert result is None
        # Should have only processed seq 5

    # --- Duplicate Observation Rejected ---

    def test_duplicate_observation_rejected(self):
        """Same sequence number submitted twice is rejected."""
        detector = self._make_detector()
        closed = _make_closed_hand_landmarks()

        obs = _make_obs(1, closed)
        detector.process_observation(obs)
        result = detector.process_observation(obs)

        assert result is None

    # --- Low Confidence Rejection ---

    def test_low_confidence_produces_no_intent(self):
        """Observations below min_confidence threshold produce no intent."""
        detector = self._make_detector()
        closed = _make_closed_hand_landmarks()

        for seq in range(1, 6):
            obs = _make_obs(seq, closed, confidence=0.3, perception_ns=seq * 33_000_000)
            result = detector.process_observation(obs)
            assert result is None

        assert detector.state == InteractionState.IDLE

    # --- Malformed Landmarks ---

    def test_malformed_landmarks_produce_no_intent(self):
        """Landmarks with wrong count produce no intent."""
        detector = self._make_detector()
        bad_lm = ((0.1, 0.2, 0.3),) * 10  # Only 10 landmarks

        for seq in range(1, 6):
            obs = _make_obs(seq, bad_lm, perception_ns=seq * 33_000_000)
            result = detector.process_observation(obs)
            assert result is None

        assert detector.state == InteractionState.IDLE

    # --- Inconsistent Landmarks (NaN) ---

    def test_nan_landmarks_produce_no_intent(self):
        """NaN in landmark values produces no intent."""
        detector = self._make_detector()
        lm = list(_make_closed_hand_landmarks())
        lm[8] = (float('nan'), 0.5, 0.0)
        bad_lm = tuple(lm)

        for seq in range(1, 6):
            obs = _make_obs(seq, bad_lm, perception_ns=seq * 33_000_000)
            result = detector.process_observation(obs)
            assert result is None

    # --- Rapid Gesture Toggling (Hysteresis) ---

    def test_hysteresis_prevents_rapid_toggling(self):
        """Quick open/close/open does not produce spurious intents due to hysteresis gap."""
        detector = self._make_detector()
        closed = _make_closed_hand_landmarks()
        open_h = _make_open_hand_landmarks()

        intents = []
        # 1 closed, 1 open, 1 closed, 1 open, ... — never reaches min_stable_observations
        for seq in range(1, 20):
            lm = closed if seq % 2 == 1 else open_h
            obs = _make_obs(seq, lm, perception_ns=seq * 33_000_000)
            result = detector.process_observation(obs)
            if result is not None:
                intents.append(result)

        assert len(intents) == 0

    # --- Tracking Interruption ---

    def test_tracking_interruption_resets_forming(self):
        """If during FORMING, an observation has low confidence, state resets to IDLE."""
        detector = self._make_detector()
        closed = _make_closed_hand_landmarks()

        # Two forming observations
        detector.process_observation(_make_obs(1, closed, perception_ns=100_000_000))
        detector.process_observation(_make_obs(2, closed, perception_ns=200_000_000))
        assert detector.state == InteractionState.FORMING

        # Tracking lost — low confidence
        result = detector.process_observation(
            _make_obs(3, closed, confidence=0.1, perception_ns=300_000_000)
        )
        assert result is None
        assert detector.state == InteractionState.IDLE

    # --- Recovery After Interruption ---

    def test_recovery_after_interruption(self):
        """After tracking loss, a fresh sequence of stable grasps produces intent."""
        detector = self._make_detector()
        closed = _make_closed_hand_landmarks()

        # Build up and lose
        detector.process_observation(_make_obs(1, closed, perception_ns=100_000_000))
        detector.process_observation(_make_obs(2, closed, perception_ns=200_000_000))
        detector.process_observation(_make_obs(3, closed, confidence=0.1, perception_ns=300_000_000))
        assert detector.state == InteractionState.IDLE

        # Recover with fresh stable sequence
        results = []
        for seq in range(4, 7):
            obs = _make_obs(seq, closed, perception_ns=seq * 33_000_000 + 300_000_000)
            r = detector.process_observation(obs)
            if r is not None:
                results.append(r)

        assert len(results) == 1
        assert results[0].action == ACTION_GRASP

    # --- Sustained Tracking Loss ---

    def test_transient_vs_sustained_tracking_loss(self):
        """Differentiates transient failure vs sustained tracking loss -> CANCEL."""
        detector = self._make_detector(min_stable_observations=2, tracking_loss_threshold=3)
        closed = _make_closed_hand_landmarks()

        # Build stable grasp
        detector.process_observation(_make_obs(1, closed, perception_ns=100_000_000))
        detector.process_observation(_make_obs(2, closed, perception_ns=200_000_000))
        assert detector.state == InteractionState.STABLE_GRASP

        # 1st failure (transient, no command)
        r1 = detector.process_observation(_make_obs(3, closed, confidence=0.1, perception_ns=300_000_000))
        assert r1 is None
        assert detector.state == InteractionState.STABLE_GRASP

        # 2nd failure (transient, no command)
        r2 = detector.process_observation(_make_obs(4, closed, confidence=0.1, perception_ns=400_000_000))
        assert r2 is None
        assert detector.state == InteractionState.STABLE_GRASP

        # 3rd failure (sustained -> CANCEL)
        r3 = detector.process_observation(_make_obs(5, closed, confidence=0.1, perception_ns=500_000_000))
        assert r3 is not None
        assert r3.action == ACTION_CANCEL
        assert r3.value == 0.0
        assert r3.confidence == 0.0
        # Should reset immediately after emission
        assert detector.state == InteractionState.IDLE

    # --- Insufficient Temporal Stability ---

    def test_insufficient_temporal_stability(self):
        """Observations too spread apart (outside temporal window) don't stabilise."""
        detector = self._make_detector(temporal_window_ns=100_000_000)  # 100ms window
        closed = _make_closed_hand_landmarks()

        # Each observation 200ms apart — window prunes all but the latest
        for seq in range(1, 10):
            obs = _make_obs(seq, closed, perception_ns=seq * 200_000_000)
            result = detector.process_observation(obs)
            assert result is None  # Never enough within window

    # --- No Hand (Open Hand) ---

    def test_no_hand_open_hand_produces_no_intent(self):
        """Open hand observations produce no intent at all."""
        detector = self._make_detector()
        open_h = _make_open_hand_landmarks()

        for seq in range(1, 10):
            obs = _make_obs(seq, open_h, perception_ns=seq * 33_000_000)
            result = detector.process_observation(obs)
            assert result is None

        assert detector.state == InteractionState.IDLE


# ---------------------------------------------------------------------------
# Unit Tests: Intent Immutability
# ---------------------------------------------------------------------------

class TestIntentImmutability:
    def test_ultron_intent_is_frozen(self):
        """UltronIntent must be immutable per Phase A contract."""
        intent = UltronIntent(
            correlation_id="corr-immutable",
            intent_timestamp_ns=time.monotonic_ns(),
            action=ACTION_GRASP,
            value=0.85,
            confidence=0.9,
        )
        with pytest.raises(AttributeError):
            intent.action = "MODIFIED"  # type: ignore
        with pytest.raises(AttributeError):
            intent.confidence = 0.1  # type: ignore


# ---------------------------------------------------------------------------
# Unit Tests: Configuration Validation
# ---------------------------------------------------------------------------

class TestIntentConfig:
    def test_default_config_is_valid(self):
        config = IntentConfig()
        assert config.min_stable_observations >= 1
        assert config.temporal_window_ns > 0

    def test_invalid_min_stable_observations(self):
        with pytest.raises(ValueError):
            IntentConfig(min_stable_observations=0)

    def test_invalid_temporal_window(self):
        with pytest.raises(ValueError):
            IntentConfig(temporal_window_ns=0)

    def test_invalid_release_above_grasp(self):
        with pytest.raises(ValueError):
            IntentConfig(grasp_threshold=0.5, release_threshold=0.6)

    def test_invalid_min_confidence(self):
        with pytest.raises(ValueError):
            IntentConfig(min_confidence=-0.1)


# ---------------------------------------------------------------------------
# Performance Test
# ---------------------------------------------------------------------------

class TestIntentPerformance:
    def test_intent_detection_latency(self):
        """Intent detection must complete within the strict timing budget."""
        detector = IntentDetector(IntentConfig(min_stable_observations=1))
        closed = _make_closed_hand_landmarks()
        obs = _make_obs(1, closed, perception_ns=100_000_000)

        t0 = time.perf_counter()
        detector.process_observation(obs)
        t1 = time.perf_counter()

        latency_ms = (t1 - t0) * 1000.0
        # Assert measured property matches expectation roughly
        reported_latency_ms = detector.last_intent_latency_ns / 1_000_000.0

        # Budget is strictly defined in config/module
        budget_ms = INTENT_LATENCY_BUDGET_NS / 1_000_000.0

        # We allow standard test variance, but the algorithm must be O(1) and very fast.
        assert latency_ms < 5.0, f"Intent detection took {latency_ms:.3f}ms, expected very low"
        assert reported_latency_ms < budget_ms, f"Reported latency {reported_latency_ms} exceeds budget {budget_ms}"
