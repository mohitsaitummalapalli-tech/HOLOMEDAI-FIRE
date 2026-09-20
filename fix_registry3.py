import re

path = "tests/unit/devices/control/test_m49_adversarial_proofs.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace('registry._devices["dev_e"] = DummyDevice("exec_e", session)', 'registry._devices["dev_e"] = DummyDevice("exec_e", session)\n    registry._registration_order = ["dev_e"]')

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
