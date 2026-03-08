"""
Patch 84a — main.py: Daily Auto-Report Agent (Agent 5)
1. DB table: daily_performance_reports
2. _generate_daily_report(date_str) — full analysis + pattern detection + recommendations
3. _daily_report_loop() — midnight UTC background task
4. Wire into lifespan (creation + cancellation)
5. GET /api/brain/daily-reports?limit=7
6. POST /api/brain/daily-report/trigger?date=YYYY-MM-DD
"""
import subprocess, sys, os
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard" / "backend" / "main.py"

text = MAIN.read_text()

# ─────────────────────────────────────────────────────────────────────────────
# R1 — Insert daily report agent functions before journal/learnings anchor
# ─────────────────────────────────────────────────────────────────────────────
ANCHOR_R1 = '@app.get("/api/brain/edge-stats")'
assert ANCHOR_R1 in text, f"R1 anchor not found"

AGENT_CODE = '''# ─── Daily Auto-Report Agent (Patch 84) ─────────────────────────────────────────

def _ensure_daily_reports_table():
    """Create daily_performance_reports table if it doesn\'t exist."""
    import sqlite3 as _sq
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    with _sq.connect(db_path) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS daily_performance_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL UNIQUE,
                metrics_json TEXT NOT NULL,
                patterns_json TEXT NOT NULL,
                recommendations_json TEXT NOT NULL,
                generated_at TEXT NOT NULL
            )
        """)


def _parse_notes(notes_str):
    """Parse pipe-delimited key=value notes field."""
    if not notes_str:
        return {}
    result = {}
    for part in notes_str.split("|"):
        if "=" in part:
            k, _, v = part.partition("=")
            result[k.strip()] = v.strip()
    return result


def _generate_daily_report(date_str):
    """
    Generate full performance report for UTC date YYYY-MM-DD.
    Returns dict with metrics, patterns, recommendations.
    Stores to DB and sends Slack notification.
    """
    import sqlite3 as _sq
    import json as _json
    from datetime import datetime as _dt

    _ensure_daily_reports_table()
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")

    with _sq.connect(db_path) as c:
        c.row_factory = _sq.Row

        # Check if report already exists
        existing = c.execute(
            "SELECT id FROM daily_performance_reports WHERE report_date=?", (date_str,)
        ).fetchone()
        if existing:
            row = c.execute(
                "SELECT metrics_json, patterns_json, recommendations_json, generated_at "
                "FROM daily_performance_reports WHERE report_date=?", (date_str,)
            ).fetchone()
            return {
                "date": date_str,
                "metrics": _json.loads(row["metrics_json"]),
                "patterns": _json.loads(row["patterns_json"]),
                "recommendations": _json.loads(row["recommendations_json"]),
                "generated_at": row["generated_at"],
            }

        # ── Fetch all closed paper trades for this date ──
        rows = c.execute("""
            SELECT pnl_pct, pnl_usd, exit_reason, notes, opened_ts_utc,
                   closed_ts_utc, symbol, side
            FROM perp_positions
            WHERE status=\'CLOSED\' AND dry_run=1
              AND DATE(closed_ts_utc) = ?
              AND pnl_pct IS NOT NULL
            ORDER BY closed_ts_utc
        """, (date_str,)).fetchall()

        # ── Open positions at report time ──
        open_count = c.execute(
            "SELECT COUNT(*) FROM perp_positions WHERE status=\'OPEN\' AND dry_run=1"
        ).fetchone()[0]

    trades = [dict(r) for r in rows]
    n = len(trades)

    if n == 0:
        metrics = {
            "total_trades": 0, "win_rate": None, "avg_pnl_pct": None,
            "total_pnl_usd": 0, "avg_winner_pnl": None, "avg_loser_pnl": None,
            "win_loss_ratio": None, "open_positions": open_count,
            "exit_breakdown": {}, "symbol_breakdown": {},
            "dip_buy_count": 0, "dip_buy_win_rate": None,
            "scalp_count": 0, "scalp_win_rate": None,
        }
        patterns = ["No trades executed — check SCALP_ENABLED and scanner logs"]
        recs = ["Verify service is running: journalctl -u memecoin-dashboard -n 50",
                "Check if SCALP_ENABLED=true in .env",
                "If running, volume filter may be too strict — check debug logs for SKIP messages"]
    else:
        # ── Core metrics ──
        winners = [t for t in trades if (t["pnl_pct"] or 0) > 0]
        losers  = [t for t in trades if (t["pnl_pct"] or 0) <= 0]
        win_rate = len(winners) / n

        pnls = [t["pnl_pct"] for t in trades if t["pnl_pct"] is not None]
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0

        pnl_usd_vals = [t["pnl_usd"] for t in trades if t["pnl_usd"] is not None]
        total_pnl_usd = sum(pnl_usd_vals) if pnl_usd_vals else 0

        avg_winner = sum(t["pnl_pct"] for t in winners) / len(winners) if winners else 0
        avg_loser  = sum(t["pnl_pct"] for t in losers) / len(losers) if losers else 0
        rr_ratio   = round(avg_winner / abs(avg_loser), 2) if avg_loser != 0 and avg_winner > 0 else 0

        # ── Exit breakdown ──
        exit_bd = {}
        for t in trades:
            reason = t["exit_reason"] or "UNKNOWN"
            if reason not in exit_bd:
                exit_bd[reason] = {"count": 0, "pnls": []}
            exit_bd[reason]["count"] += 1
            if t["pnl_pct"] is not None:
                exit_bd[reason]["pnls"].append(t["pnl_pct"])
        exit_summary = {
            k: {"count": v["count"], "avg_pnl": round(sum(v["pnls"]) / len(v["pnls"]), 3) if v["pnls"] else 0}
            for k, v in exit_bd.items()
        }

        # ── Symbol breakdown ──
        sym_bd = {}
        for t in trades:
            s = t["symbol"]
            if s not in sym_bd:
                sym_bd[s] = {"count": 0, "wins": 0, "pnls": []}
            sym_bd[s]["count"] += 1
            if (t["pnl_pct"] or 0) > 0:
                sym_bd[s]["wins"] += 1
            if t["pnl_pct"] is not None:
                sym_bd[s]["pnls"].append(t["pnl_pct"])
        sym_summary = {
            s: {
                "count": v["count"],
                "win_rate": round(v["wins"] / v["count"] * 100, 1),
                "avg_pnl": round(sum(v["pnls"]) / len(v["pnls"]), 3) if v["pnls"] else 0,
            }
            for s, v in sym_bd.items()
        }

        # ── Source breakdown (dip_buy vs scalp) ──
        dip_buy = [t for t in trades if "source=dip_buy" in (t["notes"] or "")]
        scalp   = [t for t in trades if "source=dip_buy" not in (t["notes"] or "")]
        db_wr = round(sum(1 for t in dip_buy if (t["pnl_pct"] or 0) > 0) / len(dip_buy) * 100, 1) if dip_buy else None
        sc_wr = round(sum(1 for t in scalp  if (t["pnl_pct"] or 0) > 0) / len(scalp)  * 100, 1) if scalp else None

        # ── Avg hold time for losers (early vs late SL) ──
        sl_trades = [t for t in trades if t["exit_reason"] == "STOP_LOSS"]
        fast_sl = 0  # hit SL in <10 min
        for t in sl_trades:
            try:
                o = _dt.fromisoformat(t["opened_ts_utc"].replace("Z", "+00:00"))
                cl = _dt.fromisoformat(t["closed_ts_utc"].replace("Z", "+00:00"))
                if (cl - o).total_seconds() < 600:
                    fast_sl += 1
            except Exception:
                pass

        metrics = {
            "total_trades": n,
            "win_rate": round(win_rate * 100, 1),
            "avg_pnl_pct": round(avg_pnl, 3),
            "total_pnl_usd": round(total_pnl_usd, 2),
            "avg_winner_pnl": round(avg_winner, 3),
            "avg_loser_pnl": round(avg_loser, 3),
            "win_loss_ratio": rr_ratio,
            "open_positions": open_count,
            "exit_breakdown": exit_summary,
            "symbol_breakdown": sym_summary,
            "dip_buy_count": len(dip_buy),
            "dip_buy_win_rate": db_wr,
            "scalp_count": len(scalp),
            "scalp_win_rate": sc_wr,
            "fast_sl_count": fast_sl,
        }

        # ── Pattern detection ──
        patterns = []
        recs = []

        # Baseline comparison (pre-Patch 82: 84% TL, 48% WR, -0.12% avg, 1:1 R:R)
        tl = exit_summary.get("TIME_LIMIT", {})
        sl = exit_summary.get("STOP_LOSS", {})
        trail = exit_summary.get("TRAIL_STOP", {})
        early = exit_summary.get("EARLY_CUT", {})
        winner_ext = exit_summary.get("WINNER_TRAIL", {})

        tl_pct = tl.get("count", 0) / n * 100 if n > 0 else 0
        sl_pct = sl.get("count", 0) / n * 100 if n > 0 else 0

        # Signal volume
        if n < 5:
            patterns.append(f"LOW SIGNAL COUNT: only {n} trades today — filters may be too aggressive")
            recs.append("Lower SCALP_5M_THRESHOLD from 0.25 to 0.20 to increase signal volume")
        elif n > 40:
            patterns.append(f"HIGH SIGNAL COUNT: {n} trades — consider raising threshold to maintain quality")
            recs.append("Raise SCALP_5M_THRESHOLD to 0.30 to tighten entry quality")
        else:
            patterns.append(f"Signal volume: {n} trades — healthy range")

        # TIME_LIMIT progress vs 84% baseline
        if tl_pct > 70:
            patterns.append(f"TIME_LIMIT exits: {tl_pct:.0f}% (baseline: 84%) — exits still timer-dominated")
            recs.append("Trailing stops not engaging enough. Lower WINNER_EXTEND_TRAIL_PCT or reduce SCALP_BREAKEVEN_TRIGGER")
        elif tl_pct > 50:
            patterns.append(f"TIME_LIMIT exits: {tl_pct:.0f}% — improving from 84% baseline, progress noted")
        else:
            patterns.append(f"TIME_LIMIT exits: {tl_pct:.0f}% — target <50% achieved! Exits resolving via TP/SL/Trail")

        # STOP_LOSS quality
        if sl.get("count", 0) > 0:
            sl_avg = sl.get("avg_pnl", -3.4)
            if sl_avg < -3.0:
                patterns.append(f"STOP_LOSS avg: {sl_avg:.2f}% — still deep (baseline -3.4%)")
                recs.append(f"SL exits at {sl_avg:.1f}% avg — lower SCALP_STOP_PCT from 0.5 to 0.4")
            elif sl_avg < -2.0:
                patterns.append(f"STOP_LOSS avg: {sl_avg:.2f}% — improving from -3.4% baseline")
            else:
                patterns.append(f"STOP_LOSS avg: {sl_avg:.2f}% — tight losses, system cutting well")

            if fast_sl > 0:
                patterns.append(f"Fast SL (<10min): {fast_sl}/{sl.get('count', 0)} — premature entries detected")
                recs.append("Entries hitting SL within 10 min suggests MACD/volume filter can be tightened further")

        # New exit types (Patch 82 effectiveness)
        if trail.get("count", 0) > 0:
            trail_avg = trail.get("avg_pnl", 0)
            patterns.append(f"TRAIL_STOP: {trail['count']} trades, avg {trail_avg:+.2f}% — trailing stops engaged!")
            if trail_avg < 0:
                recs.append(f"Trail stops exiting negative ({trail_avg:.2f}%). Consider raising SCALP_BREAKEVEN_TRIGGER to protect more gain before activating trail")
        else:
            patterns.append("TRAIL_STOP: 0 — not firing yet. Trades may not be reaching breakeven trigger")
            recs.append("No trail stops today. Check SCALP_BREAKEVEN_TRIGGER (currently 0.3%). May need price action to cooperate")

        if early.get("count", 0) > 0:
            early_avg = early.get("avg_pnl", 0)
            patterns.append(f"EARLY_CUT: {early['count']} trades, avg {early_avg:+.2f}% — cutting losers fast")
            if early_avg < -1.0:
                recs.append(f"Early cuts at {early_avg:.2f}% avg — tighten EARLY_CUT_LOSS_PCT from 0.3 to 0.25")
        else:
            patterns.append("EARLY_CUT: 0 — not firing. Either trades recover within 5min or losses stay below 0.3%")

        if winner_ext.get("count", 0) > 0:
            w_avg = winner_ext.get("avg_pnl", 0)
            patterns.append(f"WINNER_TRAIL: {winner_ext['count']} extensions, avg {w_avg:+.2f}% — letting winners run")

        # Win rate vs 48% baseline
        if win_rate * 100 < 45:
            patterns.append(f"Win rate: {win_rate*100:.1f}% — below 48% baseline. Entry quality degraded")
            recs.append("Win rate worse than baseline. MACD/RSI/volume filters may need recalibration")
        elif win_rate * 100 < 55:
            patterns.append(f"Win rate: {win_rate*100:.1f}% — near baseline (48%). Filters maintaining minimum quality")
        elif win_rate * 100 < 65:
            patterns.append(f"Win rate: {win_rate*100:.1f}% — above 48% baseline. Entry quality improving!")
        else:
            patterns.append(f"Win rate: {win_rate*100:.1f}% — strong! Well above 48% baseline")

        # R:R ratio vs 1:1 baseline
        if rr_ratio == 0:
            patterns.append("R:R ratio: N/A — no completed winners or losers to compare")
        elif rr_ratio < 1.0:
            patterns.append(f"R:R ratio: {rr_ratio:.2f}:1 — losses still larger than wins (baseline 1:1)")
            recs.append("Losses exceeding wins — focus on either tightening SL or letting winners run longer")
        elif rr_ratio < 1.5:
            patterns.append(f"R:R ratio: {rr_ratio:.2f}:1 — near parity, improving from 1:1 baseline")
        elif rr_ratio < 2.0:
            patterns.append(f"R:R ratio: {rr_ratio:.2f}:1 — good! Target is 2:1+")
        else:
            patterns.append(f"R:R ratio: {rr_ratio:.2f}:1 — STRONG EDGE. Target 2:1+ achieved!")
            recs.append(f"R:R at {rr_ratio:.2f}:1 — run Kelly calculator. If f > 5%, consider live trading evaluation")

        # Dip-buy vs scalp comparison
        if dip_buy and scalp and db_wr and sc_wr:
            if db_wr > sc_wr + 10:
                patterns.append(f"Dip-buy outperforming: {db_wr:.0f}% WR vs scalp {sc_wr:.0f}% WR")
                recs.append("Dip-buy strategy showing edge. Consider raising dip-buy size relative to momentum scalps")
            elif db_wr < sc_wr - 10:
                patterns.append(f"Scalp outperforming dip-buy: {sc_wr:.0f}% vs {db_wr:.0f}% WR")
            else:
                patterns.append(f"Dip-buy vs scalp WR similar: {db_wr:.0f}% vs {sc_wr:.0f}%")
        elif dip_buy:
            patterns.append(f"Dip-buy signals: {len(dip_buy)} trades ({db_wr:.0f}% WR)" if db_wr else f"Dip-buy signals: {len(dip_buy)} trades")

        # Overall PnL trend
        if avg_pnl > 0.5:
            patterns.append(f"Avg PnL: {avg_pnl:+.3f}% — profitable! Baseline was -0.12%")
        elif avg_pnl > 0:
            patterns.append(f"Avg PnL: {avg_pnl:+.3f}% — slightly positive (baseline -0.12%)")
        elif avg_pnl > -0.12:
            patterns.append(f"Avg PnL: {avg_pnl:+.3f}% — at/near baseline -0.12%. Filters not yet profitable")
        else:
            patterns.append(f"Avg PnL: {avg_pnl:+.3f}% — worse than baseline. Investigate exit quality")
            recs.append("Negative avg PnL — review exit_breakdown for dominant losing exit type and tune first")

        # Live readiness signal
        if win_rate * 100 >= 55 and rr_ratio >= 1.5 and avg_pnl > 0.3:
            patterns.append("LIVE READINESS SIGNAL: WR ≥55%, R:R ≥1.5:1, avg PnL >0.3% — edge detected!")
            recs.append("System showing positive edge. Run /brain/live-checklist and evaluate for live trading")

    # ── Store to DB ──
    now_str = _dt.utcnow().isoformat() + "Z"
    _ensure_daily_reports_table()
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    with _sq.connect(db_path) as c:
        c.execute("""
            INSERT OR REPLACE INTO daily_performance_reports
            (report_date, metrics_json, patterns_json, recommendations_json, generated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (date_str, _json.dumps(metrics), _json.dumps(patterns), _json.dumps(recs), now_str))

    # ── Slack notification ──
    try:
        m = metrics
        lines = [
            f"*Daily Report — {date_str}*",
            f"Trades: {m.get('total_trades', 0)} | WR: {m.get('win_rate', 0):.0f}% | Avg PnL: {m.get('avg_pnl_pct', 0):+.2f}%",
            f"R:R: {m.get('win_loss_ratio', 0):.2f}:1 | Total PnL: ${m.get('total_pnl_usd', 0):+.2f}",
            "",
            "*Key Patterns:*",
        ] + [f"• {p}" for p in patterns[:4]] + [
            "",
            "*Top Recommendation:*",
            f"→ {recs[0]}" if recs else "→ No recommendations today",
        ]
        _send_sim_alert("\\n".join(lines), severity="INFO", subject=f"Daily Report {date_str}")
    except Exception as _se:
        log.debug("Daily report Slack error: %s", _se)

    log.info("[DAILY_REPORT] Generated for %s: %d trades, WR=%.0f%%, avg=%.3f%%",
             date_str, metrics.get("total_trades", 0), metrics.get("win_rate", 0) or 0,
             metrics.get("avg_pnl_pct", 0) or 0)

    return {"date": date_str, "metrics": metrics, "patterns": patterns,
            "recommendations": recs, "generated_at": now_str}


async def _daily_report_loop():
    """Background task: generates yesterday\'s report at 00:05–00:15 UTC daily."""
    import asyncio as _aio
    from datetime import datetime as _dt, timedelta as _td

    _generated_today = set()

    while True:
        try:
            await _aio.sleep(600)  # check every 10 minutes
            now_utc = _dt.utcnow()
            # Fire between 00:05 and 00:15 UTC
            if now_utc.hour == 0 and 5 <= now_utc.minute <= 15:
                yesterday = (now_utc - _td(days=1)).strftime("%Y-%m-%d")
                if yesterday not in _generated_today:
                    log.info("[DAILY_REPORT] Generating report for %s", yesterday)
                    try:
                        _generate_daily_report(yesterday)
                        _generated_today.add(yesterday)
                        # Keep set small
                        if len(_generated_today) > 7:
                            _generated_today.pop()
                    except Exception as _re:
                        log.warning("[DAILY_REPORT] Generation error: %s", _re)
        except _aio.CancelledError:
            break
        except Exception as _e:
            log.warning("[DAILY_REPORT] Loop error: %s", _e)


@app.get("/api/brain/daily-reports")
async def brain_daily_reports(limit: int = 7, _: str = Depends(get_current_user)):
    """Last N daily performance reports."""
    import sqlite3 as _sq, json as _json
    _ensure_daily_reports_table()
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    try:
        with _sq.connect(db_path) as c:
            c.row_factory = _sq.Row
            rows = c.execute(
                "SELECT report_date, metrics_json, patterns_json, recommendations_json, generated_at "
                "FROM daily_performance_reports ORDER BY report_date DESC LIMIT ?", (limit,)
            ).fetchall()
        return JSONResponse([{
            "date": r["report_date"],
            "metrics": _json.loads(r["metrics_json"]),
            "patterns": _json.loads(r["patterns_json"]),
            "recommendations": _json.loads(r["recommendations_json"]),
            "generated_at": r["generated_at"],
        } for r in rows])
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/brain/daily-report/trigger")
async def brain_daily_report_trigger(date: str = None, _: str = Depends(get_current_user)):
    """Manually trigger daily report for a given date (default: yesterday)."""
    from datetime import datetime as _dt, timedelta as _td
    import sqlite3 as _sq
    if not date:
        date = (_dt.utcnow() - _td(days=1)).strftime("%Y-%m-%d")
    # Delete existing so it regenerates
    _ensure_daily_reports_table()
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    with _sq.connect(db_path) as c:
        c.execute("DELETE FROM daily_performance_reports WHERE report_date=?", (date,))
    try:
        result = _generate_daily_report(date)
        return JSONResponse(result)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


'''

