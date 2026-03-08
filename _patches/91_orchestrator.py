#!/usr/bin/env python3
"""Patch 91 — Abrons Orchestrator: 7-Agent Autonomous Trading System.

Operations on /root/memecoin_engine/dashboard/backend/main.py:
  OP-1: Import orchestrator + telegram_alerts after perp_monitor_step import
  OP-2: Wire Telegram into sync _fire_alert() (before Slack block)
  OP-3: Add _watchdog_agent_loop() + _research_agent_loop() functions
  OP-4: Register 2 new tasks in lifespan() + add to cleanup tuple
  OP-5: Add heartbeat calls to SWING + SCALP + alert loops
  OP-6: Add MEMORY.md writes after SWING/SCALP signal log.info lines
  OP-7: Add 5 new API endpoints before /api/journal/learnings
"""
from pathlib import Path
import subprocess

MAIN = Path("/root/memecoin_engine/dashboard/backend/main.py")
text = MAIN.read_text()

# ─────────────────────────────────────────────────────────────────────────────
# OP-1: Import orchestrator + telegram_alerts
# ─────────────────────────────────────────────────────────────────────────────
OLD1 = "from utils.perp_executor import perp_monitor_step  # type: ignore"
assert text.count(OLD1) == 1, f"OP-1: expected 1, found {text.count(OLD1)}"
NEW1 = (
    "from utils.perp_executor import perp_monitor_step  # type: ignore\n"
    "from utils.orchestrator import (\n"
    "    heartbeat as _orch_hb, append_memory as _orch_mem,\n"
    "    get_status as _orch_status, write_research as _orch_write_res,\n"
    "    read_memory as _orch_read_mem, read_research as _orch_read_res,\n"
    ")  # Patch 91\n"
    "from utils.telegram_alerts import (\n"
    "    send_telegram as _send_tg, send_telegram_sync as _send_tg_sync,\n"
    "    should_rate_limit as _tg_rl,\n"
    ")  # Patch 91"
)
text = text.replace(OLD1, NEW1)
assert text.count(NEW1) == 1, "OP-1 replacement error"
print("OP-1: imports added ✓")

# ─────────────────────────────────────────────────────────────────────────────
# OP-2: Wire Telegram into sync _fire_alert() — before the Slack block
# ─────────────────────────────────────────────────────────────────────────────
OLD2 = "        slack_url = _os.environ.get(\"ALERT_SLACK_WEBHOOK\", \"\")"
assert text.count(OLD2) == 1, f"OP-2: expected 1, found {text.count(OLD2)}"
NEW2 = (
    "        # Telegram alert (Patch 91)\n"
    "        try:\n"
    "            from utils.telegram_alerts import send_telegram_sync as _tg_s, should_rate_limit as _tg_rl2\n"
    "            if not _tg_rl2(f\"tg_{alert_type}\"):\n"
    "                _tg_emoji = \"\\U0001f534\" if severity == \"CRITICAL\" else \"\\U0001f7e1\" if severity == \"WARNING\" else \"\\u2139\\ufe0f\"\n"
    "                _tg_s(f\"{_tg_emoji} [{severity}] {alert_type}\", message)\n"
    "        except Exception as _tge:\n"
    "            log.debug(\"telegram alert error: %s\", _tge)\n"
    "        slack_url = _os.environ.get(\"ALERT_SLACK_WEBHOOK\", \"\")"
)
text = text.replace(OLD2, NEW2)
assert text.count(NEW2) == 1, "OP-2 replacement error"
print("OP-2: Telegram wired into _fire_alert ✓")

# ─────────────────────────────────────────────────────────────────────────────
# OP-3: Add _watchdog_agent_loop() + _research_agent_loop() before lifespan()
# ─────────────────────────────────────────────────────────────────────────────
OLD3 = "async def lifespan(app: FastAPI):"
assert text.count(OLD3) == 1, f"OP-3: expected 1, found {text.count(OLD3)}"

