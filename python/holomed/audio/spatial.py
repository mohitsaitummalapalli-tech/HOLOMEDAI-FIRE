# -*- coding: utf-8 -*-
"""M02 Audio Subsystem Deterministic Spatial Audio & Direction-of-Arrival (DOA).

Implements multi-channel Time-Difference-Of-Arrival (TDOA) estimation,
microphone baseline validation (D195), supersonic gating (D198),
and canonical azimuth/elevation angle normalization (D196).
"""

import math
from typing import Optional, Sequence

from holomed.audio.exceptions import AudioValidationError
from holomed.audio.models import (
    AcousticDirection,
    MicrophonePosition,
    MIN_MICROPHONE_BASELINE_M,
    SPEED_OF_SOUND_MPS,
)


def validate_microphone_geometry(microphones: Sequence[MicrophonePosition]) -> None:
    """Validate spatial bounds and minimum separation distance across all pairs (D195)."""
    count = len(microphones)
    if count < 2:
        return

    # Check uniqueness of IDs
    seen_ids = set()
    for m in microphones:
        if m.microphone_id in seen_ids:
            raise AudioValidationError(f"Duplicate microphone_id {m.microphone_id} in array configuration")
        seen_ids.add(m.microphone_id)

    # Check pairwise Euclidean baseline >= MIN_MICROPHONE_BASELINE_M (0.005 m)
    for i in range(count):
        m1 = microphones[i]
        for j in range(i + 1, count):
            m2 = microphones[j]
            dist = math.sqrt((m1.x_m - m2.x_m) ** 2 + (m1.y_m - m2.y_m) ** 2 + (m1.z_m - m2.z_m) ** 2)
            if dist < MIN_MICROPHONE_BASELINE_M:
                raise AudioValidationError(
                    f"Microphone baseline between mic {m1.microphone_id} and mic {m2.microphone_id} "
                    f"({dist:.5f}m) is below minimum threshold ({MIN_MICROPHONE_BASELINE_M}m)"
                )


