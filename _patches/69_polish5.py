"""
Patch 69 — Dashboard Polish 5
==============================
Backend changes:

1. Fix simulate-review bugs:
   a. Query looks for SIMULATE_ENABLED only — auto-simulate logs SIMULATE_AUTO_STARTED,
      so the sim window was never found. Fix: include both event types.
   b. SELECT used wrong column names (realized_pnl_pct, entry_ts_utc) — DB has
      pnl_pct and opened_ts_utc. This caused a silent exception, always returning 0 trades.
   c. Add all_time stats (avg_pnl, win_rate, closed_count across ALL paper trades)
      so the frontend can fall back when current sim has no closed trades yet.

2. Add `coins` param to /api/news/cryptopanic:
   - coins=memecoins → override currencies to meme token set
   - coins=majors    → override currencies to BTC,ETH
"""

from pathlib import Path
import py_compile, tempfile, shutil

MAIN = Path(__file__).resolve().parent.parent / "dashboard" / "backend" / "main.py"
main = MAIN.read_text()

# ── 1a. Fix SIMULATE_ENABLED query to include SIMULATE_AUTO_STARTED ─────────

OLD_SIM_EVENT = (
    '                sim_row = c.execute(\n'
    '                    "SELECT ts FROM live_transition_log WHERE event_type=\'SIMULATE_ENABLED\' "\n'
    '                    "ORDER BY ts DESC LIMIT 1"\n'
    '                ).fetchone()\n'
)
NEW_SIM_EVENT = (
    '                sim_row = c.execute(\n'
    '                    "SELECT ts FROM live_transition_log "\n'
    '                    "WHERE event_type IN (\'SIMULATE_ENABLED\', \'SIMULATE_AUTO_STARTED\') "\n'
    '                    "ORDER BY ts DESC LIMIT 1"\n'
    '                ).fetchone()\n'
)
assert OLD_SIM_EVENT in main, "simulate-review SIMULATE_ENABLED query anchor not found"
main = main.replace(OLD_SIM_EVENT, NEW_SIM_EVENT)
print("✅ 1a: simulate-review now finds SIMULATE_AUTO_STARTED events")

# ── 1b. Fix wrong column names in SELECT ─────────────────────────────────────

OLD_SELECT = (
    '                    trades = c.execute(\n'
    '                        "SELECT symbol, side, status, realized_pnl_pct, entry_ts_utc, "\n'
    '                        "closed_ts_utc, exit_reason FROM perp_positions "\n'
    '                        "WHERE dry_run=1 AND entry_ts_utc >= ? ORDER BY entry_ts_utc DESC",\n'
    '                        (sim_start_ts,)\n'
    '                    ).fetchall()\n'
    '                    result["trade_count"] = len(trades)\n'
    '                    closed = [t for t in trades if t["status"] == "CLOSED" and t["realized_pnl_pct"] is not None]\n'
    '                    result["closed_count"] = len(closed)\n'
    '\n'
    '                    if closed:\n'
    '                        pnls = [float(t["realized_pnl_pct"]) for t in closed]\n'
)
NEW_SELECT = (
    '                    trades = c.execute(\n'
    '                        "SELECT symbol, side, status, pnl_pct, opened_ts_utc, "\n'
    '                        "closed_ts_utc, exit_reason FROM perp_positions "\n'
    '                        "WHERE dry_run=1 AND opened_ts_utc >= ? ORDER BY opened_ts_utc DESC",\n'
    '                        (sim_start_ts,)\n'
    '                    ).fetchall()\n'
    '                    result["trade_count"] = len(trades)\n'
    '                    closed = [t for t in trades if t["status"] == "CLOSED" and t["pnl_pct"] is not None]\n'
    '                    result["closed_count"] = len(closed)\n'
    '\n'
    '                    if closed:\n'
    '                        pnls = [float(t["pnl_pct"]) for t in closed]\n'
)
assert OLD_SELECT in main, "simulate-review SELECT anchor not found"
main = main.replace(OLD_SELECT, NEW_SELECT)
print("✅ 1b: fixed column names (pnl_pct, opened_ts_utc)")

# Fix recent_trades block — also uses wrong column names
OLD_RECENT = (
    '                    result["recent_trades"] = [\n'
    '                        {\n'
    '                            "symbol": t["symbol"], "side": t["side"],\n'
    '                            "status": t["status"],\n'
    '                            "pnl_pct": float(t["realized_pnl_pct"]) if t["realized_pnl_pct"] is not None else None,\n'
    '                            "entry_ts": t["entry_ts_utc"],\n'
    '                            "exit_reason": t["exit_reason"],\n'
    '                        }\n'
    '                        for t in (trades or [])[:10]\n'
    '                    ]\n'
)
NEW_RECENT = (
    '                    result["recent_trades"] = [\n'
    '                        {\n'
    '                            "symbol": t["symbol"], "side": t["side"],\n'
    '                            "status": t["status"],\n'
    '                            "pnl_pct": float(t["pnl_pct"]) if t["pnl_pct"] is not None else None,\n'
    '                            "entry_ts": t["opened_ts_utc"],\n'
    '                            "exit_reason": t["exit_reason"],\n'
    '                        }\n'
    '                        for t in (trades or [])[:10]\n'
    '                    ]\n'
)
assert OLD_RECENT in main, "simulate-review recent_trades anchor not found"
main = main.replace(OLD_RECENT, NEW_RECENT)
print("✅ 1b: fixed recent_trades column names")

# ── 1c. Add all_time stats to initial result dict ────────────────────────────

