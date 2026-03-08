#!/usr/bin/env python3
"""Add task_post_exit to the all_tasks cancel tuple."""
MAIN_PY = '/root/memecoin_engine/dashboard/backend/main.py'

with open(MAIN_PY, 'r') as f:
    code = f.read()

old = "                 task_spot_mon, task_spot_scan, task_wl_momentum)"
new = "                 task_spot_mon, task_spot_scan, task_wl_momentum, task_post_exit)"

if old in code and 'task_post_exit)' not in code:
    code = code.replace(old, new, 1)
    with open(MAIN_PY, 'w') as f:
        f.write(code)
    print("✓ Added task_post_exit to all_tasks tuple")
else:
    print("⚠ Already there or pattern not found")
