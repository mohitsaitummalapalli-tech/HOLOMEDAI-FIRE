import re

def parse_pyright_normalized(filepath):
    # Returns a dict of { normalized_string: original_string }
    errors = {}
    with open(filepath, 'r', encoding='utf-16') as f:
        for line in f:
            line = line.strip()
            if line and " - error: " in line:
                # regex to strip line and column numbers: filename:line:col - error: msg
                # example: c:\...\test.py:212:66 - error: Argument...
                # we want: c:\...\test.py - error: Argument...
                match = re.match(r'^(.*:\\[^:]+\.py):\d+:\d+( - error: .*)$', line)
                if match:
                    norm = match.group(1) + match.group(2)
                    errors[norm] = line
    return errors

base = parse_pyright_normalized('pyright_base.txt')
curr = parse_pyright_normalized('pyright_curr.txt')

base_keys = set(base.keys())
curr_keys = set(curr.keys())

removed_keys = base_keys - curr_keys
new_keys = curr_keys - base_keys

print(f"Base errors: {len(base_keys)}")
print(f"Curr errors: {len(curr_keys)}")
print(f"Removed errors: {len(removed_keys)}")
print(f"New errors: {len(new_keys)}")

with open('pyright_removed.txt', 'w', encoding='utf-8') as f:
    for k in sorted(list(removed_keys)):
        f.write(base[k] + '\n')
