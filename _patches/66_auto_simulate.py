"""
Patch 66 — Automatic Simulation & Hands-Off Operation
=======================================================
1. _send_sim_alert() — standalone alert helper (Slack + email, reuses existing env vars)
2. _run_auto_simulate_check() — synchronous 5-min check: auto-enable, DD guard, 24h milestone
3. _auto_simulate_loop() — async wrapper (run_in_executor pattern)
4. Lifespan: startup auto-enable + register new task
5. GET /api/risk/auto-simulate-status — config + live state for dashboard
6. POST /api/risk/auto-simulate-config — write AUTO_SIMULATE_* vars to .env
"""

from pathlib import Path
import py_compile, tempfile, shutil

MAIN = Path(__file__).resolve().parent.parent / "dashboard" / "backend" / "main.py"
main = MAIN.read_text()

# ── 1. Insert _send_sim_alert + _run_auto_simulate_check + _auto_simulate_loop ─
# Insert before the @asynccontextmanager decorator (which precedes lifespan)

INSERT_BEFORE_LIFESPAN = '@asynccontextmanager'
assert INSERT_BEFORE_LIFESPAN in main, "asynccontextmanager anchor not found"

AUTO_SIM_CODE = r'''
# ── Auto-Simulate Monitor ─────────────────────────────────────────────────────

def _send_sim_alert(message: str, severity: str = "WARNING") -> None:
    """Fire a Slack + email alert for auto-simulate events (non-dedup, caller controls rate)."""
    import os as _os
    log.info("[AUTO-SIM] %s: %s", severity, message)
    slack_url = _os.environ.get("ALERT_SLACK_WEBHOOK", "")
    if slack_url:
        try:
            import requests as _req
            emoji = "\U0001f534" if severity == "CRITICAL" else "\U0001f7e1" if severity == "WARNING" else "\U0001f7e2"
            _req.post(slack_url, json={"text": f"{emoji} [AUTO-SIM {severity}] {message}"}, timeout=5)
        except Exception:
            pass
    email_to  = _os.environ.get("ALERT_EMAIL", "")
    smtp_host = _os.environ.get("SMTP_HOST", "")
    smtp_user = _os.environ.get("SMTP_USER", "")
    smtp_pass = _os.environ.get("SMTP_PASS", "")
    if email_to and smtp_host and smtp_user and smtp_pass:
        try:
            import smtplib
            from email.mime.text import MIMEText
            smtp_port = int(_os.environ.get("SMTP_PORT", "465"))
            msg = MIMEText(message)
            msg["Subject"] = f"[Memecoin Engine] AUTO-SIM {severity}"
            msg["From"] = smtp_user
            msg["To"] = email_to
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as srv:
                srv.login(smtp_user, smtp_pass)
                srv.sendmail(smtp_user, [email_to], msg.as_string())
        except Exception:
            pass


def _run_auto_simulate_check() -> None:
    """
    Synchronous check (runs in executor every 5 min):
    1. If not simulating + AUTO_SIMULATE_ON_START=true + no DD cooldown → auto-enable
    2. If simulating + DD > AUTO_SIMULATE_DD_THRESHOLD → auto-end + 24h cooldown
    3. If simulating + 24h reached + not yet finalized → auto-finalize + alert
    4. If checklist all_clear → alert once (6h dedup)
    """
    import sqlite3 as _sq, re as _re, json as _json, sys as _sys
    auto_start   = os.environ.get("AUTO_SIMULATE_ON_START",    "true").lower() in ("1", "true", "yes")
    dd_threshold = float(os.environ.get("AUTO_SIMULATE_DD_THRESHOLD", "5.0"))
    real_money   = os.environ.get("REAL_MONEY_MODE",    "false").lower() in ("1", "true", "yes")
    sim_active   = os.environ.get("SIMULATE_LIVE_MODE", "false").lower() in ("1", "true", "yes")

    if real_money:
        return  # never interfere with live trading

    db_path  = os.path.join(_engine_root(), "data_storage", "engine.db")
    env_path = os.path.join(_engine_root(), ".env")

    def _write_sim_env(enable: bool) -> None:
        new_val = "true" if enable else "false"
        if os.path.exists(env_path):
            env_text = open(env_path).read()
            if "SIMULATE_LIVE_MODE" in env_text:
                env_text = _re.sub(r"SIMULATE_LIVE_MODE=\S*", f"SIMULATE_LIVE_MODE={new_val}", env_text)
            else:
                env_text += f"\nSIMULATE_LIVE_MODE={new_val}\n"
            open(env_path, "w").write(env_text)
        os.environ["SIMULATE_LIVE_MODE"] = new_val
        _pe = _sys.modules.get("utils.perp_executor")
        if _pe and hasattr(_pe, "_apply_live_mode_tuning"):
            _pe._apply_live_mode_tuning("SIMULATE_LIVE_MODE", new_val)

    def _log_event(event_type: str, reason: str, metadata: dict | None = None) -> None:
        try:
            with _sq.connect(db_path) as c:
                c.execute(
                    "CREATE TABLE IF NOT EXISTS live_transition_log "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, event_type TEXT, "
                    "symbol TEXT, side TEXT, mode TEXT, reason TEXT, metadata TEXT)"
                )
                c.execute(
                    "INSERT INTO live_transition_log (ts, event_type, symbol, side, mode, reason, metadata) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (datetime.utcnow().isoformat() + "Z", event_type, "", "", "AUTO-SIMULATE",
                     reason, _json.dumps(metadata or {})),
                )
        except Exception as _le:
            log.warning("auto_simulate log_event error: %s", _le)

    try:
        with _sq.connect(db_path) as conn:
            conn.row_factory = _sq.Row

            # ── Step 1: Auto-enable if not active ────────────────────────────
            if not sim_active and auto_start:
                # 24h cooldown after DD auto-end
                in_cooldown = False
                try:
                    dd_row = conn.execute(
                        "SELECT ts FROM live_transition_log WHERE event_type='SIMULATE_AUTO_ENDED' "
                        "ORDER BY ts DESC LIMIT 1"
                    ).fetchone()
                    if dd_row:
                        last_dd_dt = datetime.fromisoformat(dd_row[0].replace("Z", ""))
                        cooldown_h = (datetime.utcnow() - last_dd_dt).total_seconds() / 3600
                        if cooldown_h < 24.0:
                            in_cooldown = True
                            log.debug("auto_simulate: DD cooldown %.1fh remaining", 24.0 - cooldown_h)
                except Exception:
                    pass

                if not in_cooldown:
                    _write_sim_env(True)
                    _log_event("SIMULATE_AUTO_STARTED",
                               "AUTO_SIMULATE_ON_START=true — auto-enabled by background monitor")
                    _send_sim_alert(
                        "Simulation mode auto-enabled. Engine running with real-money sizing on paper routing. "
                        "Monitor Brain > Simulate Live Status.", "INFO"
                    )
                return  # done for this tick

            if not sim_active:
                return  # manually disabled, respect it

            # ── Step 2: DD guard ─────────────────────────────────────────────
            sim_row = None
            try:
                sim_row = conn.execute(
                    "SELECT ts FROM live_transition_log "
                    "WHERE event_type IN ('SIMULATE_ENABLED','SIMULATE_AUTO_STARTED') "
                    "ORDER BY ts DESC LIMIT 1"
                ).fetchone()
            except Exception:
                pass

            if not sim_row:
                return

            sim_start    = sim_row[0]
            sim_dt       = datetime.fromisoformat(sim_start.replace("Z", ""))
            hours_active = (datetime.utcnow() - sim_dt).total_seconds() / 3600

            try:
                closed_trades = conn.execute(
                    "SELECT realized_pnl_pct, exit_reason FROM perp_positions "
                    "WHERE dry_run=1 AND entry_ts_utc >= ? AND status='CLOSED' "
                    "AND realized_pnl_pct IS NOT NULL ORDER BY entry_ts_utc",
                    (sim_start,)
                ).fetchall()
                if closed_trades:
                    pnls   = [float(t[0]) for t in closed_trades]
                    cum = 0.0; peak = 0.0; max_dd = 0.0
                    for p in pnls:
                        cum += p; peak = max(peak, cum)
                        max_dd = max(max_dd, peak - cum)
                    max_dd = round(max_dd, 2)
                    if max_dd > dd_threshold:
                        _write_sim_env(False)
                        _log_event("SIMULATE_AUTO_ENDED",
                                   f"Auto-ended: max_dd={max_dd:.1f}% exceeded {dd_threshold:.1f}% threshold")
                        _send_sim_alert(
                            f"AUTO-END SIMULATION: DD THRESHOLD BREACHED — "
                            f"drawdown {max_dd:.1f}% exceeded {dd_threshold:.1f}% limit. "
                            f"Simulation disabled. 24h cooldown before auto-restart. "
                            f"Review in Brain > Simulate Live Status.", "WARNING"
                        )
                        return
            except Exception as _de:
                log.debug("auto_simulate DD check error: %s", _de)

            # ── Step 3: 24h milestone → auto-finalize ────────────────────────
            if hours_active >= 24.0:
                try:
                    already = conn.execute(
                        "SELECT id FROM live_transition_log "
                        "WHERE event_type='SIMULATE_REVIEW_COMPLETE' AND ts >= ? LIMIT 1",
                        (sim_start,)
                    ).fetchone()
                    if not already:
                        # Compute final stats + recommendation inline
                        closed = conn.execute(
                            "SELECT realized_pnl_pct, exit_reason FROM perp_positions "
                            "WHERE dry_run=1 AND entry_ts_utc >= ? AND status='CLOSED' "
                            "AND realized_pnl_pct IS NOT NULL ORDER BY entry_ts_utc",
                            (sim_start,)
                        ).fetchall()
                        n_closed = len(closed)
                        pnls   = [float(t[0]) for t in closed] if closed else []
                        avg_pnl   = round(sum(pnls) / len(pnls), 2) if pnls else 0.0
                        total_pnl = round(sum(pnls), 2) if pnls else 0.0
                        win_rate  = round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1) if pnls else 0.0
                        tl_pct    = round(sum(1 for t in closed if t[1] == "TIME_LIMIT") / len(pnls) * 100, 1) if pnls else 0.0
                        cum = 0.0; peak = 0.0; max_dd_f = 0.0
                        for p in pnls:
                            cum += p; peak = max(peak, cum)
                            max_dd_f = max(max_dd_f, peak - cum)
                        max_dd_f = round(max_dd_f, 2)
                        criteria = {
                            "avg_pnl_above_0_5pct":     avg_pnl > 0.5,
                            "max_dd_below_3pct":         max_dd_f < 3.0,
                            "win_rate_above_45pct":      win_rate >= 45.0,
                            "time_limit_below_70pct":    tl_pct < 70.0,
                            "ran_24h_or_more":           True,
                            "at_least_3_closed":         n_closed >= 3,
                        }
                        passed_c = sum(1 for v in criteria.values() if v)
                        total_c  = len(criteria)
                        if passed_c == total_c:
                            rec_level = "RECOMMENDED"
                            rec_msg   = "All metrics positive — engine ready for real capital"
                        elif passed_c >= total_c - 1:
                            failing   = [k for k, v in criteria.items() if not v]
                            rec_level = "PROCEED WITH CAUTION"
                            rec_msg   = "Minor concerns: " + ", ".join(failing)
                        else:
                            failing   = [k for k, v in criteria.items() if not v]
                            rec_level = "NOT RECOMMENDED"
                            rec_msg   = "Multiple criteria failing: " + ", ".join(failing)

                        stats = {
                            "hours_active": round(hours_active, 1),
                            "closed_count": n_closed, "avg_pnl_pct": avg_pnl,
                            "total_pnl_pct": total_pnl, "win_rate_pct": win_rate,
                            "time_limit_pct": tl_pct, "max_dd_pct": max_dd_f,
                            "recommendation": {
                                "level": rec_level, "message": rec_msg,
                                "passed": passed_c, "total": total_c,
                            },
                        }
                        _log_event("SIMULATE_REVIEW_COMPLETE",
                                   f"Auto-finalized after {hours_active:.1f}h: {rec_level}", stats)
                        _send_sim_alert(
                            f"AUTO-READY FOR LIVE — 24h simulation complete!\n"
                            f"Recommendation: {rec_level}\n{rec_msg}\n"
                            f"Stats: {n_closed} trades, avg PNL {avg_pnl:+.2f}%, "
                            f"win rate {win_rate:.1f}%, max DD {max_dd_f:.2f}%\n"
                            f"Go to Risk tab to review and enable Real Money Trading.",
                            "WARNING" if rec_level != "RECOMMENDED" else "INFO"
                        )
                except Exception as _fe:
                    log.debug("auto_simulate finalize error: %s", _fe)

    except Exception as exc:
        log.debug("_run_auto_simulate_check error: %s", exc)


async def _auto_simulate_loop() -> None:
    """Background task — auto-simulate monitor, runs every 5 minutes."""
    await asyncio.sleep(90)  # startup delay — let other loops warm up first
    while True:
        try:
            await asyncio.get_event_loop().run_in_executor(None, _run_auto_simulate_check)
        except Exception as _e:
            log.debug("_auto_simulate_loop error: %s", _e)
        await asyncio.sleep(300)  # 5 minutes


'''

