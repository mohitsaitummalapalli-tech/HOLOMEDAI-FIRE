# -*- coding: utf-8 -*-
"""Synchronous gesture processing pipeline for M03."""

from __future__ import annotations

import time
from typing import Callable, Optional

from holomed.gesture.exceptions import (
    GesturePipelineError,
    GestureValidationError,
)
from holomed.gesture.models import (
    GestureProcessingResult,
    GestureQuality,
    GestureResult,
    HandLandmark3D,
    HandObservation,
    HandTrack,
    HandTrackState,
    MAX_GESTURE_HISTORY,
    MAX_GESTURE_PROCESSING_BUDGET_MS,
    quantize_coord,
    unproject_spatial_landmark,
)
from holomed.gesture.primitives import (
    classify_gesture_primitive,
    compute_finger_states,
)
from holomed.gesture.spatial import compute_spatial_interaction
from holomed.gesture.state_machine import GestureStateMachine
from holomed.gesture.tracker import HandTracker
from holomed.runtime.logging import SecretFilter


class GesturePipeline:
    """Synchronous pipeline orchestrating unprojection, tracking, and gesture recognition."""

    def __init__(
        self,
        tracker: Optional[HandTracker] = None,
        state_machine: Optional[GestureStateMachine] = None,
        event_emitter: Optional[Callable[[str, dict], None]] = None,
        secret_filter: Optional[SecretFilter] = None,
    ) -> None:
        self._tracker: HandTracker = tracker or HandTracker()
        self._state_machine: GestureStateMachine = state_machine or GestureStateMachine(event_emitter=event_emitter)
        self._event_emitter: Optional[Callable[[str, dict], None]] = event_emitter
        self._secret_filter: Optional[SecretFilter] = secret_filter

        self._history: list[GestureResult] = []
        self._execution_count: int = 0
        self._budget_overruns: int = 0

    @property
    def history(self) -> tuple[GestureResult, ...]:
        return tuple(self._history)

    def process(self, observation: HandObservation) -> GestureProcessingResult:
        """Process a single hand observation synchronously."""
        start_time = time.perf_counter()
        quality = GestureQuality.HEALTHY
        error_msg: Optional[str] = None
        tracks: tuple[HandTrack, ...] = ()
        gestures: list[GestureResult] = []

        try:
            # 1. Unproject landmarks to canonical metric HandLandmark3D
            unprojected: list[HandLandmark3D] = []
            for lm in observation.landmarks:
                try:
                    p3d = unproject_spatial_landmark(lm)
                    unprojected.append(p3d)
                except GestureValidationError as e:
                    # Missing or invalid depth results in DEGRADED quality
                    quality = GestureQuality.DEGRADED
                    error_msg = str(e)
                    break

            if quality == GestureQuality.DEGRADED and unprojected == []:
                # Critical unprojection failure
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return GestureProcessingResult(
                    frame_id=observation.frame_id,
                    device_id=observation.device_id,
                    sequence_number=observation.sequence_number,
                    timestamp_utc=observation.timestamp_utc,
                    epoch_id=observation.epoch_id,
                    quality=GestureQuality.DEGRADED,
                    tracks=(),
                    gestures=(),
                    execution_time_ms=quantize_coord(elapsed_ms, 4),
                    budget_exceeded=False,
                    error_message=self._secret_filter.redact(error_msg) if self._secret_filter and error_msg else error_msg,
                )

            # 2. Update Hand Tracker
            tracks, matched_track_id = self._tracker.update(unprojected, observation.handedness)

            # Check if any tracks dropped out, notify state machine
            active_ids = {t.track_id for t in tracks}
            for t in tracks:
                if t.state in (HandTrackState.LOST, HandTrackState.RETIRED):
                    self._state_machine.cancel_track_gestures(
                        t.track_id,
                        observation.sequence_number,
                        observation.epoch_id,
                    )
            self._state_machine.prune_untracked(active_ids)

            # 3. Geometric Feature Extraction and Spatial Solvers
            if matched_track_id is not None and unprojected:
                lms_dict = {lm.landmark_id: lm for lm in unprojected}
                finger_states = compute_finger_states(lms_dict)
                spatial_interaction = compute_spatial_interaction(lms_dict, observation.handedness)
                primitive, prim_conf = classify_gesture_primitive(lms_dict, finger_states)

                # Composite confidence: combination of observation confidence and primitive confidence
                combined_conf = quantize_coord(observation.confidence * 0.4 + prim_conf * 0.6, 6)

                # 4. State Machine Update
                res = self._state_machine.update(
                    hand_track_id=matched_track_id,
                    detected_gesture=primitive,
                    confidence=combined_conf,
                    sequence_number=observation.sequence_number,
                    epoch_id=observation.epoch_id,
                    timestamp_utc=observation.timestamp_utc,
                    interaction_point=spatial_interaction.pinch_point or spatial_interaction.pointing_ray_origin,
                    interaction_direction=spatial_interaction.pointing_ray_direction,
                )
                if res is not None:
                    gestures.append(res)
                    self._record_history(res)

                # Degenerate pointing ray marks degraded quality
                if primitive.value == "POINT" and spatial_interaction.pointing_ray_direction is None:
                    quality = GestureQuality.DEGRADED

            # Check if active track is coasting
            matched_track = next((t for t in tracks if t.track_id == matched_track_id), None)
            if matched_track and matched_track.state == HandTrackState.COASTING:
                quality = GestureQuality.DEGRADED

        except Exception as ex:
            quality = GestureQuality.FAILED
            raw_err = f"Pipeline execution error: {ex}"
            error_msg = self._secret_filter.redact(raw_err) if self._secret_filter else raw_err

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        budget_exceeded = elapsed_ms > MAX_GESTURE_PROCESSING_BUDGET_MS

        if budget_exceeded:
            self._budget_overruns += 1
            if quality == GestureQuality.HEALTHY:
                quality = GestureQuality.DEGRADED

        self._execution_count += 1

        return GestureProcessingResult(
            frame_id=observation.frame_id,
            device_id=observation.device_id,
            sequence_number=observation.sequence_number,
            timestamp_utc=observation.timestamp_utc,
            epoch_id=observation.epoch_id,
            quality=quality,
            tracks=tracks,
            gestures=tuple(gestures),
            execution_time_ms=quantize_coord(elapsed_ms, 4),
            budget_exceeded=budget_exceeded,
            error_message=error_msg,
        )

    def reset(self) -> None:
        """Reset internal tracker, state machine, and history."""
        self._tracker.reset()
        self._state_machine.reset()
        self._history.clear()
        self._execution_count = 0
        self._budget_overruns = 0

    def _record_history(self, result: GestureResult) -> None:
        """Store gesture result in bounded FIFO history."""
        if len(self._history) >= MAX_GESTURE_HISTORY:
            self._history.pop(0)
        self._history.append(result)
