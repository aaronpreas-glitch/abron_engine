#!/usr/bin/env python3
"""Patch 91c — Fix sys.path for Patch 91 module-level imports.

The orchestrator/telegram_alerts imports fail because `utils` isn't on sys.path
at module load time (main.py runs from dashboard/backend/).
This patch inserts the engine root into sys.path before the try-import block.
"""
from pathlib import Path
import subprocess

MAIN = Path("/root/memecoin_engine/dashboard/backend/main.py")
text = MAIN.read_text()

# Replace the Patch 91 try-import block to add sys.path setup first
OLD = (
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
assert text.count(OLD) == 1, f"anchor count: {text.count(OLD)}"

NEW = (
    "# Patch 91 — Orchestrator agent registry + Telegram alerts\n"
    "import sys as _sys_orch\n"
    "_orch_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))\n"
    "if _orch_root not in _sys_orch.path:\n"
    "    _sys_orch.path.insert(0, _orch_root)\n"
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
text = text.replace(OLD, NEW)
assert text.count(NEW) == 1, "replacement error"
print("sys.path insert added before Patch 91 imports ✓")

MAIN.write_text(text)
r = subprocess.run(["python3", "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR:", r.stderr)
    raise SystemExit(1)
print("Patch 91c: compile OK ✓")
