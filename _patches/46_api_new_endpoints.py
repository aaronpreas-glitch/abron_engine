#!/usr/bin/env python3
"""Patch 46: 5 new API endpoints + update dynamic_reasons in main.py.

New endpoints:
1. GET /api/brain/dynamic-exit-pnl-lift — Compare avg PnL of dynamic vs TIME_LIMIT
2. GET /api/brain/good-call-trend — Daily good_call rate trend
3. GET /api/brain/best-worst-exits — Top 3 best/worst dynamic exit trades
4. GET /api/brain/avg-pnl-by-exit-type — Avg PnL per exit_reason
5. GET /api/brain/exit-tuner-log — Recent auto-tune analysis results

Also updates dynamic_reasons string to include PROFIT_LOCK, SENTIMENT_TRAIL.
"""
import pathlib

FILE = pathlib.Path("/root/memecoin_engine/dashboard/backend/main.py")
content = FILE.read_text()
changes = 0

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Update dynamic_reasons to include PROFIT_LOCK, SENTIMENT_TRAIL
# ═══════════════════════════════════════════════════════════════════════════════
old = '''            dynamic_reasons = (
                "'DYNAMIC_TRAIL','ML_PROB_DROP','ML_EARLY_EXIT',"
                "'TRAILING_ATR_EXTEND','TRAILING_ATR_WINNER','PARTIAL_PROFIT'"
            )'''
new = '''            dynamic_reasons = (
                "'DYNAMIC_TRAIL','ML_PROB_DROP','ML_EARLY_EXIT',"
                "'TRAILING_ATR_EXTEND','TRAILING_ATR_WINNER','PARTIAL_PROFIT',"
                "'PROFIT_LOCK','SENTIMENT_TRAIL'"
            )'''
assert old in content, "FAIL [1/2]: dynamic_reasons not found"
content = content.replace(old, new, 1)
changes += 1
print("[1/2] Updated dynamic_reasons to include PROFIT_LOCK, SENTIMENT_TRAIL")

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Insert 5 new endpoints before /api/journal/learnings
# ═══════════════════════════════════════════════════════════════════════════════
marker = '''@app.get("/api/journal/learnings")'''
assert marker in content, "FAIL [2/2]: journal/learnings marker not found"

