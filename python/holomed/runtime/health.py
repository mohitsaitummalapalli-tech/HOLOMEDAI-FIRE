"""In-process health evaluation and aggregation subsystem."""

from datetime import datetime, timezone
from types import MappingProxyType
from typing import Mapping

from holomed.runtime.models import HealthStatus, RuntimeHealth, RuntimeState, ServiceHealth
from holomed.runtime.service import IService


class HealthEvaluator:
    """Evaluates in-process synchronous health checks with exception isolation."""

    @staticmethod
    def evaluate(
        services: Mapping[str, IService],
        engine_state: RuntimeState,
    ) -> RuntimeHealth:
        """Evaluate synchronous health status across all registered active services."""
        now_utc = datetime.now(timezone.utc).isoformat()
        service_snapshots: dict[str, ServiceHealth] = {}

        for name in sorted(services.keys()):
            service = services[name]
            try:
                sh = service.health()
                if not isinstance(sh, ServiceHealth):
                    sh = ServiceHealth(
                        name=name,
                        status=HealthStatus.FAILED,
                        message=f"Invalid health report type: {type(sh).__name__}",
                        timestamp_utc=now_utc,
                    )
            except Exception as e:
                sh = ServiceHealth(
                    name=name,
                    status=HealthStatus.FAILED,
                    message=f"Health evaluation raised exception: {e}",
                    timestamp_utc=now_utc,
                )
            service_snapshots[name] = sh

        # Calculate aggregate status based on precedence: FAILED > UNHEALTHY > DEGRADED > HEALTHY
        statuses = [sh.status for sh in service_snapshots.values()]
        if engine_state == RuntimeState.FAILED or HealthStatus.FAILED in statuses:
            aggregate = HealthStatus.FAILED
        elif HealthStatus.UNHEALTHY in statuses:
            aggregate = HealthStatus.UNHEALTHY
        elif HealthStatus.DEGRADED in statuses:
            aggregate = HealthStatus.DEGRADED
        else:
            aggregate = HealthStatus.HEALTHY

        return RuntimeHealth(
            engine_state=engine_state,
            aggregate_status=aggregate,
            services=MappingProxyType(service_snapshots),
            timestamp_utc=now_utc,
        )