OLD_RESULT_INIT = (
    '        "auto_ended": False,\n'
    '        "recent_trades": [],\n'
    '        "ts": datetime.utcnow().isoformat() + "Z",\n'
    '    }\n'
    '    try:\n'
    '        with _sq.connect(db_path) as c:\n'
    '            c.row_factory = _sq.Row\n'
    '            # Get most recent SIMULATE_ENABLED event\n'
)
NEW_RESULT_INIT = (
    '        "auto_ended": False,\n'
    '        "recent_trades": [],\n'
    '        "ts": datetime.utcnow().isoformat() + "Z",\n'
    '        "all_time_avg_pnl_pct": 0.0,\n'
    '        "all_time_closed_count": 0,\n'
    '        "all_time_win_rate_pct": 0.0,\n'
    '    }\n'
    '    try:\n'
    '        with _sq.connect(db_path) as c:\n'
    '            c.row_factory = _sq.Row\n'
    '            # Get most recent SIMULATE_ENABLED event\n'
)
assert OLD_RESULT_INIT in main, "simulate-review result init anchor not found"
main = main.replace(OLD_RESULT_INIT, NEW_RESULT_INIT)
print("✅ 1c: added all_time fields to result init")

# Insert all_time query BEFORE recommendation block
OLD_BEFORE_REC = (
    '    except Exception as exc:\n'
    '        log.warning("simulate_review error: %s", exc)\n'
    '        result["error"] = str(exc)\n'
    '\n'
    '    # Build recommendation based on simulation stats\n'
    '    try:\n'
)
NEW_BEFORE_REC = (
    '    except Exception as exc:\n'
    '        log.warning("simulate_review error: %s", exc)\n'
    '        result["error"] = str(exc)\n'
    '\n'
    '    # All-time historical stats (fallback for new simulation sessions)\n'
    '    try:\n'
    '        with _sq.connect(db_path) as _c_at:\n'
    '            _c_at.row_factory = _sq.Row\n'
    '            _at_rows = _c_at.execute(\n'
    '                "SELECT pnl_pct FROM perp_positions "\n'
    '                "WHERE dry_run=1 AND status=\'CLOSED\' AND pnl_pct IS NOT NULL"\n'
    '            ).fetchall()\n'
    '            _at_pnls = [float(r[0]) for r in _at_rows]\n'
    '            if _at_pnls:\n'
    '                result["all_time_avg_pnl_pct"]   = round(sum(_at_pnls) / len(_at_pnls), 2)\n'
    '                result["all_time_closed_count"]   = len(_at_pnls)\n'
    '                result["all_time_win_rate_pct"]   = round(sum(1 for p in _at_pnls if p > 0) / len(_at_pnls) * 100, 1)\n'
    '    except Exception as _ate:\n'
    '        log.warning("simulate_review all_time error: %s", _ate)\n'
    '\n'
    '    # Build recommendation based on simulation stats\n'
    '    try:\n'
)
assert OLD_BEFORE_REC in main, "simulate-review pre-recommendation anchor not found"
main = main.replace(OLD_BEFORE_REC, NEW_BEFORE_REC)
print("✅ 1c: all_time stats query inserted")

# ── 2. Add coins param to /api/news/cryptopanic ──────────────────────────────

OLD_CP_SIG = (
    '@app.get("/api/news/cryptopanic")\n'
    'async def news_cryptopanic(\n'
    '    request: Request,\n'
    '    currencies: str = "BTC,ETH,SOL,PEPE,DOGE,SHIB,TRUMP,WIF,BONK,FLOKI,MEW,BRETT,POPCAT,PNUT,FARTCOIN",\n'
    '    filter: str = "all",\n'
    '    _: str = Depends(get_current_user),\n'
    '):\n'
)
NEW_CP_SIG = (
    '@app.get("/api/news/cryptopanic")\n'
    'async def news_cryptopanic(\n'
    '    request: Request,\n'
    '    currencies: str = "BTC,ETH,SOL,PEPE,DOGE,SHIB,TRUMP,WIF,BONK,FLOKI,MEW,BRETT,POPCAT,PNUT,FARTCOIN",\n'
    '    filter: str = "all",\n'
    '    coins: str | None = None,\n'
    '    _: str = Depends(get_current_user),\n'
    '):\n'
)
assert OLD_CP_SIG in main, "cryptopanic endpoint signature anchor not found"
main = main.replace(OLD_CP_SIG, NEW_CP_SIG)
print("✅ 2: cryptopanic endpoint signature updated with coins param")

OLD_CP_CACHE = (
    '    cache_key = (currencies.upper(), filter.lower())\n'
    '    now = _time.time()\n'
)
NEW_CP_CACHE = (
    '    # Map coins filter → currency override\n'
    '    if coins:\n'
    '        _ck = coins.lower()\n'
    '        if _ck == "memecoins":\n'
    '            currencies = "WIF,BONK,PEPE,DOGE,SHIB,FLOKI,MEW,BRETT,POPCAT,PNUT,FARTCOIN,SOL"\n'
    '        elif _ck in ("majors", "btc-eth"):\n'
    '            currencies = "BTC,ETH"\n'
    '    cache_key = (currencies.upper(), filter.lower())\n'
    '    now = _time.time()\n'
)
assert OLD_CP_CACHE in main, "cryptopanic cache_key anchor not found"
main = main.replace(OLD_CP_CACHE, NEW_CP_CACHE)
print("✅ 2: cryptopanic coins → currencies mapping added")

# ── Write + compile ──────────────────────────────────────────────────────────

MAIN.write_text(main)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ main.py compiles OK")
print("\nPatch 69 complete")
