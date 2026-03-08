"""
Patch 70 — 24h Simulation Review & Health Report
=================================================
1. Fix _run_auto_simulate_check 24h milestone: same column-name bug as Patch 69
   (realized_pnl_pct / entry_ts_utc → pnl_pct / opened_ts_utc).
   Without this fix the 24h review always computes 0 trades.

2. Add `subject` param to _send_sim_alert so the 24h alert fires with a
   descriptive email subject: "24h Simulation Complete — RECOMMENDED / CAUTION".

3. New endpoint: GET /api/brain/sim-review
   Returns the most recent SIMULATE_REVIEW_COMPLETE metadata plus supplemental
   stats (skipped_signals_count, good_call_rate).
"""

from pathlib import Path
import py_compile, tempfile, shutil

MAIN = Path(__file__).resolve().parent.parent / "dashboard" / "backend" / "main.py"
main = MAIN.read_text()

# ── 1. Fix 24h milestone column names ───────────────────────────────────────

OLD_24H_QUERY = (
    '                        closed = conn.execute(\n'
    '                            "SELECT realized_pnl_pct, exit_reason FROM perp_positions "\n'
    '                            "WHERE dry_run=1 AND entry_ts_utc >= ? AND status=\'CLOSED\' "\n'
    '                            "AND realized_pnl_pct IS NOT NULL ORDER BY entry_ts_utc",\n'
    '                            (sim_start,)\n'
    '                        ).fetchall()\n'
)
NEW_24H_QUERY = (
    '                        closed = conn.execute(\n'
    '                            "SELECT pnl_pct, exit_reason FROM perp_positions "\n'
    '                            "WHERE dry_run=1 AND opened_ts_utc >= ? AND status=\'CLOSED\' "\n'
    '                            "AND pnl_pct IS NOT NULL ORDER BY opened_ts_utc",\n'
    '                            (sim_start,)\n'
    '                        ).fetchall()\n'
)
assert OLD_24H_QUERY in main, "24h milestone query anchor not found"
main = main.replace(OLD_24H_QUERY, NEW_24H_QUERY)
print("✅ 1: 24h milestone column names fixed (pnl_pct, opened_ts_utc)")

# ── 2a. Add `subject` param to _send_sim_alert ──────────────────────────────

OLD_ALERT_SIG = (
    'def _send_sim_alert(message: str, severity: str = "WARNING") -> None:\n'
    '    """Fire a Slack + email alert for auto-simulate events (non-dedup, caller controls rate)."""\n'
)
NEW_ALERT_SIG = (
    'def _send_sim_alert(message: str, severity: str = "WARNING", subject: str | None = None) -> None:\n'
    '    """Fire a Slack + email alert for auto-simulate events (non-dedup, caller controls rate)."""\n'
)
assert OLD_ALERT_SIG in main, "_send_sim_alert signature anchor not found"
main = main.replace(OLD_ALERT_SIG, NEW_ALERT_SIG)
print("✅ 2a: _send_sim_alert gained optional subject param")

# ── 2b. Use subject in email Subject header ──────────────────────────────────

OLD_SUBJECT_LINE = (
    '            msg["Subject"] = f"[Memecoin Engine] AUTO-SIM {severity}"\n'
)
NEW_SUBJECT_LINE = (
    '            msg["Subject"] = subject or f"[Memecoin Engine] AUTO-SIM {severity}"\n'
)
assert OLD_SUBJECT_LINE in main, "_send_sim_alert email subject anchor not found"
main = main.replace(OLD_SUBJECT_LINE, NEW_SUBJECT_LINE)
print("✅ 2b: email subject uses custom subject when provided")

# ── 2c. Update 24h milestone alert call with improved subject ────────────────

OLD_24H_ALERT = (
    '                        _send_sim_alert(\n'
    '                            f"AUTO-READY FOR LIVE — 24h simulation complete!\\n"\n'
    '                            f"Recommendation: {rec_level}\\n{rec_msg}\\n"\n'
    '                            f"Stats: {n_closed} trades, avg PNL {avg_pnl:+.2f}%, "\n'
    '                            f"win rate {win_rate:.1f}%, max DD {max_dd_f:.2f}%\\n"\n'
    '                            f"Go to Risk tab to review and enable Real Money Trading.",\n'
    '                            "WARNING" if rec_level != "RECOMMENDED" else "INFO"\n'
    '                        )\n'
)
NEW_24H_ALERT = (
    '                        _send_sim_alert(\n'
    '                            f"24h simulation complete!\\n"\n'
    '                            f"Recommendation: {rec_level}\\n{rec_msg}\\n"\n'
    '                            f"Stats: {n_closed} trades, avg PNL {avg_pnl:+.2f}%, "\n'
    '                            f"win rate {win_rate:.1f}%, max DD {max_dd_f:.2f}%\\n"\n'
    '                            f"Go to Risk tab to review the full report.",\n'
    '                            "WARNING" if rec_level != "RECOMMENDED" else "INFO",\n'
    '                            subject=f"24h Simulation Complete — {rec_level}",\n'
    '                        )\n'
)
assert OLD_24H_ALERT in main, "24h milestone _send_sim_alert call anchor not found"
main = main.replace(OLD_24H_ALERT, NEW_24H_ALERT)
print("✅ 2c: 24h alert subject updated to '24h Simulation Complete — [LEVEL]'")

