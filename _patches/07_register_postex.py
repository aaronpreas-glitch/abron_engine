#!/usr/bin/env python3
"""Register task_post_exit in lifespan."""
MAIN_PY = '/root/memecoin_engine/dashboard/backend/main.py'

with open(MAIN_PY, 'r') as f:
    code = f.read()

if 'task_post_exit' not in code:
    # Add after task_wl_momentum
    old = "    task_wl_momentum = asyncio.create_task(_watchlist_momentum_loop())"
    new = """    task_wl_momentum = asyncio.create_task(_watchlist_momentum_loop())
    task_post_exit   = asyncio.create_task(_post_exit_monitor_loop())"""

    if old in code:
        code = code.replace(old, new, 1)
        print("✓ Added task_post_exit creation")
    else:
        print("✗ Could not find insertion point")

    # Add to cancel block
    old_cancel = "    task_wl_momentum"
    # Find the cancel block — look for ".cancel()" near task_wl_momentum
    lines = code.split('\n')
    for i, line in enumerate(lines):
        if 'task_wl_momentum.cancel()' in line:
            indent = line[:len(line) - len(line.lstrip())]
            lines.insert(i + 1, f'{indent}task_post_exit.cancel()')
            print(f"✓ Added task_post_exit.cancel() at line {i+2}")
            break
    else:
        print("⚠ Could not find cancel block for task_wl_momentum")

    code = '\n'.join(lines)

    with open(MAIN_PY, 'w') as f:
        f.write(code)
    print("✅ Post-exit task registered")
else:
    print("⚠ task_post_exit already registered")
