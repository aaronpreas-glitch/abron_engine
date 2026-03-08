#!/usr/bin/env python3
"""Fix: Add ML_MIN_WIN_PROB lambda definition to perp_executor.py"""

PERP_PY = "/root/memecoin_engine/utils/perp_executor.py"

with open(PERP_PY, "r") as f:
    code = f.read()

# Check if the lambda DEFINITION exists (not just the call)
if 'ML_MIN_WIN_PROB    = lambda' not in code and 'ML_MIN_WIN_PROB = lambda' not in code:
    # Find the SCALP_ENABLED line
    lines = code.split('\n')
    inserted = False
    for i, line in enumerate(lines):
        if 'SCALP_ENABLED' in line and 'lambda' in line:
            indent = ''
            for ch in line:
                if ch == ' ':
                    indent += ' '
                else:
                    break
            ml_line = f'{indent}ML_MIN_WIN_PROB     = lambda: _float("ML_MIN_WIN_PROB", 0.0)  # 0=disabled; 0.5=skip <50%'
            lines.insert(i, ml_line)
            inserted = True
            print(f"✓ Added ML_MIN_WIN_PROB lambda at line {i+1}")
            break
    if not inserted:
        print("✗ Could not find SCALP_ENABLED line")
    else:
        code = '\n'.join(lines)
        with open(PERP_PY, "w") as f:
            f.write(code)
else:
    print("⚠ ML_MIN_WIN_PROB lambda already defined")

print("✅ Done")
