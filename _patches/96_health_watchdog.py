#!/usr/bin/env python3
"""Patch 96 — Bug & Health Watchdog + Live Data Integrity Agents.

Adds two new 60s background agents to main.py:

1. _health_watchdog_loop()  — scans logs for errors, checks trade flow,
   detects EV_FILTER dominance, zero-ml_wp signals, endpoint failures.
   Logs HEALTH_CHECK to MEMORY.md. Telegrams on anomaly.

2. _data_integrity_loop()   — verifies Kraken / DexScreener / CryptoPanic
   feeds are returning fresh data (< 5 min old). Logs DATA_INTEGRITY_CHECK.
   Telegrams if any feed is stale or erroring.

Also adds 2 new API endpoints:
  GET /api/orchestrator/health          — latest health check result
  GET /api/orchestrator/data-integrity  — latest data integrity result

And extends the import block to expose set/get_health_status and
set/get_data_integrity_status from utils/orchestrator.py.
"""
from pathlib import Path
import subprocess, sys, re

# ── 0. Update utils/orchestrator.py on VPS to match local version ─────────────
ORC = Path("/root/memecoin_engine/utils/orchestrator.py")
orc_text = ORC.read_text()

# Add health_watchdog + data_integrity to agent registry
OLD_AGENTS = '''_agents: dict[str, dict] = {
    "monitoring": {"interval_s": 120,   "last_beat": 0.0, "status": "init"},
    "research":   {"interval_s": 14400, "last_beat": 0.0, "status": "init"},
    "trading":    {"interval_s": 60,    "last_beat": 0.0, "status": "init"},
    "watchdog":   {"interval_s": 300,   "last_beat": 0.0, "status": "init"},
    "optimizer":  {"interval_s": 86400, "last_beat": 0.0, "status": "init"},
    "scalp_scan": {"interval_s": 30,    "last_beat": 0.0, "status": "init"},
    "alert":      {"interval_s": 300,   "last_beat": 0.0, "status": "init"},
}

_mem_lock = threading.Lock()   # protect concurrent writes to MEMORY.md'''

NEW_AGENTS = '''_agents: dict[str, dict] = {
    "monitoring":     {"interval_s": 120,   "last_beat": 0.0, "status": "init"},
    "research":       {"interval_s": 14400, "last_beat": 0.0, "status": "init"},
    "trading":        {"interval_s": 60,    "last_beat": 0.0, "status": "init"},
    "watchdog":       {"interval_s": 300,   "last_beat": 0.0, "status": "init"},
    "optimizer":      {"interval_s": 86400, "last_beat": 0.0, "status": "init"},
    "scalp_scan":     {"interval_s": 30,    "last_beat": 0.0, "status": "init"},
    "alert":          {"interval_s": 300,   "last_beat": 0.0, "status": "init"},
    "health_watchdog":{"interval_s": 60,    "last_beat": 0.0, "status": "init"},  # Patch 96
    "data_integrity": {"interval_s": 60,    "last_beat": 0.0, "status": "init"},  # Patch 96
}

_mem_lock = threading.Lock()   # protect concurrent writes to MEMORY.md

# ── Health check & data integrity state (Patch 96) ────────────────────────────
_health_check: dict = {}
_data_integrity: dict = {}
_hc_lock = threading.Lock()
_di_lock = threading.Lock()'''

if OLD_AGENTS not in orc_text:
    # Already patched (local version already applied)
    print("Step 0a: orchestrator.py agents already updated — skipping")
else:
    orc_text = orc_text.replace(OLD_AGENTS, NEW_AGENTS)
    print("Step 0a: Added health_watchdog + data_integrity agents ✓")

# Add set/get functions before load_config
NEW_HEALTH_FUNCS = '''def set_health_status(result: dict) -> None:
    """Store the latest health check result (Patch 96)."""
    global _health_check
    with _hc_lock:
        _health_check = result


def get_health_status() -> dict:
    """Return the latest health check result (Patch 96)."""
    with _hc_lock:
        return _health_check.copy()


def set_data_integrity_status(result: dict) -> None:
    """Store the latest data integrity check result (Patch 96)."""
    global _data_integrity
    with _di_lock:
        _data_integrity = result


def get_data_integrity_status() -> dict:
    """Return the latest data integrity check result (Patch 96)."""
    with _di_lock:
        return _data_integrity.copy()


def load_config() -> dict:'''

if 'set_health_status' not in orc_text:
    assert orc_text.count('def load_config() -> dict:') == 1
    orc_text = orc_text.replace('def load_config() -> dict:', NEW_HEALTH_FUNCS)
    print("Step 0b: Added set/get health + data integrity functions ✓")
else:
    print("Step 0b: health functions already present — skipping")

