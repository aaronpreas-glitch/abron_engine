#!/usr/bin/env python3
"""Patch 49: Real-Time Performance Monitoring & Alerts + Bull Market Readiness Score.

Changes to dashboard/backend/main.py:
1. Add _run_performance_alert_check() + _performance_alert_loop() before lifespan
2. Register _performance_alert_loop task in lifespan + cleanup
3. Add 4 new endpoints:
   - GET  /api/alerts/performance      (list + unread count)
   - POST /api/alerts/performance/read  (mark all read)
   - GET  /api/brain/bull-readiness    (readiness score + components)
"""
import pathlib

FILE = pathlib.Path("/root/memecoin_engine/dashboard/backend/main.py")
content = FILE.read_text()
changes = 0

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Insert alert loop functions before async def lifespan(app: FastAPI):
# ═══════════════════════════════════════════════════════════════════════════════
ALERT_FUNCTIONS = '''
# ── Performance Monitoring & Alert Loop ──────────────────────────────────────

def _run_performance_alert_check():
    """
    Synchronous check — runs every 5 min via run_in_executor.
    Inserts rows to performance_alerts_log + broadcasts via WebSocket.
    6-hour dedup: each alert_type only fires once per 6h.
    Optional: pushes to Slack/email if env vars set.
    """
    import sqlite3, json as _json, os as _os
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    import pathlib as _pl

    _db = str(_pl.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")

    def _conn():
        c = sqlite3.connect(_db)
        c.row_factory = sqlite3.Row
        return c

    # Ensure table exists
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS performance_alerts_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                alert_type  TEXT,
                severity    TEXT,
                value       REAL,
                threshold   REAL,
                message     TEXT,
                metadata    TEXT,
                read        INTEGER DEFAULT 0
            )
        """)
        c.commit()

    def _last_alert_age_h(alert_type: str) -> float:
        """Return hours since last alert of this type, or 9999 if none."""
        with _conn() as c:
            row = c.execute(
                "SELECT MAX(ts_utc) FROM performance_alerts_log WHERE alert_type = ?",
                (alert_type,)
            ).fetchone()
            last = row[0] if row else None
        if not last:
            return 9999.0
        try:
            last_dt = _dt.fromisoformat(last.replace("Z", "+00:00"))
            return (_dt.now(_tz.utc) - last_dt).total_seconds() / 3600.0
        except Exception:
            return 9999.0

    def _fire_alert(alert_type, severity, value, threshold, message, metadata=None):
        """Insert alert, broadcast via WS, and optionally push Slack/email."""
        if _last_alert_age_h(alert_type) < 6.0:
            return  # dedup: same type fired within 6h
        meta_str = _json.dumps(metadata or {})
        with _conn() as c:
            c.execute("""
                INSERT INTO performance_alerts_log
                    (alert_type, severity, value, threshold, message, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (alert_type, severity, round(float(value), 3),
                  round(float(threshold), 3), message, meta_str))
            c.commit()
        log.info("[PERF-ALERT] %s [%s] %s", severity, alert_type, message)

        # WebSocket broadcast (fire-and-forget)
        try:
            import asyncio as _asyncio
            from dashboard.backend.ws_manager import manager as _ws_manager  # type: ignore
            payload = {
                "type": "performance_alert",
                "data": {
                    "alert_type": alert_type, "severity": severity,
                    "value": value, "message": message,
                    "ts": _dt.now(_tz.utc).isoformat() + "Z",
                }
            }
            try:
                loop = _asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                loop.create_task(_ws_manager.broadcast(payload))
        except Exception as _we:
            log.debug("alert broadcast error: %s", _we)

        # Optional: Slack push
        slack_url = _os.environ.get("ALERT_SLACK_WEBHOOK", "")
        if slack_url:
            try:
                import requests as _req
                emoji = "🔴" if severity == "CRITICAL" else "🟡" if severity == "WARNING" else "🔵"
                _req.post(slack_url, json={"text": f"{emoji} [{severity}] {message}"}, timeout=5)
            except Exception as _se:
                log.debug("Slack push error: %s", _se)

        # Optional: Email push (only CRITICAL + WARNING)
        email_to = _os.environ.get("ALERT_EMAIL", "")
        smtp_host = _os.environ.get("SMTP_HOST", "")
        smtp_user = _os.environ.get("SMTP_USER", "")
        smtp_pass = _os.environ.get("SMTP_PASS", "")
        if email_to and smtp_host and smtp_user and smtp_pass and severity in ("CRITICAL", "WARNING"):
            try:
                import smtplib
                from email.mime.text import MIMEText
                smtp_port = int(_os.environ.get("SMTP_PORT", "465"))
                msg = MIMEText(f"[{severity}] {alert_type}\\n\\n{message}")
                msg["Subject"] = f"[Memecoin Engine] {severity}: {alert_type}"
                msg["From"] = smtp_user
                msg["To"] = email_to
                with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_user, [email_to], msg.as_string())
                log.info("[PERF-ALERT] Email sent to %s", email_to)
            except Exception as _ee:
                log.debug("Email push error: %s", _ee)

    # ── Check 1: Trades/day avg > 3 ────────────────────────────────────────
    try:
        from datetime import datetime as _dt2, timezone as _tz2, timedelta as _td2
        cutoff_7d = (_dt2.now(_tz2.utc) - _td2(days=7)).isoformat()
        with _conn() as c:
            row = c.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status='CLOSED' AND closed_ts_utc > ?",
                (cutoff_7d,)
            ).fetchone()
        tpd = round((row[0] or 0) / 7.0, 2)
        if tpd > 3:
            _fire_alert("TRADES_OVER_CAP", "WARNING", tpd, 3.0,
                       f"Avg {tpd:.1f} trades/day > 3 goal — selectivity gate may need tuning")
    except Exception as _e:
        log.debug("alert check 1 error: %s", _e)

    # ── Check 2: TIME_LIMIT % > 75% in last 24h ────────────────────────────
    try:
        cutoff_24h = (_dt.now(_tz.utc) - _td(hours=24)).isoformat()
        with _conn() as c:
            total_row = c.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status='CLOSED' AND closed_ts_utc >= ?",
                (cutoff_24h,)
            ).fetchone()
            tl_row = c.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status='CLOSED' AND closed_ts_utc >= ? AND exit_reason='TIME_LIMIT'",
                (cutoff_24h,)
            ).fetchone()
        total_24h = int(total_row[0] or 0)
        tl_24h = int(tl_row[0] or 0)
        if total_24h >= 3:
            tl_pct = round(tl_24h / total_24h * 100, 1)
            if tl_pct > 75:
                _fire_alert("TIME_LIMIT_HIGH", "WARNING", tl_pct, 75.0,
                           f"TIME_LIMIT exits at {tl_pct}% (last 24h) — dynamic exit rarely triggering")
    except Exception as _e:
        log.debug("alert check 2 error: %s", _e)

    # ── Check 3: >8 skipped signals with avg pred_ret > 1.5% ──────────────
    try:
        today_start = _dt.now(_tz.utc).strftime("%Y-%m-%d") + "T00:00:00"
        with _conn() as c:
            try:
                sk_rows = c.execute("""
                    SELECT COUNT(*) as n, AVG(pred_ret) as avg_ret
                    FROM skipped_signals_log
                    WHERE ts_utc >= ? AND pred_ret > 1.5
                """, (today_start,)).fetchone()
                n_high = int(sk_rows[0] or 0)
                avg_ret = float(sk_rows[1] or 0)
                if n_high > 8:
                    _fire_alert("LOOSE_GATES_SUGGESTION", "INFO", n_high, 8.0,
                               f"{n_high} high-quality signals skipped today (avg pred_ret={avg_ret:.1f}%) — consider loosening gates")
            except Exception:
                pass  # table may not exist yet
    except Exception as _e:
        log.debug("alert check 3 error: %s", _e)

    # ── Check 4: DAILY_CAP hit ─────────────────────────────────────────────
    try:
        with _conn() as c:
            try:
                cap_row = c.execute("""
                    SELECT COUNT(*) FROM skipped_signals_log
                    WHERE ts_utc >= ? AND skip_reason = 'DAILY_CAP'
                """, (today_start,)).fetchone()
                n_cap = int(cap_row[0] or 0)
                if n_cap > 0:
                    _fire_alert("DAILY_CAP_HIT", "CRITICAL", n_cap, 1.0,
                               f"Daily trade cap reached — {n_cap} signal(s) blocked today")
            except Exception:
                pass
    except Exception as _e:
        log.debug("alert check 4 error: %s", _e)

    # ── Check 5: Discipline score < 50 ────────────────────────────────────
    try:
        cutoff_7d2 = (_dt.now(_tz.utc) - _td(days=7)).isoformat()
        with _conn() as c:
            tpd_rows = c.execute("""
                SELECT DATE(closed_ts_utc) as day, COUNT(*) as n
                FROM perp_positions WHERE status='CLOSED' AND closed_ts_utc >= ?
                GROUP BY DATE(closed_ts_utc)
            """, (cutoff_7d2,)).fetchall()
            avg_tpd2 = sum(r[1] for r in tpd_rows) / max(len(tpd_rows), 1)
            avg_pnl_row = c.execute("""
                SELECT AVG(pnl_pct) FROM (
                    SELECT pnl_pct FROM perp_positions
                    WHERE status='CLOSED' AND pnl_pct IS NOT NULL
                    ORDER BY closed_ts_utc DESC LIMIT 20
                )
            """).fetchone()
            avg_pnl2 = round(float(avg_pnl_row[0] or 0), 3)
            gcr2 = 0
            try:
                gcr_row2 = c.execute("""
                    SELECT COUNT(*) as t, SUM(CASE WHEN outcome='good_call' THEN 1 ELSE 0 END) as g
                    FROM dynamic_exit_log WHERE outcome IS NOT NULL
                """).fetchone()
                gcr2 = round(int(gcr_row2[1] or 0) / max(int(gcr_row2[0] or 1), 1) * 100, 1)
            except Exception:
                pass
        disc_pts = 40 if avg_tpd2 <= 3 else round(40 * max(0, (5 - avg_tpd2) / 2.0))
        qual_pts = 30 if avg_pnl2 >= 0.8 else round(30 * max(0, avg_pnl2 / 0.8))
        acc_pts  = 30 if gcr2 >= 65 else round(30 * max(0, (gcr2 - 40) / 25.0))
        disc_score = disc_pts + qual_pts + acc_pts
        if disc_score < 50:
            _fire_alert("DISCIPLINE_DROP", "WARNING", disc_score, 50.0,
                       f"Discipline score {disc_score}/100 below threshold — check trades/day + PNL quality")
    except Exception as _e:
        log.debug("alert check 5 error: %s", _e)


async def _performance_alert_loop():
    """Background task — runs performance alert checks every 5 minutes."""
    await asyncio.sleep(30)  # brief delay to let server fully start
    while True:
        try:
            await asyncio.get_event_loop().run_in_executor(None, _run_performance_alert_check)
        except Exception as _e:
            log.debug("_performance_alert_loop error: %s", _e)
        await asyncio.sleep(300)  # 5 minutes


'''

