#!/usr/bin/env python3
"""Patch 50: Enhanced monitoring — 8-rule alert check + 5-component Bull Readiness Score.

Changes to dashboard/backend/main.py:
1. Replace _run_performance_alert_check() with 8 checks:
   CRITICAL: TIME_LIMIT > 80%, good_call_rate < 50% (last 10), daily_drawdown < -2%, DAILY_CAP hit
   WARNING:  trades/day > 3 (7d avg), signal starvation < 2% acceptance (6h), discipline_score < 50
   INFO:     high-quality signals skipped > 10 with pred_ret > 1.5%
2. Replace brain_bull_readiness endpoint with 5-component score (20pts each):
   Discipline | ML Quality | Exit Quality | Profitability | Stability
"""
import pathlib

FILE = pathlib.Path("/root/memecoin_engine/dashboard/backend/main.py")
content = FILE.read_text()
changes = 0

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Replace _run_performance_alert_check (up to async def _performance_alert_loop)
# ═══════════════════════════════════════════════════════════════════════════════
OLD_CHECK_START = 'def _run_performance_alert_check():'
OLD_CHECK_END   = '\nasync def _performance_alert_loop():'

idx_start = content.find(OLD_CHECK_START)
idx_end   = content.find(OLD_CHECK_END, idx_start)
assert idx_start != -1, "FAIL [1/2]: _run_performance_alert_check not found"
assert idx_end   != -1, "FAIL [1/2]: _performance_alert_loop boundary not found"

OLD_CHECK = content[idx_start:idx_end]