text = text.replace(ANCHOR_R1, AGENT_CODE + ANCHOR_R1)
print("✓ R1: Daily report agent functions + endpoints added")

# ─────────────────────────────────────────────────────────────────────────────
# R2 — Add task_daily_report to lifespan creation block
# ─────────────────────────────────────────────────────────────────────────────
OLD_R2 = (
    '    task_weekly_report = asyncio.create_task(_weekly_report_loop())\n'
    '    task_perf_alerts   = asyncio.create_task(_performance_alert_loop())\n'
    '    task_auto_simulate = asyncio.create_task(_auto_simulate_loop())'
)
NEW_R2 = (
    '    task_weekly_report = asyncio.create_task(_weekly_report_loop())\n'
    '    task_perf_alerts   = asyncio.create_task(_performance_alert_loop())\n'
    '    task_auto_simulate = asyncio.create_task(_auto_simulate_loop())\n'
    '    task_daily_report  = asyncio.create_task(_daily_report_loop())'
)

assert OLD_R2 in text, "R2 anchor (lifespan creation) not found"
text = text.replace(OLD_R2, NEW_R2)
print("✓ R2: task_daily_report added to lifespan creation")

# ─────────────────────────────────────────────────────────────────────────────
# R3 — Add task_daily_report to lifespan cancellation tuple
# ─────────────────────────────────────────────────────────────────────────────
OLD_R3 = (
    '    all_tasks = (task_poller, task_tracker, task_perp_mon, task_perp_scan,\n'
    '                 task_scalp_mon, task_scalp_scan, task_mid_mon, task_mid_scan,\n'
    '                 task_spot_mon, task_spot_scan, task_wl_momentum, task_post_exit,\n'
    '                 task_weekly_report, task_perf_alerts, task_auto_simulate)'
)
NEW_R3 = (
    '    all_tasks = (task_poller, task_tracker, task_perp_mon, task_perp_scan,\n'
    '                 task_scalp_mon, task_scalp_scan, task_mid_mon, task_mid_scan,\n'
    '                 task_spot_mon, task_spot_scan, task_wl_momentum, task_post_exit,\n'
    '                 task_weekly_report, task_perf_alerts, task_auto_simulate,\n'
    '                 task_daily_report)'
)

assert OLD_R3 in text, "R3 anchor (lifespan cancellation tuple) not found"
text = text.replace(OLD_R3, NEW_R3)
print("✓ R3: task_daily_report added to lifespan cancellation tuple")

# ─────────────────────────────────────────────────────────────────────────────
# Write + compile
# ─────────────────────────────────────────────────────────────────────────────
MAIN.write_text(text)

r = subprocess.run(
    [sys.executable, "-m", "py_compile", str(MAIN)],
    capture_output=True, text=True
)
if r.returncode != 0:
    print("✗ compile error:", r.stderr)
    sys.exit(1)
print("✓ main.py compiles OK")
print("✓ Patch 84a complete — Daily Auto-Report Agent deployed")