old_lifespan = '''async def lifespan(app: FastAPI):'''
assert old_lifespan in content, "FAIL [1/3]: lifespan not found"
content = content.replace(old_lifespan, ALERT_FUNCTIONS + old_lifespan, 1)
changes += 1
print("[1/3] Inserted _run_performance_alert_check + _performance_alert_loop")

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Add task_perf_alerts to lifespan + cleanup
# ═══════════════════════════════════════════════════════════════════════════════
old_lifespan_body = '''    task_weekly_report = asyncio.create_task(_weekly_report_loop())
    log.info("Dashboard started — perp swing + scalp + mid + spot paper bots (3-track learning) running.")
    yield
    all_tasks = (task_poller, task_tracker, task_perp_mon, task_perp_scan,
                 task_scalp_mon, task_scalp_scan, task_mid_mon, task_mid_scan,
                 task_spot_mon, task_spot_scan, task_wl_momentum, task_post_exit, task_weekly_report)'''
new_lifespan_body = '''    task_weekly_report = asyncio.create_task(_weekly_report_loop())
    task_perf_alerts = asyncio.create_task(_performance_alert_loop())
    log.info("Dashboard started — perp swing + scalp + mid + spot paper bots (3-track learning) + performance alerts running.")
    yield
    all_tasks = (task_poller, task_tracker, task_perp_mon, task_perp_scan,
                 task_scalp_mon, task_scalp_scan, task_mid_mon, task_mid_scan,
                 task_spot_mon, task_spot_scan, task_wl_momentum, task_post_exit,
                 task_weekly_report, task_perf_alerts)'''
