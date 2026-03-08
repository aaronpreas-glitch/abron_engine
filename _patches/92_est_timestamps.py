#!/usr/bin/env python3
"""Patch 92 — Switch all orchestrator timestamps from UTC to EST (UTC-5).

Changes:
  1. utils/orchestrator.py  — append_memory() timestamp header
  2. dashboard/backend/main.py — _research_agent_loop() Generated header
"""
from pathlib import Path
import subprocess

# ─────────────────────────────────────────────────────────────────────────────
# 1. utils/orchestrator.py
# ─────────────────────────────────────────────────────────────────────────────
ORCH = Path("/root/memecoin_engine/utils/orchestrator.py")
text = ORCH.read_text()

OLD1 = "from datetime import datetime, timezone"
assert text.count(OLD1) == 1, f"Step1: {text.count(OLD1)}"
NEW1 = "from datetime import datetime, timezone, timedelta\n_EST = timezone(timedelta(hours=-5))"
text = text.replace(OLD1, NEW1)
print("Step 1a: added _EST constant ✓")

OLD2 = 'datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")'
assert text.count(OLD2) == 1, f"Step1b: {text.count(OLD2)}"
NEW2 = 'datetime.now(_EST).strftime("%Y-%m-%d %H:%M:%S EST")'
text = text.replace(OLD2, NEW2)
print("Step 1b: orchestrator.py timestamp → EST ✓")

ORCH.write_text(text)
r = subprocess.run(["python3", "-m", "py_compile", str(ORCH)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR:", r.stderr); raise SystemExit(1)
print("orchestrator.py compile OK ✓")

# ─────────────────────────────────────────────────────────────────────────────
# 2. dashboard/backend/main.py — research loop
# ─────────────────────────────────────────────────────────────────────────────
MAIN = Path("/root/memecoin_engine/dashboard/backend/main.py")
text = MAIN.read_text()

OLD3 = (
    "            from datetime import datetime as _dtr, timezone as _tzr\n"
    '            _now_str = _dtr.now(_tzr.utc).strftime("%Y-%m-%d %H:%M UTC")'
)
assert text.count(OLD3) == 1, f"Step2: {text.count(OLD3)}"
NEW3 = (
    "            from datetime import datetime as _dtr, timezone as _tzr, timedelta as _tdd\n"
    "            _EST_r = _tzr(_tdd(hours=-5))\n"
    '            _now_str = _dtr.now(_EST_r).strftime("%Y-%m-%d %H:%M EST")'
)
text = text.replace(OLD3, NEW3)
print("Step 2: main.py research timestamp → EST ✓")

MAIN.write_text(text)
r = subprocess.run(["python3", "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR:", r.stderr); raise SystemExit(1)
print("main.py compile OK ✓")
print("Patch 92: EST timestamps applied successfully")
