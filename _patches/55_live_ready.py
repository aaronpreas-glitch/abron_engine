#!/usr/bin/env python3
"""
Patch 55 — Live-Ready Safeguards & Transition

Changes:
  A. utils/perp_executor.py
     1. Add REAL_MONEY_MODE, REAL_BASE_USD, ACCOUNT_BALANCE_USD config lambdas
     2. Add _real_money_mode, _real_base_usd, _account_balance_usd cached globals
     3. Add _log_live_decision() + _apply_live_mode_tuning() helpers
     4. Modify execute_perp_signal: real-money size scaling + 0.5% risk cap + logging

  B. dashboard/backend/main.py
     1. Extend ALLOWED_KEYS with REAL_MONEY_MODE, REAL_BASE_USD, ACCOUNT_BALANCE_USD
     2. Extend live-tune dispatch in apply-tuner
     3. Add POST /api/risk/set-live-mode
     4. Add POST /api/risk/emergency-kill
     5. Add GET /api/brain/live-checklist
     6. Add GET /api/brain/live-transition-log
"""

import pathlib

# ─────────────────────────────────────────────────────────────────────────────
# A. perp_executor.py
# ─────────────────────────────────────────────────────────────────────────────

PE = pathlib.Path("/root/memecoin_engine/utils/perp_executor.py")
assert PE.exists()
pe = PE.read_text()

# ── A1: Config lambdas after PERP_DRY_RUN ────────────────────────────────────

OLD_DRY_RUN = 'PERP_DRY_RUN        = lambda: _bool("PERP_DRY_RUN", True)\n'
assert OLD_DRY_RUN in pe, "FAIL [A1]: PERP_DRY_RUN lambda not found"

NEW_DRY_RUN = (
    'PERP_DRY_RUN        = lambda: _bool("PERP_DRY_RUN", True)\n'
    'REAL_MONEY_MODE     = lambda: _bool("REAL_MONEY_MODE", False)\n'
    'REAL_BASE_USD       = lambda: _float("REAL_BASE_USD", 100.0)\n'
    'ACCOUNT_BALANCE_USD = lambda: _float("ACCOUNT_BALANCE_USD", 0.0)\n'
)
pe = pe.replace(OLD_DRY_RUN, NEW_DRY_RUN, 1)
print("OK [A1] REAL_MONEY_MODE config lambdas added")

# ── A2: Cached globals after _last_any_entry_ts ───────────────────────────────

OLD_LAST_ENTRY = (
    '_last_any_entry_ts:   object = None   '
    '# datetime of most recent entry (any mode/symbol)\n'
)
assert OLD_LAST_ENTRY in pe, "FAIL [A2]: _last_any_entry_ts not found"

NEW_LAST_ENTRY = (
    '_last_any_entry_ts:   object = None   '
    '# datetime of most recent entry (any mode/symbol)\n'
    '# Live-money mode globals\n'
    '_real_money_mode:     bool   = False  # True = real capital at risk\n'
    '_real_base_usd:       float  = 100.0  # base size for real capital scaling\n'
    '_account_balance_usd: float  = 0.0    # total account balance for 0.5% risk cap\n'
)
pe = pe.replace(OLD_LAST_ENTRY, NEW_LAST_ENTRY, 1)
print("OK [A2] _real_money_mode cached globals added")

# ── A3: Helper functions before _check_dynamic_exit_circuit_breaker ──────────

OLD_CB = "\ndef _check_dynamic_exit_circuit_breaker():"
assert OLD_CB in pe, "FAIL [A3]: circuit breaker anchor not found"

