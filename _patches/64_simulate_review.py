"""
Patch 64 — Simulate Live Review & Final Safeguards
====================================================
perp_executor.py:
  1. Add SIMULATE_LIVE_MODE lambda + _simulate_live_mode cached global
  2. Extend _apply_live_mode_tuning to handle SIMULATE_LIVE_MODE
  3. In execute_perp_signal: simulate branch caps size at 0.25% + logs SIMULATED_TRADE

main.py:
  4. Update /api/risk/simulate-live to call _apply_live_mode_tuning in-memory
  5. Add simulate_24h as 7th check in /api/brain/live-checklist
  6. New /api/risk/simulate-review endpoint (stats + auto-end DD>5%)
"""

from pathlib import Path
import py_compile, tempfile, shutil

ROOT      = Path(__file__).resolve().parent.parent
EXECUTOR  = ROOT / "utils" / "perp_executor.py"
MAIN      = ROOT / "dashboard" / "backend" / "main.py"

# ══════════════════════════════════════════════════════════════════════════════
# A. perp_executor.py
# ══════════════════════════════════════════════════════════════════════════════

ex = EXECUTOR.read_text()

# ── A1. Add SIMULATE_LIVE_MODE lambda ────────────────────────────────────────

OLD_LAMBDA = 'ACCOUNT_BALANCE_USD = lambda: _float("ACCOUNT_BALANCE_USD", 0.0)'
NEW_LAMBDA = (
    'ACCOUNT_BALANCE_USD = lambda: _float("ACCOUNT_BALANCE_USD", 0.0)\n'
    'SIMULATE_LIVE_MODE  = lambda: _bool("SIMULATE_LIVE_MODE", False)'
)
assert OLD_LAMBDA in ex, "ACCOUNT_BALANCE_USD lambda anchor not found"
ex = ex.replace(OLD_LAMBDA, NEW_LAMBDA)
print("✅ A1: SIMULATE_LIVE_MODE lambda added")

# ── A2. Add _simulate_live_mode cached global ─────────────────────────────────

OLD_GLOB = '_account_balance_usd: float  = 0.0    # total account balance for 0.5% risk cap'
NEW_GLOB = (
    '_account_balance_usd: float  = 0.0    # total account balance for 0.5% risk cap\n'
    '_simulate_live_mode:  bool   = False  # True = paper routing with real-money sizing (test)'
)
assert OLD_GLOB in ex, "_account_balance_usd global anchor not found"
ex = ex.replace(OLD_GLOB, NEW_GLOB)
print("✅ A2: _simulate_live_mode global added")

# ── A3. Extend _apply_live_mode_tuning to handle SIMULATE_LIVE_MODE ───────────

OLD_TUNING = (
    '    global _real_money_mode, _real_base_usd, _account_balance_usd\n'
    '    try:\n'
    '        if key == "REAL_MONEY_MODE":'
)
NEW_TUNING = (
    '    global _real_money_mode, _real_base_usd, _account_balance_usd, _simulate_live_mode\n'
    '    try:\n'
    '        if key == "SIMULATE_LIVE_MODE":\n'
    '            _simulate_live_mode = value.lower() in ("1", "true", "yes")\n'
    '        elif key == "REAL_MONEY_MODE":'
)
assert OLD_TUNING in ex, "_apply_live_mode_tuning anchor not found"
ex = ex.replace(OLD_TUNING, NEW_TUNING)
print("✅ A3: _apply_live_mode_tuning extended for SIMULATE_LIVE_MODE")

# ── A4. Modify execute_perp_signal rm_mode gate to add simulate branch ────────

OLD_RM = (
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
)
NEW_RM = (
    '    # Real-money / simulate mode: scale size + cap at balance % + log decision\n'
    '    _rm_mode  = _real_money_mode\n'
    '    _sim_mode = _simulate_live_mode\n'
    '    if _rm_mode:\n'
    '        _rm_scale = _real_base_usd / 100.0\n'
    '        size_usd = size_usd * _rm_scale\n'
    '        _bal = _account_balance_usd\n'
    '        if _bal > 0:\n'
    '            _max_size = (_bal * 0.005) * leverage  # 0.5% of balance\n'
    '            size_usd = min(size_usd, _max_size)\n'
    '        _log_live_decision("LIVE_TRADE_ATTEMPT", symbol, side, mode_tag,\n'
    '                           f"size={size_usd:.2f} scale={_rm_scale:.2f}")\n'
    '    elif _sim_mode:\n'
    '        # Simulate: real-money sizing but capped at 0.25% of balance (extra conservative)\n'
    '        _rm_scale = _real_base_usd / 100.0\n'
    '        size_usd = size_usd * _rm_scale\n'
    '        _bal = _account_balance_usd\n'
    '        if _bal > 0:\n'
    '            _max_size = (_bal * 0.0025) * leverage  # 0.25% of balance\n'
    '            size_usd = min(size_usd, _max_size)\n'
    '        _log_live_decision("SIMULATED_TRADE", symbol, side, mode_tag,\n'
    '                           f"SIMULATE size={size_usd:.2f} scale={_rm_scale:.2f}")\n'
    '    else:\n'
    '        _log_live_decision("PAPER_TRADE", symbol, side, mode_tag,\n'
    '                           "REAL_MONEY_MODE=false")\n'
)
assert OLD_RM in ex, "rm_mode gate block anchor not found in perp_executor.py"
ex = ex.replace(OLD_RM, NEW_RM)
print("✅ A4: simulate branch added to rm_mode gate in execute_perp_signal")