main = main.replace(INSERT_BEFORE_LIFESPAN,
                    AUTO_SIM_CODE + INSERT_BEFORE_LIFESPAN)
print("✅ _send_sim_alert + _run_auto_simulate_check + _auto_simulate_loop inserted")

# ── 2. Register task in lifespan + startup auto-enable ────────────────────────

OLD_LIFESPAN_TASK = (
    '    task_perf_alerts = asyncio.create_task(_performance_alert_loop())\n'
    '    log.info("Dashboard started \u2014 perp swing + scalp + mid + spot paper bots (3-track learning) + performance alerts running.")\n'
    '    yield\n'
    '    all_tasks = (task_poller, task_tracker, task_perp_mon, task_perp_scan,\n'
    '                 task_scalp_mon, task_scalp_scan, task_mid_mon, task_mid_scan,\n'
    '                 task_spot_mon, task_spot_scan, task_wl_momentum, task_post_exit,\n'
    '                 task_weekly_report, task_perf_alerts)\n'
)
NEW_LIFESPAN_TASK = (
    '    task_perf_alerts   = asyncio.create_task(_performance_alert_loop())\n'
    '    task_auto_simulate = asyncio.create_task(_auto_simulate_loop())\n'
    '    # Startup: auto-enable simulate mode if configured\n'
    '    import re as _re_s\n'
    '    _auto_start_env = os.environ.get("AUTO_SIMULATE_ON_START", "true").lower() in ("1", "true", "yes")\n'
    '    _rm_active      = os.environ.get("REAL_MONEY_MODE", "false").lower() in ("1", "true", "yes")\n'
    '    _sim_already_on = os.environ.get("SIMULATE_LIVE_MODE", "false").lower() in ("1", "true", "yes")\n'
    '    if _auto_start_env and not _rm_active and not _sim_already_on:\n'
    '        _env_path_s = os.path.join(_engine_root(), ".env")\n'
    '        if os.path.exists(_env_path_s):\n'
    '            _env_txt = open(_env_path_s).read()\n'
    '            if "SIMULATE_LIVE_MODE" in _env_txt:\n'
    '                _env_txt = _re_s.sub(r"SIMULATE_LIVE_MODE=\\S*", "SIMULATE_LIVE_MODE=true", _env_txt)\n'
    '            else:\n'
    '                _env_txt += "\\nSIMULATE_LIVE_MODE=true\\n"\n'
    '            open(_env_path_s, "w").write(_env_txt)\n'
    '        os.environ["SIMULATE_LIVE_MODE"] = "true"\n'
    '        try:\n'
    '            import sqlite3 as _sq_s\n'
    '            _db_s = os.path.join(_engine_root(), "data_storage", "engine.db")\n'
    '            with _sq_s.connect(_db_s) as _c_s:\n'
    '                _c_s.execute("CREATE TABLE IF NOT EXISTS live_transition_log "\n'
    '                             "(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, event_type TEXT, "\n'
    '                             "symbol TEXT, side TEXT, mode TEXT, reason TEXT, metadata TEXT)")\n'
    '                _c_s.execute("INSERT INTO live_transition_log (ts, event_type, symbol, side, mode, reason) "\n'
    '                             "VALUES (?,?,?,?,?,?)",\n'
    '                             (datetime.utcnow().isoformat() + "Z", "SIMULATE_AUTO_STARTED",\n'
    '                              "", "", "AUTO-SIMULATE", "AUTO-ENABLED SIMULATE LIVE MODE ON STARTUP"))\n'
    '        except Exception as _se_s:\n'
    '            log.warning("startup auto-simulate log error: %s", _se_s)\n'
    '        log.info("[AUTO-SIM] Simulation mode auto-enabled on startup (AUTO_SIMULATE_ON_START=true)")\n'
    '    log.info("Dashboard started \u2014 perp swing + scalp + mid + spot paper bots (3-track learning) + performance alerts running.")\n'
    '    yield\n'
    '    all_tasks = (task_poller, task_tracker, task_perp_mon, task_perp_scan,\n'
    '                 task_scalp_mon, task_scalp_scan, task_mid_mon, task_mid_scan,\n'
    '                 task_spot_mon, task_spot_scan, task_wl_momentum, task_post_exit,\n'
    '                 task_weekly_report, task_perf_alerts, task_auto_simulate)\n'
)
assert OLD_LIFESPAN_TASK in main, "lifespan task anchor not found"
main = main.replace(OLD_LIFESPAN_TASK, NEW_LIFESPAN_TASK)
print("✅ _auto_simulate_loop registered in lifespan + startup auto-enable")

