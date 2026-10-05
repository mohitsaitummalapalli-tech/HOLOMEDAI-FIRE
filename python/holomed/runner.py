# -*- coding: utf-8 -*-
"""Production Live-Slice Entry Point for HoloMed AI (M49)."""

import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional

from holomed.anatomy.service import AnatomyService
from holomed.audio.service import AudioService
from holomed.core.dispatcher import MessageDispatcher
from holomed.devices.control.manager import DeviceControlManager
from holomed.devices.endpoints.unity_device import UnityVirtualDevice
from holomed.devices.manager import DeviceManager
from holomed.devices.models import DeviceDescriptor, DeviceType
from holomed.devices.resolution import ExecutionResolutionGate
from holomed.devices.transport import TelemetryTransport, TelemetryPublisher
from holomed.gesture.service import GestureService
from holomed.persistence.service import PersistenceService
from holomed.platform.models import CycleStatus
from holomed.platform.service import PlatformService
from holomed.runtime.context import RuntimeContext
from holomed.configuration.models import AppConfig
from holomed.runtime.logging import SecretFilter
from holomed.tools.service import ToolService
from holomed.ultron.service import UltronService
from holomed.vision.service import VisionService
from holomed.xr.service import XRService
from holomed.input.camera import CameraInputNode, OpenCVCameraSource
from holomed.input.perception import MediaPipePerceptionPipeline, ConcreteMediaPipeAdapter
from holomed.input.models import PerceptionObservation


