"""
Patch 71 — Live Monitor Card
- Backend: GET /api/risk/live-monitor (10s cache, open positions, exposure, daily PnL, equity curve)
- Frontend: LiveMonitorCard in Brain.tsx overview tab (collapsible, only shown when real/sim-live active)
"""
import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard/backend/main.py"
BRAIN = ROOT / "dashboard/frontend/dist"   # frontend patched locally — this is placeholder

# ─── 1. BACKEND — main.py ─────────────────────────────────────────────────────

main_text = MAIN.read_text()

ANCHOR_JOURNAL = '@app.get("/api/journal/learnings")'
assert ANCHOR_JOURNAL in main_text, "anchor @app.get('/api/journal/learnings') not found"

NEW_ENDPOINT = '''
# ── Live Monitor Card cache ────────────────────────────────────────────────────
_live_monitor_cache: dict = {"ts": 0.0, "data": None}

@app.get("/api/risk/live-monitor")
async def risk_live_monitor(_: str = Depends(get_current_user)):
    """Real-time live/sim-live stats card. 10s cache. Only meaningful when real_money_mode or simulate_live_mode."""
    import time as _t, sqlite3 as _sq, json as _jj
    global _live_monitor_cache
    now = _t.time()
    if _live_monitor_cache["data"] and now - _live_monitor_cache["ts"] < 10:
        return JSONResponse(_live_monitor_cache["data"])

    real_money = os.environ.get("REAL_MONEY_MODE", "false").lower() in ("1", "true", "yes")
    sim_live   = os.environ.get("SIMULATE_LIVE_MODE", "false").lower() in ("1", "true", "yes")
    real_base  = float(os.environ.get("REAL_BASE_USD", "100") or "100")
    acct_bal   = float(os.environ.get("ACCOUNT_BALANCE_USD", "0") or "0")

    # For live money use dry_run=0; for sim-live use dry_run=1; default paper
    dry_run_val = 0 if real_money else 1

    result: dict = {
        "real_money_mode":    real_money,
        "simulate_live_mode": sim_live,
        "active":             real_money or sim_live,
        "real_base_usd":      real_base,
        "account_balance_usd": acct_bal,
        "open_count":         0,
        "open_positions":     [],
        "exposure_usd":       0.0,
        "daily_pnl_usd":      0.0,
        "daily_pnl_pct":      0.0,
        "current_risk_pct":   0.0,
        "last_5_trades":      [],
        "equity_curve":       [],
        "ts":                 datetime.utcnow().isoformat() + "Z",
    }

    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    try:
        with _sq.connect(db_path) as c:
            c.row_factory = _sq.Row

            # ── Open positions ──────────────────────────────────────────────
            opens = c.execute(
                "SELECT symbol, side, entry_price, size_usd, opened_ts_utc "
                "FROM perp_positions WHERE status='OPEN' AND dry_run=? "
                "ORDER BY opened_ts_utc DESC",
                (dry_run_val,)
            ).fetchall()
            result["open_count"] = len(opens)
            exposure = 0.0
            pos_list = []
            for p in opens:
                sz = float(p["size_usd"] or 0)
                exposure += sz
                pos_list.append({
                    "symbol":      p["symbol"],
                    "side":        p["side"],
                    "entry_price": p["entry_price"],
                    "size_usd":    sz,
                    "opened_ts":   p["opened_ts_utc"],
                })
            result["open_positions"] = pos_list
            result["exposure_usd"]   = round(exposure, 2)
            if acct_bal > 0 and exposure > 0:
                result["current_risk_pct"] = round(exposure / acct_bal * 100, 2)

            # ── Today's closed PnL ──────────────────────────────────────────
            today_str = datetime.utcnow().strftime("%Y-%m-%d")
            today_rows = c.execute(
                "SELECT pnl_usd, pnl_pct FROM perp_positions "
                "WHERE status='CLOSED' AND dry_run=? AND closed_ts_utc >= ? "
                "ORDER BY closed_ts_utc DESC",
                (dry_run_val, today_str)
            ).fetchall()
            if today_rows:
                result["daily_pnl_usd"] = round(sum(float(r["pnl_usd"] or 0) for r in today_rows), 2)
                result["daily_pnl_pct"] = round(sum(float(r["pnl_pct"] or 0) for r in today_rows), 2)

            # ── Last 5 trades ───────────────────────────────────────────────
            last5 = c.execute(
                "SELECT symbol, side, pnl_pct, pnl_usd, exit_reason, closed_ts_utc "
                "FROM perp_positions WHERE status='CLOSED' AND dry_run=? "
                "ORDER BY closed_ts_utc DESC LIMIT 5",
                (dry_run_val,)
            ).fetchall()
            result["last_5_trades"] = [
                {
                    "symbol":      r["symbol"],
                    "side":        r["side"],
                    "pnl_pct":     round(float(r["pnl_pct"] or 0), 2),
                    "pnl_usd":     round(float(r["pnl_usd"] or 0), 2),
                    "exit_reason": r["exit_reason"],
                    "closed_ts":   r["closed_ts_utc"],
                }
                for r in last5
            ]

            # ── Equity curve — last 50 closed trades ────────────────────────
            eq_rows = c.execute(
                "SELECT pnl_pct FROM perp_positions WHERE status='CLOSED' AND dry_run=? "
                "ORDER BY closed_ts_utc ASC LIMIT 50",
                (dry_run_val,)
            ).fetchall()
            cum = 0.0
            eq = []
            for i, r in enumerate(eq_rows):
                cum += float(r["pnl_pct"] or 0)
                eq.append({"n": i + 1, "cum_pct": round(cum, 2)})
            result["equity_curve"] = eq

    except Exception as exc:
        log.warning("live_monitor error: %s", exc)
        result["error"] = str(exc)

    _live_monitor_cache["data"] = result
    _live_monitor_cache["ts"]   = now
    return JSONResponse(result)

'''

main_text = main_text.replace(ANCHOR_JOURNAL, NEW_ENDPOINT + ANCHOR_JOURNAL)
assert "_live_monitor_cache" in main_text, "live_monitor cache not inserted"
MAIN.write_text(main_text)
print("✓ main.py — /api/risk/live-monitor inserted")

# ── Compile check ────────────────────────────────────────────────────────
r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("✗ compile error:", r.stderr)
    sys.exit(1)
print("✓ main.py compiles OK")