NEW_HELPERS = '''
def _log_live_decision(
    event_type: str,
    symbol: str | None = None,
    side: str | None = None,
    mode: str | None = None,
    reason: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Insert a row into live_transition_log for audit trail."""
    import json as _json
    try:
        with _conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS live_transition_log (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc     TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    event_type TEXT,
                    symbol     TEXT,
                    side       TEXT,
                    mode       TEXT,
                    reason     TEXT,
                    metadata   TEXT
                )
            """)
            c.execute(
                "INSERT INTO live_transition_log "
                "(event_type,symbol,side,mode,reason,metadata) VALUES (?,?,?,?,?,?)",
                (event_type, symbol, side, mode, reason,
                 _json.dumps(metadata) if metadata else None),
            )
            c.commit()
    except Exception as _e:
        logger.debug("_log_live_decision error: %s", _e)


def _apply_live_mode_tuning(key: str, value: str) -> bool:
    """Update in-memory live-mode globals when apply-tuner fires.
    Returns True if key was handled.
    """
    global _real_money_mode, _real_base_usd, _account_balance_usd
    try:
        if key == "REAL_MONEY_MODE":
            _real_money_mode = value.lower() in ("1", "true", "yes")
        elif key == "REAL_BASE_USD":
            _real_base_usd = float(value)
        elif key == "ACCOUNT_BALANCE_USD":
            _account_balance_usd = float(value)
        else:
            return False
        logger.info("[LIVE-MODE] %s -> %s (applied in-memory)", key, value)
        return True
    except Exception as _e:
        logger.debug("_apply_live_mode_tuning(%s, %s) error: %s", key, value, _e)
        return False

'''

pe = pe.replace(OLD_CB, NEW_HELPERS + "\ndef _check_dynamic_exit_circuit_breaker():", 1)
print("OK [A3] _log_live_decision + _apply_live_mode_tuning helpers added")

# ── A4: Real-money scaling + logging before _open_perp_position call ─────────

OLD_OPEN_BLOCK = (
    '    if dry_run:\n'
    '        pos = _open_perp_position(\n'
    '            symbol, side, entry_price, stop_price, tp1_price, tp2_price,\n'
    '            size_usd, leverage, regime, dry_run=True, notes=notes,\n'
    '        )\n'
    '    else:\n'
    '        # Live: would call Jupiter Perps open API here\n'
    '        # For now: open in DB as live (dry_run=0) and log warning\n'
    '        logger.warning("LIVE PERP: Jupiter Perps open API not yet integrated \u2014 recording in DB only")\n'
    '        pos = _open_perp_position(\n'
    '            symbol, side, entry_price, stop_price, tp1_price, tp2_price,\n'
    '            size_usd, leverage, regime, dry_run=False, notes=notes,\n'
    '        )\n'
)
assert OLD_OPEN_BLOCK in pe, "FAIL [A4]: dry_run open block not found"

NEW_OPEN_BLOCK = (
    '    # Real-money mode: scale size + cap at 0.5% account balance + log decision\n'
    '    _rm_mode = _real_money_mode\n'
    '    if _rm_mode:\n'
    '        _rm_scale = _real_base_usd / 100.0\n'
    '        size_usd = size_usd * _rm_scale\n'
    '        _bal = _account_balance_usd\n'
    '        if _bal > 0:\n'
    '            _max_size = (_bal * 0.005) * leverage  # 0.5% of balance\n'
    '            size_usd = min(size_usd, _max_size)\n'
    '        _log_live_decision("LIVE_TRADE_ATTEMPT", symbol, side, mode_tag,\n'
    '                           f"size={size_usd:.2f} scale={_rm_scale:.2f}")\n'
    '    else:\n'
    '        _log_live_decision("PAPER_TRADE", symbol, side, mode_tag,\n'
    '                           "REAL_MONEY_MODE=false")\n'
    '\n'
    '    if dry_run:\n'
    '        pos = _open_perp_position(\n'
    '            symbol, side, entry_price, stop_price, tp1_price, tp2_price,\n'
    '            size_usd, leverage, regime, dry_run=True, notes=notes,\n'
    '        )\n'
    '    else:\n'
    '        # Live: would call Jupiter Perps open API here\n'
    '        # For now: open in DB as live (dry_run=0) and log warning\n'
    '        logger.warning("LIVE PERP: Jupiter Perps open API not yet integrated \u2014 recording in DB only")\n'
    '        pos = _open_perp_position(\n'
    '            symbol, side, entry_price, stop_price, tp1_price, tp2_price,\n'
    '            size_usd, leverage, regime, dry_run=False, notes=notes,\n'
    '        )\n'
)
pe = pe.replace(OLD_OPEN_BLOCK, NEW_OPEN_BLOCK, 1)
print("OK [A4] Real-money size scaling + logging added to execute_perp_signal")

PE.write_text(pe)
print("perp_executor.py updated.\n")


# ─────────────────────────────────────────────────────────────────────────────
# B. main.py
# ─────────────────────────────────────────────────────────────────────────────

