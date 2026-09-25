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

removed = base - curr
print(f"Base errors: {len(base)}")
print(f"Curr errors: {len(curr)}")
print(f"Removed errors: {len(removed)}")

with open('pyright_removed.txt', 'w', encoding='utf-8') as f:
    for err in sorted(list(removed)):
        f.write(err + '\n')
