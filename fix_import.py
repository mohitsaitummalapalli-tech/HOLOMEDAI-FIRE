import re

path = "tests/unit/devices/control/test_m49_adversarial_proofs.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

new_content = content.replace("from holomed.persistence.session import DurableSessionStore", "from holomed.persistence.sessions import DurableSessionStore")

with open(path, "w", encoding="utf-8") as f:
    f.write(new_content)