WATCHDOG_FUNC = '''async def _watchdog_agent_loop():
    """Patch 91 — Agent 4: Watchdog. Heartbeat every 5 min, Telegram on stalls/DD."""
    import sqlite3 as _sql3w
    from datetime import timedelta as _tdw
    _dbw = str(Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
    await asyncio.sleep(90)   # let other loops start first
    while True:
        try:
            statuses = _orch_status()
            stalled = [s["name"] for s in statuses if s["health"] == "stalled"]
            alive_n = len([s for s in statuses if s["health"] == "alive"])
            with _sql3w.connect(_dbw) as _cw:
                open_pos = _cw.execute(
                    "SELECT COUNT(*) FROM perp_positions WHERE status='OPEN'"
                ).fetchone()[0]
                _since = (datetime.utcnow() - _tdw(hours=24)).isoformat()
                trades_24h = _cw.execute(
                    "SELECT COUNT(*) FROM perp_positions WHERE opened_ts_utc >= ? AND status='CLOSED'",
                    (_since,)
                ).fetchone()[0]
                dd_row = _cw.execute(
                    "SELECT COALESCE(SUM(pnl_pct), 0.0) FROM perp_positions "
                    "WHERE opened_ts_utc >= ? AND status='CLOSED'",
                    (_since,)
                ).fetchone()
                dd_24h = float(dd_row[0]) if dd_row else 0.0
            hb_msg = (
                f"Alive={alive_n}/7 | Open={open_pos} | "
                f"24h_trades={trades_24h} | DD={dd_24h:+.2f}%"
            )
            if stalled:
                hb_msg = f"STALLED:{stalled} | " + hb_msg
                if not _tg_rl(f"watchdog_stall"):
                    await _send_tg("Watchdog Alert", hb_msg, "\\U0001f534")
                log.warning("[WATCHDOG] %s", hb_msg)
            elif dd_24h < -3.0:
                if not _tg_rl("watchdog_dd"):
                    await _send_tg("Drawdown Alert", f"24h DD={dd_24h:+.2f}% | {hb_msg}", "\\U0001f534")
                log.warning("[WATCHDOG] DD alert: %s", hb_msg)
            else:
                log.debug("[WATCHDOG] %s", hb_msg)
            _orch_mem("watchdog", hb_msg)
            _orch_hb("watchdog")
        except Exception as _we:
            log.debug("_watchdog_agent_loop error: %s", _we)
        await asyncio.sleep(300)  # 5 min


async def _research_agent_loop(once: bool = False):
    """Patch 91 — Agent 2: Research. Every 4h fetch Kraken OHLC → RESEARCH.md + Telegram."""
    import sqlite3 as _sql3r
    import aiohttp as _aior
    from datetime import timedelta as _tdr
    _dbr = str(Path(__file__).resolve().parent.parent.parent / "data_storage" / "engine.db")
    # local Kraken pair map (same as scan loops)
    _RES_PAIRS = {
        "SOL": ("SOLUSD",   "SOLUSD"),
        "BTC": ("XBTUSD",   "XXBTZUSD"),
        "ETH": ("ETHUSD",   "XETHZUSD"),
        "SUI": ("SUIUSD",   "SUIUSD"),
        "TON": ("TONUSD",   "TONUSD"),
        "AVAX":("AVAXUSD",  "AVAXUSD"),
        "ARB": ("ARBUSD",   "ARBUSD"),
        "OP":  ("OPUSD",    "OPUSD"),
        "NEAR":("NEARUSD",  "NEARUSD"),
        "INJ": ("INJUSD",   "INJUSD"),
        "SEI": ("SEIUSD",   "SEIUSD"),
        "APT": ("APTUSD",   "APTUSD"),
    }
    if not once:
        await asyncio.sleep(300)   # 5 min startup delay
    while True:
        try:
            from datetime import datetime as _dtr, timezone as _tzr
            _now_str = _dtr.now(_tzr.utc).strftime("%Y-%m-%d %H:%M UTC")
            lines = [
                f"# Market Research Digest — {_now_str}",
                "",
                "## Symbol Summary (4h Kraken OHLC)",
                "| Symbol | 4h% | RSI | MACD_H | Regime |",
                "|--------|-----|-----|--------|--------|",
            ]
            async with _aior.ClientSession(timeout=_aior.ClientTimeout(total=15)) as sess:
                for sym, (pair, rkey) in _RES_PAIRS.items():
                    try:
                        url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=240"
                        async with sess.get(url) as r:
                            jd = await r.json()
                        candles = list(jd.get("result", {}).values())[0] if jd.get("result") else []
                        if len(candles) < 20:
                            continue
                        rsi_val = _compute_rsi(candles)
                        macd_val = _compute_macd(candles)
                        macd_h = round(macd_val["histogram"], 4) if macd_val else None
                        closes = [float(c[4]) for c in candles]
                        chg_4h = round((closes[-2] - closes[-3]) / closes[-3] * 100, 2) if len(closes) >= 3 else 0.0
                        rsi_r = round(rsi_val, 1) if rsi_val is not None else "?"
                        regime = "BEAR" if (rsi_val or 50) < 40 else "BULL" if (rsi_val or 50) > 60 else "NEUTRAL"
                        lines.append(f"| {sym:<5} | {chg_4h:+.2f}% | {rsi_r} | {macd_h} | {regime} |")
                    except Exception:
                        continue
            # Trade stats from DB
            with _sql3r.connect(_dbr) as _cr:
                _since_r = (_dtr.utcnow() - _tdr(hours=24)).isoformat()
                row = _cr.execute(
                    "SELECT COUNT(*), AVG(pnl_pct), "
                    "SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) "
                    "FROM perp_positions WHERE opened_ts_utc >= ? AND status='CLOSED'",
                    (_since_r,)
                ).fetchone()
                n, avg_pnl, wins = (row[0] or 0), (row[1] or 0.0), (row[2] or 0)
            wr = round(wins / n * 100, 1) if n > 0 else 0.0
            lines += [
                "",
                "## 24h Performance",
                f"- Trades: {n} | Win rate: {wr}% | Avg PnL: {avg_pnl:+.2f}%",
                "",
                "## Notes",
                "- Research agent running every 4h (trigger on-demand via POST /api/orchestrator/trigger-research)",
            ]
            content = "\\n".join(lines)
            _orch_write_res(content)
            summary = f"Trades={n} | WR={wr}% | AvgPnL={avg_pnl:+.2f}%"
            _orch_mem("research", f"Digest updated | {summary}")
            _orch_hb("research")
            if not _tg_rl("research_update"):
                await _send_tg("Research Update", f"Market digest ready\\n{summary}", "\\U0001f4ca")
            log.info("[RESEARCH] %s", summary)
        except Exception as _re:
            log.debug("_research_agent_loop error: %s", _re)
        if once:
            break
        await asyncio.sleep(14400)   # 4h


'''