NEW_CHECK = '''def _run_performance_alert_check():
    """
    Synchronous check — runs every 5 min via run_in_executor.
    8 alert conditions; 6-hour dedup per alert_type.
    Broadcasts via WS; optionally pushes to Slack/Email.
    """
    import sqlite3, json as _json, os as _os
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    import pathlib as _pl

    _db = str(_pl.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")

    def _conn():
        c = sqlite3.connect(_db)
        c.row_factory = sqlite3.Row
        return c

    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS performance_alerts_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc     TEXT DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%SZ\',\'now\')),
                alert_type TEXT, severity TEXT, value REAL, threshold REAL,
                message    TEXT, metadata TEXT, read INTEGER DEFAULT 0
            )
        """)
        c.commit()

    def _last_alert_age_h(alert_type):
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
        if _last_alert_age_h(alert_type) < 6.0:
            return
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
        slack_url = _os.environ.get("ALERT_SLACK_WEBHOOK", "")
        if slack_url:
            try:
                import requests as _req
                emoji = "\\U0001f534" if severity == "CRITICAL" else "\\U0001f7e1" if severity == "WARNING" else "\\U0001f535"
                _req.post(slack_url, json={"text": f"{emoji} [{severity}] {message}"}, timeout=5)
            except Exception:
                pass
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
            except Exception:
                pass

    cutoff_24h  = (_dt.now(_tz.utc) - _td(hours=24)).isoformat()
    cutoff_6h   = (_dt.now(_tz.utc) - _td(hours=6)).isoformat()
    cutoff_7d   = (_dt.now(_tz.utc) - _td(days=7)).isoformat()
    today_str   = _dt.now(_tz.utc).strftime("%Y-%m-%d")
    today_start = today_str + "T00:00:00"

    # ── 1. TIME_LIMIT % > 80% (24h) → CRITICAL; > 75% → WARNING ─────────
    try:
        with _conn() as c:
            total_24h = int(c.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status=\'CLOSED\' AND closed_ts_utc >= ?",
                (cutoff_24h,)
            ).fetchone()[0] or 0)
            tl_24h = int(c.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status=\'CLOSED\' AND closed_ts_utc >= ? AND exit_reason=\'TIME_LIMIT\'",
                (cutoff_24h,)
            ).fetchone()[0] or 0)
        if total_24h >= 5:
            tl_pct = round(tl_24h / total_24h * 100, 1)
            if tl_pct > 80:
                _fire_alert("TIME_LIMIT_CRITICAL", "CRITICAL", tl_pct, 80.0,
                            f"TIME_LIMIT exits at {tl_pct:.0f}% (24h) — dynamic exit critically under-performing")
            elif tl_pct > 75:
                _fire_alert("TIME_LIMIT_HIGH", "WARNING", tl_pct, 75.0,
                            f"TIME_LIMIT exits at {tl_pct:.0f}% (24h) — dynamic exit rarely triggering")
    except Exception as _e:
        log.debug("alert check 1 error: %s", _e)

    # ── 2. Good call rate < 50% (last 10 outcomes) → CRITICAL ─────────────
    try:
        with _conn() as c:
            gcr_row = c.execute("""
                SELECT COUNT(*) as t,
                       SUM(CASE WHEN outcome=\'good_call\' THEN 1 ELSE 0 END) as g
                FROM (SELECT outcome FROM dynamic_exit_log
                      WHERE outcome IS NOT NULL ORDER BY ts_utc DESC LIMIT 10)
            """).fetchone()
        n_out  = int(gcr_row["t"] or 0)
        n_good = int(gcr_row["g"] or 0)
        if n_out >= 5:
            gcr10 = round(n_good / n_out * 100, 1)
            if gcr10 < 50:
                _fire_alert("LOW_GOOD_CALL_RATE", "CRITICAL", gcr10, 50.0,
                            f"Dynamic exit good_call rate {gcr10:.0f}% (last {n_out}) — exits may be premature")
    except Exception as _e:
        log.debug("alert check 2 error: %s", _e)

    # ── 3. Daily drawdown < -2% → CRITICAL ────────────────────────────────
    try:
        with _conn() as c:
            dd_row = c.execute("""
                SELECT SUM(pnl_pct) FROM perp_positions
                WHERE status=\'CLOSED\' AND DATE(closed_ts_utc) = ? AND pnl_pct IS NOT NULL
            """, (today_str,)).fetchone()
        daily_dd = round(float(dd_row[0] or 0), 2)
        if daily_dd < -2.0:
            _fire_alert("DAILY_DRAWDOWN", "CRITICAL", daily_dd, -2.0,
                        f"Daily P&L at {daily_dd:+.2f}% — drawdown threshold breached")
    except Exception as _e:
        log.debug("alert check 3 error: %s", _e)

    # ── 4. Daily cap hit → CRITICAL ───────────────────────────────────────
    try:
        with _conn() as c:
            try:
                cap_row = c.execute("""
                    SELECT COUNT(*) FROM skipped_signals_log
                    WHERE ts_utc >= ? AND skip_reason = \'DAILY_CAP\'
                """, (today_start,)).fetchone()
                n_cap = int(cap_row[0] or 0)
                if n_cap > 0:
                    _fire_alert("DAILY_CAP_HIT", "CRITICAL", n_cap, 1.0,
                                f"Daily trade cap reached — {n_cap} signal(s) blocked today")
            except Exception:
                pass
    except Exception as _e:
        log.debug("alert check 4 error: %s", _e)

    # ── 5. Trades/day > 3 (7d avg) → WARNING ─────────────────────────────
    try:
        with _conn() as c:
            cnt_row = c.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status=\'CLOSED\' AND closed_ts_utc > ?",
                (cutoff_7d,)
            ).fetchone()
        tpd = round((cnt_row[0] or 0) / 7.0, 2)
        if tpd > 3:
            _fire_alert("TRADES_OVER_CAP", "WARNING", tpd, 3.0,
                        f"Avg {tpd:.1f} trades/day (7d) > 3 goal — selectivity gate may need tuning")
    except Exception as _e:
        log.debug("alert check 5 error: %s", _e)

    # ── 6. Signal starvation: < 2% acceptance in last 6h → WARNING ────────
    try:
        with _conn() as c:
            accepted_6h = int(c.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE opened_ts_utc >= ?",
                (cutoff_6h,)
            ).fetchone()[0] or 0)
            skipped_6h = 0
            try:
                skipped_6h = int(c.execute(
                    "SELECT COUNT(*) FROM skipped_signals_log WHERE ts_utc >= ?",
                    (cutoff_6h,)
                ).fetchone()[0] or 0)
            except Exception:
                pass
        total_6h = accepted_6h + skipped_6h
        if total_6h >= 10:
            acc_rate = round(accepted_6h / total_6h * 100, 1)
            if acc_rate < 2.0:
                _fire_alert("SIGNAL_STARVATION", "WARNING", acc_rate, 2.0,
                            f"Only {acc_rate:.1f}% signals accepted in last 6h ({total_6h} seen) — gates may be too tight")
    except Exception as _e:
        log.debug("alert check 6 error: %s", _e)

    # ── 7. Discipline score < 50 → WARNING ────────────────────────────────
    try:
        with _conn() as c:
            tpd_rows = c.execute("""
                SELECT DATE(closed_ts_utc) as day, COUNT(*) as n
                FROM perp_positions WHERE status=\'CLOSED\' AND closed_ts_utc >= ?
                GROUP BY DATE(closed_ts_utc)
            """, (cutoff_7d,)).fetchall()
            avg_tpd2 = sum(r[1] for r in tpd_rows) / max(len(tpd_rows), 1)
            avg_pnl_row = c.execute("""
                SELECT AVG(pnl_pct) FROM (
                    SELECT pnl_pct FROM perp_positions
                    WHERE status=\'CLOSED\' AND pnl_pct IS NOT NULL
                    ORDER BY closed_ts_utc DESC LIMIT 20
                )
            """).fetchone()
            avg_pnl2 = round(float(avg_pnl_row[0] or 0), 3)
            gcr2 = 0
            try:
                gcr_row2 = c.execute("""
                    SELECT COUNT(*) as t,
                           SUM(CASE WHEN outcome=\'good_call\' THEN 1 ELSE 0 END) as g
                    FROM dynamic_exit_log WHERE outcome IS NOT NULL
                """).fetchone()
                gcr2 = round(int(gcr_row2["g"] or 0) / max(int(gcr_row2["t"] or 1), 1) * 100, 1)
            except Exception:
                pass
        d_pts = 40 if avg_tpd2 <= 3 else round(40 * max(0.0, (5 - avg_tpd2) / 2.0))
        q_pts = 30 if avg_pnl2 >= 0.8 else round(30 * max(0.0, avg_pnl2 / 0.8))
        a_pts = 30 if gcr2 >= 65 else round(30 * max(0.0, (gcr2 - 40) / 25.0))
        disc_score = d_pts + q_pts + a_pts
        if disc_score < 50:
            _fire_alert("DISCIPLINE_DROP", "WARNING", disc_score, 50.0,
                        f"Discipline score {disc_score}/100 below threshold — check trades/day + PNL quality")
    except Exception as _e:
        log.debug("alert check 7 error: %s", _e)

    # ── 8. High-quality signals skipped > 10 today (pred_ret > 1.5%) → INFO
    try:
        with _conn() as c:
            try:
                sk_rows = c.execute("""
                    SELECT COUNT(*) as n, AVG(pred_ret) as avg_ret
                    FROM skipped_signals_log
                    WHERE ts_utc >= ? AND pred_ret > 1.5
                """, (today_start,)).fetchone()
                n_high  = int(sk_rows[0] or 0)
                avg_ret = float(sk_rows[1] or 0)
                if n_high > 10:
                    _fire_alert("LOOSE_GATES_SUGGESTION", "INFO", n_high, 10.0,
                                f"{n_high} high-quality signals skipped today (avg pred_ret={avg_ret:.1f}%) — consider loosening gates")
            except Exception:
                pass
    except Exception as _e:
        log.debug("alert check 8 error: %s", _e)
'''