EXECUTOR.write_text(ex)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(EXECUTOR, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ perp_executor.py compiles OK")

# ══════════════════════════════════════════════════════════════════════════════
# B. main.py
# ══════════════════════════════════════════════════════════════════════════════

main = MAIN.read_text()

# ── B1. Update simulate-live endpoint to call _apply_live_mode_tuning ─────────

OLD_SIM_SET = (
    '        os.environ["SIMULATE_LIVE_MODE"] = new_val\n'
    '        db_path = os.path.join(_engine_root(), "data_storage", "engine.db")\n'
)
NEW_SIM_SET = (
    '        os.environ["SIMULATE_LIVE_MODE"] = new_val\n'
    '        import sys as _sys\n'
    '        _pe_mod = _sys.modules.get("utils.perp_executor")\n'
    '        if _pe_mod and hasattr(_pe_mod, "_apply_live_mode_tuning"):\n'
    '            _pe_mod._apply_live_mode_tuning("SIMULATE_LIVE_MODE", new_val)\n'
    '        db_path = os.path.join(_engine_root(), "data_storage", "engine.db")\n'
)
assert OLD_SIM_SET in main, "simulate-live os.environ anchor not found"
main = main.replace(OLD_SIM_SET, NEW_SIM_SET)
print("✅ B1: simulate-live endpoint calls _apply_live_mode_tuning in-memory")

# ── B2. Add simulate_24h as 7th check in live-checklist ──────────────────────

OLD_CL_END = (
    '            checks.append({\n'
    '                "id": "ml_accuracy",\n'
    '                "label": "ML accuracy >60%",\n'
    '                "pass": ml_acc_val > 60.0,\n'
    '                "value": ml_acc_val,\n'
    '                "target": 60.0,\n'
    '            })\n'
    '\n'
    '    except Exception as _e:\n'
    '        log.warning("brain_live_checklist error: %s", _e)\n'
)
NEW_CL_END = (
    '            checks.append({\n'
    '                "id": "ml_accuracy",\n'
    '                "label": "ML accuracy >60%",\n'
    '                "pass": ml_acc_val > 60.0,\n'
    '                "value": ml_acc_val,\n'
    '                "target": 60.0,\n'
    '            })\n'
    '\n'
    '            # ── 7. Simulate Live run ≥ 24h ──────────────────────────────\n'
    '            try:\n'
    '                sim_row = c.execute(\n'
    '                    "SELECT ts FROM live_transition_log WHERE event_type=\'SIMULATE_ENABLED\' "\n'
    '                    "ORDER BY ts DESC LIMIT 1"\n'
    '                ).fetchone()\n'
    '                if sim_row:\n'
    '                    sim_dt = datetime.fromisoformat(sim_row[0].replace("Z", ""))\n'
    '                    sim_hours = (datetime.utcnow() - sim_dt).total_seconds() / 3600\n'
    '                    sim_val  = f"{sim_hours:.1f}h"\n'
    '                    sim_pass = sim_hours >= 24.0\n'
    '                else:\n'
    '                    sim_val  = "0h"\n'
    '                    sim_pass = False\n'
    '            except Exception:\n'
    '                sim_val  = "err"\n'
    '                sim_pass = False\n'
    '            checks.append({\n'
    '                "id": "simulate_24h",\n'
    '                "label": "Simulate Live run \\u2265 24h",\n'
    '                "pass": sim_pass,\n'
    '                "value": sim_val,\n'
    '                "target": "24h",\n'
    '            })\n'
    '\n'
    '    except Exception as _e:\n'
    '        log.warning("brain_live_checklist error: %s", _e)\n'
)
assert OLD_CL_END in main, "live-checklist ml_accuracy+except anchor not found"
main = main.replace(OLD_CL_END, NEW_CL_END)
print("✅ B2: simulate_24h check added to live-checklist (now 7 checks)")

# ── B3. New /api/risk/simulate-review endpoint ────────────────────────────────

INSERT_ANCHOR = '@app.get("/api/journal/learnings")'
assert INSERT_ANCHOR in main, "learnings anchor not found"

SIM_REVIEW_EP = r'''@app.get("/api/risk/simulate-review")
async def risk_simulate_review(_: str = Depends(get_current_user)):
    """Simulation run stats: trades, PNL, max DD. Auto-ends if DD > 5%."""
    import sqlite3 as _sq, re as _re
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    sim_active = os.environ.get("SIMULATE_LIVE_MODE", "false").lower() in ("1", "true", "yes")
    result: dict = {
        "active": sim_active,
        "start_ts": None,
        "hours_active": 0.0,
        "trade_count": 0,
        "closed_count": 0,
        "win_rate_pct": 0.0,
        "avg_pnl_pct": 0.0,
        "total_pnl_pct": 0.0,
        "max_dd_pct": 0.0,
        "time_limit_pct": 0.0,
        "auto_ended": False,
        "recent_trades": [],
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    try:
        with _sq.connect(db_path) as c:
            c.row_factory = _sq.Row
            # Get most recent SIMULATE_ENABLED event
            sim_row = None
            try:
                sim_row = c.execute(
                    "SELECT ts FROM live_transition_log WHERE event_type='SIMULATE_ENABLED' "
                    "ORDER BY ts DESC LIMIT 1"
                ).fetchone()
            except Exception:
                pass

            if sim_row:
                sim_start_ts = sim_row[0]
                result["start_ts"] = sim_start_ts
                sim_dt = datetime.fromisoformat(sim_start_ts.replace("Z", ""))
                result["hours_active"] = round((datetime.utcnow() - sim_dt).total_seconds() / 3600, 1)

                # Trades opened during simulate window (paper trades only)
                try:
                    trades = c.execute(
                        "SELECT symbol, side, status, realized_pnl_pct, entry_ts_utc, "
                        "closed_ts_utc, exit_reason FROM perp_positions "
                        "WHERE dry_run=1 AND entry_ts_utc >= ? ORDER BY entry_ts_utc DESC",
                        (sim_start_ts,)
                    ).fetchall()
                    result["trade_count"] = len(trades)
                    closed = [t for t in trades if t["status"] == "CLOSED" and t["realized_pnl_pct"] is not None]
                    result["closed_count"] = len(closed)

                    if closed:
                        pnls = [float(t["realized_pnl_pct"]) for t in closed]
                        result["avg_pnl_pct"]   = round(sum(pnls) / len(pnls), 2)
                        result["total_pnl_pct"]  = round(sum(pnls), 2)
                        wins = sum(1 for p in pnls if p > 0)
                        result["win_rate_pct"]   = round(wins / len(pnls) * 100, 1)
                        tl = sum(1 for t in closed if t["exit_reason"] == "TIME_LIMIT")
                        result["time_limit_pct"] = round(tl / len(pnls) * 100, 1)
                        # Running drawdown
                        cum = 0.0; peak = 0.0; max_dd = 0.0
                        for p in reversed(pnls):
                            cum  += p
                            peak  = max(peak, cum)
                            max_dd = max(max_dd, peak - cum)
                        result["max_dd_pct"] = round(max_dd, 2)

                        # Auto-end if DD exceeds 5%
                        if result["max_dd_pct"] > 5.0 and sim_active:
                            env_path = os.path.join(_engine_root(), ".env")
                            if os.path.exists(env_path):
                                env_text = open(env_path).read()
                                env_text = _re.sub(r"SIMULATE_LIVE_MODE=\S*", "SIMULATE_LIVE_MODE=false", env_text)
                                open(env_path, "w").write(env_text)
                            os.environ["SIMULATE_LIVE_MODE"] = "false"
                            import sys as _sys
                            _pe_mod = _sys.modules.get("utils.perp_executor")
                            if _pe_mod and hasattr(_pe_mod, "_apply_live_mode_tuning"):
                                _pe_mod._apply_live_mode_tuning("SIMULATE_LIVE_MODE", "false")
                            try:
                                c.execute(
                                    "INSERT INTO live_transition_log (ts, event_type, symbol, side, mode, reason) "
                                    "VALUES (?,?,?,?,?,?)",
                                    (datetime.utcnow().isoformat() + "Z", "SIMULATE_AUTO_ENDED", "", "", "SIMULATE",
                                     f"Auto-ended: max_dd={result['max_dd_pct']:.1f}% exceeded 5% threshold"),
                                )
                            except Exception:
                                pass
                            result["active"]     = False
                            result["auto_ended"] = True

                    result["recent_trades"] = [
                        {
                            "symbol": t["symbol"], "side": t["side"],
                            "status": t["status"],
                            "pnl_pct": float(t["realized_pnl_pct"]) if t["realized_pnl_pct"] is not None else None,
                            "entry_ts": t["entry_ts_utc"],
                            "exit_reason": t["exit_reason"],
                        }
                        for t in (trades or [])[:10]
                    ]
                except Exception as _te:
                    log.warning("simulate_review trade query error: %s", _te)

    except Exception as exc:
        log.warning("simulate_review error: %s", exc)
        result["error"] = str(exc)

    return JSONResponse(result)


'''

main = main.replace(INSERT_ANCHOR, SIM_REVIEW_EP + INSERT_ANCHOR)
print("✅ B3: /api/risk/simulate-review endpoint inserted")

MAIN.write_text(main)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ main.py compiles OK")
print("\nPatch 64 complete")