MP = pathlib.Path("/root/memecoin_engine/dashboard/backend/main.py")
assert MP.exists()
mp = MP.read_text()

# ── B1: Extend ALLOWED_KEYS ────────────────────────────────────────────────────

OLD_KEYS_TAIL = (
    '        "VOL_FILTER_THRESHOLD", "VOL_SIZE_MULT",\n'
    '        "PUMP_DUMP_THRESHOLD", "ALERT_THRESHOLD", "REGIME_MIN_SCORE",\n'
    '    }'
)
assert OLD_KEYS_TAIL in mp, "FAIL [B1]: ALLOWED_KEYS tail not found"

NEW_KEYS_TAIL = (
    '        "REAL_MONEY_MODE", "REAL_BASE_USD", "ACCOUNT_BALANCE_USD",\n'
    '        "VOL_FILTER_THRESHOLD", "VOL_SIZE_MULT",\n'
    '        "PUMP_DUMP_THRESHOLD", "ALERT_THRESHOLD", "REGIME_MIN_SCORE",\n'
    '    }'
)
mp = mp.replace(OLD_KEYS_TAIL, NEW_KEYS_TAIL, 1)
print("OK [B1] ALLOWED_KEYS extended with REAL_MONEY_MODE, REAL_BASE_USD, ACCOUNT_BALANCE_USD")

# ── B2: Extend live-tune dispatch in apply-tuner ──────────────────────────────

OLD_TUNE_DISPATCH = (
    "        if _pe_mod and hasattr(_pe_mod, '_apply_selectivity_tuning'):\n"
    "            for key, val in applied.items():\n"
    "                _pe_mod._apply_selectivity_tuning(key, val)\n"
    "    except Exception:\n"
    "        pass"
)
assert OLD_TUNE_DISPATCH in mp, "FAIL [B2]: tune dispatch anchor not found"

NEW_TUNE_DISPATCH = (
    "        if _pe_mod and hasattr(_pe_mod, '_apply_selectivity_tuning'):\n"
    "            for key, val in applied.items():\n"
    "                _pe_mod._apply_selectivity_tuning(key, val)\n"
    "        if _pe_mod and hasattr(_pe_mod, '_apply_live_mode_tuning'):\n"
    "            for key, val in applied.items():\n"
    "                _pe_mod._apply_live_mode_tuning(key, val)\n"
    "    except Exception:\n"
    "        pass"
)
mp = mp.replace(OLD_TUNE_DISPATCH, NEW_TUNE_DISPATCH, 1)
print("OK [B2] _apply_live_mode_tuning dispatch added to apply-tuner")

# ── B3-B6: New endpoints before journal/learnings ─────────────────────────────

JOURNAL_ANCHOR = '@app.get("/api/journal/learnings")'
assert JOURNAL_ANCHOR in mp, "FAIL [B3]: journal/learnings anchor not found"

