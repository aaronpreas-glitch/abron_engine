"""
Patch 81b — main.py: Quant Edge API additions
1. Extend ALLOWED_KEYS with 8 new Quant Edge config keys
2. Add per-symbol breakdown to simulate-review response
3. New GET /api/brain/quant-edge endpoint
"""
import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard" / "backend" / "main.py"

text = MAIN.read_text()

# ─────────────────────────────────────────────────────────────────────────────
# R1 — Extend ALLOWED_KEYS with Quant Edge config keys
# ─────────────────────────────────────────────────────────────────────────────
OLD_R1 = '        "PUMP_DUMP_THRESHOLD", "ALERT_THRESHOLD", "REGIME_MIN_SCORE",\n    }'
NEW_R1 = ('        "PUMP_DUMP_THRESHOLD", "ALERT_THRESHOLD", "REGIME_MIN_SCORE",\n'
          '        # Quant Edge keys (Patch 81)\n'
          '        "KELLY_FRACTION", "MAX_KELLY_CAP",\n'
          '        "EV_MIN_THRESHOLD", "EV_ATR_RATIO_MIN",\n'
          '        "MOONSHOT_MIN_WP", "MOONSHOT_MIN_SENT", "MOONSHOT_SIZE_MULT",\n'
          '        "PORTFOLIO_MAX_EXPOSURE",\n'
          '    }')

assert OLD_R1 in text, "R1 anchor (ALLOWED_KEYS closing) not found"
text = text.replace(OLD_R1, NEW_R1)
print("✓ R1: ALLOWED_KEYS extended with 8 Quant Edge keys")

# ─────────────────────────────────────────────────────────────────────────────
# R2 — Add per-symbol breakdown to simulate-review response
# ─────────────────────────────────────────────────────────────────────────────
OLD_R2 = '''    # All-time historical stats (fallback for new simulation sessions)
    try:
        with _sq.connect(db_path) as _c_at:
            _c_at.row_factory = _sq.Row
            _at_rows = _c_at.execute(
                "SELECT pnl_pct FROM perp_positions "
                "WHERE dry_run=1 AND status='CLOSED' AND pnl_pct IS NOT NULL"
            ).fetchall()
            _at_pnls = [float(r[0]) for r in _at_rows]
            if _at_pnls:
                result["all_time_avg_pnl_pct"]   = round(sum(_at_pnls) / len(_at_pnls), 2)
                result["all_time_closed_count"]   = len(_at_pnls)
                result["all_time_win_rate_pct"]   = round(sum(1 for p in _at_pnls if p > 0) / len(_at_pnls) * 100, 1)
    except Exception as _ate:
        log.warning("simulate_review all_time error: %s", _ate)'''

NEW_R2 = '''    # All-time historical stats (fallback for new simulation sessions)
    try:
        with _sq.connect(db_path) as _c_at:
            _c_at.row_factory = _sq.Row
            _at_rows = _c_at.execute(
                "SELECT pnl_pct FROM perp_positions "
                "WHERE dry_run=1 AND status='CLOSED' AND pnl_pct IS NOT NULL"
            ).fetchall()
            _at_pnls = [float(r[0]) for r in _at_rows]
            if _at_pnls:
                result["all_time_avg_pnl_pct"]   = round(sum(_at_pnls) / len(_at_pnls), 2)
                result["all_time_closed_count"]   = len(_at_pnls)
                result["all_time_win_rate_pct"]   = round(sum(1 for p in _at_pnls if p > 0) / len(_at_pnls) * 100, 1)
    except Exception as _ate:
        log.warning("simulate_review all_time error: %s", _ate)

    # Per-symbol breakdown (Patch 81)
    try:
        with _sq.connect(db_path) as _c_sym:
            _sym_rows = _c_sym.execute(
                "SELECT symbol, COUNT(*) as cnt,"
                " SUM(CASE WHEN pnl_pct>0 THEN 1 ELSE 0 END) as wins,"
                " AVG(pnl_pct) as avg_pnl,"
                " SUM(CASE WHEN exit_reason='TIME_LIMIT' THEN 1 ELSE 0 END) as tl_cnt"
                " FROM perp_positions"
                " WHERE dry_run=1 AND status='CLOSED' AND pnl_pct IS NOT NULL"
                " GROUP BY symbol ORDER BY cnt DESC"
            ).fetchall()
            result["symbol_breakdown"] = [
                {
                    "symbol": r[0],
                    "trades": r[1],
                    "win_rate": round(r[2] / r[1] * 100, 1) if r[1] > 0 else 0,
                    "avg_pnl": round(r[3], 3) if r[3] is not None else 0,
                    "tl_pct": round(r[4] / r[1] * 100, 1) if r[1] > 0 else 0,
                }
                for r in _sym_rows
            ]
    except Exception as _sbe:
        log.warning("simulate_review symbol_breakdown error: %s", _sbe)
        result["symbol_breakdown"] = []'''

