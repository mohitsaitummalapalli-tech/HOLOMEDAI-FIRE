import re

path = "tests/unit/devices/control/test_m49_adversarial_proofs.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Make the device registry return a mocked device
device_mock_code = """
    class DummyLease:
        def __init__(self, execution_id, session_id):
            self.execution_id = execution_id
            self.session_id = session_id

    class DummyEndpoint:
        def __init__(self, execution_id, session_id):
            self.active_lease = DummyLease(execution_id, session_id)

    class DummyDevice:
        def __init__(self, execution_id, session_id):
            self.device_id = "dev_e"
            self.endpoints = [DummyEndpoint(execution_id, session_id)]
            
    registry = DeviceRegistry(RegistryAuthorityToken())
    registry._devices["dev_e"] = DummyDevice("exec_e", session)
    
    manager = DeviceControlManager(registry, capacity_releaser=store.record_operation_terminated)
    manager._lease_registry.release_lease = lambda e, s: None
"""

content = content.replace("""    class DummyLease:
        def __init__(self, execution_id, session_id):
            self.execution_id = execution_id
            self.session_id = session_id

    class DummyEndpoint:
        def __init__(self, execution_id, session_id):
            self.active_lease = DummyLease(execution_id, session_id)

    class DummyDevice:
        def __init__(self, execution_id, session_id):
            self.device_id = "dev_e"
            self.endpoints = [DummyEndpoint(execution_id, session_id)]

    registry = DeviceRegistry(RegistryAuthorityToken())
    registry._devices["dev_e"] = DummyDevice("exec_e", session)
    manager = DeviceControlManager(registry, capacity_releaser=store.record_operation_terminated)""", device_mock_code.strip())

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
