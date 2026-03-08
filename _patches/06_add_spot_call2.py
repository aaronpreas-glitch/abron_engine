#!/usr/bin/env python3
"""Add _process_spot_learnings() call at the second location in auto_tune."""
TUNE_PY = '/root/memecoin_engine/auto_tune.py'

with open(TUNE_PY, 'r') as f:
    code = f.read()

old = '        _process_perp_learnings()\n        _adaptive_perp_tune()\n'
new = '        _process_perp_learnings()\n        _process_spot_learnings()\n        _adaptive_perp_tune()\n'

if old in code and '_process_spot_learnings()\n        _adaptive_perp_tune()' not in code:
    code = code.replace(old, new, 1)
    with open(TUNE_PY, 'w') as f:
        f.write(code)
    print("✓ Added _process_spot_learnings() at second location")
else:
    print("⚠ Already there or pattern not found")
