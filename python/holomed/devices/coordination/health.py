"""HoloMed AI - Device health evaluation and deterministic aggregation engine."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from holomed.devices.coordination.models import DeviceObservation
from holomed.runtime.logging import SecretFilter
from holomed.runtime.models import HealthStatus, ServiceHealth
from holomed.runtime.service import ServiceState


class DeviceHealthAggregator:
    """Deterministic, secret-redacted aggregate health computation engine.

    Enforces status precedence:
    FAILED > UNHEALTHY > DEGRADED > HEALTHY
    """

    def __init__(self, secret_filter: Optional[SecretFilter] = None) -> None:
        self._secret_filter = secret_filter or SecretFilter()

    def aggregate(
        self,
        observations: Sequence[DeviceObservation],
        coordinator_state: ServiceState,
        service_name: str = "device_coordination",
    ) -> ServiceHealth:
        """Compute synchronous aggregate health snapshot with strict precedence."""
        now_utc = datetime.now(timezone.utc).isoformat()

        # 1. Check Coordinator Service Lifecycle State
        if coordinator_state == ServiceState.UNINITIALIZED:
            return ServiceHealth(
                name=service_name,
                status=HealthStatus.FAILED,
                message="DeviceCoordinationService UNINITIALIZED",
                timestamp_utc=now_utc,
            )
        if coordinator_state == ServiceState.FAILED:
            return ServiceHealth(
                name=service_name,
                status=HealthStatus.FAILED,
                message="DeviceCoordinationService FAILED",
                timestamp_utc=now_utc,
            )
        if coordinator_state == ServiceState.STOPPED:
            return ServiceHealth(
                name=service_name,
                status=HealthStatus.HEALTHY,
                message="DeviceCoordinationService cleanly stopped",
                timestamp_utc=now_utc,
            )

        # 2. Empty Observation Set
        if not observations:
            return ServiceHealth(
                name=service_name,
                status=HealthStatus.HEALTHY,
                message="No registered devices; aggregate health HEALTHY",
                timestamp_utc=now_utc,
            )

        # 3. Evaluate Observation Status Counts & Collect Non-Healthy Summaries
        counts = {
            HealthStatus.FAILED: 0,
            HealthStatus.UNHEALTHY: 0,
            HealthStatus.DEGRADED: 0,
            HealthStatus.HEALTHY: 0,
        }
        issue_details: list[str] = []

        for obs in observations:
            if not isinstance(obs, DeviceObservation):
                counts[HealthStatus.FAILED] += 1
                issue_details.append("unknown: Invalid observation object")
                continue

            st = obs.health_status if obs.health_status in counts else HealthStatus.FAILED
            counts[st] += 1

            if st != HealthStatus.HEALTHY and obs.health_message:
                redacted_item = self._secret_filter.redact(obs.health_message)
                issue_details.append(f"{obs.device_id}: {redacted_item}")

        # 4. Status Precedence Resolution
        if counts[HealthStatus.FAILED] > 0:
            agg_status = HealthStatus.FAILED
        elif counts[HealthStatus.UNHEALTHY] > 0:
            agg_status = HealthStatus.UNHEALTHY
        elif counts[HealthStatus.DEGRADED] > 0:
            agg_status = HealthStatus.DEGRADED
        else:
            agg_status = HealthStatus.HEALTHY

        detail_suffix = f" [{'; '.join(issue_details[:3])}]" if issue_details else ""
        raw_msg = (
            f"Device coordination aggregate: {agg_status.name} (devices={len(observations)}, "
            f"healthy={counts[HealthStatus.HEALTHY]}, degraded={counts[HealthStatus.DEGRADED]}, "
            f"unhealthy={counts[HealthStatus.UNHEALTHY]}, failed={counts[HealthStatus.FAILED]}){detail_suffix}"
        )
        redacted_msg = self._secret_filter.redact(raw_msg)

        return ServiceHealth(
            name=service_name,
            status=agg_status,
            message=redacted_msg,
            timestamp_utc=now_utc,
        )