# ── 3. Insert /api/brain/sim-review endpoint ─────────────────────────────────

INSERT_ANCHOR = '@app.get("/api/journal/learnings")'
assert INSERT_ANCHOR in main, "journal/learnings anchor not found"

SIM_REVIEW_EP = r'''@app.get("/api/brain/sim-review")
async def brain_sim_review(_: str = Depends(get_current_user)):
    """Most recent SIMULATE_REVIEW_COMPLETE report + supplemental 24h stats."""
    import sqlite3 as _sq, json as _json
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    result: dict = {
        "has_completed_review": False,
        "report_ts": None,
        "hours_active": 0.0,
        "closed_count": 0,
        "win_rate_pct": 0.0,
        "avg_pnl_pct": 0.0,
        "total_pnl_pct": 0.0,
        "max_dd_pct": 0.0,
        "time_limit_pct": 0.0,
        "recommendation": None,
        "skipped_signals_count": 0,
        "good_call_rate": 0.0,
        "next_action": "",
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    try:
        with _sq.connect(db_path) as c:
            c.row_factory = _sq.Row
            # Most recent completed review
            try:
                row = c.execute(
                    "SELECT ts, metadata FROM live_transition_log "
                    "WHERE event_type='SIMULATE_REVIEW_COMPLETE' "
                    "ORDER BY ts DESC LIMIT 1"
                ).fetchone()
                if row:
                    result["has_completed_review"] = True
                    result["report_ts"] = row["ts"]
                    try:
                        meta = _json.loads(row["metadata"] or "{}")
                        for k in ("hours_active", "closed_count", "win_rate_pct", "avg_pnl_pct",
                                  "total_pnl_pct", "max_dd_pct", "time_limit_pct", "recommendation"):
                            if k in meta:
                                result[k] = meta[k]
                    except Exception:
                        pass
            except Exception:
                pass
            # Skipped signals last 24h
            try:
                sr = c.execute(
                    "SELECT COUNT(*) FROM skipped_signals_log "
                    "WHERE ts >= datetime('now', '-24 hours')"
                ).fetchone()
                result["skipped_signals_count"] = sr[0] if sr else 0
            except Exception:
                pass
            # Dynamic exit good-call rate last 24h
            try:
                gcr = c.execute(
                    "SELECT COUNT(*) as total, "
                    "SUM(CASE WHEN good_call=1 THEN 1 ELSE 0 END) as good "
                    "FROM dynamic_exit_log WHERE ts >= datetime('now', '-24 hours')"
                ).fetchone()
                if gcr and gcr[0] and gcr[0] > 0:
                    result["good_call_rate"] = round((gcr[1] or 0) / gcr[0] * 100, 1)
            except Exception:
                pass
    except Exception as exc:
        log.warning("brain_sim_review error: %s", exc)
        result["error"] = str(exc)
    # Next-action suggestion
    rec = result.get("recommendation") or {}
    level = rec.get("level", "") if isinstance(rec, dict) else ""
    if level == "RECOMMENDED":
        result["next_action"] = "All checks passed. Start with Conservative preset to go live safely."
    elif "CAUTION" in level:
        result["next_action"] = "Most checks passed. Apply Conservative preset and monitor closely for another cycle."
    elif level == "NOT RECOMMENDED":
        result["next_action"] = "Multiple criteria failing. Extend simulation and review ML gate settings."
    else:
        result["next_action"] = "Simulation in progress — check back after 24h."
    return JSONResponse(result)


'''

main = main.replace(INSERT_ANCHOR, SIM_REVIEW_EP + INSERT_ANCHOR)
print("✅ 3: GET /api/brain/sim-review endpoint inserted")

# ── Write + compile ──────────────────────────────────────────────────────────

MAIN.write_text(main)

tmp = Path(tempfile.mktemp(suffix=".py"))
shutil.copy(MAIN, tmp)
py_compile.compile(str(tmp), doraise=True)
tmp.unlink()
print("✅ main.py compiles OK")
print("\nPatch 70 complete")
