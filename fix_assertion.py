import re

path = "tests/unit/devices/control/test_m49_adversarial_proofs.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Replace the assertion logic
old_logic = """    # 2. Lifecycle generation mismatch -> Rejected at the Gate (never reaches store)
    _run_pipeline(_make_event(gen=999))
    record_at_gate = gate.resolve_timeout("exec_e", 1)
    assert record_at_gate.terminal_resolution_status is False  # Reconciler/Gate ignored it"""

new_logic = """    # 2. Lifecycle generation mismatch -> Rejected at the Gate (never reaches store)
    _run_pipeline(_make_event(gen=999))
    record_at_gate = gate._records.get("exec_e")
    assert record_at_gate is not None
    assert record_at_gate.terminal_resolution_status is False  # Reconciler/Gate ignored it"""

content = content.replace(old_logic, new_logic)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
