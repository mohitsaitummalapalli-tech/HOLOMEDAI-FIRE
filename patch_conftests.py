import os
import glob
import re

patch_code = """    # M42 fallback replacement for isolated testing
    from holomed.protocol.builders import create_response
    def mock_status_handler(msg):
        return create_response(msg, {"status": "ACTIVE"})
    disp.register_query_handler("platform.session.status.get", mock_status_handler, "mock_platform_service")
    return disp"""

for filepath in glob.glob("tests/unit/**/conftest.py", recursive=True) + glob.glob("tests/unit/**/test_*.py", recursive=True):
    with open(filepath, "r") as f:
        content = f.read()
    
    if "def message_dispatcher" in content and "mock_status_handler" not in content:
        # Find the function def message_dispatcher
        # and replace the `    return disp` inside it with our patch_code
        
        # We will split by lines and look for "def message_dispatcher"
        lines = content.split('\n')
        new_lines = []
        in_disp = False
        for line in lines:
            if line.startswith("def message_dispatcher"):
                in_disp = True
            
            if in_disp and re.match(r"^\s+return disp\s*$", line):
                indent = line[:len(line) - len(line.lstrip())]
                # Write patch with same indent
                patched = patch_code.replace("    ", indent)
                new_lines.append(patched)
                in_disp = False
            else:
                new_lines.append(line)
                
        with open(filepath, "w") as f:
            f.write("\n".join(new_lines))
        print(f"Patched {filepath}")
