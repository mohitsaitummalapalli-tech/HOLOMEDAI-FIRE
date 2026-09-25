import re

def parse_pyright(filepath):
    errors = []
    with open(filepath, 'r', encoding='utf-16') as f:
        for line in f:
            line = line.strip()
            if line and " - error: " in line:
                errors.append(line)
    return set(errors)

base = parse_pyright('pyright_base.txt')
curr = parse_pyright('pyright_curr.txt')

new = curr - base
print(f"New errors: {len(new)}")
for err in sorted(list(new)):
    print(err)