NEW3 = WATCHDOG_FUNC + OLD3
text = text.replace(OLD3, NEW3, 1)
assert text.count("async def _watchdog_agent_loop") == 1, "OP-3 watchdog error"
assert text.count("async def _research_agent_loop") == 1, "OP-3 research error"
print("OP-3: _watchdog_agent_loop + _research_agent_loop added ✓")

# ─────────────────────────────────────────────────────────────────────────────
# OP-4a: Register new tasks in lifespan()
# ─────────────────────────────────────────────────────────────────────────────
OLD4a = "    task_perf_alerts   = asyncio.create_task(_performance_alert_loop())"
assert text.count(OLD4a) == 1, f"OP-4a: expected 1, found {text.count(OLD4a)}"
NEW4a = (
    "    task_perf_alerts   = asyncio.create_task(_performance_alert_loop())\n"
    "    task_watchdog      = asyncio.create_task(_watchdog_agent_loop())   # Patch 91\n"
    "    task_research      = asyncio.create_task(_research_agent_loop())   # Patch 91"
)
text = text.replace(OLD4a, NEW4a)
assert text.count(NEW4a) == 1, "OP-4a replacement error"
print("OP-4a: tasks registered in lifespan ✓")

# ─────────────────────────────────────────────────────────────────────────────
# OP-4b: Add new tasks to cleanup tuple
# ─────────────────────────────────────────────────────────────────────────────
OLD4b = (
    "    all_tasks = (task_poller, task_tracker, task_perp_mon, task_perp_scan,\n"
    "                 task_scalp_mon, task_scalp_scan, task_mid_mon, task_mid_scan,\n"
    "                 task_spot_mon, task_spot_scan, task_wl_momentum, task_post_exit,\n"
    "                 task_weekly_report, task_perf_alerts, task_auto_simulate,\n"
    "                 task_daily_report)"
)
assert text.count(OLD4b) == 1, f"OP-4b: expected 1, found {text.count(OLD4b)}"
NEW4b = (
    "    all_tasks = (task_poller, task_tracker, task_perp_mon, task_perp_scan,\n"
    "                 task_scalp_mon, task_scalp_scan, task_mid_mon, task_mid_scan,\n"
    "                 task_spot_mon, task_spot_scan, task_wl_momentum, task_post_exit,\n"
    "                 task_weekly_report, task_perf_alerts, task_auto_simulate,\n"
    "                 task_daily_report,\n"
    "                 task_watchdog, task_research)  # Patch 91"
)
text = text.replace(OLD4b, NEW4b)
assert text.count(NEW4b) == 1, "OP-4b replacement error"
print("OP-4b: cleanup tuple updated ✓")

