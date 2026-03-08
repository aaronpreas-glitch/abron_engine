#!/usr/bin/env python3
"""Patch 48: 4 new API endpoints for Selectivity / Discipline insights.

New endpoints:
1. GET /api/brain/skipped-signals      — last 24h skipped signals, top reasons, what-ifs
2. GET /api/brain/daily-trade-count    — today's trade count vs cap, 7-day trend
3. GET /api/brain/selectivity-performance — trades/day trend, avg PNL/trade, discipline score
4. GET /api/brain/discipline-score     — lightweight composite score endpoint
"""
import pathlib

FILE = pathlib.Path("/root/memecoin_engine/dashboard/backend/main.py")
content = FILE.read_text()
changes = 0

# ═══════════════════════════════════════════════════════════════════════════════
# Insert 4 new endpoints before /api/journal/learnings
# ═══════════════════════════════════════════════════════════════════════════════
marker = '''@app.get("/api/journal/learnings")'''
assert marker in content, "FAIL: journal/learnings marker not found"

NEW_ENDPOINTS = '''
# ── Selectivity: Skipped Signals ────────────────────────────────────────────
@app.get("/api/brain/skipped-signals")
async def brain_skipped_signals(_: str = Depends(get_current_user)):
    """Last 24h skipped signals: total, top reasons, recent list, safety flag."""
    try:
        import sqlite3, pathlib as _pl
        db = str(_pl.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            # Ensure table exists (may not yet if no signals have been skipped)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS skipped_signals_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%SZ\',\'now\')),
                    symbol TEXT, side TEXT, mode TEXT, skip_reason TEXT,
                    ml_wp REAL, pred_ret REAL, sent_boost REAL, regime TEXT, notes TEXT
                )
            """)
            since_24h = (
                __import__("datetime").datetime.utcnow()
                - __import__("datetime").timedelta(hours=24)
            ).isoformat()

            total_24h_row = conn.execute(
                "SELECT COUNT(*) FROM skipped_signals_log WHERE ts_utc >= ?", (since_24h,)
            ).fetchone()
            total_24h = int(total_24h_row[0] or 0)

            reason_rows = conn.execute("""
                SELECT skip_reason, COUNT(*) as n
                FROM skipped_signals_log WHERE ts_utc >= ?
                GROUP BY skip_reason ORDER BY n DESC LIMIT 6
            """, (since_24h,)).fetchall()
            top_reasons = [{"reason": r["skip_reason"], "n": r["n"]} for r in reason_rows]

            recent_rows = conn.execute("""
                SELECT ts_utc, symbol, side, mode, skip_reason, ml_wp, pred_ret, sent_boost
                FROM skipped_signals_log ORDER BY ts_utc DESC LIMIT 20
            """).fetchall()
            recent = [dict(r) for r in recent_rows]

        return {
            "total_24h": total_24h,
            "top_reasons": top_reasons,
            "recent": recent,
            "safety_raise_needed": total_24h > 10,
        }
    except Exception as e:
        return {"error": str(e), "total_24h": 0, "top_reasons": [], "recent": [], "safety_raise_needed": False}


# ── Selectivity: Daily Trade Count ─────────────────────────────────────────
@app.get("/api/brain/daily-trade-count")
async def brain_daily_trade_count(_: str = Depends(get_current_user)):
    """Today\'s trade count vs cap, 7-day trade frequency trend."""
    try:
        import sqlite3, pathlib as _pl
        from datetime import datetime as _dt, timezone as _tz
        db = str(_pl.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
            today_start = today + "T00:00:00"

            today_row = conn.execute("""
                SELECT COUNT(*) FROM perp_positions
                WHERE status=\'CLOSED\' AND DATE(closed_ts_utc) = ?
            """, (today,)).fetchone()
            today_count = int(today_row[0] or 0)

            # 7-day trade trend
            cutoff = (_dt.now(_tz.utc) - __import__("datetime").timedelta(days=7)).isoformat()
            trend_rows = conn.execute("""
                SELECT DATE(closed_ts_utc) as day, COUNT(*) as n
                FROM perp_positions WHERE status=\'CLOSED\' AND closed_ts_utc >= ?
                GROUP BY DATE(closed_ts_utc) ORDER BY day ASC
            """, (cutoff,)).fetchall()
            trades_7d = [{"day": r["day"], "n": r["n"]} for r in trend_rows]

        # Color logic: >= cap red, >= 3 amber, else green
        cap = 5  # default; mirrors _daily_trade_cap global in perp_executor
        if today_count >= cap:
            color = "red"
        elif today_count >= 3:
            color = "amber"
        else:
            color = "green"

        return {
            "today": today_count,
            "cap": cap,
            "cb_active": False,
            "color": color,
            "trades_7d": trades_7d,
        }
    except Exception as e:
        return {"error": str(e), "today": 0, "cap": 5, "cb_active": False, "color": "green", "trades_7d": []}


# ── Selectivity: Selectivity Performance ────────────────────────────────────
@app.get("/api/brain/selectivity-performance")
async def brain_selectivity_performance(_: str = Depends(get_current_user)):
    """Trades/day trend, avg PNL per trade, skipped %, discipline score."""
    try:
        import sqlite3, pathlib as _pl
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        db = str(_pl.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            cutoff_7d = (_dt.now(_tz.utc) - _td(days=7)).isoformat()
            cutoff_24h = (_dt.now(_tz.utc) - _td(hours=24)).isoformat()

            # Trades per day (last 7 days)
            tpd_rows = conn.execute("""
                SELECT DATE(closed_ts_utc) as day, COUNT(*) as n
                FROM perp_positions WHERE status=\'CLOSED\' AND closed_ts_utc >= ?
                GROUP BY DATE(closed_ts_utc) ORDER BY day ASC
            """, (cutoff_7d,)).fetchall()
            trades_per_day = [{"day": r["day"], "n": r["n"]} for r in tpd_rows]

            # Avg PNL per trade (last 20)
            avg_row = conn.execute("""
                SELECT AVG(pnl_pct) FROM (
                    SELECT pnl_pct FROM perp_positions
                    WHERE status=\'CLOSED\' AND pnl_pct IS NOT NULL
                    ORDER BY closed_ts_utc DESC LIMIT 20
                )
            """).fetchone()
            avg_pnl_per_trade = round(float(avg_row[0] or 0), 3)

            # Overall avg PNL
            oa_row = conn.execute("""
                SELECT AVG(pnl_pct) FROM perp_positions WHERE status=\'CLOSED\' AND pnl_pct IS NOT NULL
            """).fetchone()
            overall_avg_pnl = round(float(oa_row[0] or 0), 3)

            # Skipped signals 24h
            skipped_24h = 0
            signals_seen_24h = 0
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS skipped_signals_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts_utc TEXT DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%SZ\',\'now\')),
                        symbol TEXT, side TEXT, mode TEXT, skip_reason TEXT,
                        ml_wp REAL, pred_ret REAL, sent_boost REAL, regime TEXT, notes TEXT
                    )
                """)
                sk_row = conn.execute(
                    "SELECT COUNT(*) FROM skipped_signals_log WHERE ts_utc >= ?", (cutoff_24h,)
                ).fetchone()
                skipped_24h = int(sk_row[0] or 0)
                closed_24h_row = conn.execute(
                    "SELECT COUNT(*) FROM perp_positions WHERE status=\'CLOSED\' AND closed_ts_utc >= ?", (cutoff_24h,)
                ).fetchone()
                closed_24h = int(closed_24h_row[0] or 0)
                signals_seen_24h = skipped_24h + closed_24h
            except Exception:
                pass
            skipped_pct_24h = round(skipped_24h / signals_seen_24h * 100) if signals_seen_24h > 0 else 0

            # Good call rate (from dynamic_exit_log)
            gcr = 0
            try:
                gcr_row = conn.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN outcome=\'good_call\' THEN 1 ELSE 0 END) as good
                    FROM dynamic_exit_log WHERE outcome IS NOT NULL
                """).fetchone()
                gcr = round(int(gcr_row["good"] or 0) / max(int(gcr_row["total"] or 1), 1) * 100, 1)
            except Exception:
                pass

        # Discipline Score (0-100)
        avg_tpd = sum(r["n"] for r in trades_per_day) / max(len(trades_per_day), 1)
        disc_pts = 40 * max(0, min(1, (3 - avg_tpd) / 3)) if avg_tpd < 3 else 0
        if avg_tpd <= 3:
            disc_pts = 40
        elif avg_tpd <= 5:
            disc_pts = 40 * (5 - avg_tpd) / 2.0
        qual_pts = 30 * max(0, min(1, (avg_pnl_per_trade - 0.0) / 0.8)) if avg_pnl_per_trade < 0.8 else 30
        acc_pts = 30 * max(0, min(1, (gcr - 40) / 25.0)) if gcr < 65 else 30
        discipline_score = round(disc_pts + qual_pts + acc_pts)

        return {
            "trades_per_day": trades_per_day,
            "avg_pnl_per_trade": avg_pnl_per_trade,
            "overall_avg_pnl": overall_avg_pnl,
            "skipped_pct_24h": skipped_pct_24h,
            "skipped_24h": skipped_24h,
            "good_call_rate": gcr,
            "discipline_score": discipline_score,
        }
    except Exception as e:
        return {"error": str(e), "trades_per_day": [], "avg_pnl_per_trade": 0,
                "overall_avg_pnl": 0, "skipped_pct_24h": 0, "discipline_score": 0}


# ── Selectivity: Discipline Score (lightweight polling) ─────────────────────
@app.get("/api/brain/discipline-score")
async def brain_discipline_score(_: str = Depends(get_current_user)):
    """Lightweight discipline score with breakdown."""
    try:
        import sqlite3, pathlib as _pl
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        db = str(_pl.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            cutoff_7d = (_dt.now(_tz.utc) - _td(days=7)).isoformat()

            tpd_rows = conn.execute("""
                SELECT DATE(closed_ts_utc) as day, COUNT(*) as n
                FROM perp_positions WHERE status=\'CLOSED\' AND closed_ts_utc >= ?
                GROUP BY DATE(closed_ts_utc)
            """, (cutoff_7d,)).fetchall()
            avg_tpd = sum(r["n"] for r in tpd_rows) / max(len(tpd_rows), 1)

            avg_row = conn.execute("""
                SELECT AVG(pnl_pct) FROM (
                    SELECT pnl_pct FROM perp_positions WHERE status=\'CLOSED\' AND pnl_pct IS NOT NULL
                    ORDER BY closed_ts_utc DESC LIMIT 20
                )
            """).fetchone()
            avg_pnl = round(float(avg_row[0] or 0), 3)

            gcr = 0
            try:
                gcr_row = conn.execute("""
                    SELECT COUNT(*) as total, SUM(CASE WHEN outcome=\'good_call\' THEN 1 ELSE 0 END) as good
                    FROM dynamic_exit_log WHERE outcome IS NOT NULL
                """).fetchone()
                gcr = round(int(gcr_row["good"] or 0) / max(int(gcr_row["total"] or 1), 1) * 100, 1)
            except Exception:
                pass

        disc_pts = 40 if avg_tpd <= 3 else round(40 * max(0, (5 - avg_tpd) / 2.0))
        qual_pts = 30 if avg_pnl >= 0.8 else round(30 * max(0, avg_pnl / 0.8))
        acc_pts  = 30 if gcr >= 65 else round(30 * max(0, (gcr - 40) / 25.0))
        score = disc_pts + qual_pts + acc_pts

        return {
            "score": score,
            "breakdown": {"discipline": disc_pts, "quality": qual_pts, "accuracy": acc_pts},
            "metrics": {"avg_tpd": round(avg_tpd, 2), "avg_pnl": avg_pnl, "good_call_rate": gcr},
        }
    except Exception as e:
        return {"error": str(e), "score": 0, "breakdown": {}, "metrics": {}}


'''

content = content.replace(marker, NEW_ENDPOINTS + marker, 1)
changes += 1
print("[1/1] Inserted 4 selectivity API endpoints")

assert changes == 1, f"Expected 1 change block, got {changes}"
FILE.write_text(content)
print("\n✅ Patch 48 applied — Selectivity API endpoints live")
print("   GET /api/brain/skipped-signals")
print("   GET /api/brain/daily-trade-count")
print("   GET /api/brain/selectivity-performance")
print("   GET /api/brain/discipline-score")
