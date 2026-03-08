#!/usr/bin/env python3
"""Fix f-string quoting issue in perp_executor.py scalp notes block."""
PERP_PY = '/root/memecoin_engine/utils/perp_executor.py'

with open(PERP_PY, 'r') as f:
    lines = f.readlines()

fixed = False
for i, line in enumerate(lines):
    if 'mode=SCALP|source={signal.get("source","scalp")}' in line:
        lines[i] = line.replace(
            'signal.get("source","scalp")',
            "signal.get('source','scalp')"
        )
        print(f'Fixed line {i+1}: f-string quoting')
        fixed = True

if not fixed:
    print('No fix needed — pattern not found')
else:
    with open(PERP_PY, 'w') as f:
        f.writelines(lines)
    print('Saved')