# ─────────────────────────────────────────────────────────────────────────────
# OP-5: Add heartbeat to SWING scanner (top of while loop)
# ─────────────────────────────────────────────────────────────────────────────
OLD5 = (
    "    while True:\n"
    "        await asyncio.sleep(120)  # 2 min\n"
    "        try:\n"
    "            perp_enabled = os.getenv(\"PERP_EXECUTOR_ENABLED\", \"false\").lower() == \"true\""
)
assert text.count(OLD5) == 1, f"OP-5: expected 1, found {text.count(OLD5)}"
NEW5 = (
    "    while True:\n"
    "        await asyncio.sleep(120)  # 2 min\n"
    "        _orch_hb(\"monitoring\")  # Patch 91\n"
    "        try:\n"
    "            perp_enabled = os.getenv(\"PERP_EXECUTOR_ENABLED\", \"false\").lower() == \"true\""
)
text = text.replace(OLD5, NEW5)
assert text.count(NEW5) == 1, "OP-5 replacement error"
print("OP-5: SWING heartbeat added ✓")

# ─────────────────────────────────────────────────────────────────────────────
# OP-6: Add heartbeat to SCALP scanner (top of while loop)
# ─────────────────────────────────────────────────────────────────────────────
OLD6 = (
    "        await asyncio.sleep(30)\n"
    "        try:\n"
    "            scalp_enabled = os.getenv(\"SCALP_ENABLED\", \"false\").lower() == \"true\""
)
assert text.count(OLD6) == 1, f"OP-6: expected 1, found {text.count(OLD6)}"
NEW6 = (
    "        await asyncio.sleep(30)\n"
    "        _orch_hb(\"scalp_scan\")  # Patch 91\n"
    "        try:\n"
    "            scalp_enabled = os.getenv(\"SCALP_ENABLED\", \"false\").lower() == \"true\""
)
text = text.replace(OLD6, NEW6)
assert text.count(NEW6) == 1, "OP-6 replacement error"
print("OP-6: SCALP heartbeat added ✓")

# ─────────────────────────────────────────────────────────────────────────────
# OP-7: Add heartbeat to performance alert loop
# ─────────────────────────────────────────────────────────────────────────────
OLD7 = (
    "        try:\n"
    "            await asyncio.get_event_loop().run_in_executor(None, _run_performance_alert_check)\n"
    "        except Exception as _e:\n"
    "            log.debug(\"_performance_alert_loop error: %s\", _e)\n"
    "        await asyncio.sleep(300)  # 5 minutes"
)
assert text.count(OLD7) == 1, f"OP-7: expected 1, found {text.count(OLD7)}"
NEW7 = (
    "        try:\n"
    "            await asyncio.get_event_loop().run_in_executor(None, _run_performance_alert_check)\n"
    "        except Exception as _e:\n"
    "            log.debug(\"_performance_alert_loop error: %s\", _e)\n"
    "        _orch_hb(\"alert\")  # Patch 91\n"
    "        await asyncio.sleep(300)  # 5 minutes"
)
text = text.replace(OLD7, NEW7)
assert text.count(NEW7) == 1, "OP-7 replacement error"
print("OP-7: alert loop heartbeat added ✓")

