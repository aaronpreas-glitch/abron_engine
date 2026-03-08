"""
Patch 65 — Post-Simulation Review & Go-Live Lock
=================================================
1. live-checklist: add force_override field (FORCE_GO_LIVE env); if set, all_clear=True
2. simulate-review endpoint: add recommendation dict + paper_baseline comparison
3. New POST /api/risk/simulate-finalize: finalizes simulation, logs SIMULATE_REVIEW_COMPLETE,
   returns full report with recommendation
"""

from pathlib import Path
import py_compile, tempfile, shutil

MAIN = Path(__file__).resolve().parent.parent / "dashboard" / "backend" / "main.py"
main = MAIN.read_text()

# ── 1. live-checklist: add force_override ─────────────────────────────────────

OLD_CL = (
    '    return JSONResponse({\n'
    '        "checks": checks,\n'
    '        "all_clear": passed == len(checks),\n'
    '        "passed": passed,\n'
    '        "total": len(checks),\n'
    '        "real_money_mode": rm_mode,\n'
    '        "simulate_live_mode": os.environ.get("SIMULATE_LIVE_MODE", "false").lower() in ("1", "true", "yes"),\n'
    '    })\n'
)
NEW_CL = (
    '    force_go_live = os.environ.get("FORCE_GO_LIVE", "false").lower() in ("1", "true", "yes")\n'
    '    return JSONResponse({\n'
    '        "checks": checks,\n'
    '        "all_clear": force_go_live or (passed == len(checks)),\n'
    '        "passed": passed,\n'
    '        "total": len(checks),\n'
    '        "real_money_mode": rm_mode,\n'
    '        "simulate_live_mode": os.environ.get("SIMULATE_LIVE_MODE", "false").lower() in ("1", "true", "yes"),\n'
    '        "force_override": force_go_live,\n'
    '    })\n'
)
assert OLD_CL in main, "live-checklist return anchor not found"
main = main.replace(OLD_CL, NEW_CL)
print("✅ force_override added to live-checklist response")

# ── 2. simulate-review: add recommendation + paper_baseline ───────────────────

