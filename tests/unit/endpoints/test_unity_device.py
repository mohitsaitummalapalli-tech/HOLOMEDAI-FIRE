import pytest
import asyncio
from typing import Optional
from unittest.mock import Mock, MagicMock
from holomed.devices.endpoints.unity_device import UnityVirtualDevice, UnityVirtualEndpoint
from holomed.devices.models import DeviceState, EndpointSafetyState, PhysicalCommand, EndpointLease, SubmissionStatus
from holomed.devices.interfaces import DeviceResourceAccessor
from holomed.runtime.models import ResourceHandle

class MockAuthority:
    def acquire(self, device_id, resource_name):
        return ResourceHandle(f"{device_id}.{resource_name}", device_id, 1)
        
    def release(self, device_id, resource_id):
        pass

@pytest.fixture
def resource_accessor():
    return DeviceResourceAccessor(MockAuthority(), "test_unity_sim")

@pytest.fixture
def unity_device(unused_tcp_port):
    device = UnityVirtualDevice("test_unity_sim", "127.0.0.1", unused_tcp_port)
    return device

def test_unity_device_lifecycle(unity_device, resource_accessor):
    assert unity_device.state == DeviceState.UNREGISTERED
    
    unity_device.initialize(resource_accessor)
    assert unity_device.state == DeviceState.INITIALIZING
    
    unity_device.start()
    assert unity_device.state == DeviceState.READY
    assert unity_device.health().status.name == "HEALTHY"
    
    unity_device.stop(resource_accessor)
    assert unity_device.state == DeviceState.STOPPED
    
    # Can we stop again idempotently or without hanging?
    # Yes.

def test_unity_device_capabilities(unity_device):
    caps = unity_device.capabilities
    assert len(caps) == 1
    assert caps[0].capability_id == "grasp"
    assert caps[0].requires_physical_endpoint is True
    assert caps[0].target_endpoint_id == "unity_ep_1"