# ─────────────────────────────────────────────────────────────────────────────
# OP-8: Add MEMORY.md write after SWING LONG signal sent
# ─────────────────────────────────────────────────────────────────────────────
OLD8 = (
    "                        log.info(\"[SWING] -> LONG %s 4h=%.3f%% RSI=%s MACD=%s src=%s vol=%.1f\",\n"
    "                                 symbol, chg_4h, rsi, macd_cross, _signal_source, _vol_ratio)"
)
assert text.count(OLD8) == 1, f"OP-8: expected 1, found {text.count(OLD8)}"
NEW8 = (
    "                        log.info(\"[SWING] -> LONG %s 4h=%.3f%% RSI=%s MACD=%s src=%s vol=%.1f\",\n"
    "                                 symbol, chg_4h, rsi, macd_cross, _signal_source, _vol_ratio)\n"
    "                        _orch_mem(\"monitoring\", f\"SWING {symbol} LONG 4h={chg_4h:+.3f}% RSI={rsi} MACD_H={macd_hist} src={_signal_source}\")  # Patch 91"
)
text = text.replace(OLD8, NEW8)
assert text.count(NEW8) == 1, "OP-8 replacement error"
print("OP-8: SWING LONG MEMORY write added ✓")

# ─────────────────────────────────────────────────────────────────────────────
# OP-9: Add MEMORY.md write after SWING SHORT signal sent
# ─────────────────────────────────────────────────────────────────────────────
OLD9 = (
    "                        log.info(\"[SWING] -> SHORT %s 4h=%.3f%% RSI=%s MACD=%s src=%s vol=%.1f\",\n"
    "                                 symbol, chg_4h, rsi, macd_cross, _signal_source, _vol_ratio)"
)
assert text.count(OLD9) == 1, f"OP-9: expected 1, found {text.count(OLD9)}"
NEW9 = (
    "                        log.info(\"[SWING] -> SHORT %s 4h=%.3f%% RSI=%s MACD=%s src=%s vol=%.1f\",\n"
    "                                 symbol, chg_4h, rsi, macd_cross, _signal_source, _vol_ratio)\n"
    "                        _orch_mem(\"monitoring\", f\"SWING {symbol} SHORT 4h={chg_4h:+.3f}% RSI={rsi} MACD_H={macd_hist} src={_signal_source}\")  # Patch 91"
)
text = text.replace(OLD9, NEW9)
assert text.count(NEW9) == 1, "OP-9 replacement error"
print("OP-9: SWING SHORT MEMORY write added ✓")

# ─────────────────────────────────────────────────────────────────────────────
# OP-10: Add MEMORY.md write after SCALP LONG signal sent
# ─────────────────────────────────────────────────────────────────────────────
OLD10 = "                        log.info(\"[SCALP] -> LONG %s 5m=%.3f%% RSI=%s MACD=%s src=%s vol=%.1f\", symbol, chg_5m, rsi, macd_cross, _signal_source, _vol_ratio)"
assert text.count(OLD10) == 1, f"OP-10: expected 1, found {text.count(OLD10)}"
NEW10 = (
    "                        log.info(\"[SCALP] -> LONG %s 5m=%.3f%% RSI=%s MACD=%s src=%s vol=%.1f\", symbol, chg_5m, rsi, macd_cross, _signal_source, _vol_ratio)\n"
    "                        _orch_mem(\"monitoring\", f\"SCALP {symbol} LONG 5m={chg_5m:+.3f}% RSI={rsi} MACD_H={macd_hist} src={_signal_source}\")  # Patch 91"
)
text = text.replace(OLD10, NEW10)
assert text.count(NEW10) == 1, "OP-10 replacement error"
print("OP-10: SCALP LONG MEMORY write added ✓")

