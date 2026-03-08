#!/usr/bin/env python3
"""Fix: Add ML_MIN_WIN_PROB lambda to perp_executor.py"""

PERP_PY = "/root/memecoin_engine/utils/perp_executor.py"

with open(PERP_PY, "r") as f:
    code = f.read()

if 'ML_MIN_WIN_PROB' not in code:
    # Find the SCALP_ENABLED line with exact spacing
    lines = code.split('\n')
    for i, line in enumerate(lines):
        if 'SCALP_ENABLED' in line and 'lambda' in line:
            # Insert ML lambda before SCALP_ENABLED
            indent = line[:len(line) - len(line.lstrip())]
            ml_line = f'{indent}ML_MIN_WIN_PROB    = lambda: _float("ML_MIN_WIN_PROB", 0.0)  # 0 = no filter; 0.5 = skip <50% win_prob'
            lines.insert(i, ml_line)
            print(f"✓ Added ML_MIN_WIN_PROB lambda at line {i+1}")
            break
    code = '\n'.join(lines)

    with open(PERP_PY, "w") as f:
        f.write(code)
else:
    print("⚠ ML_MIN_WIN_PROB already exists")

# Also verify ML_MIN_WIN_PROB is referenced where needed
if 'ML_MIN_WIN_PROB()' in code:
    print("✓ ML_MIN_WIN_PROB() call verified")
else:
    print("⚠ ML_MIN_WIN_PROB() not called in code")

print("\n✅ Lambda fix complete")