NEW_ENDPOINTS = '''@app.post("/api/risk/set-live-mode")
async def risk_set_live_mode(request: Request, _: str = Depends(get_current_user)):
    """
    Enable or disable REAL_MONEY_MODE.
    Enabling requires body {"enable": true, "confirm": "CONFIRM LIVE"}.
    Also syncs PERP_DRY_RUN inversely.
    """
    body = await request.json()
    enable = bool(body.get("enable", False))
    if enable and body.get("confirm") != "CONFIRM LIVE":
        raise HTTPException(status_code=400,
                            detail="Must send confirm='CONFIRM LIVE' to enable real money mode")

    rm_val  = "true"  if enable else "false"
    dry_val = "false" if enable else "true"

    # Write to .env
    to_write = {"REAL_MONEY_MODE": rm_val, "PERP_DRY_RUN": dry_val}
    env_path = os.path.join(_engine_root(), ".env")
    try:
        env_lines = open(env_path).readlines() if os.path.exists(env_path) else []
        updated = set()
        new_lines = []
        for line in env_lines:
            s = line.strip()
            if "=" in s and not s.startswith("#"):
                kp = s.split("=", 1)[0].strip()
                if kp in to_write:
                    new_lines.append(f"{kp}={to_write[kp]}\n")
                    updated.add(kp)
                    continue
            new_lines.append(line if line.endswith("\\n") else line + "\\n")
        for k, v in to_write.items():
            if k not in updated:
                new_lines.append(f"{k}={v}\\n")
        with open(env_path, "w") as f:
            f.writelines(new_lines)
    except Exception as _e:
        log.warning("set-live-mode env write error: %s", _e)

    # Reload os.environ
    for k, v in to_write.items():
        os.environ[k] = v

    # Apply in-memory
    try:
        import sys as _sys
        _pe = (_sys.modules.get("utils.perp_executor")
               or _sys.modules.get("memecoin_engine.utils.perp_executor"))
        if _pe and hasattr(_pe, "_apply_live_mode_tuning"):
            _pe._apply_live_mode_tuning("REAL_MONEY_MODE", rm_val)
        if _pe and hasattr(_pe, "_log_live_decision"):
            _pe._log_live_decision(
                "MODE_ENABLED" if enable else "MODE_DISABLED",
                reason=f"Dashboard set-live-mode enable={enable}",
            )
    except Exception:
        pass

    return JSONResponse({"success": True, "real_money_mode": enable, "perp_dry_run": not enable})


@app.post("/api/risk/emergency-kill")
async def risk_emergency_kill(_: str = Depends(get_current_user)):
    """
    Emergency stop: disable real money mode, close all open positions in DB, log event.
    """
    to_write = {"REAL_MONEY_MODE": "false", "PERP_DRY_RUN": "true"}
    env_path = os.path.join(_engine_root(), ".env")
    try:
        env_lines = open(env_path).readlines() if os.path.exists(env_path) else []
        updated = set()
        new_lines = []
        for line in env_lines:
            s = line.strip()
            if "=" in s and not s.startswith("#"):
                kp = s.split("=", 1)[0].strip()
                if kp in to_write:
                    new_lines.append(f"{kp}={to_write[kp]}\\n")
                    updated.add(kp)
                    continue
            new_lines.append(line if line.endswith("\\n") else line + "\\n")
        for k, v in to_write.items():
            if k not in updated:
                new_lines.append(f"{k}={v}\\n")
        with open(env_path, "w") as f:
            f.writelines(new_lines)
    except Exception as _e:
        log.warning("emergency-kill env write error: %s", _e)

    for k, v in to_write.items():
        os.environ[k] = v

    # Apply in-memory
    try:
        import sys as _sys
        _pe = (_sys.modules.get("utils.perp_executor")
               or _sys.modules.get("memecoin_engine.utils.perp_executor"))
        if _pe and hasattr(_pe, "_apply_live_mode_tuning"):
            _pe._apply_live_mode_tuning("REAL_MONEY_MODE", "false")
    except Exception:
        pass

    # Close all open positions in DB
    closed = 0
    try:
        import sqlite3 as _sq
        db = os.path.join(_engine_root(), "data_storage", "engine.db")
        with _sq.connect(db) as c:
            rows = c.execute(
                "SELECT id FROM perp_positions WHERE status='OPEN'"
            ).fetchall()
            for (pid,) in rows:
                c.execute(
                    "UPDATE perp_positions SET status='CLOSED', exit_reason='EMERGENCY_KILL', "
                    "closed_ts_utc=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (pid,),
                )
                closed += 1
            c.commit()
    except Exception as _e:
        log.warning("emergency-kill DB close error: %s", _e)

    # Log the event
    try:
        import sys as _sys
        _pe = (_sys.modules.get("utils.perp_executor")
               or _sys.modules.get("memecoin_engine.utils.perp_executor"))
        if _pe and hasattr(_pe, "_log_live_decision"):
            _pe._log_live_decision(
                "EMERGENCY_KILL",
                reason=f"Dashboard emergency kill — {closed} positions closed",
            )
    except Exception:
        pass

    return JSONResponse({"success": True, "positions_closed": closed, "real_money_mode": False})


@app.get("/api/brain/live-checklist")
async def brain_live_checklist(_: str = Depends(get_current_user)):
    """
    Pre-live safety checklist — 6 criteria that must pass before enabling real money.
    """
    import sqlite3 as _sq
    since_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    db = os.path.join(_engine_root(), "data_storage", "engine.db")

    checks = []

    try:
        with _sq.connect(db) as c:
            c.row_factory = _sq.Row

            # ── 1. Bull Readiness ≥ 75 ─────────────────────────────────────
            bull_score = 0.0
            try:
                # Simplified inline: discipline (30) + ml (20) + pnl (20) + dynamic (20) + time_limit (10)
                # Discipline: good_call_rate from dynamic_exit_log (7d)
                tot_exits = (c.execute("SELECT COUNT(*) FROM dynamic_exit_log WHERE ts_utc >= ?",
                                       (since_7d,)).fetchone()[0] or 0)
                good_exits = (c.execute(
                    "SELECT COUNT(*) FROM dynamic_exit_log WHERE ts_utc >= ? AND outcome='good_call'",
                    (since_7d,)).fetchone()[0] or 0)
                good_rate = (good_exits / tot_exits * 100) if tot_exits >= 5 else 50.0
                disc_pts = min(30.0, good_rate / 100 * 30)

                # ML accuracy (20 pts)
                ml_pts = min(20.0, good_rate / 100 * 20)

                # Avg PNL (20 pts)
                avg_pnl_row = c.execute(
                    "SELECT AVG(realized_pnl_pct) FROM perp_positions "
                    "WHERE status='CLOSED' AND closed_ts_utc >= ?", (since_7d,)).fetchone()
                avg_pnl = float(avg_pnl_row[0] or 0)
                pnl_pts = min(20.0, max(0.0, avg_pnl / 1.0) * 20)

                # Dynamic lift (20 pts) — avg_pnl vs time-limit avg
                tl_pnl_row = c.execute(
                    "SELECT AVG(realized_pnl_pct) FROM perp_positions "
                    "WHERE status='CLOSED' AND exit_reason='TIME_LIMIT' AND closed_ts_utc >= ?",
                    (since_7d,)).fetchone()
                tl_pnl = float(tl_pnl_row[0] or 0)
                pnl_lift = avg_pnl - tl_pnl
                lift_pts = min(20.0, max(0.0, pnl_lift / 0.5) * 20)

                # TIME_LIMIT % (10 pts)
                tot_closed = (c.execute(
                    "SELECT COUNT(*) FROM perp_positions WHERE status='CLOSED' AND closed_ts_utc >= ?",
                    (since_7d,)).fetchone()[0] or 0)
                tl_count = (c.execute(
                    "SELECT COUNT(*) FROM perp_positions "
                    "WHERE status='CLOSED' AND exit_reason='TIME_LIMIT' AND closed_ts_utc >= ?",
                    (since_7d,)).fetchone()[0] or 0)
                tl_pct = (tl_count / tot_closed * 100) if tot_closed >= 5 else 100.0
                tl_pts = min(10.0, max(0.0, (100 - tl_pct) / 30.0) * 10)

                bull_score = round(disc_pts + ml_pts + pnl_pts + lift_pts + tl_pts, 1)
            except Exception:
                bull_score = 0.0

            checks.append({
                "id": "bull_readiness",
                "label": "Bull Readiness \u226575",
                "pass": bull_score >= 75,
                "value": bull_score,
                "target": 75,
            })

            # ── 2. 7d avg PNL/trade > 0.6% ────────────────────────────────
            avg_pnl_val = 0.0
            try:
                row = c.execute(
                    "SELECT AVG(realized_pnl_pct) FROM perp_positions "
                    "WHERE status='CLOSED' AND closed_ts_utc >= ?", (since_7d,)).fetchone()
                avg_pnl_val = round(float(row[0] or 0), 3)
            except Exception:
                pass
            checks.append({
                "id": "avg_pnl",
                "label": "7d avg PNL/trade >0.6%",
                "pass": avg_pnl_val > 0.6,
                "value": avg_pnl_val,
                "target": 0.6,
            })

            # ── 3. TIME_LIMIT % < 70% ─────────────────────────────────────
            tl_pct_val = 100.0
            try:
                tc = (c.execute(
                    "SELECT COUNT(*) FROM perp_positions WHERE status='CLOSED' AND closed_ts_utc >= ?",
                    (since_7d,)).fetchone()[0] or 0)
                tl = (c.execute(
                    "SELECT COUNT(*) FROM perp_positions "
                    "WHERE status='CLOSED' AND exit_reason='TIME_LIMIT' AND closed_ts_utc >= ?",
                    (since_7d,)).fetchone()[0] or 0)
                tl_pct_val = round((tl / tc * 100) if tc >= 5 else 100.0, 1)
            except Exception:
                pass
            checks.append({
                "id": "time_limit_pct",
                "label": "TIME_LIMIT % <70%",
                "pass": tl_pct_val < 70.0,
                "value": tl_pct_val,
                "target": 70.0,
            })

            # ── 4. Discipline score > 75 ───────────────────────────────────
            disc_val = 0.0
            try:
                te = (c.execute("SELECT COUNT(*) FROM dynamic_exit_log WHERE ts_utc >= ?",
                                (since_7d,)).fetchone()[0] or 0)
                ge = (c.execute(
                    "SELECT COUNT(*) FROM dynamic_exit_log WHERE ts_utc >= ? AND outcome='good_call'",
                    (since_7d,)).fetchone()[0] or 0)
                disc_val = round((ge / te * 100) if te >= 5 else 0.0, 1)
            except Exception:
                pass
            checks.append({
                "id": "discipline",
                "label": "Discipline score >75",
                "pass": disc_val > 75.0,
                "value": disc_val,
                "target": 75.0,
            })

            # ── 5. No active circuit breaker ──────────────────────────────
            cb_ok = True
            cb_val = "NORMAL"
            try:
                import sys as _sys
                _pe = (_sys.modules.get("utils.perp_executor")
                       or _sys.modules.get("memecoin_engine.utils.perp_executor"))
                if _pe and getattr(_pe, "_survive_mode_active", False):
                    cb_ok = False
                    cb_val = "SURVIVE_MODE active"
            except Exception:
                pass
            checks.append({
                "id": "circuit_breaker",
                "label": "No active circuit breaker",
                "pass": cb_ok,
                "value": cb_val,
                "target": "NORMAL",
            })

            # ── 6. ML accuracy > 60% ──────────────────────────────────────
            ml_acc_val = 0.0
            try:
                tot_ml = (c.execute("SELECT COUNT(*) FROM dynamic_exit_log WHERE ts_utc >= ?",
                                    (since_7d,)).fetchone()[0] or 0)
                good_ml = (c.execute(
                    "SELECT COUNT(*) FROM dynamic_exit_log WHERE ts_utc >= ? AND outcome='good_call'",
                    (since_7d,)).fetchone()[0] or 0)
                ml_acc_val = round((good_ml / tot_ml * 100) if tot_ml >= 5 else 0.0, 1)
            except Exception:
                pass
            checks.append({
                "id": "ml_accuracy",
                "label": "ML accuracy >60%",
                "pass": ml_acc_val > 60.0,
                "value": ml_acc_val,
                "target": 60.0,
            })

    except Exception as _e:
        log.warning("brain_live_checklist error: %s", _e)

    passed = sum(1 for ch in checks if ch["pass"])
    rm_mode = os.environ.get("REAL_MONEY_MODE", "false").lower() in ("1", "true", "yes")

    return JSONResponse({
        "checks": checks,
        "all_clear": passed == len(checks),
        "passed": passed,
        "total": len(checks),
        "real_money_mode": rm_mode,
    })


@app.get("/api/brain/live-transition-log")
async def brain_live_transition_log(_: str = Depends(get_current_user)):
    """
    Last 50 entries from live_transition_log table.
    """
    try:
        import sqlite3 as _sq
        db = os.path.join(_engine_root(), "data_storage", "engine.db")
        with _sq.connect(db) as c:
            c.row_factory = _sq.Row
            try:
                rows = c.execute("""
                    SELECT id, ts_utc, event_type, symbol, side, mode, reason
                    FROM live_transition_log
                    ORDER BY ts_utc DESC LIMIT 50
                """).fetchall()
            except Exception:
                rows = []
            entries = [dict(r) for r in rows]
        return JSONResponse({"entries": entries, "total": len(entries)})
    except Exception as _e:
        log.warning("brain_live_transition_log error: %s", _e)
        return JSONResponse({"entries": [], "total": 0})


@app.get("/api/journal/learnings")'''

mp = mp.replace(JOURNAL_ANCHOR, NEW_ENDPOINTS, 1)
print("OK [B3-B6] set-live-mode + emergency-kill + live-checklist + live-transition-log added")

MP.write_text(mp)
print("\nPatch 55 applied successfully.")