assert old_lifespan_body in content, "FAIL [2/3]: lifespan body anchor not found"
content = content.replace(old_lifespan_body, new_lifespan_body, 1)
changes += 1
print("[2/3] Registered _performance_alert_loop in lifespan")

# ═══════════════════════════════════════════════════════════════════════════════
# 3. Insert 4 new endpoints before /api/journal/learnings
# ═══════════════════════════════════════════════════════════════════════════════
marker = '''@app.get("/api/journal/learnings")'''
assert marker in content, "FAIL [3/3]: journal/learnings marker not found"

NEW_ENDPOINTS = '''
# ── Performance Alerts: List ─────────────────────────────────────────────────
@app.get("/api/alerts/performance")
async def alerts_performance(_: str = Depends(get_current_user)):
    """Return last 50 performance alerts with unread count."""
    try:
        import sqlite3, pathlib as _pl
        db = str(_pl.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE IF NOT EXISTS performance_alerts_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    alert_type TEXT, severity TEXT, value REAL, threshold REAL,
                    message TEXT, metadata TEXT, read INTEGER DEFAULT 0
                )
            """)
            rows = conn.execute(
                "SELECT id, ts_utc, alert_type, severity, value, threshold, message, read "
                "FROM performance_alerts_log ORDER BY ts_utc DESC LIMIT 50"
            ).fetchall()
            unread_row = conn.execute(
                "SELECT COUNT(*) FROM performance_alerts_log WHERE read = 0"
            ).fetchone()
        return {
            "alerts": [dict(r) for r in rows],
            "unread_count": int(unread_row[0] or 0),
            "total": len(rows),
        }
    except Exception as e:
        return {"error": str(e), "alerts": [], "unread_count": 0, "total": 0}


# ── Performance Alerts: Mark Read ────────────────────────────────────────────
@app.post("/api/alerts/performance/read")
async def alerts_performance_mark_read(_: str = Depends(get_current_user)):
    """Mark all performance alerts as read."""
    try:
        import sqlite3, pathlib as _pl
        db = str(_pl.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        with sqlite3.connect(db) as conn:
            result = conn.execute(
                "UPDATE performance_alerts_log SET read = 1 WHERE read = 0"
            )
            conn.commit()
        return {"marked_read": result.rowcount}
    except Exception as e:
        return {"error": str(e), "marked_read": 0}


# ── Bull Market Readiness Score ───────────────────────────────────────────────
@app.get("/api/brain/bull-readiness")
async def brain_bull_readiness(_: str = Depends(get_current_user)):
    """
    Composite Bull Market Readiness Score (0-100).
    Components:
      - Discipline score 30%  (from selectivity-performance logic)
      - ML good_call rate 20% (from dynamic_exit_log)
      - Avg PNL/trade 20%     (last 20 closed trades; 1% = full score)
      - Dynamic exit lift 20% (vs TIME_LIMIT; 0.5% lift = full score)
      - TIME_LIMIT % 10%      (24h; 40% TL = full, 70%+ TL = 0)
    """
    try:
        import sqlite3, pathlib as _pl
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        db = str(_pl.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            cutoff_7d = (_dt.now(_tz.utc) - _td(days=7)).isoformat()
            cutoff_24h = (_dt.now(_tz.utc) - _td(hours=24)).isoformat()

            # ── Discipline score ──
            tpd_rows = conn.execute("""
                SELECT DATE(closed_ts_utc) as day, COUNT(*) as n
                FROM perp_positions WHERE status='CLOSED' AND closed_ts_utc >= ?
                GROUP BY DATE(closed_ts_utc)
            """, (cutoff_7d,)).fetchall()
            avg_tpd = sum(r["n"] for r in tpd_rows) / max(len(tpd_rows), 1)

            avg_pnl_row = conn.execute("""
                SELECT AVG(pnl_pct) FROM (
                    SELECT pnl_pct FROM perp_positions
                    WHERE status='CLOSED' AND pnl_pct IS NOT NULL
                    ORDER BY closed_ts_utc DESC LIMIT 20
                )
            """).fetchone()
            avg_pnl = round(float(avg_pnl_row[0] or 0), 3)

            # Good call rate
            gcr = 0.0
            try:
                gcr_row = conn.execute("""
                    SELECT COUNT(*) as t, SUM(CASE WHEN outcome='good_call' THEN 1 ELSE 0 END) as g
                    FROM dynamic_exit_log WHERE outcome IS NOT NULL
                """).fetchone()
                gcr = round(int(gcr_row["g"] or 0) / max(int(gcr_row["t"] or 1), 1) * 100, 1)
            except Exception:
                pass

            # PNL lift vs TIME_LIMIT
            dyn_reasons = (
                "'DYNAMIC_TRAIL','ML_PROB_DROP','ML_EARLY_EXIT',"
                "'TRAILING_ATR_EXTEND','TRAILING_ATR_WINNER','PARTIAL_PROFIT',"
                "'PROFIT_LOCK','SENTIMENT_TRAIL'"
            )
            dyn_row = conn.execute(f"""
                SELECT AVG(pnl_pct) FROM perp_positions
                WHERE status='CLOSED' AND exit_reason IN ({dyn_reasons})
            """).fetchone()
            tl_row = conn.execute("""
                SELECT AVG(pnl_pct) FROM perp_positions
                WHERE status='CLOSED' AND exit_reason='TIME_LIMIT'
            """).fetchone()
            dyn_avg = float(dyn_row[0] or 0)
            tl_avg  = float(tl_row[0] or 0)
            pnl_lift = round(dyn_avg - tl_avg, 3)

            # TIME_LIMIT % (24h)
            total_24h = int(conn.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status='CLOSED' AND closed_ts_utc >= ?",
                (cutoff_24h,)
            ).fetchone()[0] or 0)
            tl_24h = int(conn.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status='CLOSED' AND closed_ts_utc >= ? AND exit_reason='TIME_LIMIT'",
                (cutoff_24h,)
            ).fetchone()[0] or 0)
            tl_pct = round(tl_24h / total_24h * 100, 1) if total_24h >= 3 else 50.0

        # ── Score components ──
        # Discipline (30 pts)
        disc_sub = 40 if avg_tpd <= 3 else round(40 * max(0, (5 - avg_tpd) / 2.0))
        qual_sub = 30 if avg_pnl >= 0.8 else round(30 * max(0, avg_pnl / 0.8))
        acc_sub  = 30 if gcr >= 65 else round(30 * max(0, (gcr - 40) / 25.0))
        discipline_score = disc_sub + qual_sub + acc_sub
        disc_pts = round(discipline_score / 100 * 30)

        # ML accuracy (20 pts)
        ml_pts = round(min(gcr, 100) / 100 * 20)

        # Avg PNL (20 pts) — 1% = full
        pnl_pts = round(min(1.0, max(0.0, avg_pnl / 1.0)) * 20)

        # Dynamic lift (20 pts) — 0.5% lift = full
        lift_pts = round(min(1.0, max(0.0, pnl_lift / 0.5)) * 20)

        # TIME_LIMIT % (10 pts) — 40% TL = full, 70%+ = 0
        tl_pts = round(max(0.0, min(1.0, (70 - tl_pct) / 30.0)) * 10)

        total_score = disc_pts + ml_pts + pnl_pts + lift_pts + tl_pts

        if total_score >= 80:
            label, color = "Ready for Live", "green"
        elif total_score >= 60:
            label, color = "Close to Ready", "amber"
        else:
            label, color = "Still Training", "red"

        return {
            "score": total_score,
            "label": label,
            "color": color,
            "go_live_ready": total_score >= 80,
            "components": {
                "discipline": {
                    "pts": disc_pts, "max": 30,
                    "value": discipline_score,
                    "label": f"Discipline Score {discipline_score}/100",
                },
                "ml_accuracy": {
                    "pts": ml_pts, "max": 20,
                    "value": gcr,
                    "label": f"Good Call Rate {gcr:.1f}%",
                },
                "avg_pnl": {
                    "pts": pnl_pts, "max": 20,
                    "value": avg_pnl,
                    "label": f"Avg PNL {avg_pnl:+.2f}%/trade",
                },
                "dynamic_lift": {
                    "pts": lift_pts, "max": 20,
                    "value": pnl_lift,
                    "label": f"PNL Lift vs TL {pnl_lift:+.2f}%",
                },
                "time_limit_pct": {
                    "pts": tl_pts, "max": 10,
                    "value": tl_pct,
                    "label": f"TIME_LIMIT {tl_pct:.0f}% (24h)",
                },
            },
            "ts_utc": _dt.now(_tz.utc).isoformat() + "Z",
        }
    except Exception as e:
        return {"error": str(e), "score": 0, "label": "Still Training", "color": "red",
                "go_live_ready": False, "components": {}, "ts_utc": ""}


'''

content = content.replace(marker, NEW_ENDPOINTS + marker, 1)
changes += 1
print("[3/3] Inserted 3 new endpoints (alerts list, mark-read, bull-readiness)")

# ═══════════════════════════════════════════════════════════════════════════════
# Write
# ═══════════════════════════════════════════════════════════════════════════════
assert changes == 3, f"Expected 3 changes, got {changes}"
FILE.write_text(content)
print(f"\n✅ Patch 49 applied ({changes}/3 changes) — Monitoring & Alerts live")
print("   Background: _performance_alert_loop (every 5 min)")
print("   DB: performance_alerts_log table (auto-created)")
print("   Endpoints:")
print("     GET  /api/alerts/performance")
print("     POST /api/alerts/performance/read")
print("     GET  /api/brain/bull-readiness")
print("   Alerts: TRADES_OVER_CAP | TIME_LIMIT_HIGH | LOOSE_GATES_SUGGESTION | DAILY_CAP_HIT | DISCIPLINE_DROP")
print("   Optional push: ALERT_SLACK_WEBHOOK + ALERT_EMAIL/SMTP_* in .env")