# ── 3. Two new endpoints: auto-simulate-status + auto-simulate-config ─────────

INSERT_ANCHOR = '@app.get("/api/journal/learnings")'
assert INSERT_ANCHOR in main, "learnings anchor not found"

AUTO_SIM_ENDPOINTS = r'''@app.get("/api/risk/auto-simulate-status")
async def risk_auto_simulate_status(_: str = Depends(get_current_user)):
    """Auto-simulate config + live state for dashboard banner."""
    import sqlite3 as _sq
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    auto_start   = os.environ.get("AUTO_SIMULATE_ON_START",    "true").lower() in ("1", "true", "yes")
    dd_threshold = float(os.environ.get("AUTO_SIMULATE_DD_THRESHOLD", "5.0"))
    sim_active   = os.environ.get("SIMULATE_LIVE_MODE", "false").lower() in ("1", "true", "yes")
    real_money   = os.environ.get("REAL_MONEY_MODE", "false").lower() in ("1", "true", "yes")
    alert_email  = bool(os.environ.get("ALERT_EMAIL", ""))
    alert_slack  = bool(os.environ.get("ALERT_SLACK_WEBHOOK", ""))
    last_event   = None
    hours_active = 0.0
    in_cooldown  = False
    cooldown_remaining_h = 0.0
    try:
        with _sq.connect(db_path) as c:
            c.row_factory = _sq.Row
            row = c.execute(
                "SELECT ts, event_type, reason FROM live_transition_log "
                "ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            if row:
                last_event = {"ts": row["ts"], "event_type": row["event_type"], "reason": row["reason"]}
            sim_row = c.execute(
                "SELECT ts FROM live_transition_log "
                "WHERE event_type IN ('SIMULATE_ENABLED','SIMULATE_AUTO_STARTED') "
                "ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            if sim_row:
                sim_dt = datetime.fromisoformat(sim_row[0].replace("Z", ""))
                hours_active = round((datetime.utcnow() - sim_dt).total_seconds() / 3600, 1)
            dd_row = c.execute(
                "SELECT ts FROM live_transition_log WHERE event_type='SIMULATE_AUTO_ENDED' "
                "ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            if dd_row:
                last_dd_dt = datetime.fromisoformat(dd_row[0].replace("Z", ""))
                cooldown_h = (datetime.utcnow() - last_dd_dt).total_seconds() / 3600
                if cooldown_h < 24.0:
                    in_cooldown = True
                    cooldown_remaining_h = round(24.0 - cooldown_h, 1)
    except Exception as exc:
        log.debug("auto_simulate_status error: %s", exc)
    return JSONResponse({
        "auto_start_enabled": auto_start,
        "dd_threshold":        dd_threshold,
        "sim_active":          sim_active,
        "real_money_active":   real_money,
        "hours_active":        hours_active,
        "alert_email_configured":  alert_email,
        "alert_slack_configured":  alert_slack,
        "last_event":          last_event,
        "in_dd_cooldown":      in_cooldown,
        "cooldown_remaining_h": cooldown_remaining_h,
    })


@app.post("/api/risk/auto-simulate-config")
async def risk_auto_simulate_config(request: Request, _: str = Depends(get_current_user)):
    """Update AUTO_SIMULATE_* env vars in .env file."""
    import re as _re
    body = await request.json()
    env_path = os.path.join(_engine_root(), ".env")
    allowed = {"AUTO_SIMULATE_ON_START", "AUTO_SIMULATE_DD_THRESHOLD", "ALERT_EMAIL",
               "ALERT_SLACK_WEBHOOK", "SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_PORT"}
    updated = []
    try:
        env_text = open(env_path).read() if os.path.exists(env_path) else ""
        for key, val in body.items():
            if key not in allowed:
                continue
            str_val = str(val)
            if key in env_text:
                env_text = _re.sub(rf"{key}=\S*", f"{key}={str_val}", env_text)
            else:
                env_text += f"\n{key}={str_val}\n"
            os.environ[key] = str_val
            updated.append(key)
        if os.path.exists(env_path):
            open(env_path, "w").write(env_text)
        return JSONResponse({"ok": True, "updated": updated,
                             "ts": datetime.utcnow().isoformat() + "Z"})
    except Exception as exc:
        log.warning("auto_simulate_config error: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


'''

main = main.replace(INSERT_ANCHOR, AUTO_SIM_ENDPOINTS + INSERT_ANCHOR)
print("✅ /api/risk/auto-simulate-status and /api/risk/auto-simulate-config inserted")

MAIN.write_text(main)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ main.py compiles OK")
print("\nPatch 66 complete")
