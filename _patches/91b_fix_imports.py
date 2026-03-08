#!/usr/bin/env python3
"""Patch 91b — Fix wrongly-placed imports from OP-1 of patch 91.

OP-1 inserted module-level imports inside an indented try: block.
This patch:
  1. Removes them from inside the try: block
  2. Adds them properly at the top-level import section
"""
from pathlib import Path
import subprocess

MAIN = Path("/root/memecoin_engine/dashboard/backend/main.py")
text = MAIN.read_text()

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Remove wrongly-indented imports from inside the try: block
# ─────────────────────────────────────────────────────────────────────────────
OLD1 = (
    "            from utils.perp_executor import perp_monitor_step  # type: ignore\n"
    "from utils.orchestrator import (\n"
    "    heartbeat as _orch_hb, append_memory as _orch_mem,\n"
    "    get_status as _orch_status, write_research as _orch_write_res,\n"
    "    read_memory as _orch_read_mem, read_research as _orch_read_res,\n"
    ")  # Patch 91\n"
    "from utils.telegram_alerts import (\n"
    "    send_telegram as _send_tg, send_telegram_sync as _send_tg_sync,\n"
    "    should_rate_limit as _tg_rl,\n"
    ")  # Patch 91"
)
assert text.count(OLD1) == 1, f"Step1: expected 1, found {text.count(OLD1)}"
NEW1 = "            from utils.perp_executor import perp_monitor_step  # type: ignore"
text = text.replace(OLD1, NEW1)
print("Step 1: wrongly-placed imports removed ✓")

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Add at module top-level, after `from pydantic import BaseModel`
# ─────────────────────────────────────────────────────────────────────────────
OLD2 = "from pydantic import BaseModel"
assert text.count(OLD2) == 1, f"Step2: expected 1, found {text.count(OLD2)}"
NEW2 = (
    "from pydantic import BaseModel\n"
    "\n"
    "# Patch 91 — Orchestrator agent registry + Telegram alerts\n"
    "try:\n"
    "    from utils.orchestrator import (\n"
    "        heartbeat as _orch_hb, append_memory as _orch_mem,\n"
    "        get_status as _orch_status, write_research as _orch_write_res,\n"
    "        read_memory as _orch_read_mem, read_research as _orch_read_res,\n"
    "    )\n"
    "    from utils.telegram_alerts import (\n"
    "        send_telegram as _send_tg, send_telegram_sync as _send_tg_sync,\n"
    "        should_rate_limit as _tg_rl,\n"
    "    )\n"
    "except ImportError as _orch_ie:\n"
    "    import logging as _il; _il.getLogger(__name__).warning('Patch 91 import error: %s', _orch_ie)\n"
    "    def _orch_hb(a): pass\n"
    "    def _orch_mem(a, m): pass\n"
    "    def _orch_status(): return []\n"
    "    def _orch_write_res(c): pass\n"
    "    def _orch_read_mem(n=50): return ''\n"
    "    def _orch_read_res(): return ''\n"
    "    async def _send_tg(t, b, e='🤖'): return False\n"
    "    def _send_tg_sync(t, b, e='🤖'): return False\n"
    "    def _tg_rl(k, l=300): return False"
)
text = text.replace(OLD2, NEW2)
assert text.count(NEW2) == 1, "Step 2 replacement error"
print("Step 2: module-level imports added with fallback ✓")

# ─────────────────────────────────────────────────────────────────────────────
MAIN.write_text(text)
r = subprocess.run(
    ["python3", "-m", "py_compile", str(MAIN)],
    capture_output=True, text=True
)
if r.returncode != 0:
    print("COMPILE ERROR:", r.stderr)
    raise SystemExit(1)
print("Patch 91b: compile OK ✓")
print(f"main.py now {len(text.splitlines())} lines")
