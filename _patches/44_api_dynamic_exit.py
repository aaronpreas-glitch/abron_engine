#!/usr/bin/env python3
"""Patch 44 — API endpoints for dynamic exit performance + extend trade detail.

1. Extend /api/perps/trade/{trade_id} with dynamic_exit_timeline + partial_closes
2. Add GET /api/brain/dynamic-exit-performance
3. Add GET /api/perps/time-limit-pct
"""
import os

TARGET = "/root/memecoin_engine/dashboard/backend/main.py"

with open(TARGET, "r") as f:
    code = f.read()

# ═══════════════════════════════════════════════════════════════
# 1. Extend /api/perps/trade/{trade_id} with timeline + partial_closes
# ═══════════════════════════════════════════════════════════════
old_trade_return = '''        return {
            "trade": trade,
            "notes_parsed": notes_parsed,
            "post_exit": post_exit,
            "ml_prediction": ml_pred,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/perps/mini-chart/{symbol}")'''

new_trade_return = '''        # Dynamic exit timeline
        dyn_timeline = []
        try:
            dyn_rows = conn.execute("""
                SELECT * FROM dynamic_exit_log WHERE position_id = ? ORDER BY ts_utc ASC
            """, (trade_id,)).fetchall()
            dyn_timeline = [dict(r) for r in dyn_rows]
        except Exception:
            pass

        # Partial closes
        partial_closes = []
        if trade.get("partial_closes"):
            import json as _json
            try:
                partial_closes = _json.loads(trade["partial_closes"])
            except Exception:
                pass

        return {
            "trade": trade,
            "notes_parsed": notes_parsed,
            "post_exit": post_exit,
            "ml_prediction": ml_pred,
            "dynamic_exit_timeline": dyn_timeline,
            "partial_closes": partial_closes,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/perps/mini-chart/{symbol}")'''

assert old_trade_return in code, "Cannot find perps/trade return block"
code = code.replace(old_trade_return, new_trade_return, 1)
print("[OK] Extended /api/perps/trade/{id} with timeline + partial_closes")

# ═══════════════════════════════════════════════════════════════
# 2. Add /api/brain/dynamic-exit-performance + /api/perps/time-limit-pct
#    Insert before /api/journal/learnings
# ═══════════════════════════════════════════════════════════════
MARKER = '@app.get("/api/journal/learnings")'

NEW_ENDPOINTS = '''
@app.get("/api/brain/dynamic-exit-performance")
async def brain_dynamic_exit_performance():
    """Aggregate stats for dynamic exit decisions."""
    import sqlite3
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Exit reason distribution
            by_reason = conn.execute("""
                SELECT exit_reason, COUNT(*) as n,
                       ROUND(AVG(pnl_pct), 3) as avg_pnl,
                       SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins
                FROM perp_positions
                WHERE status='CLOSED' AND exit_reason IS NOT NULL
                GROUP BY exit_reason ORDER BY n DESC
            """).fetchall()

            # Dynamic exit log stats
            dyn_stats = conn.execute("""
                SELECT del.action, del.outcome, COUNT(*) as n,
                       ROUND(AVG(del.pnl_at_decision), 3) as avg_pnl_at_decision,
                       ROUND(AVG(pp.pnl_pct), 3) as avg_final_pnl
                FROM dynamic_exit_log del
                LEFT JOIN perp_positions pp ON del.position_id = pp.id
                GROUP BY del.action, del.outcome
            """).fetchall()

            # TIME_LIMIT % daily for trend chart
            time_limit_daily = conn.execute("""
                SELECT DATE(closed_ts_utc) as day,
                       COUNT(*) as total,
                       SUM(CASE WHEN exit_reason='TIME_LIMIT' THEN 1 ELSE 0 END) as time_limit_n
                FROM perp_positions
                WHERE status='CLOSED' AND closed_ts_utc IS NOT NULL
                GROUP BY day ORDER BY day ASC
            """).fetchall()

            total_closed = conn.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status='CLOSED'"
            ).fetchone()[0] or 0
            time_limit_total = conn.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status='CLOSED' AND exit_reason='TIME_LIMIT'"
            ).fetchone()[0] or 0
            dynamic_reasons = (
                "'DYNAMIC_TRAIL','ML_PROB_DROP','ML_EARLY_EXIT',"
                "'TRAILING_ATR_EXTEND','TRAILING_ATR_WINNER','PARTIAL_PROFIT'"
            )
            dynamic_total = conn.execute(
                f"SELECT COUNT(*) FROM perp_positions WHERE status='CLOSED' AND exit_reason IN ({dynamic_reasons})"
            ).fetchone()[0] or 0

            # Good call rate
            good_calls = conn.execute(
                "SELECT COUNT(*) FROM dynamic_exit_log WHERE outcome='good_call'"
            ).fetchone()[0] or 0
            total_labeled = conn.execute(
                "SELECT COUNT(*) FROM dynamic_exit_log WHERE outcome IS NOT NULL AND outcome != ''"
            ).fetchone()[0] or 0

        return {
            "by_reason": [dict(r) for r in by_reason],
            "dynamic_exit_stats": [dict(r) for r in dyn_stats],
            "time_limit_daily": [dict(r) for r in time_limit_daily],
            "total_closed": total_closed,
            "time_limit_total": time_limit_total,
            "dynamic_total": dynamic_total,
            "time_limit_pct": round(time_limit_total / total_closed * 100, 1) if total_closed else 0,
            "good_call_rate": round(good_calls / total_labeled * 100, 1) if total_labeled else 0,
            "total_labeled": total_labeled,
        }
    except Exception as e:
        return {"error": str(e), "by_reason": [], "dynamic_exit_stats": []}


@app.get("/api/perps/time-limit-pct")
async def perps_time_limit_pct():
    """Daily TIME_LIMIT exit percentage for command center."""
    import sqlite3
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Last 24 hours
            row = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN exit_reason='TIME_LIMIT' THEN 1 ELSE 0 END) as tl
                FROM perp_positions
                WHERE status='CLOSED' AND closed_ts_utc > datetime('now', '-1 day')
            """).fetchone()
            total = row["total"] or 0
            tl = row["tl"] or 0
            # All time
            all_row = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN exit_reason='TIME_LIMIT' THEN 1 ELSE 0 END) as tl
                FROM perp_positions WHERE status='CLOSED'
            """).fetchone()
            return {
                "total_24h": total,
                "time_limit_24h": tl,
                "pct_24h": round(tl / total * 100, 1) if total else 0,
                "total_all": all_row["total"] or 0,
                "time_limit_all": all_row["tl"] or 0,
                "pct_all": round((all_row["tl"] or 0) / (all_row["total"] or 1) * 100, 1),
            }
    except Exception as e:
        return {"error": str(e), "pct_24h": 0, "pct_all": 0}


''' + MARKER

assert MARKER in code, f"Cannot find marker: {MARKER}"
code = code.replace(MARKER, NEW_ENDPOINTS, 1)
print("[OK] Added /api/brain/dynamic-exit-performance endpoint")
print("[OK] Added /api/perps/time-limit-pct endpoint")

with open(TARGET, "w") as f:
    f.write(code)
print(f"[OK] Wrote {TARGET} ({len(code)} bytes)")

# Verify compilation
import subprocess
result = subprocess.run(
    ["python3", "-c", f"import py_compile; py_compile.compile('{TARGET}', doraise=True)"],
    capture_output=True, text=True
)
if result.returncode == 0:
    print("[OK] main.py compiles successfully")
else:
    print(f"[ERROR] Compilation failed:\n{result.stderr}")
    import sys
    sys.exit(1)