# ─────────────────────────────────────────────────────────────────────────────
# OP-11: Add MEMORY.md write after SCALP SHORT signal sent
# ─────────────────────────────────────────────────────────────────────────────
OLD11 = "                        log.info(\"[SCALP] -> SHORT %s 5m=%.3f%% RSI=%s MACD=%s src=%s vol=%.1f\", symbol, chg_5m, rsi, macd_cross, _signal_source, _vol_ratio)"
assert text.count(OLD11) == 1, f"OP-11: expected 1, found {text.count(OLD11)}"
NEW11 = (
    "                        log.info(\"[SCALP] -> SHORT %s 5m=%.3f%% RSI=%s MACD=%s src=%s vol=%.1f\", symbol, chg_5m, rsi, macd_cross, _signal_source, _vol_ratio)\n"
    "                        _orch_mem(\"monitoring\", f\"SCALP {symbol} SHORT 5m={chg_5m:+.3f}% RSI={rsi} MACD_H={macd_hist} src={_signal_source}\")  # Patch 91"
)
text = text.replace(OLD11, NEW11)
assert text.count(NEW11) == 1, "OP-11 replacement error"
print("OP-11: SCALP SHORT MEMORY write added ✓")

# ─────────────────────────────────────────────────────────────────────────────
# OP-12: Add 5 new API endpoints before /api/journal/learnings
# ─────────────────────────────────────────────────────────────────────────────
OLD12 = "@app.get(\"/api/journal/learnings\")"
assert text.count(OLD12) == 1, f"OP-12: expected 1, found {text.count(OLD12)}"
NEW12 = (
    """# ── Orchestrator API (Patch 91) ────────────────────────────────────────────

@app.get("/api/orchestrator/status")
async def orchestrator_status(_: str = Depends(get_current_user)):
    \"\"\"Return health status for all 7 named agents.\"\"\"
    from datetime import datetime as _dtos, timezone as _tzos
    return {"agents": _orch_status(), "ts": _dtos.now(_tzos.utc).isoformat() + "Z"}


@app.get("/api/orchestrator/memory")
async def orchestrator_memory(lines: int = 50, _: str = Depends(get_current_user)):
    \"\"\"Return the last N lines of MEMORY.md.\"\"\"
    return {"memory": _orch_read_mem(min(lines, 200))}


@app.get("/api/orchestrator/research")
async def orchestrator_research(_: str = Depends(get_current_user)):
    \"\"\"Return the latest RESEARCH.md content.\"\"\"
    return {"research": _orch_read_res()}


@app.post("/api/orchestrator/alert-test")
async def orchestrator_alert_test(_: str = Depends(get_current_user)):
    \"\"\"Send a test Telegram ping to verify alerting is working.\"\"\"
    ok = await _send_tg(
        "Abrons Engine \\u2014 Test Alert",
        "Orchestrator is live and watching 24/7 \\U0001f7e2\\nAll agents nominal.",
        "\\U0001f916"
    )
    return {"ok": ok, "message": "Telegram ping sent" if ok else "Telegram not configured (check TELEGRAM_TOKEN + TELEGRAM_CHAT_ID in .env)"}


@app.post("/api/orchestrator/trigger-research")
async def orchestrator_trigger_research(_: str = Depends(get_current_user)):
    \"\"\"Trigger an on-demand research cycle immediately.\"\"\"
    asyncio.create_task(_research_agent_loop(once=True))
    return {"ok": True, "message": "Research cycle triggered (results in RESEARCH.md within ~30s)"}


"""
    + OLD12
)
text = text.replace(OLD12, NEW12, 1)
assert text.count("async def orchestrator_status") == 1, "OP-12 endpoint error"
print("OP-12: 5 orchestrator endpoints added ✓")

# ─────────────────────────────────────────────────────────────────────────────
# Write + compile check
# ─────────────────────────────────────────────────────────────────────────────
MAIN.write_text(text)
r = subprocess.run(
    ["python3", "-m", "py_compile", str(MAIN)],
    capture_output=True, text=True
)
if r.returncode != 0:
    print("COMPILE ERROR:", r.stderr)
    raise SystemExit(1)
print("Patch 91: compile OK ✓")
print(f"main.py now {len(text.splitlines())} lines")
