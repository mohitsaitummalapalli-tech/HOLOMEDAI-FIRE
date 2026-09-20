import re

path = "tests/unit/devices/control/test_m49_adversarial_proofs.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Add RegistryAuthorityToken import
content = content.replace("from holomed.devices.registry import DeviceRegistry", "from holomed.devices.registry import DeviceRegistry, RegistryAuthorityToken")

# Fix the manager instantiation
content = content.replace("DeviceRegistry()", "DeviceRegistry(RegistryAuthorityToken())")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