assert OLD_R2 in text, "R2 anchor (All-time historical stats) not found"
text = text.replace(OLD_R2, NEW_R2)
print("✓ R2: Per-symbol breakdown added to simulate-review")

# ─────────────────────────────────────────────────────────────────────────────
# R3 — New GET /api/brain/quant-edge endpoint (before journal/learnings)
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR = '@app.get("/api/journal/learnings")'
assert ANCHOR in text, f"R3 anchor '{ANCHOR}' not found in main.py"

QUANT_EDGE_ENDPOINT = '''@app.get("/api/brain/quant-edge")
async def brain_quant_edge(_: str = Depends(get_current_user)):
    """Real-time quant edge dashboard: Kelly, Heat, EV skips, Moonshots, symbol stats."""
    import sqlite3 as _sq
    from datetime import datetime, timezone, timedelta
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    out = {
        "kelly": {"win_rate": None, "avg_win": None, "avg_loss": None,
                  "kelly_f": None, "half_kelly": None, "sample_n": 0},
        "heat": {"current_pct": 0, "max_exposure": 800, "open_positions": 0,
                 "total_exposure": 0},
        "ev_skipped_24h": 0,
        "heat_skipped_24h": 0,
        "moonshot_count": 0,
        "moonshot_trades": [],
        "symbol_breakdown": [],
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    try:
        with _sq.connect(db_path) as c:
            # Kelly params — last 50 closed paper trades
            rows = c.execute(
                "SELECT pnl_pct FROM perp_positions "
                "WHERE status='CLOSED' AND dry_run=1 AND pnl_pct IS NOT NULL "
                "ORDER BY closed_ts_utc DESC LIMIT 50"
            ).fetchall()
            if rows and len(rows) >= 10:
                pnls = [float(r[0]) for r in rows]
                wins = [p for p in pnls if p > 0]
                losses = [abs(p) for p in pnls if p < 0]
                wr = len(wins) / len(pnls)
                aw = sum(wins) / len(wins) if wins else 1.0
                al = sum(losses) / len(losses) if losses else 1.0
                b = aw / al if al > 0 else 1.0
                kf = max((wr * b - (1 - wr)) / b, 0) if b > 0 else 0
                hk = kf * 0.5
                out["kelly"] = {
                    "win_rate": round(wr * 100, 1),
                    "avg_win": round(aw, 3),
                    "avg_loss": round(al, 3),
                    "kelly_f": round(kf * 100, 2),
                    "half_kelly": round(hk * 100, 2),
                    "sample_n": len(pnls),
                }

            # Portfolio heat — sum open positions
            opens = c.execute(
                "SELECT size_usd, leverage FROM perp_positions WHERE status='OPEN'"
            ).fetchall()
            total_exp = sum(
                float(r[0] or 0) * float(r[1] or 1) for r in opens
            )
            max_exp = float(os.environ.get("PORTFOLIO_MAX_EXPOSURE", "800"))
            out["heat"] = {
                "current_pct": round(total_exp / max_exp * 100, 1) if max_exp > 0 else 0,
                "max_exposure": max_exp,
                "open_positions": len(opens),
                "total_exposure": round(total_exp, 2),
            }

            # EV-skipped + Heat-skipped signals in last 24h
            cutoff_24h = (datetime.utcnow() - timedelta(hours=24)).isoformat()
            try:
                ev_skip = c.execute(
                    "SELECT COUNT(*) FROM skipped_signals_log "
                    "WHERE reason LIKE 'EV_FILTER%' AND ts >= ?",
                    (cutoff_24h,)
                ).fetchone()[0]
                out["ev_skipped_24h"] = ev_skip
            except Exception:
                pass
            try:
                heat_skip = c.execute(
                    "SELECT COUNT(*) FROM skipped_signals_log "
                    "WHERE reason LIKE 'PORTFOLIO_HEAT%' AND ts >= ?",
                    (cutoff_24h,)
                ).fetchone()[0]
                out["heat_skipped_24h"] = heat_skip
            except Exception:
                pass

            # Moonshot trades (all time)
            try:
                moon_rows = c.execute(
                    "SELECT id, symbol, side, pnl_pct, opened_ts_utc, status, notes "
                    "FROM perp_positions WHERE notes LIKE '%MOONSHOT%' "
                    "ORDER BY opened_ts_utc DESC LIMIT 20"
                ).fetchall()
                out["moonshot_count"] = c.execute(
                    "SELECT COUNT(*) FROM perp_positions WHERE notes LIKE '%MOONSHOT%'"
                ).fetchone()[0]
                out["moonshot_trades"] = [
                    {
                        "id": r[0], "symbol": r[1], "side": r[2],
                        "pnl_pct": float(r[3]) if r[3] is not None else None,
                        "ts": r[4], "status": r[5],
                    }
                    for r in moon_rows
                ]
            except Exception:
                pass

            # Per-symbol breakdown (all time, closed paper trades)
            try:
                sym_rows = c.execute(
                    "SELECT symbol, COUNT(*) as cnt,"
                    " SUM(CASE WHEN pnl_pct>0 THEN 1 ELSE 0 END) as wins,"
                    " AVG(pnl_pct) as avg_pnl,"
                    " SUM(CASE WHEN exit_reason='TIME_LIMIT' THEN 1 ELSE 0 END) as tl_cnt,"
                    " SUM(CASE WHEN notes LIKE '%MOONSHOT%' THEN 1 ELSE 0 END) as moon_cnt"
                    " FROM perp_positions"
                    " WHERE dry_run=1 AND status='CLOSED' AND pnl_pct IS NOT NULL"
                    " GROUP BY symbol ORDER BY cnt DESC"
                ).fetchall()
                out["symbol_breakdown"] = [
                    {
                        "symbol": r[0],
                        "trades": r[1],
                        "win_rate": round(r[2] / r[1] * 100, 1) if r[1] > 0 else 0,
                        "avg_pnl": round(r[3], 3) if r[3] is not None else 0,
                        "tl_pct": round(r[4] / r[1] * 100, 1) if r[1] > 0 else 0,
                        "moonshots": r[5],
                    }
                    for r in sym_rows
                ]
            except Exception:
                pass

    except Exception as exc:
        out["error"] = str(exc)

    return JSONResponse(out)


'''

text = text.replace(ANCHOR, QUANT_EDGE_ENDPOINT + ANCHOR)
print("✓ R3: /api/brain/quant-edge endpoint added")

# ─────────────────────────────────────────────────────────────────────────────
# Write + compile
# ─────────────────────────────────────────────────────────────────────────────
MAIN.write_text(text)

r = subprocess.run(
    [sys.executable, "-m", "py_compile", str(MAIN)],
    capture_output=True, text=True
)
if r.returncode != 0:
    print("✗ compile error:", r.stderr)
    sys.exit(1)
print("✓ main.py compiles OK")
print("✓ Patch 81b complete")
