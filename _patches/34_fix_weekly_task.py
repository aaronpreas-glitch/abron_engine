#!/usr/bin/env python3
"""Fix: Register task_weekly_report in lifespan."""

MAIN_PY = "/root/memecoin_engine/dashboard/backend/main.py"
with open(MAIN_PY, "r") as f:
    code = f.read()

if 'task_weekly_report' not in code:
    # Find task_post_exit line and add weekly after it
    lines = code.split('\n')
    for i, line in enumerate(lines):
        if 'task_post_exit' in line and 'create_task' in line:
            indent = line[:len(line) - len(line.lstrip())]
            new_line = f'{indent}task_weekly_report = asyncio.create_task(_weekly_report_loop())'
            lines.insert(i + 1, new_line)
            print(f"✓ Added task_weekly_report at line {i+2}")
            break

    # Add to all_tasks tuple
    for i, line in enumerate(lines):
        if 'task_post_exit)' in line and 'all_tasks' in lines[max(0,i-2):i+1].__repr__():
            lines[i] = line.replace('task_post_exit)', 'task_post_exit, task_weekly_report)')
            print(f"✓ Added to all_tasks at line {i+1}")
            break
        elif 'task_post_exit)' in line:
            lines[i] = line.replace('task_post_exit)', 'task_post_exit, task_weekly_report)')
            print(f"✓ Added to all_tasks at line {i+1}")
            break

    code = '\n'.join(lines)
    with open(MAIN_PY, "w") as f:
        f.write(code)
else:
    print("⚠ task_weekly_report already registered")

print("✅ Done")