new_endpoints = '''
# ── Dynamic Exit: PNL Lift vs TIME_LIMIT ──
@app.get("/api/brain/dynamic-exit-pnl-lift")
async def brain_dynamic_exit_pnl_lift(_: str = Depends(get_current_user)):
    """Compare avg PnL of dynamic exits vs TIME_LIMIT exits."""
    try:
        _ensure_engine_path()
        import sqlite3
        db = str(pathlib.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            dyn_reasons = (
                "'DYNAMIC_TRAIL','ML_PROB_DROP','ML_EARLY_EXIT',"
                "'TRAILING_ATR_EXTEND','TRAILING_ATR_WINNER','PARTIAL_PROFIT',"
                "'PROFIT_LOCK','SENTIMENT_TRAIL'"
            )
            # Dynamic exits
            dyn_row = conn.execute(f"""
                SELECT COUNT(*) as n,
                       AVG(pnl_pct) as avg_pnl,
                       SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins
                FROM perp_positions
                WHERE status='CLOSED' AND exit_reason IN ({dyn_reasons})
            """).fetchone()
            # TIME_LIMIT exits
            tl_row = conn.execute("""
                SELECT COUNT(*) as n,
                       AVG(pnl_pct) as avg_pnl,
                       SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins
                FROM perp_positions
                WHERE status='CLOSED' AND exit_reason='TIME_LIMIT'
            """).fetchone()
            dyn_avg = round(float(dyn_row["avg_pnl"] or 0), 3)
            tl_avg = round(float(tl_row["avg_pnl"] or 0), 3)
            dyn_n = int(dyn_row["n"] or 0)
            tl_n = int(tl_row["n"] or 0)
            dyn_wr = round(int(dyn_row["wins"] or 0) / dyn_n * 100, 1) if dyn_n > 0 else 0
            tl_wr = round(int(tl_row["wins"] or 0) / tl_n * 100, 1) if tl_n > 0 else 0
            pnl_lift = round(dyn_avg - tl_avg, 3)
            return {
                "dynamic": {"n": dyn_n, "avg_pnl": dyn_avg, "wr": dyn_wr},
                "time_limit": {"n": tl_n, "avg_pnl": tl_avg, "wr": tl_wr},
                "pnl_lift": pnl_lift,
                "lift_positive": pnl_lift > 0
            }
    except Exception as e:
        return {"error": str(e), "dynamic": {}, "time_limit": {}, "pnl_lift": 0, "lift_positive": False}


# ── Dynamic Exit: Good Call Trend (daily) ──
@app.get("/api/brain/good-call-trend")
async def brain_good_call_trend(_: str = Depends(get_current_user)):
    """Daily good_call rate from dynamic_exit_log."""
    try:
        _ensure_engine_path()
        import sqlite3
        db = str(pathlib.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT DATE(ts_utc) as day,
                       COUNT(*) as total,
                       SUM(CASE WHEN outcome='good_call' THEN 1 ELSE 0 END) as good,
                       SUM(CASE WHEN outcome='bad_call' THEN 1 ELSE 0 END) as bad
                FROM dynamic_exit_log
                WHERE outcome IS NOT NULL
                GROUP BY DATE(ts_utc)
                ORDER BY day
            """).fetchall()
            return {"trend": [dict(r) for r in rows]}
    except Exception as e:
        return {"error": str(e), "trend": []}


# ── Dynamic Exit: Best/Worst Exits ──
@app.get("/api/brain/best-worst-exits")
async def brain_best_worst_exits(_: str = Depends(get_current_user)):
    """Top 3 best and worst dynamic exit trades by pnl_pct."""
    try:
        _ensure_engine_path()
        import sqlite3
        db = str(pathlib.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        dyn_reasons = (
            "'DYNAMIC_TRAIL','ML_PROB_DROP','ML_EARLY_EXIT',"
            "'TRAILING_ATR_EXTEND','TRAILING_ATR_WINNER','PARTIAL_PROFIT',"
            "'PROFIT_LOCK','SENTIMENT_TRAIL'"
        )
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            best = conn.execute(f"""
                SELECT id, symbol, side, entry_price, close_price, pnl_pct, exit_reason,
                       closed_at
                FROM perp_positions
                WHERE status='CLOSED' AND exit_reason IN ({dyn_reasons})
                ORDER BY pnl_pct DESC LIMIT 3
            """).fetchall()
            worst = conn.execute(f"""
                SELECT id, symbol, side, entry_price, close_price, pnl_pct, exit_reason,
                       closed_at
                FROM perp_positions
                WHERE status='CLOSED' AND exit_reason IN ({dyn_reasons})
                ORDER BY pnl_pct ASC LIMIT 3
            """).fetchall()
            return {
                "best": [dict(r) for r in best],
                "worst": [dict(r) for r in worst]
            }
    except Exception as e:
        return {"error": str(e), "best": [], "worst": []}


# ── Dynamic Exit: Avg PNL by Exit Type ──
@app.get("/api/brain/avg-pnl-by-exit-type")
async def brain_avg_pnl_by_exit_type(_: str = Depends(get_current_user)):
    """Average PnL per exit_reason for all closed perp positions."""
    try:
        _ensure_engine_path()
        import sqlite3
        db = str(pathlib.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT exit_reason,
                       COUNT(*) as n,
                       ROUND(AVG(pnl_pct), 3) as avg_pnl,
                       SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins
                FROM perp_positions
                WHERE status='CLOSED' AND exit_reason IS NOT NULL
                GROUP BY exit_reason
                ORDER BY n DESC
            """).fetchall()
            return {"by_type": [dict(r) for r in rows]}
    except Exception as e:
        return {"error": str(e), "by_type": []}


# ── Dynamic Exit: Auto-Tuner Log ──
@app.get("/api/brain/exit-tuner-log")
async def brain_exit_tuner_log(_: str = Depends(get_current_user)):
    """Recent auto-tune analysis results from dynamic_exit_tuner_log."""
    try:
        _ensure_engine_path()
        import sqlite3, json
        db = str(pathlib.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            # Check if table exists
            table_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='dynamic_exit_tuner_log'"
            ).fetchone()
            if not table_exists:
                return {"entries": []}
            rows = conn.execute("""
                SELECT ts_utc, total_closed, analysis, adjustments
                FROM dynamic_exit_tuner_log
                ORDER BY ts_utc DESC LIMIT 10
            """).fetchall()
            entries = []
            for r in rows:
                entries.append({
                    "ts_utc": r["ts_utc"],
                    "total_closed": r["total_closed"],
                    "analysis": json.loads(r["analysis"]) if r["analysis"] else {},
                    "adjustments": json.loads(r["adjustments"]) if r["adjustments"] else []
                })
            return {"entries": entries}
    except Exception as e:
        return {"error": str(e), "entries": []}


'''

content = content.replace(marker, new_endpoints + marker, 1)
changes += 1
print("[2/2] Inserted 5 new API endpoints before /api/journal/learnings")

# ═══════════════════════════════════════════════════════════════════════════════
# Write the file
# ═══════════════════════════════════════════════════════════════════════════════
assert changes == 2, f"Expected 2 changes, got {changes}"
FILE.write_text(content)

# Verify it compiles
import py_compile
py_compile.compile(str(FILE), doraise=True)
print(f"\n✅ Patch 46 applied successfully — all {changes} changes verified, file compiles OK")
