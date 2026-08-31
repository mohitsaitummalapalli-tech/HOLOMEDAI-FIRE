"""HoloMed AI - Central DeviceManager implementing IService."""

from __future__ import annotations

import re
import unicodedata
import weakref
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from holomed.runtime.logging import SecretFilter, StructuredLogger
from holomed.core.subscription import validate_concrete_topic
from holomed.devices.authority import DeviceResourceAuthority
from holomed.devices.exceptions import (
    DeviceCapacityError,
    DeviceDiscoveryError,
    DeviceError,
    DeviceFactoryError,
    DeviceFactoryMissingError,
    DeviceLifecycleError,
    DeviceNotFoundError,
    DeviceResourceIntegrityError,
    DeviceResourceOwnershipError,
    DeviceShutdownError,
    DeviceValidationError,
)
from holomed.devices.freshness import DeviceFreshnessTracker
from holomed.devices.interfaces import (
    DeviceDiscoveryProvider,
    DeviceResourceAccessor,
    IDevice,
    IDeviceEventSink,
    NullDeviceEventSink,
    RegistryAuthorityToken,
)
from holomed.devices.models import (
    DeviceCapability,
    DeviceDescriptor,
    DeviceFactory,
    DeviceHealth,
    DeviceRefreshReport,
    DeviceShutdownFailureRecord,
    DeviceState,
    DeviceType,
    MAX_CAPABILITIES_PER_DEVICE,
    MAX_DISCOVERY_BATCH_SIZE,
    MAX_METADATA_ENTRIES,
    MAX_METADATA_TOTAL_BYTES,
    deep_freeze_parameter,
)
from holomed.devices.registry import DeviceRegistry
from holomed.protocol.builders import create_event
from holomed.runtime.context import RuntimeContext
from holomed.runtime.exceptions import ServiceInitializationError, ServiceLifecycleError
from holomed.runtime.models import (
    HealthStatus,
    OwnedResourceSet,
    ResourceHandle,
    ServiceHealth,
)
from holomed.runtime.service import IService, ServiceState