content = content.replace(OLD_CHECK, NEW_CHECK, 1)
changes += 1
print("[1/2] Replaced _run_performance_alert_check with 8-rule version")

# ═══════════════════════════════════════════════════════════════════════════════
# 2. Replace brain_bull_readiness endpoint (up to /api/journal/learnings)
# ═══════════════════════════════════════════════════════════════════════════════
OLD_BULL_START = '@app.get("/api/brain/bull-readiness")'
OLD_BULL_END   = '\n@app.get("/api/journal/learnings")'

idx_bs = content.find(OLD_BULL_START)
idx_be = content.find(OLD_BULL_END, idx_bs)
assert idx_bs != -1, "FAIL [2/2]: /api/brain/bull-readiness not found"
assert idx_be != -1, "FAIL [2/2]: /api/journal/learnings boundary not found"

OLD_BULL = content[idx_bs:idx_be]

NEW_BULL = '''@app.get("/api/brain/bull-readiness")
async def brain_bull_readiness(_: str = Depends(get_current_user)):
    """
    5-component Bull Market Readiness Score (0-100, 20 pts each):
      1. Discipline   — trades/day, acceptance rate, daily-cap avoidance
      2. ML Quality   — good_call_rate, avg ml_win_prob of entries
      3. Exit Quality — TIME_LIMIT%, dynamic PNL lift, exit good_call contribution
      4. Profitability — avg PNL/trade, win rate 7d, weekly PNL sum
      5. Stability    — no CRITICAL alerts 24h, no daily drawdown, consistent activity
    Labels: READY (>=80) | CLOSE (>=60) | TRAINING (>=40) | NOT_READY (<40)
    """
    try:
        import sqlite3, pathlib as _pl
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        db = str(_pl.Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            cutoff_7d  = (_dt.now(_tz.utc) - _td(days=7)).isoformat()
            cutoff_24h = (_dt.now(_tz.utc) - _td(hours=24)).isoformat()
            today_str  = _dt.now(_tz.utc).strftime("%Y-%m-%d")

            # Trades per day (7d)
            tpd_rows = conn.execute("""
                SELECT DATE(closed_ts_utc) as day, COUNT(*) as n
                FROM perp_positions WHERE status=\'CLOSED\' AND closed_ts_utc >= ?
                GROUP BY DATE(closed_ts_utc)
            """, (cutoff_7d,)).fetchall()
            avg_tpd = sum(r["n"] for r in tpd_rows) / max(len(tpd_rows), 1)
            n_7d = sum(r["n"] for r in tpd_rows)

            # Closed/skipped in 24h (for acceptance rate)
            closed_24h = int(conn.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status=\'CLOSED\' AND closed_ts_utc >= ?",
                (cutoff_24h,)
            ).fetchone()[0] or 0)
            skipped_24h = 0
            try:
                skipped_24h = int(conn.execute(
                    "SELECT COUNT(*) FROM skipped_signals_log WHERE ts_utc >= ?", (cutoff_24h,)
                ).fetchone()[0] or 0)
            except Exception:
                pass

            # DAILY_CAP today
            cap_today = 0
            try:
                cap_today = int(conn.execute(
                    "SELECT COUNT(*) FROM skipped_signals_log WHERE ts_utc >= ? AND skip_reason=\'DAILY_CAP\'",
                    (today_str + "T00:00:00",)
                ).fetchone()[0] or 0)
            except Exception:
                pass

            # Good call rate (all time)
            gcr = 0.0
            try:
                gcr_row = conn.execute("""
                    SELECT COUNT(*) as t,
                           SUM(CASE WHEN outcome=\'good_call\' THEN 1 ELSE 0 END) as g
                    FROM dynamic_exit_log WHERE outcome IS NOT NULL
                """).fetchone()
                gcr = round(int(gcr_row["g"] or 0) / max(int(gcr_row["t"] or 1), 1) * 100, 1)
            except Exception:
                pass

            # Avg ml_win_prob from notes (last 30 entries)
            ml_wp_avg = 0.0
            try:
                notes_rows = conn.execute("""
                    SELECT notes FROM perp_positions
                    WHERE status=\'CLOSED\' AND closed_ts_utc >= ? AND notes LIKE \'%ml_wp=%\'
                    ORDER BY closed_ts_utc DESC LIMIT 30
                """, (cutoff_7d,)).fetchall()
                wp_vals = []
                for row in notes_rows:
                    n = row["notes"] or ""
                    if "ml_wp=" in n:
                        try:
                            wp_vals.append(float(n.split("ml_wp=")[1].split("|")[0]))
                        except Exception:
                            pass
                if wp_vals:
                    ml_wp_avg = round(sum(wp_vals) / len(wp_vals), 3)
            except Exception:
                pass

            # Avg PNL last 20
            avg_pnl_row = conn.execute("""
                SELECT AVG(pnl_pct) FROM (
                    SELECT pnl_pct FROM perp_positions
                    WHERE status=\'CLOSED\' AND pnl_pct IS NOT NULL
                    ORDER BY closed_ts_utc DESC LIMIT 20
                )
            """).fetchone()
            avg_pnl = round(float(avg_pnl_row[0] or 0), 3)

            # Win rate 7d
            wr_row = conn.execute("""
                SELECT COUNT(*) as t,
                       SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as w
                FROM perp_positions WHERE status=\'CLOSED\' AND closed_ts_utc >= ?
            """, (cutoff_7d,)).fetchone()
            win_rate_7d = round(int(wr_row["w"] or 0) / max(int(wr_row["t"] or 1), 1) * 100, 1)

            # Weekly PNL sum
            wpnl_row = conn.execute("""
                SELECT SUM(pnl_pct) FROM perp_positions
                WHERE status=\'CLOSED\' AND closed_ts_utc >= ? AND pnl_pct IS NOT NULL
            """, (cutoff_7d,)).fetchone()
            weekly_pnl = round(float(wpnl_row[0] or 0), 2)

            # TIME_LIMIT % 24h
            tl_24h = int(conn.execute(
                "SELECT COUNT(*) FROM perp_positions WHERE status=\'CLOSED\' AND closed_ts_utc >= ? AND exit_reason=\'TIME_LIMIT\'",
                (cutoff_24h,)
            ).fetchone()[0] or 0)
            tl_pct = round(tl_24h / closed_24h * 100, 1) if closed_24h >= 3 else 50.0

            # Dynamic PNL lift vs TIME_LIMIT
            dyn_in = ("\'DYNAMIC_TRAIL\',\'ML_PROB_DROP\',\'ML_EARLY_EXIT\',"
                      "\'TRAILING_ATR_EXTEND\',\'TRAILING_ATR_WINNER\',"
                      "\'PARTIAL_PROFIT\',\'PROFIT_LOCK\',\'SENTIMENT_TRAIL\'")
            dyn_pnl = float(conn.execute(
                f"SELECT AVG(pnl_pct) FROM perp_positions WHERE status=\'CLOSED\' AND exit_reason IN ({dyn_in})"
            ).fetchone()[0] or 0)
            tl_pnl = float(conn.execute(
                "SELECT AVG(pnl_pct) FROM perp_positions WHERE status=\'CLOSED\' AND exit_reason=\'TIME_LIMIT\'"
            ).fetchone()[0] or 0)
            pnl_lift = round(dyn_pnl - tl_pnl, 3)

            # CRITICAL alerts 24h
            crit_24h = 0
            try:
                crit_24h = int(conn.execute(
                    "SELECT COUNT(*) FROM performance_alerts_log WHERE severity=\'CRITICAL\' AND ts_utc >= ?",
                    (cutoff_24h,)
                ).fetchone()[0] or 0)
            except Exception:
                pass

            # Daily drawdown
            dd_row = conn.execute("""
                SELECT SUM(pnl_pct) FROM perp_positions
                WHERE status=\'CLOSED\' AND DATE(closed_ts_utc) = ? AND pnl_pct IS NOT NULL
            """, (today_str,)).fetchone()
            daily_dd = round(float(dd_row[0] or 0), 2)

        # ── Component scoring (20 pts each) ────────────────────────────────

        # 1. DISCIPLINE (20 pts)
        # avg_tpd ≤3→8, prorated to 5, 0 above 5
        tpd_pts = 8 if avg_tpd <= 3 else round(max(0.0, (5 - avg_tpd) / 2.0) * 8)
        # acceptance rate: ideal 10-40%; <2% starvation=0, >60%=low selectivity
        total_sig = closed_24h + skipped_24h
        acc_rate  = closed_24h / total_sig if total_sig >= 3 else 0.3
        if total_sig < 3:
            acc_pts = 4  # neutral — not enough data
        elif acc_rate < 0.02:
            acc_pts = 0
        elif acc_rate <= 0.40:
            acc_pts = round(min(1.0, acc_rate / 0.30) * 6)
        else:
            acc_pts = max(2, round((1.0 - (acc_rate - 0.40)) * 6))
        # no DAILY_CAP today = 6 pts
        cap_pts = 0 if cap_today > 0 else 6
        disc_pts  = tpd_pts + acc_pts + cap_pts
        disc_val  = round((disc_pts / 20) * 100)
        disc_lbl  = f"avg {avg_tpd:.1f} trades/day · {round(acc_rate * 100)}% acceptance"

        # 2. ML QUALITY (20 pts)
        # good_call_rate ≥65%→12, from 40%
        gcr_pts = 12 if gcr >= 65 else round(max(0.0, (gcr - 40) / 25.0) * 12)
        # avg ml_win_prob ≥0.75→8; neutral 4 if no data
        if ml_wp_avg == 0.0:
            wp_pts = 4
        elif ml_wp_avg >= 0.75:
            wp_pts = 8
        elif ml_wp_avg >= 0.60:
            wp_pts = round((ml_wp_avg - 0.60) / 0.15 * 8)
        else:
            wp_pts = 0
        ml_pts = gcr_pts + wp_pts
        ml_val  = round(gcr, 1)
        ml_lbl  = (f"Good call rate {gcr:.0f}% · avg win_prob {ml_wp_avg:.0%}"
                   if ml_wp_avg > 0 else f"Good call rate {gcr:.0f}%")

        # 3. EXIT QUALITY (20 pts)
        # TL% ≤40%→8, 0 at ≥70%
        tl_exit_pts = 8 if tl_pct <= 40 else round(max(0.0, (70 - tl_pct) / 30.0) * 8)
        # pnl_lift ≥0.5%→6
        lift_pts = 6 if pnl_lift >= 0.5 else round(max(0.0, pnl_lift / 0.5) * 6) if pnl_lift > 0 else 0
        # good_call contribution ≥60%→6
        gcr_ep = 6 if gcr >= 60 else round(max(0.0, gcr / 60.0) * 6)
        exit_pts = tl_exit_pts + lift_pts + gcr_ep
        exit_val  = round(tl_pct, 1)
        exit_lbl  = f"TIME_LIMIT {tl_pct:.0f}% · lift {pnl_lift:+.2f}%"

        # 4. PROFITABILITY (20 pts)
        # avg_pnl ≥1%→8; 0 at ≤0%
        pnl_pts = 8 if avg_pnl >= 1.0 else round(max(0.0, avg_pnl) * 8) if avg_pnl > 0 else 0
        # win_rate ≥55%→6; from 40%
        wr_pts = 6 if win_rate_7d >= 55 else round(max(0.0, (win_rate_7d - 40) / 15.0) * 6) if win_rate_7d > 40 else 0
        # weekly_pnl > 0→6
        wpnl_pts = 6 if weekly_pnl > 0 else round(max(0.0, (weekly_pnl + 5) / 5.0) * 6) if weekly_pnl > -5 else 0
        prof_pts = pnl_pts + wr_pts + wpnl_pts
        prof_val  = round(avg_pnl, 3)
        prof_lbl  = f"Avg {avg_pnl:+.2f}%/trade · {win_rate_7d:.0f}% win rate"

        # 5. STABILITY (20 pts)
        # no CRITICAL alerts 24h→10
        crit_pts = 0 if crit_24h > 0 else 10
        # daily drawdown > -2%→5
        dd_pts = 5 if daily_dd > -2.0 else round(max(0.0, (daily_dd + 5) / 3.0) * 5) if daily_dd > -5 else 0
        # consistent activity (≥3 trades in 7d)→5
        act_pts = 5 if n_7d >= 3 else round(n_7d / 3.0 * 5)
        stab_pts = crit_pts + dd_pts + act_pts
        stab_val  = round((stab_pts / 20) * 100)
        stab_lbl  = f"{crit_24h} critical alert(s) 24h · dd {daily_dd:+.1f}%"

        total_score = disc_pts + ml_pts + exit_pts + prof_pts + stab_pts
        if total_score >= 80:
            label, color, label_key = "Ready for Live", "green",  "READY"
        elif total_score >= 60:
            label, color, label_key = "Close to Ready", "amber",  "CLOSE"
        elif total_score >= 40:
            label, color, label_key = "Still Training", "amber",  "TRAINING"
        else:
            label, color, label_key = "Not Ready",      "red",    "NOT_READY"

        return {
            "score": total_score,
            "label": label,
            "label_key": label_key,
            "color": color,
            "go_live_ready": total_score >= 80,
            "components": {
                "discipline":    {"pts": disc_pts,  "max": 20, "value": disc_val,  "label": disc_lbl},
                "ml_quality":    {"pts": ml_pts,    "max": 20, "value": ml_val,    "label": ml_lbl},
                "exit_quality":  {"pts": exit_pts,  "max": 20, "value": exit_val,  "label": exit_lbl},
                "profitability": {"pts": prof_pts,  "max": 20, "value": prof_val,  "label": prof_lbl},
                "stability":     {"pts": stab_pts,  "max": 20, "value": stab_val,  "label": stab_lbl},
            },
            "ts_utc": _dt.now(_tz.utc).isoformat() + "Z",
        }
    except Exception as e:
        return {"error": str(e), "score": 0, "label": "Not Ready", "label_key": "NOT_READY",
                "color": "red", "go_live_ready": False, "components": {}, "ts_utc": ""}
'''

content = content.replace(OLD_BULL, NEW_BULL, 1)
changes += 1
print("[2/2] Replaced brain_bull_readiness with 5-component version")

assert changes == 2, f"Expected 2 changes, got {changes}"
FILE.write_text(content)
print(f"\n✅ Patch 50 applied ({changes}/2 changes)")
print("   _run_performance_alert_check: 8 rules (4 CRITICAL, 2 WARNING, 1 INFO)")
print("   bull-readiness: Discipline + ML Quality + Exit Quality + Profitability + Stability")
