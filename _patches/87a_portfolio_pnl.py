#!/usr/bin/env python3
"""Patch 87a — Add realized_pnl_usd_today to /api/stats/today endpoint.

Adds sum of pnl_usd for today's closed perp positions to the stats response,
so the header can show portfolio PnL in USD.
"""
from pathlib import Path
import subprocess

MAIN = Path("/root/memecoin_engine/dashboard/backend/main.py")
text = MAIN.read_text()

# ── R1: Add pnl_usd to the stats query and compute sum ─────────────────────
OLD = '            rows = c.execute("""\n                SELECT pnl_pct FROM perp_positions\n                WHERE status = \'CLOSED\'\n                  AND date(closed_ts_utc) = date(\'now\')\n            """).fetchall()'

NEW = '            rows = c.execute("""\n                SELECT pnl_pct, pnl_usd FROM perp_positions\n                WHERE status = \'CLOSED\'\n                  AND date(closed_ts_utc) = date(\'now\')\n            """).fetchall()'

assert text.count(OLD) == 1, f"R1: expected 1 match, found {text.count(OLD)}"
text = text.replace(OLD, NEW)

# ── R2: Compute realized_pnl_usd_today from pnl_usd column ─────────────────
OLD2 = '        pnls = [float(r["pnl_pct"]) for r in rows if r["pnl_pct"] is not None]'

NEW2 = ('        pnls     = [float(r["pnl_pct"]) for r in rows if r["pnl_pct"] is not None]\n'
        '        usd_vals = [float(r["pnl_usd"]) for r in rows if r["pnl_usd"] is not None]\n'
        '        realized_pnl_usd = round(sum(usd_vals), 2) if usd_vals else None')

assert text.count(OLD2) == 1, f"R2: expected 1 match, found {text.count(OLD2)}"
text = text.replace(OLD2, NEW2)

# ── R3: Include realized_pnl_usd_today in return JSON ──────────────────────
OLD3 = '            "trades_today": trades_today,\n            "avg_pnl_pct":  avg_pnl,\n            "win_rate_pct": win_rate,\n            "fg_value":     fg_value,\n            "fg_label":     fg_label,'

NEW3 = ('            "trades_today":          trades_today,\n'
        '            "avg_pnl_pct":           avg_pnl,\n'
        '            "win_rate_pct":          win_rate,\n'
        '            "realized_pnl_usd_today": realized_pnl_usd,\n'
        '            "fg_value":              fg_value,\n'
        '            "fg_label":              fg_label,')

assert text.count(OLD3) == 1, f"R3: expected 1 match, found {text.count(OLD3)}"
text = text.replace(OLD3, NEW3)

MAIN.write_text(text)
print("87a R1-R3: realized_pnl_usd_today added to stats/today ✓")

r = subprocess.run(["python3", "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR:", r.stderr)
    raise SystemExit(1)
print("87a compile OK ✓")
print("\n87a patch complete — restart service to activate")
