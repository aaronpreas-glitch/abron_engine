"""
Patch 77b — Fix extend-simulation column names
live_transition_log schema: ts, event_type, reason (NOT ts_utc, NOT note)
"""

import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard/backend/main.py"

main_text = MAIN.read_text()

OLD = '''            c.execute(
                "INSERT INTO live_transition_log (event_type, ts_utc, note) VALUES (?, ?, ?)",
                ("SIMULATE_AUTO_STARTED", ts_now, "Extended +24h via dashboard button"),
            )'''

NEW = '''            c.execute(
                "INSERT INTO live_transition_log (ts, event_type, reason) VALUES (?, ?, ?)",
                (ts_now, "SIMULATE_AUTO_STARTED", "Extended +24h via dashboard button"),
            )'''

assert OLD in main_text, "old SQL not found"
main_text = main_text.replace(OLD, NEW)
MAIN.write_text(main_text)
print("✓ main.py — fixed extend-simulation column names")

r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("✗ compile error:", r.stderr); sys.exit(1)
print("✓ main.py compiles OK")