class ProductionLiveSliceRunner:
    """Instantiates and wires the production M49 HoloMed AI pipeline."""

    def __init__(self, storage_root: Path) -> None:
        self.storage_root = storage_root
        # Use an empty or default config for tests/runner
        from holomed.configuration.models import EnvironmentProfile, LogLevel
        self.context = RuntimeContext(
            epoch_id=1,
            app_config=AppConfig(
                app_name="runner",
                environment=EnvironmentProfile.TESTING,
                host="127.0.0.1",
                port=8080,
                log_level=LogLevel.INFO
            )
        )
        self.secret_filter = SecretFilter()
        self.dispatcher = MessageDispatcher()
        self.dispatcher.initialize(self.context)

        # 1. Device Manager and Discovery
        self.dm = DeviceManager(logger=None)

        # We must initialize device manager to get the registry
        self.dm.initialize(self.context)

        # 2. Setup Device Control Manager (DCM)
        self.resolution_gate = ExecutionResolutionGate()
        self.dcm = DeviceControlManager(
            registry=self.dm._registry,  # type: ignore
            resolution_gate=self.resolution_gate,
            authoritative_epoch_provider=lambda: self.context.epoch_id
        )
        self.dcm.initialize(self.context)

        # 3. Setup Telemetry
        self.telemetry_transport = TelemetryTransport()

        # 4. Domain Services
        self.vs = VisionService(device_manager=self.dm)
        self.aus = AudioService(device_manager=self.dm)
        self.gs = GestureService(device_manager=self.dm)
        self.us = UltronService(device_manager=self.dm)
        self.ans = AnatomyService(device_manager=self.dm)
        self.xs = XRService(device_manager=self.dm)
        self.ts = ToolService(device_manager=self.dm)

        # Initialize domain services
        for srv in (self.vs, self.aus, self.gs, self.us, self.ans, self.xs, self.ts):
            srv.initialize(self.context)

        self.services = {
            "device_manager": self.dm,
            "device_control_manager": self.dcm,
            "vision_service": self.vs,
            "audio_service": self.aus,
            "gesture_service": self.gs,
            "ultron_service": self.us,
            "anatomy_service": self.ans,
            "xr_service": self.xs,
            "tool_service": self.ts,
        }

        # 5. Platform Service
        self.platform = PlatformService(
            services=self.services,
            dispatcher=self.dispatcher,
            secret_filter=self.secret_filter,
        )
        self.platform.initialize(self.context)

        self.persistence = PersistenceService(
            platform_service=self.platform,
            dispatcher=self.dispatcher,
            storage_root=self.storage_root,
            secret_filter=self.secret_filter,
        )
        self.persistence.initialize(self.context)

        # 7. Wire up DCM production safety components
        from holomed.devices.control.recovery import StateRehydrationEngine
        from holomed.persistence.authority import ControllerAuthorityStore

        # Wire capacity callbacks
        session_store = self.persistence.session_store
        assert session_store is not None, "session_store must be initialized"
        self.dcm._session_validator = lambda s, gen: session_store.has_session(s)
        self.dcm._capacity_checker = lambda s: session_store.get_active_physical_operations()
        self.dcm._capacity_admitter = session_store.record_operation_admitted
        self.dcm._capacity_releaser = session_store.record_operation_terminated
        self.dcm._capacity_snapshot_provider = session_store.get_active_operations_snapshot

        # Wire state rehydration engine
        auth_store = ControllerAuthorityStore(self.storage_root)

        # In a real environment, epochs are managed by the deployment.
        # For the standalone runner, we auto-allocate if missing.
        from holomed.persistence.exceptions import PersistenceResourceMissingError
        try:
            auth_store.read_current_epoch()
        except PersistenceResourceMissingError:
            auth_store.allocate_next_epoch()

        from holomed.persistence.authority import DeviceEpochAuthority
        dev_auth = DeviceEpochAuthority(self.storage_root / "devices")
        try:
            dev_auth.read_current_device_epoch("unity_slice_01")
        except PersistenceResourceMissingError:
            dev_auth.allocate_next_device_epoch("unity_slice_01")

        self.dcm._rehydration_engine = StateRehydrationEngine(
            session_store=session_store,
            resolution_gate=self.resolution_gate,
            authority_store=auth_store
        )

        self.unity_device: Optional[UnityVirtualDevice] = None

        # Real Camera & Perception Input
        self.camera_node = CameraInputNode(lambda: OpenCVCameraSource(0))
        self.perception_pipeline = MediaPipePerceptionPipeline(self.camera_node, ConcreteMediaPipeAdapter())

    def start(self) -> None:
        """Start all services in topological order."""
        self.dispatcher.start()

        self.dm.start()
        self.dcm.start()

        for srv in (self.vs, self.aus, self.gs, self.us, self.ans, self.xs, self.ts):
            srv.start()

        self.platform.start()
        self.persistence.start()

        # Start perception components
        self.camera_node.start()
        self.perception_pipeline.start()

        # Register Unity Virtual Device Factory
        self.dm.register_factory(DeviceType.SIMULATED_GENERIC, lambda desc: UnityVirtualDevice(
            device_id=desc.device_id,
            host="127.0.0.1",
            port=50051,
            publisher=self.telemetry_transport.publisher
        ))

        # Instantiate and register the device directly
        self.unity_device = UnityVirtualDevice(
            device_id="unity_slice_01",
            host="127.0.0.1",
            port=50051,
            publisher=self.telemetry_transport.publisher
        )
        self.dm.register_device(self.unity_device)
        self.dm.initialize_device("unity_slice_01")
        self.dm.start_device("unity_slice_01")

    def stop(self) -> None:
        """Stop all services in reverse topological order."""
        if self.unity_device:
            try:
                self.dm.stop_device("unity_slice_01")
                self.dm.deregister_device("unity_slice_01")
            except Exception:
                pass

        # Stop perception components
        self.perception_pipeline.stop()
        self.camera_node.stop()

        self.persistence.stop()
        self.platform.stop()

        for srv in (self.ts, self.xs, self.ans, self.us, self.gs, self.aus, self.vs):
            srv.stop()

        self.dcm.stop()
        self.dm.stop()
        self.dispatcher.stop()

    def run_live_cycles(self, session_id: str, count: int) -> bool:
        """Execute cycles pulling from real camera/perception input."""
        self.platform.start_session(session_id)
        self.persistence.start_session(session_id)

        success = True

        # Run execution cycles
        for seq in range(count):
            # Wait for a valid perception observation
            # Poll at ~30Hz until we get an observation, max 1 second per cycle
            obs = None
            for _ in range(30):
                obs = self.perception_pipeline.get_latest_observation()
                if obs is not None:
                    break
                time.sleep(0.033)

            cycle_params = {
                "target_device_id": "unity_slice_01",
                "target_endpoint_id": "end_01"
            }
            if obs:
                cycle_params["observation"] = obs

            summary = self.platform.tick(
                session_id=session_id,
                sequence_number=seq,
                cycle_params=cycle_params
            )
            self.persistence.record_cycle(session_id, seq, summary)
            if summary.status != CycleStatus.COMPLETED:
                success = False

        self.platform.stop_session(session_id)
        self.persistence.close_session(session_id)

        # Verify persistence chain
        rep = self.persistence.replay_session(session_id)
        if not rep.hash_chain_valid:
            return False

        return success

    def run_headless_cycles(self, session_id: str, count: int) -> bool:
        """Execute the requested number of cycles synchronously."""
        self.platform.start_session(session_id)
        self.persistence.start_session(session_id)

        success = True

        # Run execution cycles
        for seq in range(count):
            summary = self.platform.tick(
                session_id=session_id,
                sequence_number=seq,
                # Provide a dummy intent observation so the pipeline exercises Ultron -> DCM path
                cycle_params={
                    "observation": self._create_dummy_observation(),
                    "target_device_id": "unity_slice_01",
                    "target_endpoint_id": "end_01"
                }
            )
            self.persistence.record_cycle(session_id, seq, summary)
            if summary.status != CycleStatus.COMPLETED:
                success = False

        self.platform.stop_session(session_id)
        self.persistence.close_session(session_id)

        # Verify persistence chain
        rep = self.persistence.replay_session(session_id)
        if not rep.hash_chain_valid:
            return False

        return success

    def _create_dummy_observation(self) -> Any:
        # Create a mock perception observation indicating an action
        return PerceptionObservation(
            capture_timestamp_ns=time.monotonic_ns(),
            perception_timestamp_ns=time.monotonic_ns() + 100,
            frame_sequence=1,
            correlation_id=str(uuid.uuid4()),
            landmarks=((0.0, 0.0, 0.0), (0.1, 0.1, 0.1), (0.2, 0.2, 0.2)),
            confidence=0.95
        )