ORC.write_text(orc_text)
r = subprocess.run(["python3", "-m", "py_compile", str(ORC)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR orchestrator.py:", r.stderr); sys.exit(1)
print("orchestrator.py compile OK ✓")

# ── 1. Patch main.py ───────────────────────────────────────────────────────────
MX = Path("/root/memecoin_engine/dashboard/backend/main.py")
text = MX.read_text()

# ── 1a. Extend orchestrator import block ──────────────────────────────────────
OLD_IMPORT = '''    from utils.orchestrator import (
        heartbeat as _orch_hb, append_memory as _orch_mem,
        get_status as _orch_status, write_research as _orch_write_res,
        read_memory as _orch_read_mem, read_research as _orch_read_res,
    )'''

NEW_IMPORT = '''    from utils.orchestrator import (
        heartbeat as _orch_hb, append_memory as _orch_mem,
        get_status as _orch_status, write_research as _orch_write_res,
        read_memory as _orch_read_mem, read_research as _orch_read_res,
        set_health_status as _orch_set_health, get_health_status as _orch_get_health,   # Patch 96
        set_data_integrity_status as _orch_set_di, get_data_integrity_status as _orch_get_di,  # Patch 96
    )'''

assert text.count(OLD_IMPORT) == 1, f"Step 1a: {text.count(OLD_IMPORT)} matches"
text = text.replace(OLD_IMPORT, NEW_IMPORT)
# Also update fallback stubs
OLD_FALLBACK = '''    def _orch_read_res(): return ''
    async def _send_tg'''
NEW_FALLBACK = '''    def _orch_read_res(): return ''
    def _orch_set_health(r): pass
    def _orch_get_health(): return {}
    def _orch_set_di(r): pass
    def _orch_get_di(): return {}
    async def _send_tg'''
assert text.count(OLD_FALLBACK) == 1, f"Step 1a stub: {text.count(OLD_FALLBACK)} matches"
text = text.replace(OLD_FALLBACK, NEW_FALLBACK)
print("Step 1a: Extended orchestrator imports ✓")

# ── 1b. Add two new loop functions before lifespan ────────────────────────────
NEW_LOOPS = '''async def _health_watchdog_loop():
    """Patch 96 — Bug & Health Watchdog: 60s checks on trade flow, ML quality, log errors."""
    import sqlite3 as _sql96, subprocess as _sp96
    from datetime import timedelta as _td96
    _db96 = "/root/memecoin_engine/data_storage/engine.db"
    await asyncio.sleep(30)   # let other loops start first
    while True:
        try:
            result = {"ts": datetime.utcnow().isoformat() + "Z", "status": "ok", "checks": {}, "alerts": []}

            # 1. Trades in last 4h
            with _sql96.connect(_db96) as _c:
                _since4h = (datetime.utcnow() - _td96(hours=4)).isoformat()
                _t4h = _c.execute(
                    "SELECT COUNT(*) FROM perp_positions WHERE opened_ts_utc >= ?", (_since4h,)
                ).fetchone()[0]
            result["checks"]["trades_4h"] = _t4h
            if _t4h == 0:
                result["alerts"].append("STALE_TRADES: 0 trades opened in last 4h")

            # 2. Open positions count
            with _sql96.connect(_db96) as _c:
                _open = _c.execute("SELECT COUNT(*) FROM perp_positions WHERE status='OPEN'").fetchone()[0]
            result["checks"]["open_positions"] = _open

            # 3. EV_FILTER dominance check (last 30 min)
            with _sql96.connect(_db96) as _c:
                _since30 = (datetime.utcnow() - _td96(minutes=30)).isoformat()
                _total_skip = _c.execute(
                    "SELECT COUNT(*) FROM skipped_signals_log WHERE ts_utc >= ?", (_since30,)
                ).fetchone()[0]
                _ev_skip = _c.execute(
                    "SELECT COUNT(*) FROM skipped_signals_log WHERE ts_utc >= ? AND skip_reason LIKE 'EV_FILTER%'",
                    (_since30,)
                ).fetchone()[0]
            result["checks"]["skips_30m"] = _total_skip
            result["checks"]["ev_filter_skips_30m"] = _ev_skip
            if _total_skip > 5 and _ev_skip / _total_skip > 0.9:
                result["alerts"].append(
                    f"EV_FILTER_DOMINANCE: {_ev_skip}/{_total_skip} skips are EV_FILTER in 30m"
                )

            # 4. Zero ml_wp signals check (last 30 min)
            with _sql96.connect(_db96) as _c:
                _zero_wp = _c.execute(
                    "SELECT COUNT(*) FROM skipped_signals_log WHERE ts_utc >= ? AND ml_wp = 0 AND mode = 'SWING'",
                    (_since30,)
                ).fetchone()[0]
            result["checks"]["zero_ml_wp_swing_30m"] = _zero_wp
            if _zero_wp > 15:
                result["alerts"].append(
                    f"ZERO_ML_WP: {_zero_wp} SWING signals with ml_wp=0 in 30m — ML predictor may be cold"
                )

            # 5. Scan service logs for ERRORs in last 60s
            try:
                _proc = _sp96.run(
                    ["journalctl", "-u", "memecoin-dashboard", "--since", "60 seconds ago",
                     "-n", "200", "--no-pager"],
                    capture_output=True, text=True, timeout=6
                )
                _err_lines = [l for l in _proc.stdout.splitlines()
                              if " ERROR " in l or " CRITICAL " in l or "Traceback" in l]
                result["checks"]["log_errors_60s"] = len(_err_lines)
                if _err_lines:
                    result["alerts"].append(f"LOG_ERRORS: {len(_err_lines)} ERROR/CRITICAL in last 60s")
                    result["checks"]["error_samples"] = [l[-120:] for l in _err_lines[:3]]
            except Exception:
                pass

            # Finalize
            if result["alerts"]:
                result["status"] = "warning"
            _orch_set_health(result)
            _orch_hb("health_watchdog")
            _alert_count = len(result["alerts"])
            _orch_mem("health_watchdog",
                      f"HEALTH_CHECK: {result['status'].upper()} | trades_4h={_t4h} | open={_open} | "
                      f"skips_30m={_total_skip} | ev_dom={_ev_skip} | zero_wp={_zero_wp} | alerts={_alert_count}")
            if result["alerts"]:
                _body = "\\n".join(result["alerts"])
                await _send_tg("⚠️ Health Watchdog", _body, "⚠️")
        except Exception as _he:
            log.debug("_health_watchdog_loop error: %s", _he)
        await asyncio.sleep(60)


async def _data_integrity_loop():
    """Patch 96 — Live Data Integrity Agent: 60s feed freshness check."""
    import requests as _req96, time as _t96
    await asyncio.sleep(45)   # stagger startup
    while True:
        try:
            result = {"ts": datetime.utcnow().isoformat() + "Z", "feeds": {}, "alerts": []}

            # 1. Kraken OHLC — fetch SOL 5m candle, check age
            try:
                _r = _req96.get(
                    "https://api.kraken.com/0/public/OHLC?pair=SOLUSD&interval=5",
                    timeout=6
                )
                _data = _r.json()
                _candles = list(_data.get("result", {}).values())[0] if _data.get("result") else []
                if _candles:
                    _last_ts = int(_candles[-1][0])
                    _age = int(_t96.time()) - _last_ts
                    _status = "ok" if _age < 300 else "stale"
                    result["feeds"]["kraken"] = {"age_s": _age, "status": _status}
                    if _age >= 300:
                        result["alerts"].append(f"KRAKEN_STALE: Last candle is {_age}s old (>{300}s)")
                else:
                    result["feeds"]["kraken"] = {"status": "empty"}
                    result["alerts"].append("KRAKEN_EMPTY: Kraken OHLC returned no candles")
            except Exception as _ke:
                result["feeds"]["kraken"] = {"status": "error", "error": str(_ke)[:80]}
                result["alerts"].append(f"KRAKEN_ERROR: {str(_ke)[:60]}")

            # 2. DexScreener — check API reachability
            try:
                _r2 = _req96.get(
                    "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112",
                    timeout=6
                )
                if _r2.status_code == 200:
                    result["feeds"]["dexscreener"] = {"status": "ok", "code": 200}
                else:
                    result["feeds"]["dexscreener"] = {"status": "error", "code": _r2.status_code}
                    result["alerts"].append(f"DEXSCREENER_ERROR: HTTP {_r2.status_code}")
            except Exception as _de:
                result["feeds"]["dexscreener"] = {"status": "error", "error": str(_de)[:80]}
                result["alerts"].append(f"DEXSCREENER_ERROR: {str(_de)[:60]}")

            # 3. CryptoPanic — only if API key configured
            _cp_key = os.environ.get("CRYPTOPANIC_API_KEY", "")
            if _cp_key:
                try:
                    _r3 = _req96.get(
                        f"https://cryptopanic.com/api/v1/posts/?auth_token={_cp_key}&currencies=BTC&public=true",
                        timeout=6
                    )
                    _st3 = "ok" if _r3.status_code == 200 else "error"
                    result["feeds"]["cryptopanic"] = {"status": _st3, "code": _r3.status_code}
                    if _r3.status_code != 200:
                        result["alerts"].append(f"CRYPTOPANIC_ERROR: HTTP {_r3.status_code}")
                except Exception as _ce:
                    result["feeds"]["cryptopanic"] = {"status": "error", "error": str(_ce)[:80]}
                    result["alerts"].append(f"CRYPTOPANIC_ERROR: {str(_ce)[:60]}")
            else:
                result["feeds"]["cryptopanic"] = {"status": "not_configured"}

            _orch_set_di(result)
            _orch_hb("data_integrity")
            _ok_count = sum(1 for f in result["feeds"].values() if f.get("status") == "ok")
            _orch_mem("data_integrity",
                      f"DATA_INTEGRITY_CHECK: {_ok_count}/{len(result['feeds'])} feeds OK | alerts={len(result['alerts'])}")
            if result["alerts"]:
                _body2 = "\\n".join(result["alerts"])
                await _send_tg("📡 Data Integrity", _body2, "📡")
        except Exception as _die:
            log.debug("_data_integrity_loop error: %s", _die)
        await asyncio.sleep(60)


'''

LOOP_ANCHOR = "async def _watchdog_agent_loop():"
assert text.count(LOOP_ANCHOR) == 1, f"Step 1b: {text.count(LOOP_ANCHOR)} matches for loop anchor"
text = text.replace(LOOP_ANCHOR, NEW_LOOPS + LOOP_ANCHOR)
print("Step 1b: Added _health_watchdog_loop() and _data_integrity_loop() ✓")

# ── 1c. Register new tasks in lifespan ────────────────────────────────────────
OLD_TASKS = '    task_watchdog      = asyncio.create_task(_watchdog_agent_loop())   # Patch 91\n    task_research      = asyncio.create_task(_research_agent_loop())   # Patch 91'
NEW_TASKS = ('    task_watchdog      = asyncio.create_task(_watchdog_agent_loop())   # Patch 91\n'
             '    task_research      = asyncio.create_task(_research_agent_loop())   # Patch 91\n'
             '    task_health_wdg    = asyncio.create_task(_health_watchdog_loop())  # Patch 96\n'
             '    task_data_integ    = asyncio.create_task(_data_integrity_loop())   # Patch 96')
assert text.count(OLD_TASKS) == 1, f"Step 1c: {text.count(OLD_TASKS)} matches for task registration"
text = text.replace(OLD_TASKS, NEW_TASKS)
print("Step 1c: Registered tasks in lifespan ✓")

# ── 1d. Add new tasks to cleanup tuple ────────────────────────────────────────
OLD_CLEANUP = '                 task_watchdog, task_research)  # Patch 91'
NEW_CLEANUP = '                 task_watchdog, task_research,  # Patch 91\n                 task_health_wdg, task_data_integ)  # Patch 96'
assert text.count(OLD_CLEANUP) == 1, f"Step 1d: {text.count(OLD_CLEANUP)} matches for cleanup"
text = text.replace(OLD_CLEANUP, NEW_CLEANUP)
print("Step 1d: Added new tasks to cleanup tuple ✓")

# ── 1e. Add two new endpoints before /api/journal/learnings ───────────────────
NEW_ENDPOINTS = '''@app.get("/api/orchestrator/health")
async def orchestrator_health(_: str = Depends(get_current_user)):
    """Return latest health watchdog check result (Patch 96)."""
    result = _orch_get_health()
    if not result:
        return {"status": "init", "ts": datetime.utcnow().isoformat() + "Z",
                "checks": {}, "alerts": [], "message": "Health watchdog starting up..."}
    return result


@app.get("/api/orchestrator/data-integrity")
async def orchestrator_data_integrity(_: str = Depends(get_current_user)):
    """Return latest data integrity check result (Patch 96)."""
    result = _orch_get_di()
    if not result:
        return {"status": "init", "ts": datetime.utcnow().isoformat() + "Z",
                "feeds": {}, "alerts": [], "message": "Data integrity agent starting up..."}
    return result


@app.get("/api/journal/learnings")'''

ENDPOINT_ANCHOR = '@app.get("/api/journal/learnings")'
assert text.count(ENDPOINT_ANCHOR) == 1, f"Step 1e: {text.count(ENDPOINT_ANCHOR)} matches"
# NEW_ENDPOINTS already ends with the anchor, so this inserts the new endpoints before it
text = text.replace(ENDPOINT_ANCHOR, NEW_ENDPOINTS)
print("Step 1e: Added /api/orchestrator/health and /api/orchestrator/data-integrity endpoints ✓")

MX.write_text(text)

r = subprocess.run(["python3", "-m", "py_compile", str(MX)], capture_output=True, text=True)
if r.returncode != 0:
    print("COMPILE ERROR main.py:", r.stderr); sys.exit(1)
print("main.py compile OK ✓")

print("\nPatch 96 complete: Bug & Health Watchdog + Data Integrity agents live 🟢")
print("  • _health_watchdog_loop() — 60s: trades_4h, EV dominance, zero_ml_wp, log errors")
print("  • _data_integrity_loop()  — 60s: Kraken freshness, DexScreener, CryptoPanic")
print("  • GET /api/orchestrator/health          — latest health check")
print("  • GET /api/orchestrator/data-integrity  — latest feed status")