class DeviceManager(IService):
    """Central device abstraction manager implementing the IService lifecycle."""

    def __init__(
        self,
        event_sink: Optional[IDeviceEventSink] = None,
        logger: Optional[StructuredLogger] = None,
    ) -> None:
        self._event_sink: IDeviceEventSink = event_sink or NullDeviceEventSink()
        self._logger = logger or StructuredLogger("holomed.devices.manager")
        self._secret_filter = SecretFilter()
        self._state: ServiceState = ServiceState.UNINITIALIZED

        # Structural resources
        self._resources: Optional[OwnedResourceSet] = None
        self._registry_token = RegistryAuthorityToken()
        self._registry = DeviceRegistry(self._registry_token)
        self._authority: Optional[DeviceResourceAuthority] = None
        self._freshness_tracker = DeviceFreshnessTracker()

        # Factories & Accounting
        self._factories: Dict[DeviceType, DeviceFactory] = {}
        self._in_transaction: bool = False
        self._sink_errors_count: int = 0

    # --------------------------------------------------------------------------
    # IService Properties & Lifecycle Implementation
    # --------------------------------------------------------------------------
    @property
    def name(self) -> str:
        return "device_manager"

    @property
    def dependencies(self) -> tuple[str, ...]:
        return ()

    @property
    def resources(self) -> OwnedResourceSet:
        if self._resources is None:
            raise ServiceLifecycleError("DeviceManager is uninitialized; OwnedResourceSet is not yet created")
        return self._resources

    def initialize(self, context: RuntimeContext) -> None:
        """Acquire manager structural handles and prepare for operation."""
        if self._state != ServiceState.UNINITIALIZED:
            raise ServiceLifecycleError(f"Cannot initialize DeviceManager in state {self._state.name}")

        self._resources = OwnedResourceSet(self.name, context.epoch_id)
        self._authority = DeviceResourceAuthority(self._resources, self._registry.contains)

        acquired_structural: List[str] = []
        try:
            h1 = self._resources.acquire("manager.registry")
            acquired_structural.append(h1.resource_id)
            h2 = self._resources.acquire("manager.discovery")
            acquired_structural.append(h2.resource_id)
            h3 = self._resources.acquire("manager.event_emitter")
            acquired_structural.append(h3.resource_id)

            self._state = ServiceState.INITIALIZED
        except Exception as e:
            # Emergency rollback of structural handles in reverse order
            for r_id in reversed(acquired_structural):
                try:
                    self._resources.release(r_id)
                except Exception:
                    pass
            self._state = ServiceState.FAILED
            raise ServiceInitializationError(f"DeviceManager initialization failed: {e}") from e

    def start(self) -> None:
        """Activate DeviceManager. MUST NOT acquire new resources."""
        if self._state == ServiceState.STARTED:
            raise DeviceLifecycleError("DeviceManager is already in STARTED state")
        if self._state != ServiceState.INITIALIZED:
            raise ServiceLifecycleError(f"Cannot start DeviceManager in state {self._state.name}, expected INITIALIZED")

        self._state = ServiceState.STARTED

    def stop(self) -> None:
        """Stop all managed devices and release all structural handles."""
        if self._state in (ServiceState.STOPPED, ServiceState.UNINITIALIZED):
            return

        failures: List[DeviceShutdownFailureRecord] = []
        execution_index = 0

        # Phase 1: Teardown all registered devices in reverse registration order
        for device in reversed(self._registry.all_devices):
            dev_id = device.device_id
            execution_index += 1
            try:
                self._teardown_device_internal(device, execution_index, failures)
            except Exception as e:
                clean = self._authority.is_device_clean(dev_id) if self._authority else True
                failures.append(DeviceShutdownFailureRecord(
                    device_id=dev_id,
                    error_type=type(e).__name__,
                    error_message=self._secret_filter.redact(str(e)),
                    execution_index=execution_index,
                    unreleased_resources=tuple(sorted(h.resource_id for h in self._authority.get_device_outstanding_handles(dev_id))) if self._authority else (),
                ))

        # Phase 2: Release structural manager handles in reverse order (D135)
        structural_handles = ["manager.event_emitter", "manager.discovery", "manager.registry"]
        for r_id in structural_handles:
            execution_index += 1
            try:
                if self._resources and r_id in self._resources.records:
                    rec = self._resources.records[r_id]
                    from holomed.runtime.models import ResourceStatus
                    if rec.status == ResourceStatus.UNRELEASED_FAILURE:
                        failures.append(DeviceShutdownFailureRecord(
                            device_id="manager",
                            error_type="DeviceShutdownError",
                            error_message=self._secret_filter.redact(rec.release_error or "Structural handle in UNRELEASED_FAILURE state"),
                            execution_index=execution_index,
                            unreleased_resources=(r_id,),
                        ))
                    elif rec.status == ResourceStatus.ACQUIRED:
                        self._resources.release(r_id)
            except Exception as e:
                failures.append(DeviceShutdownFailureRecord(
                    device_id="manager",
                    error_type=type(e).__name__,
                    error_message=self._secret_filter.redact(str(e)),
                    execution_index=execution_index,
                    unreleased_resources=(r_id,),
                ))

        self._freshness_tracker.clear()

        if failures or (self._resources and not self._resources.is_empty):
            self._state = ServiceState.FAILED
            raise DeviceShutdownError("DeviceManager shutdown encountered failures", tuple(failures))

        self._state = ServiceState.STOPPED

    def health(self) -> ServiceHealth:
        """Produce synchronous health snapshot with authoritative exception boundary."""
        now_utc = datetime.now(timezone.utc).isoformat()

        if self._state == ServiceState.UNINITIALIZED:
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.FAILED,
                message="DeviceManager is UNINITIALIZED",
                timestamp_utc=now_utc,
            )

        if self._state == ServiceState.FAILED:
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.FAILED,
                message="DeviceManager is in FAILED state",
                timestamp_utc=now_utc,
            )

        if self._state == ServiceState.INITIALIZED:
            return ServiceHealth(
                name=self.name,
                status=HealthStatus.HEALTHY,
                message="DeviceManager is initialized",
                timestamp_utc=now_utc,
            )

        # In STARTED state: query all devices
        worst_status = HealthStatus.HEALTHY
        device_health_map: Dict[str, Any] = {}

        status_severity = {
            HealthStatus.HEALTHY: 0,
            HealthStatus.DEGRADED: 1,
            HealthStatus.UNHEALTHY: 2,
            HealthStatus.FAILED: 3,
        }

        for device in self._registry.all_devices:
            dev_id = device.device_id
            try:
                raw_dh = device.health()
                # Authoritative boundary validation (D88)
                if not isinstance(raw_dh, DeviceHealth):
                    clean_dh = DeviceHealth(
                        device_id=dev_id,
                        status=HealthStatus.FAILED,
                        message="Device returned invalid health object type",
                        timestamp_utc=now_utc,
                    )
                else:
                    clean_dh = DeviceHealth(
                        device_id=dev_id,  # Overrides any spoofed ID
                        status=raw_dh.status if isinstance(raw_dh.status, HealthStatus) else HealthStatus.FAILED,
                        message=self._secret_filter.redact(raw_dh.message) if isinstance(raw_dh.message, str) else "Invalid health message",
                        timestamp_utc=raw_dh.timestamp_utc if isinstance(raw_dh.timestamp_utc, str) else now_utc,
                        diagnostics=raw_dh.diagnostics if isinstance(raw_dh.diagnostics, (dict, MappingProxyType)) else MappingProxyType({}),
                    )
            except Exception as e:
                clean_dh = DeviceHealth(
                    device_id=dev_id,
                    status=HealthStatus.FAILED,
                    message=self._secret_filter.redact(f"Device health query failed: {e}"),
                    timestamp_utc=now_utc,
                )

            device_health_map[dev_id] = {
                "status": clean_dh.status.value,
                "message": clean_dh.message,
                "timestamp_utc": clean_dh.timestamp_utc,
            }

            if status_severity[clean_dh.status] > status_severity[worst_status]:
                worst_status = clean_dh.status

        device_summary = ", ".join(f"{did}:{info['status']}" for did, info in sorted(device_health_map.items()))
        msg = f"DeviceManager operating with {len(self._registry)} devices ({device_summary})" if device_summary else f"DeviceManager operating with 0 devices"
        return ServiceHealth(
            name=self.name,
            status=worst_status,
            message=self._secret_filter.redact(msg),
            timestamp_utc=now_utc,
        )

    # --------------------------------------------------------------------------
    # Public Device Management Surface
    # --------------------------------------------------------------------------
    def register_factory(self, device_type: DeviceType, factory: DeviceFactory) -> None:
        """Register a factory for instantiating devices of a specific DeviceType."""
        self._require_started("register_factory")
        if self._in_transaction:
            raise DeviceLifecycleError("Cannot register factory while another operation is in transaction")

        self._in_transaction = True
        try:
            if not isinstance(device_type, DeviceType):
                raise DeviceValidationError(f"Invalid device_type: {device_type}")
            if not callable(factory):
                raise DeviceValidationError(f"Factory for {device_type.name} must be callable")

            if device_type in self._factories:
                raise DeviceFactoryError(f"Factory already registered for {device_type.name}; replacement is strictly forbidden")

            self._factories[device_type] = factory
        finally:
            self._in_transaction = False

    def register_device(self, device: IDevice) -> None:
        """Register a new device instance into the registry."""
        self._require_started("register_device")
        if self._in_transaction:
            raise DeviceLifecycleError("Cannot register device while in transaction")

        self._in_transaction = True
        try:
            self._register_device_internal(device)
        finally:
            self._in_transaction = False

    def deregister_device(self, device_id: str) -> None:
        """Remove a device from the registry."""
        self._require_started("deregister_device")
        if self._in_transaction:
            raise DeviceLifecycleError("Cannot deregister device while in transaction")

        self._in_transaction = True
        try:
            device = self._registry.get(device_id)
            if not self._authority.is_device_clean(device_id):
                raise DeviceLifecycleError(f"Cannot deregister device '{device_id}' with dirty outstanding resources")

            self._registry.deregister(device_id, self._registry_token)
            device._state = DeviceState.UNREGISTERED

            self._emit_event("device.deregistered", {
                "device_id": device.device_id,
                "device_type": device.device_type.name,
                "physical_id": device.physical_id,
            })
        finally:
            self._in_transaction = False

    def initialize_device(self, device_id: str) -> None:
        """Transition device from REGISTERED to READY."""
        self._require_started("initialize_device")
        if self._in_transaction:
            raise DeviceLifecycleError("Cannot initialize device while in transaction")

        self._in_transaction = True
        try:
            device = self._registry.get(device_id)
            if device.state != DeviceState.REGISTERED:
                raise DeviceLifecycleError(f"Cannot initialize device '{device_id}' in state {device.state.name}, expected REGISTERED")

            device._state = DeviceState.INITIALIZING
            accessor = DeviceResourceAccessor(self._authority, device_id)
            try:
                device.initialize(accessor)
                device._state = DeviceState.READY
                self._emit_event("device.initialized", {"device_id": device.device_id, "device_type": device.device_type.name})
            except Exception as e:
                device._state = DeviceState.FAILED
                self._emit_event("device.failed", {"device_id": device.device_id, "error": self._secret_filter.redact(str(e))})
                raise
        finally:
            self._in_transaction = False

    def start_device(self, device_id: str) -> None:
        """Transition device from READY to ACTIVE."""
        self._require_started("start_device")
        if self._in_transaction:
            raise DeviceLifecycleError("Cannot start device while in transaction")

        self._in_transaction = True
        try:
            device = self._registry.get(device_id)
            if device.state != DeviceState.READY:
                raise DeviceLifecycleError(f"Cannot start device '{device_id}' in state {device.state.name}, expected READY")

            handles_before = frozenset(self._authority.get_device_outstanding_handles(device_id))
            try:
                device.start()
                handles_after = frozenset(self._authority.get_device_outstanding_handles(device_id))
                if handles_after != handles_before:
                    device._state = DeviceState.FAILED
                    raise DeviceLifecycleError(f"Device '{device_id}' acquired new resources during start(), which is strictly forbidden")

                device._state = DeviceState.ACTIVE
                self._emit_event("device.started", {"device_id": device.device_id, "device_type": device.device_type.name})
            except Exception as e:
                device._state = DeviceState.FAILED
                self._emit_event("device.failed", {"device_id": device.device_id, "error": self._secret_filter.redact(str(e))})
                raise
        finally:
            self._in_transaction = False

    def stop_device(self, device_id: str) -> None:
        """Transition device from READY or ACTIVE to STOPPED."""
        self._require_started("stop_device")
        if self._in_transaction:
            raise DeviceLifecycleError("Cannot stop device while in transaction")

        self._in_transaction = True
        try:
            device = self._registry.get(device_id)
            if device.state not in (DeviceState.READY, DeviceState.ACTIVE):
                raise DeviceLifecycleError(f"Cannot stop device '{device_id}' in state {device.state.name}, expected READY or ACTIVE")

            device._state = DeviceState.STOPPING
            accessor = DeviceResourceAccessor(self._authority, device_id)
            try:
                device.stop(accessor)
                if not self._authority.is_device_clean(device_id):
                    device._state = DeviceState.FAILED
                    raise DeviceLifecycleError(f"Device '{device_id}' returned cleanly from stop() but resources remain unreleased")

                device._state = DeviceState.STOPPED
                self._emit_event("device.stopped", {"device_id": device.device_id, "device_type": device.device_type.name})
            except Exception as e:
                device._state = DeviceState.FAILED
                self._emit_event("device.failed", {"device_id": device.device_id, "error": self._secret_filter.redact(str(e))})
                raise
        finally:
            self._in_transaction = False

    def retry_device_cleanup(self, device_id: str) -> bool:
        """Attempt recovery cleanup on a FAILED device."""
        self._require_started("retry_device_cleanup")
        if self._in_transaction:
            raise DeviceLifecycleError("Cannot retry cleanup while in transaction")

        self._in_transaction = True
        try:
            device = self._registry.get(device_id)
            if device.state != DeviceState.FAILED:
                raise DeviceLifecycleError(f"Cannot retry cleanup on device '{device_id}' in state {device.state.name}, expected FAILED")

            if self._authority.is_device_clean(device_id):
                device._state = DeviceState.STOPPED
                self._emit_event("device.stopped", {"device_id": device.device_id, "device_type": device.device_type.name})
                return True

            device._state = DeviceState.STOPPING
            accessor = DeviceResourceAccessor(self._authority, device_id)
            try:
                device.stop(accessor)
                if self._authority.is_device_clean(device_id):
                    device._state = DeviceState.STOPPED
                    self._emit_event("device.stopped", {"device_id": device.device_id, "device_type": device.device_type.name})
                    return True
                else:
                    device._state = DeviceState.FAILED
                    return False
            except Exception:
                device._state = DeviceState.FAILED
                return False
        finally:
            self._in_transaction = False

    # --------------------------------------------------------------------------
    # Discovery & Refresh Reconciliation
    # --------------------------------------------------------------------------
    def discover_devices(self, provider: DeviceDiscoveryProvider) -> Tuple[DeviceDescriptor, ...]:
        """Perform bounded ingestion and return canonically sorted snapshot descriptors."""
        self._require_started("discover_devices")
        if self._in_transaction:
            raise DeviceLifecycleError("Cannot discover devices while in transaction")

        return self._create_discovery_snapshot(provider)

    def refresh_devices(self, provider: DeviceDiscoveryProvider) -> DeviceRefreshReport:
        """Reconcile active registry against discovery batch using Prepare-Before-Retire."""
        self._require_started("refresh_devices")
        if self._in_transaction:
            raise DeviceLifecycleError("Cannot refresh devices while in transaction")

        self._in_transaction = True
        try:
            # 1. Hard Mutation Barrier: Take snapshot and validate completely before touching state
            snapshot = self._create_discovery_snapshot(provider)

            discovered_by_id = {d.device_id: d for d in snapshot}
            active_devices = {d.device_id: d for d in self._registry.all_devices}

            unchanged: List[str] = []
            added: List[str] = []
            removed: List[str] = []
            failed: Dict[str, str] = {}

            # Phase A: Identify unchanged vs changed vs disappeared
            to_replace: List[DeviceDescriptor] = []
            for dev_id, active_dev in active_devices.items():
                if dev_id not in discovered_by_id:
                    # Disappeared
                    try:
                        if active_dev.state == DeviceState.REGISTERED:
                            self._registry.deregister(dev_id, self._registry_token)
                            active_dev._state = DeviceState.UNREGISTERED
                            removed.append(dev_id)
                        elif active_dev.state in (DeviceState.READY, DeviceState.ACTIVE):
                            active_dev._state = DeviceState.STOPPING
                            accessor = DeviceResourceAccessor(self._authority, dev_id)
                            active_dev.stop(accessor)
                            if self._authority.is_device_clean(dev_id):
                                self._registry.deregister(dev_id, self._registry_token)
                                active_dev._state = DeviceState.UNREGISTERED
                                removed.append(dev_id)
                            else:
                                active_dev._state = DeviceState.FAILED
                                failed[dev_id] = "Disappeared device stop succeeded but resources remained dirty"
                        elif active_dev.state == DeviceState.STOPPED:
                            self._registry.deregister(dev_id, self._registry_token)
                            active_dev._state = DeviceState.UNREGISTERED
                            removed.append(dev_id)
                        elif active_dev.state == DeviceState.FAILED:
                            if self._authority.is_device_clean(dev_id):
                                self._registry.deregister(dev_id, self._registry_token)
                                active_dev._state = DeviceState.UNREGISTERED
                                removed.append(dev_id)
                            else:
                                failed[dev_id] = "Disappeared device is dirty FAILED; cannot deregister"
                    except Exception as e:
                        active_dev._state = DeviceState.FAILED
                        failed[dev_id] = self._secret_filter.redact(f"Failed stopping disappeared device: {e}")
                else:
                    # Present in both: check exact 5-field equality
                    new_desc = discovered_by_id[dev_id]
                    # Check active device descriptor equality
                    if (
                        active_dev.device_type == new_desc.device_type
                        and active_dev.physical_id == new_desc.physical_id
                        and active_dev.capabilities == new_desc.capabilities
                    ):
                        unchanged.append(dev_id)
                    else:
                        to_replace.append(new_desc)

            # Phase B: Prepare-Before-Retire for changed devices
            for new_desc in to_replace:
                dev_id = new_desc.device_id
                old_dev = active_devices[dev_id]
                try:
                    # 1. PREPARE: construct candidate in-memory and validate
                    candidate = self._create_candidate_instance(new_desc, exclude_device_id=dev_id)

                    # 2. RETIRE OLD: stop and deregister old device
                    if old_dev.state in (DeviceState.READY, DeviceState.ACTIVE):
                        old_dev._state = DeviceState.STOPPING
                        accessor = DeviceResourceAccessor(self._authority, dev_id)
                        old_dev.stop(accessor)

                    if not self._authority.is_device_clean(dev_id):
                        old_dev._state = DeviceState.FAILED
                        failed[dev_id] = "Old device stop left dirty resources; replacement aborted"
                        continue

                    self._registry.deregister(dev_id, self._registry_token)
                    old_dev._state = DeviceState.UNREGISTERED

                    # 3. COMMIT NEW: register, initialize, and start candidate
                    self._register_device_internal(candidate)
                    init_accessor = DeviceResourceAccessor(self._authority, dev_id)
                    candidate._state = DeviceState.INITIALIZING
                    candidate.initialize(init_accessor)
                    candidate._state = DeviceState.READY
                    candidate.start()
                    candidate._state = DeviceState.ACTIVE

                    added.append(dev_id)
                    removed.append(dev_id)
                except Exception as e:
                    failed[dev_id] = self._secret_filter.redact(f"Replacement failed: {e}")

            # Phase C: Add brand new devices
            for dev_id, new_desc in discovered_by_id.items():
                if dev_id not in active_devices:
                    try:
                        candidate = self._create_candidate_instance(new_desc, exclude_device_id=None)
                        self._register_device_internal(candidate)
                        init_accessor = DeviceResourceAccessor(self._authority, dev_id)
                        candidate._state = DeviceState.INITIALIZING
                        candidate.initialize(init_accessor)
                        candidate._state = DeviceState.READY
                        candidate.start()
                        candidate._state = DeviceState.ACTIVE
                        added.append(dev_id)
                    except Exception as e:
                        failed[dev_id] = self._secret_filter.redact(f"Addition failed: {e}")

            return DeviceRefreshReport(
                unchanged=tuple(sorted(unchanged)),
                added=tuple(sorted(added)),
                removed=tuple(sorted(removed)),
                failed=MappingProxyType(failed),
            )
        finally:
            self._in_transaction = False

    # --------------------------------------------------------------------------
    # Private Helpers & Invariant Enforcement
    # --------------------------------------------------------------------------
    def _require_started(self, operation_name: str) -> None:
        if self._state != ServiceState.STARTED:
            raise DeviceLifecycleError(f"Cannot perform {operation_name}() while DeviceManager is in state {self._state.name}, expected STARTED")

    def _emit_event(self, topic: str, payload: Dict[str, Any]) -> None:
        """Best-effort event emission with complete failure isolation (D18, D21, D105)."""
        validate_concrete_topic(topic)
        redacted_payload = {
            k: self._secret_filter.redact(v) if isinstance(v, str) else v
            for k, v in payload.items()
        }
        envelope = create_event(
            message_name=topic,
            source=self.name,
            payload=redacted_payload,
        )
        try:
            self._event_sink.emit(envelope)
        except Exception as e:
            self._sink_errors_count += 1
            self._logger.warning(
                "Failed to emit device lifecycle event to sink",
                event="device_event_sink_error",
                extra={
                    "topic": topic,
                    "error": self._secret_filter.redact(str(e)),
                },
            )

    def _register_device_internal(self, device: IDevice) -> None:
        # Enforce weak-referenceability contract (D71)
        try:
            weakref.ref(device)
        except TypeError as e:
            raise DeviceFactoryError(f"Device implementation {type(device).__name__} is not weak-referenceable: {e}") from e

        # Enforce freshness (live-object reuse protection) (D53, D115)
        if self._freshness_tracker.is_stale(device):
            raise DeviceFactoryError(f"Device instance for '{device.device_id}' is already registered or was previously retired (reused live object)")

        self._registry.register(device, self._registry_token)
        device._state = DeviceState.REGISTERED
        self._freshness_tracker.track(device)

        self._emit_event("device.registered", {
            "device_id": device.device_id,
            "device_type": device.device_type.name,
            "physical_id": device.physical_id,
        })

    def _create_candidate_instance(self, descriptor: DeviceDescriptor, exclude_device_id: Optional[str]) -> IDevice:
        if descriptor.device_type not in self._factories:
            raise DeviceFactoryMissingError(f"No factory registered for device type {descriptor.device_type.name}")

        # Check physical ID collision against active devices (excluding the one being replaced, D95)
        registered_with_physical = self._registry.get_by_physical_id(descriptor.physical_id)
        if registered_with_physical is not None and registered_with_physical.device_id != exclude_device_id:
            raise DeviceCapacityError(f"Physical ID '{descriptor.physical_id}' collides with registered device '{registered_with_physical.device_id}'")

        factory = self._factories[descriptor.device_type]
        candidate = factory(descriptor)

        # 7-point factory validation
        if not isinstance(candidate, IDevice):
            raise DeviceFactoryError(f"Factory returned {type(candidate).__name__}, expected IDevice")
        if candidate.device_id != descriptor.device_id:
            raise DeviceFactoryError(f"Factory returned device_id '{candidate.device_id}', expected '{descriptor.device_id}'")
        if candidate.physical_id != descriptor.physical_id:
            raise DeviceFactoryError(f"Factory returned physical_id '{candidate.physical_id}', expected '{descriptor.physical_id}'")
        if candidate.device_type != descriptor.device_type:
            raise DeviceFactoryError(f"Factory returned device_type '{candidate.device_type.name}', expected '{descriptor.device_type.name}'")
        if candidate.capabilities != descriptor.capabilities:
            raise DeviceFactoryError("Factory returned device capabilities mismatch with descriptor")
        if candidate.state != DeviceState.UNREGISTERED:
            raise DeviceFactoryError(f"Factory returned device in state {candidate.state.name}, expected UNREGISTERED")

        # Weak-referenceable check
        try:
            weakref.ref(candidate)
        except TypeError as e:
            raise DeviceFactoryError(f"Factory returned non-weak-referenceable instance: {e}") from e

        # Freshness check
        if self._freshness_tracker.is_stale(candidate):
            raise DeviceFactoryError("Factory returned reused stale instance")

        return candidate

    def _teardown_device_internal(
        self,
        device: IDevice,
        execution_index: int,
        failures: List[DeviceShutdownFailureRecord],
    ) -> None:
        dev_id = device.device_id
        state = device.state

        if state == DeviceState.REGISTERED:
            self._registry.deregister(dev_id, self._registry_token)
            device._state = DeviceState.UNREGISTERED
            self._emit_event("device.deregistered", {"device_id": dev_id, "device_type": device.device_type.name, "physical_id": device.physical_id})

        elif state in (DeviceState.READY, DeviceState.ACTIVE):
            device._state = DeviceState.STOPPING
            accessor = DeviceResourceAccessor(self._authority, dev_id)
            try:
                device.stop(accessor)
            except Exception as e:
                failures.append(DeviceShutdownFailureRecord(
                    device_id=dev_id,
                    error_type=type(e).__name__,
                    error_message=self._secret_filter.redact(str(e)),
                    execution_index=execution_index,
                    unreleased_resources=tuple(sorted(h.resource_id for h in self._authority.get_device_outstanding_handles(dev_id))),
                ))
            else:
                if self._authority.is_device_clean(dev_id):
                    self._registry.deregister(dev_id, self._registry_token)
                    device._state = DeviceState.UNREGISTERED
                    self._emit_event("device.stopped", {"device_id": dev_id, "device_type": device.device_type.name})
                    self._emit_event("device.deregistered", {"device_id": dev_id, "device_type": device.device_type.name, "physical_id": device.physical_id})
                else:
                    failures.append(DeviceShutdownFailureRecord(
                        device_id=dev_id,
                        error_type="DeviceLifecycleError",
                        error_message="Device stopped cleanly but outstanding handles remain",
                        execution_index=execution_index,
                        unreleased_resources=tuple(sorted(h.resource_id for h in self._authority.get_device_outstanding_handles(dev_id))),
                    ))

        elif state == DeviceState.STOPPED:
            self._registry.deregister(dev_id, self._registry_token)
            device._state = DeviceState.UNREGISTERED
            self._emit_event("device.deregistered", {"device_id": dev_id, "device_type": device.device_type.name, "physical_id": device.physical_id})

        elif state == DeviceState.FAILED:
            if self._authority.is_device_clean(dev_id):
                self._registry.deregister(dev_id, self._registry_token)
                device._state = DeviceState.UNREGISTERED
                self._emit_event("device.deregistered", {"device_id": dev_id, "device_type": device.device_type.name, "physical_id": device.physical_id})
            else:
                # Attempt recovery cleanup
                try:
                    accessor = DeviceResourceAccessor(self._authority, dev_id)
                    device.stop(accessor)
                except Exception as e:
                    failures.append(DeviceShutdownFailureRecord(
                        device_id=dev_id,
                        error_type=type(e).__name__,
                        error_message=self._secret_filter.redact(str(e)),
                        execution_index=execution_index,
                        unreleased_resources=tuple(sorted(h.resource_id for h in self._authority.get_device_outstanding_handles(dev_id))),
                    ))
                else:
                    if self._authority.is_device_clean(dev_id):
                        self._registry.deregister(dev_id, self._registry_token)
                        device._state = DeviceState.UNREGISTERED
                    else:
                        failures.append(DeviceShutdownFailureRecord(
                            device_id=dev_id,
                            error_type="DeviceLifecycleError",
                            error_message="Cleanup retry failed; resources remain dirty",
                            execution_index=execution_index,
                            unreleased_resources=tuple(sorted(h.resource_id for h in self._authority.get_device_outstanding_handles(dev_id))),
                        ))

    def _create_discovery_snapshot(self, provider: DeviceDiscoveryProvider) -> Tuple[DeviceDescriptor, ...]:
        """Ingest provider descriptors with bounded consumption and single-read snapshot reconstruction."""
        # 1. Bounded consumption (pull at most 257 items, D119, D122)
        try:
            raw_iterable = provider.discover()
            raw_iterator = iter(raw_iterable)
        except Exception as e:
            raise DeviceDiscoveryError(f"Failed to initialize discovery provider iterator: {e}") from e

        raw_list: List[Any] = []
        try:
            for idx, item in enumerate(raw_iterator):
                if idx >= MAX_DISCOVERY_BATCH_SIZE:
                    raise DeviceDiscoveryError(f"Discovery batch size exceeds maximum limit of {MAX_DISCOVERY_BATCH_SIZE}")
                raw_list.append(item)
        except DeviceDiscoveryError:
            raise
        except Exception as e:
            raise DeviceDiscoveryError(f"Error during discovery descriptor retrieval: {e}") from e

        snapshot: List[DeviceDescriptor] = []
        seen_device_ids: Set[str] = set()
        seen_physical_ids: Set[str] = set()

        for idx, raw_item in enumerate(raw_list):
            # 2. Exact descriptor type verification (D116)
            if type(raw_item) is not DeviceDescriptor:
                raise DeviceDiscoveryError(f"Item {idx} has invalid type '{type(raw_item).__name__}', expected exact 'DeviceDescriptor'")

            # 3. Single-read extraction into locals (D114, D117)
            try:
                raw_device_id = raw_item.device_id
                raw_physical_id = raw_item.physical_id
                raw_device_type = raw_item.device_type
                raw_metadata = raw_item.metadata
                raw_capabilities = raw_item.capabilities
            except Exception as e:
                raise DeviceDiscoveryError(f"Item {idx} failed reading properties: {e}") from e

            # 4. Strict type verification without coercion (D113)
            if type(raw_device_id) is not str:
                raise DeviceDiscoveryError(f"Item {idx}: device_id must be str, got {type(raw_device_id).__name__}")
            if type(raw_physical_id) is not str:
                raise DeviceDiscoveryError(f"Item {idx}: physical_id must be str, got {type(raw_physical_id).__name__}")
            if not isinstance(raw_device_type, DeviceType):
                raise DeviceDiscoveryError(f"Item {idx}: device_type must be DeviceType, got {type(raw_device_type).__name__}")
            if type(raw_metadata) not in (dict, MappingProxyType):
                raise DeviceDiscoveryError(f"Item {idx}: metadata must be exact dict or MappingProxyType")
            if type(raw_capabilities) not in (tuple, list):
                raise DeviceDiscoveryError(f"Item {idx}: capabilities must be exact tuple or list")

            # Batch uniqueness
            if raw_device_id in seen_device_ids:
                raise DeviceDiscoveryError(f"Duplicate device_id '{raw_device_id}' in discovery batch")
            if raw_physical_id in seen_physical_ids:
                raise DeviceDiscoveryError(f"Duplicate physical_id '{raw_physical_id}' in discovery batch")
            seen_device_ids.add(raw_device_id)
            seen_physical_ids.add(raw_physical_id)

            # Metadata reconstruction and NFC normalization
            if len(raw_metadata) > MAX_METADATA_ENTRIES:
                raise DeviceDiscoveryError(f"Item {idx}: metadata entries exceed maximum ({MAX_METADATA_ENTRIES})")
            clean_metadata: Dict[str, str] = {}
            total_bytes = 0
            for k, v in raw_metadata.items():
                if type(k) is not str or not re.match(r"^[a-zA-Z0-9_.-]+$", k) or not (1 <= len(k) <= 64):
                    raise DeviceDiscoveryError(f"Item {idx}: invalid metadata key '{k}'")
                if type(v) is not str:
                    raise DeviceDiscoveryError(f"Item {idx}: metadata value for '{k}' must be str")
                norm_v = unicodedata.normalize("NFC", v)
                b = len(k.encode("utf-8")) + len(norm_v.encode("utf-8"))
                total_bytes += b
                if total_bytes > MAX_METADATA_TOTAL_BYTES:
                    raise DeviceDiscoveryError(f"Item {idx}: metadata exceeds {MAX_METADATA_TOTAL_BYTES} bytes")
                clean_metadata[k] = norm_v

            # Capabilities reconstruction
            if len(raw_capabilities) > MAX_CAPABILITIES_PER_DEVICE:
                raise DeviceDiscoveryError(f"Item {idx}: capabilities count exceeds maximum ({MAX_CAPABILITIES_PER_DEVICE})")
            clean_caps: List[DeviceCapability] = []
            seen_cap_ids: Set[str] = set()
            for cap in raw_capabilities:
                if type(cap) is not DeviceCapability:
                    raise DeviceDiscoveryError(f"Item {idx}: capability element must be exact DeviceCapability")
                if cap.capability_id in seen_cap_ids:
                    raise DeviceDiscoveryError(f"Item {idx}: duplicate capability_id '{cap.capability_id}'")
                seen_cap_ids.add(cap.capability_id)
                stats = {"nodes": 0, "units": 0}
                frozen_params = deep_freeze_parameter(cap.parameters, 0, set(), stats)
                clean_caps.append(DeviceCapability(
                    capability_id=cap.capability_id,
                    category=cap.category,
                    parameters=frozen_params,
                ))
            clean_caps.sort(key=lambda c: c.capability_id)

            # Reconstruct immutable descriptor
            snapshot.append(DeviceDescriptor(
                device_id=raw_device_id,
                physical_id=raw_physical_id,
                device_type=raw_device_type,
                capabilities=tuple(clean_caps),
                metadata=MappingProxyType(clean_metadata),
            ))

        # 5. Authoritative Canonical Sort inside DeviceManager (D90)
        snapshot.sort(key=lambda d: (d.device_type.name, d.device_id))
        return tuple(snapshot)