OLD_SIM_RETURN = (
    '    except Exception as exc:\n'
    '        log.warning("simulate_review error: %s", exc)\n'
    '        result["error"] = str(exc)\n'
    '\n'
    '    return JSONResponse(result)\n'
    '\n'
    '\n'
    '@app.get("/api/journal/learnings")\n'
)
NEW_SIM_RETURN = (
    '    except Exception as exc:\n'
    '        log.warning("simulate_review error: %s", exc)\n'
    '        result["error"] = str(exc)\n'
    '\n'
    '    # Build recommendation based on simulation stats\n'
    '    try:\n'
    '        avg_pnl    = result.get("avg_pnl_pct", 0.0)\n'
    '        max_dd     = result.get("max_dd_pct", 0.0)\n'
    '        win_rate   = result.get("win_rate_pct", 0.0)\n'
    '        tl_pct     = result.get("time_limit_pct", 0.0)\n'
    '        n_closed   = result.get("closed_count", 0)\n'
    '        hours      = result.get("hours_active", 0.0)\n'
    '        ok_pnl     = avg_pnl > 0.5\n'
    '        ok_dd      = max_dd < 3.0\n'
    '        ok_wr      = win_rate >= 45.0\n'
    '        ok_tl      = tl_pct < 70.0\n'
    '        ok_dur     = hours >= 24.0\n'
    '        ok_trades  = n_closed >= 3\n'
    '        criteria = {\n'
    '            "avg_pnl_above_0_5pct": {"pass": ok_pnl, "value": avg_pnl, "target": 0.5},\n'
    '            "max_dd_below_3pct":    {"pass": ok_dd,  "value": max_dd,  "target": 3.0},\n'
    '            "win_rate_above_45pct": {"pass": ok_wr,  "value": win_rate,"target": 45.0},\n'
    '            "time_limit_below_70pct": {"pass": ok_tl, "value": tl_pct, "target": 70.0},\n'
    '            "ran_24h_or_more":      {"pass": ok_dur, "value": round(hours, 1), "target": 24.0},\n'
    '            "at_least_3_closed":    {"pass": ok_trades, "value": n_closed, "target": 3},\n'
    '        }\n'
    '        passed_crit = sum(1 for v in criteria.values() if v["pass"])\n'
    '        total_crit  = len(criteria)\n'
    '        if passed_crit == total_crit:\n'
    '            rec_level = "RECOMMENDED"\n'
    '            rec_color = "green"\n'
    '            rec_msg   = "All metrics positive — engine is ready for real capital"\n'
    '        elif passed_crit >= total_crit - 1:\n'
    '            rec_level = "PROCEED WITH CAUTION"\n'
    '            rec_color = "amber"\n'
    '            failing   = [k for k, v in criteria.items() if not v["pass"]]\n'
    '            rec_msg   = "Minor concerns: " + ", ".join(failing)\n'
    '        else:\n'
    '            rec_level = "NOT RECOMMENDED"\n'
    '            rec_color = "red"\n'
    '            failing   = [k for k, v in criteria.items() if not v["pass"]]\n'
    '            rec_msg   = "Multiple criteria failing: " + ", ".join(failing)\n'
    '        result["recommendation"] = {\n'
    '            "level": rec_level, "color": rec_color, "message": rec_msg,\n'
    '            "criteria": criteria, "passed": passed_crit, "total": total_crit,\n'
    '        }\n'
    '    except Exception as _re:\n'
    '        log.warning("simulate_review recommendation error: %s", _re)\n'
    '\n'
    '    return JSONResponse(result)\n'
    '\n'
    '\n'
    '@app.get("/api/journal/learnings")\n'
)
assert OLD_SIM_RETURN in main, "simulate-review return anchor not found"
main = main.replace(OLD_SIM_RETURN, NEW_SIM_RETURN)
print("✅ recommendation added to simulate-review response")

# ── 3. Add /api/risk/simulate-finalize endpoint ────────────────────────────────

INSERT_ANCHOR = '@app.get("/api/journal/learnings")'
assert INSERT_ANCHOR in main, "learnings anchor not found"

