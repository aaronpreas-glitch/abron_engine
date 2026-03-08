#!/usr/bin/env python3
"""Add _process_spot_learnings() calls alongside _process_perp_learnings()."""

TUNE_PY = '/root/memecoin_engine/auto_tune.py'

with open(TUNE_PY, 'r') as f:
    code = f.read()

# Check if the CALL (not definition) already exists
# The definition looks like "def _process_spot_learnings()"
# The call would be just "    _process_spot_learnings()" without "def"
lines = code.split('\n')
has_call = any(
    '_process_spot_learnings()' in line and 'def ' not in line
    for line in lines
)

if not has_call:
    old = '            _process_perp_learnings()\n'
    new = '            _process_perp_learnings()\n            _process_spot_learnings()\n'
    count = code.count(old)
    if count > 0:
        code = code.replace(old, new)
        with open(TUNE_PY, 'w') as f:
            f.write(code)
        print(f"✓ Added _process_spot_learnings() call in {count} location(s)")
    else:
        print("✗ Could not find the insertion pattern")
else:
    print("⚠ Call already exists, skipping")
