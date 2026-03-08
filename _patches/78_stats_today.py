"""
Patch 78 — GET /api/stats/today
Returns today's trading stats + Fear & Greed index for the TopBar.
  - trades_today: count of CLOSED perp_positions where date(closed_ts_utc) = today
  - avg_pnl_pct:  average pnl_pct across those trades (null if 0 trades)
  - win_rate_pct: win % (pnl_pct > 0) across those trades (null if 0 trades)
  - fg_value:     Fear & Greed value from _market_pulse_cache (null if cache cold)
  - fg_label:     F&G classification label
No external API calls — pure DB read + cache read.
"""

import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard/backend/main.py"

main_text = MAIN.read_text()

ANCHOR = '@app.get("/api/journal/learnings")'
assert ANCHOR in main_text, f"anchor not found: {ANCHOR}"
assert "stats/today" not in main_text, "stats_today already inserted"

NEW_ENDPOINT = '''
# ── Today's Stats (TopBar) ─────────────────────────────────────────────────────
@app.get("/api/stats/today")
async def stats_today(_: str = Depends(get_current_user)):
    """Today\'s PERP trading stats + Fear & Greed from cache. No external calls."""
    import sqlite3 as _sq
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    try:
        with _sq.connect(db_path) as c:
            c.row_factory = _sq.Row
            rows = c.execute("""
                SELECT pnl_pct FROM perp_positions
                WHERE status = 'CLOSED'
                  AND date(closed_ts_utc) = date('now')
            """).fetchall()

        trades_today = len(rows)
        pnls = [float(r["pnl_pct"]) for r in rows if r["pnl_pct"] is not None]

        if pnls:
            avg_pnl  = round(sum(pnls) / len(pnls), 2)
            wins     = sum(1 for p in pnls if p > 0)
            win_rate = round(wins / len(pnls) * 100, 1)
        else:
            avg_pnl  = None
            win_rate = None

        # F&G from market-pulse cache (populated by /api/social/market-pulse)
        fg_value = None
        fg_label = None
        try:
            cached = _market_pulse_cache.get("default")
            if cached:
                fg_data = (cached.get("data") or {}).get("fear_greed")
                if fg_data:
                    fg_value = int(fg_data.get("value") or 0)
                    fg_label = str(fg_data.get("label") or "")
        except Exception:
            pass  # cache may not exist yet

        return JSONResponse({
            "trades_today": trades_today,
            "avg_pnl_pct":  avg_pnl,
            "win_rate_pct": win_rate,
            "fg_value":     fg_value,
            "fg_label":     fg_label,
            "ts":           datetime.utcnow().isoformat() + "Z",
        })
    except Exception as exc:
        log.warning("stats_today error: %s", exc)
        return JSONResponse({
            "trades_today": 0,
            "avg_pnl_pct":  None,
            "win_rate_pct": None,
            "fg_value":     None,
            "fg_label":     None,
        })


'''

main_text = main_text.replace(ANCHOR, NEW_ENDPOINT + ANCHOR)
assert "stats/today" in main_text, "endpoint not inserted"
MAIN.write_text(main_text)
print("✓ main.py — /api/stats/today inserted")

r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("✗ compile error:", r.stderr); sys.exit(1)
print("✓ main.py compiles OK")