class SpatialAudioEstimator:
    """Deterministic Time-Difference-Of-Arrival (TDOA) DOA estimator."""

    def __init__(
        self,
        microphones: Sequence[MicrophonePosition],
        speed_of_sound_mps: float = SPEED_OF_SOUND_MPS,
    ) -> None:
        validate_microphone_geometry(microphones)
        # Store sorted by microphone_id ASC
        self._microphones: tuple[MicrophonePosition, ...] = tuple(
            sorted(microphones, key=lambda m: m.microphone_id)
        )
        self._c: float = speed_of_sound_mps

    @property
    def microphone_count(self) -> int:
        return len(self._microphones)

    def estimate_direction(
        self, channel_samples: Sequence[Sequence[float]], sample_rate_hz: int
    ) -> Optional[AcousticDirection]:
        """Estimate acoustic direction from multi-channel synchronous PCM samples."""
        num_channels = len(channel_samples)
        if num_channels < 2 or len(self._microphones) < 2:
            return None

        active_channels = min(num_channels, len(self._microphones))
        ref_samples = channel_samples[0]
        n_samples = len(ref_samples)
        if n_samples < 8:
            return None

        # Measure energy in reference channel
        ref_energy = sum(s * s for s in ref_samples)
        if ref_energy < 1e-6:
            return None

        # Cross-correlation delay estimation relative to channel 0
        ref_m = self._microphones[0]
        delays: list[tuple[MicrophonePosition, float, float]] = []  # (mic, delta_t, peak_corr)

        for i in range(1, active_channels):
            mic_i = self._microphones[i]
            target_samples = channel_samples[i]
            dist = math.sqrt(
                (mic_i.x_m - ref_m.x_m) ** 2 + (mic_i.y_m - ref_m.y_m) ** 2 + (mic_i.z_m - ref_m.z_m) ** 2
            )
            max_delay_s = dist / self._c
            max_lag_samples = int(math.ceil(max_delay_s * sample_rate_hz)) + 1

            # Compute cross-correlation across search window [-max_lag, +max_lag]
            best_lag = 0
            best_corr = -1.0
            total_corr_power = 0.0

            for lag in range(-max_lag_samples, max_lag_samples + 1):
                corr = 0.0
                if lag >= 0:
                    corr = sum(ref_samples[k] * target_samples[k + lag] for k in range(n_samples - lag))
                else:
                    corr = sum(ref_samples[k - lag] * target_samples[k] for k in range(n_samples + lag))

                corr_val = abs(corr)
                total_corr_power += corr_val
                if corr_val > best_corr:
                    best_corr = corr_val
                    best_lag = lag

            delta_t = best_lag / float(sample_rate_hz)

            # Supersonic gating (D198): verify |c * delta_t / dist| <= 1.0
            ratio = (self._c * delta_t) / dist
            if abs(ratio) > 1.0:
                # Clamp to physical propagation bound
                ratio = max(-1.0, min(1.0, ratio))
                delta_t = (ratio * dist) / self._c

            norm_corr = min(best_corr / (math.sqrt(ref_energy * sum(s * s for s in target_samples)) + 1e-9), 1.0)
            delays.append((mic_i, delta_t, norm_corr))

        # Geometric Angle Estimation
        if active_channels == 2:
            # 2-Microphone 1D baseline DOA
            mic1, delta_t, corr = delays[0]
            dx = mic1.x_m - ref_m.x_m
            dy = mic1.y_m - ref_m.y_m
            dz = mic1.z_m - ref_m.z_m
            baseline = math.sqrt(dx * dx + dy * dy + dz * dz)

            ratio = max(-1.0, min(1.0, (self._c * delta_t) / baseline))
            # Angle relative to baseline axis
            angle_rad = math.asin(ratio)

            # Map to azimuth/elevation based on baseline orientation
            if abs(dx) >= abs(dz) and abs(dx) >= abs(dy):
                # Baseline is primarily on X-axis (left-right)
                azimuth_deg = round(math.degrees(angle_rad), 2)
                elevation_deg = 0.0
            else:
                azimuth_deg = 0.0
                elevation_deg = round(math.degrees(angle_rad), 2)

            conf = max(0.1, min(1.0, round(corr, 4)))
            return AcousticDirection(
                azimuth_deg=azimuth_deg,
                elevation_deg=elevation_deg,
                confidence=conf,
            )

        # Multi-microphone (>= 3) least-squares plane/3D projection
        # Equation: (p_i - p_0) . u = -c * delta_t_i
        sum_xx = 0.0
        sum_xy = 0.0
        sum_xz = 0.0
        sum_yy = 0.0
        sum_yz = 0.0
        sum_zz = 0.0
        sum_xr = 0.0
        sum_yr = 0.0
        sum_zr = 0.0

        for mic_i, delta_t, _ in delays:
            dx = mic_i.x_m - ref_m.x_m
            dy = mic_i.y_m - ref_m.y_m
            dz = mic_i.z_m - ref_m.z_m
            rhs = -self._c * delta_t

            sum_xx += dx * dx
            sum_xy += dx * dy
            sum_xz += dx * dz
            sum_yy += dy * dy
            sum_yz += dy * dz
            sum_zz += dz * dz
            sum_xr += dx * rhs
            sum_yr += dy * rhs
            sum_zr += dz * rhs

        # Approximate direction components
        ux = sum_xr / (sum_xx + 1e-9)
        uy = sum_yr / (sum_yy + 1e-9)
        uz = sum_zr / (sum_zz + 1e-9)

        mag = math.sqrt(ux * ux + uy * uy + uz * uz)
        if mag < 1e-6:
            ux, uy, uz = 0.0, 0.0, 1.0
        else:
            ux /= mag
            uy /= mag
            uz /= mag

        azimuth_deg = round(math.degrees(math.atan2(ux, max(uz, 1e-6))), 2)
        elevation_deg = round(math.degrees(math.asin(max(-1.0, min(1.0, uy)))), 2)
        avg_conf = sum(d[2] for d in delays) / float(len(delays))

        return AcousticDirection(
            azimuth_deg=azimuth_deg,
            elevation_deg=elevation_deg,
            confidence=max(0.1, min(1.0, round(avg_conf, 4))),
        )