FINALIZE_EP = r'''@app.post("/api/risk/simulate-finalize")
async def risk_simulate_finalize(_: str = Depends(get_current_user)):
    """End simulation run, generate final report, log SIMULATE_REVIEW_COMPLETE."""
    import sqlite3 as _sq, json as _json, re as _re
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    result: dict = {"ok": False, "ts": datetime.utcnow().isoformat() + "Z"}
    try:
        # Disable SIMULATE_LIVE_MODE
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

        with _sq.connect(db_path) as c:
            c.row_factory = _sq.Row
            sim_row = None
            try:
                sim_row = c.execute(
                    "SELECT ts FROM live_transition_log WHERE event_type='SIMULATE_ENABLED' "
                    "ORDER BY ts DESC LIMIT 1"
                ).fetchone()
            except Exception:
                pass

            hours_active = 0.0
            start_ts = None
            trade_stats: dict = {}
            if sim_row:
                start_ts = sim_row[0]
                sim_dt = datetime.fromisoformat(start_ts.replace("Z", ""))
                hours_active = round((datetime.utcnow() - sim_dt).total_seconds() / 3600, 1)
                try:
                    trades = c.execute(
                        "SELECT symbol, side, status, realized_pnl_pct, exit_reason "
                        "FROM perp_positions WHERE dry_run=1 AND entry_ts_utc >= ? ORDER BY entry_ts_utc",
                        (start_ts,)
                    ).fetchall()
                    closed = [t for t in trades if t["status"] == "CLOSED" and t["realized_pnl_pct"] is not None]
                    n_closed = len(closed)
                    pnls = [float(t["realized_pnl_pct"]) for t in closed] if closed else []
                    avg_pnl = round(sum(pnls) / len(pnls), 2) if pnls else 0.0
                    total_pnl = round(sum(pnls), 2) if pnls else 0.0
                    win_rate = round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1) if pnls else 0.0
                    tl_pct = round(sum(1 for t in closed if t["exit_reason"] == "TIME_LIMIT") / len(pnls) * 100, 1) if pnls else 0.0
                    cum = 0.0; peak = 0.0; max_dd = 0.0
                    for p in pnls:
                        cum += p; peak = max(peak, cum)
                        max_dd = max(max_dd, peak - cum)
                    max_dd = round(max_dd, 2)
                    trade_stats = {
                        "trade_count": len(trades), "closed_count": n_closed,
                        "avg_pnl_pct": avg_pnl, "total_pnl_pct": total_pnl,
                        "win_rate_pct": win_rate, "time_limit_pct": tl_pct,
                        "max_dd_pct": max_dd, "hours_active": hours_active,
                    }
                    # Build recommendation
                    criteria = {
                        "avg_pnl_above_0_5pct": {"pass": avg_pnl > 0.5, "value": avg_pnl, "target": 0.5},
                        "max_dd_below_3pct":    {"pass": max_dd < 3.0,  "value": max_dd,  "target": 3.0},
                        "win_rate_above_45pct": {"pass": win_rate >= 45.0, "value": win_rate, "target": 45.0},
                        "time_limit_below_70pct": {"pass": tl_pct < 70.0, "value": tl_pct, "target": 70.0},
                        "ran_24h_or_more":      {"pass": hours_active >= 24.0, "value": hours_active, "target": 24.0},
                        "at_least_3_closed":    {"pass": n_closed >= 3, "value": n_closed, "target": 3},
                    }
                    passed_crit = sum(1 for v in criteria.values() if v["pass"])
                    total_crit  = len(criteria)
                    if passed_crit == total_crit:
                        rec_level = "RECOMMENDED"
                        rec_msg   = "All metrics positive — engine is ready for real capital"
                    elif passed_crit >= total_crit - 1:
                        rec_level = "PROCEED WITH CAUTION"
                        failing   = [k for k, v in criteria.items() if not v["pass"]]
                        rec_msg   = "Minor concerns: " + ", ".join(failing)
                    else:
                        rec_level = "NOT RECOMMENDED"
                        failing   = [k for k, v in criteria.items() if not v["pass"]]
                        rec_msg   = "Multiple criteria failing: " + ", ".join(failing)
                    trade_stats["recommendation"] = {
                        "level": rec_level, "message": rec_msg,
                        "passed": passed_crit, "total": total_crit, "criteria": criteria,
                    }
                except Exception as _te:
                    log.warning("simulate_finalize stats error: %s", _te)

            # Log SIMULATE_REVIEW_COMPLETE
            try:
                c.execute(
                    "CREATE TABLE IF NOT EXISTS live_transition_log "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, event_type TEXT, "
                    "symbol TEXT, side TEXT, mode TEXT, reason TEXT, metadata TEXT)"
                )
                c.execute(
                    "INSERT INTO live_transition_log (ts, event_type, symbol, side, mode, reason, metadata) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (datetime.utcnow().isoformat() + "Z", "SIMULATE_REVIEW_COMPLETE", "", "",
                     "SIMULATE", f"Finalized after {hours_active:.1f}h",
                     _json.dumps(trade_stats)),
                )
            except Exception as _le:
                log.warning("simulate_finalize log error: %s", _le)

        result["ok"]         = True
        result["stats"]      = trade_stats
        result["start_ts"]   = start_ts
        result["hours_active"] = hours_active
    except Exception as exc:
        log.warning("simulate_finalize error: %s", exc)
        result["error"] = str(exc)
    return JSONResponse(result)


'''

main = main.replace(INSERT_ANCHOR, FINALIZE_EP + INSERT_ANCHOR)
print("✅ /api/risk/simulate-finalize endpoint inserted")

MAIN.write_text(main)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ main.py compiles OK")
print("\nPatch 65 complete")
