"""HoloMed AI - DeviceSubsystemConsistencyAuditor."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Sequence

from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.coordination.coordinator import DeviceCoordinationService
from holomed.devices.data.processor import DeviceDataProcessor
from holomed.devices.manager import DeviceManager
from holomed.devices.orchestration.models import (
    MAX_AUDIT_FINDINGS,
    AuditFinding,
    AuditReport,
)
from holomed.devices.registry import DeviceRegistry
from holomed.runtime.logging import SecretFilter


NAMESPACE_REGEX = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")


class DeviceSubsystemConsistencyAuditor:
    """Evaluates atomic cross-plane consistency across manager, control, data, and coordination."""

    def __init__(
        self,
        device_manager: DeviceManager,
        control_manager: DeviceControlManager,
        data_processor: DeviceDataProcessor,
        coordination: DeviceCoordinationService,
        secret_filter: Optional[SecretFilter] = None,
    ) -> None:
        self._device_manager = device_manager
        self._control_manager = control_manager
        self._data_processor = data_processor
        self._coordination = coordination
        self._secret_filter = secret_filter or SecretFilter()

    def audit(self, active_epoch_id: int) -> AuditReport:
        """Perform comprehensive cross-plane invariant consistency audit."""
        findings: List[AuditFinding] = []
        now_utc = datetime.now(timezone.utc).isoformat()

        def add_finding(plane: str, code: str, message: str, severity: str = "ERROR") -> None:
            if len(findings) < MAX_AUDIT_FINDINGS:
                redacted_msg = self._secret_filter.redact(message)
                findings.append(
                    AuditFinding(
                        plane=plane,
                        code=code,
                        message=redacted_msg,
                        severity=severity,
                    )
                )

        # 1. Verify Service Epoch Consistency
        for svc_name, svc in (
            ("device_manager", self._device_manager),
            ("device_control_manager", self._control_manager),
            ("device_data_processor", self._data_processor),
            ("device_coordination", self._coordination),
        ):
            try:
                # Check resources epoch
                res = getattr(svc, "resources", None)
                if res is not None:
                    if res.epoch_id != active_epoch_id:
                        add_finding(
                            plane=svc_name,
                            code="ERR_EPOCH_MISMATCH",
                            message=f"Service '{svc_name}' resource epoch {res.epoch_id} mismatches active epoch {active_epoch_id}",
                        )
            except Exception as e:
                add_finding(
                    plane=svc_name,
                    code="ERR_RESOURCE_INSPECTION_FAILED",
                    message=f"Failed to inspect resources for '{svc_name}': {e}",
                    severity="WARNING",
                )

        # 2. Verify Registry vs Coordination Snapshot Consistency
        try:
            reg = getattr(self._device_manager, "registry", None) or getattr(self._device_manager, "_registry", None)
            reg_devices = {d.device_id: d for d in reg.all_devices} if reg is not None else {}
            snapshot = self._coordination.capture_snapshot()
            observed_ids = {obs.device_id for obs in snapshot.observations}

            missing_in_snapshot = set(reg_devices.keys()) - observed_ids
            if missing_in_snapshot:
                add_finding(
                    plane="coordination",
                    code="ERR_UNOBSERVED_DEVICES",
                    message=f"Devices registered in manager missing from coordination snapshot: {sorted(missing_in_snapshot)}",
                )

            foreign_in_snapshot = observed_ids - set(reg_devices.keys())
            if foreign_in_snapshot:
                add_finding(
                    plane="coordination",
                    code="ERR_ZOMBIE_OBSERVATIONS",
                    message=f"Coordination snapshot contains un-registered devices: {sorted(foreign_in_snapshot)}",
                )
        except Exception as e:
            add_finding(
                plane="coordination",
                code="ERR_SNAPSHOT_AUDIT_FAILED",
                message=f"Snapshot verification failed: {e}",
            )

        # 3. Verify Control Plane Boundaries (Commands & Queries <= 256)
        try:
            cmd_count = getattr(self._control_manager, "registered_command_count", None)
            if cmd_count is not None and cmd_count > 256:
                add_finding(
                    plane="control",
                    code="ERR_COMMAND_CAPACITY_EXCEEDED",
                    message=f"Control manager registered commands ({cmd_count}) exceeds cap of 256",
                )

            query_count = getattr(self._control_manager, "registered_query_count", None)
            if query_count is not None and query_count > 256:
                add_finding(
                    plane="control",
                    code="ERR_QUERY_CAPACITY_EXCEEDED",
                    message=f"Control manager registered queries ({query_count}) exceeds cap of 256",
                )
        except Exception as e:
            add_finding(
                plane="control",
                code="ERR_CONTROL_AUDIT_FAILED",
                message=f"Control plane verification failed: {e}",
                severity="WARNING",
            )

        # 4. Verify Data Plane Queue Depth (<= 256)
        try:
            queue = getattr(self._data_processor, "_queue", None)
            if queue is not None:
                q_size = len(queue)
                if q_size > 256:
                    add_finding(
                        plane="data",
                        code="ERR_DATA_QUEUE_OVERFLOW",
                        message=f"Data processor queue depth ({q_size}) exceeds cap of 256",
                    )
        except Exception as e:
            add_finding(
                plane="data",
                code="ERR_DATA_AUDIT_FAILED",
                message=f"Data plane verification failed: {e}",
                severity="WARNING",
            )

        # 5. Verify Resource Namespaces are Valid and Non-Empty
        for svc_name, svc in (
            ("device_manager", self._device_manager),
            ("device_control_manager", self._control_manager),
            ("device_data_processor", self._data_processor),
            ("device_coordination", self._coordination),
        ):
            try:
                res = getattr(svc, "resources", None)
                if res is not None:
                    for handle in res.outstanding_handles:
                        h_id = handle.resource_id
                        if not h_id or not NAMESPACE_REGEX.match(h_id):
                            add_finding(
                                plane=svc_name,
                                code="ERR_INVALID_RESOURCE_NAMESPACE",
                                message=f"Service '{svc_name}' owns invalid resource handle '{h_id}'",
                            )
            except Exception:
                pass

        is_clean = len(findings) == 0
        return AuditReport(
            is_consistent=is_clean,
            epoch_id=active_epoch_id,
            findings=tuple(findings),
            timestamp_utc=now_utc,
        )
