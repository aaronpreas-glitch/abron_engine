#!/usr/bin/env python3
"""
Create utils/weekly_report.py and add:
1. Weekly report generator (runs every Sunday 00:00 UTC)
2. /api/reports/weekly endpoint
3. Background task in main.py
"""

# ── Part 1: weekly_report.py module ──
REPORT_CODE = r'''"""
Weekly Automated Report Generator

Generates comprehensive weekly trading performance summaries.
Runs automatically every Sunday 00:00 UTC via background task.
Reports are stored in data_storage/reports/ as JSON.
"""
import os, json, logging, sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger("weekly_report")

_ENGINE_ROOT = os.environ.get("ENGINE_ROOT", "/root/memecoin_engine")
_DB_PATH     = os.path.join(_ENGINE_ROOT, "data_storage", "engine.db")
_REPORTS_DIR = os.path.join(_ENGINE_ROOT, "data_storage", "reports")


def _ensure_dirs():
    os.makedirs(_REPORTS_DIR, exist_ok=True)


def _parse_notes(notes: str) -> dict:
    result = {}
    if not notes:
        return result
    for part in notes.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def generate_weekly_report(week_start: datetime = None) -> dict:
    """Generate a weekly report for the week ending at week_start (or current week).

    Returns a comprehensive report dict with:
    - PnL summary, equity curve points
    - Top performers, worst performers
    - ML accuracy trend
    - Mode breakdown (SCALP/MID/SWING)
    - Sentiment highlights
    - Suggested tuner changes
    """
    _ensure_dirs()

    if week_start is None:
        now = datetime.now(timezone.utc)
        # Go back to most recent Sunday
        days_since_sunday = now.weekday() + 1 if now.weekday() != 6 else 0
        week_end = now
        week_start = now - timedelta(days=7)
    else:
        week_end = week_start + timedelta(days=7)

    start_str = week_start.isoformat()
    end_str = week_end.isoformat()

    if not os.path.exists(_DB_PATH):
        return {"status": "no_database"}

    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        # Closed trades this week
        trades = conn.execute("""
            SELECT pp.*, pet.missed_pct_5m, pet.missed_pct_15m, pet.missed_pct_30m,
                   pet.would_have_continued
            FROM perp_positions pp
            LEFT JOIN post_exit_tracking pet ON pet.position_id = pp.id
            WHERE pp.status = 'CLOSED' AND pp.pnl_pct IS NOT NULL
              AND pp.closed_ts_utc >= ? AND pp.closed_ts_utc <= ?
            ORDER BY pp.closed_ts_utc ASC
        """, (start_str, end_str)).fetchall()
        trades = [dict(r) for r in trades]

        # All-time stats for comparison
        all_trades = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                   AVG(pnl_pct) as avg_pnl,
                   SUM(pnl_pct) as total_pnl
            FROM perp_positions WHERE status='CLOSED' AND pnl_pct IS NOT NULL
        """).fetchone()

    if not trades:
        return {
            "status": "no_trades",
            "week_start": start_str,
            "week_end": end_str,
        }

    # Basic stats
    n = len(trades)
    wins = sum(1 for t in trades if (t["pnl_pct"] or 0) > 0)
    losses = n - wins
    win_rate = round(wins / n * 100, 1) if n > 0 else 0
    total_pnl = sum(t["pnl_pct"] or 0 for t in trades)
    avg_pnl = total_pnl / n if n > 0 else 0

    # Best/worst performers
    sorted_by_pnl = sorted(trades, key=lambda t: t["pnl_pct"] or 0, reverse=True)
    top_3 = sorted_by_pnl[:3]
    bottom_3 = sorted_by_pnl[-3:]

    # Equity curve
    equity_points = []
    cumulative = 0
    for i, t in enumerate(trades):
        cumulative += t["pnl_pct"] or 0
        equity_points.append({"trade": i + 1, "equity": round(cumulative, 3)})

    # Max drawdown
    peak = 0
    max_dd = 0
    cum = 0
    for t in trades:
        cum += t["pnl_pct"] or 0
        peak = max(peak, cum)
        dd = cum - peak
        max_dd = min(max_dd, dd)

    # Mode breakdown
    mode_stats = {}
    for t in trades:
        notes_p = _parse_notes(t.get("notes", "") or "")
        mode = notes_p.get("mode", "SWING")
        if mode not in mode_stats:
            mode_stats[mode] = {"n": 0, "wins": 0, "total_pnl": 0}
        mode_stats[mode]["n"] += 1
        if (t["pnl_pct"] or 0) > 0:
            mode_stats[mode]["wins"] += 1
        mode_stats[mode]["total_pnl"] += t["pnl_pct"] or 0

    for mode, stats in mode_stats.items():
        stats["win_rate"] = round(stats["wins"] / stats["n"] * 100, 1) if stats["n"] > 0 else 0
        stats["avg_pnl"] = round(stats["total_pnl"] / stats["n"], 3) if stats["n"] > 0 else 0

    # ML accuracy (check how many trades had ml_wp in notes)
    ml_stats = {"correct": 0, "incorrect": 0, "no_prediction": 0}
    for t in trades:
        notes_p = _parse_notes(t.get("notes", "") or "")
        ml_wp = notes_p.get("ml_wp")
        if ml_wp and ml_wp != "N/A":
            try:
                wp = float(ml_wp)
                actual_win = (t["pnl_pct"] or 0) > 0
                predicted_win = wp >= 0.5
                if actual_win == predicted_win:
                    ml_stats["correct"] += 1
                else:
                    ml_stats["incorrect"] += 1
            except (ValueError, TypeError):
                ml_stats["no_prediction"] += 1
        else:
            ml_stats["no_prediction"] += 1

    ml_total = ml_stats["correct"] + ml_stats["incorrect"]
    ml_accuracy = round(ml_stats["correct"] / ml_total * 100, 1) if ml_total > 0 else None

    # Sentiment highlights
    sentiment_stats = {"avg_sent": 0, "n_with_sent": 0, "boosted_trades": 0}
    for t in trades:
        notes_p = _parse_notes(t.get("notes", "") or "")
        sent = notes_p.get("sent")
        if sent and sent != "0" and sent != "0.0":
            try:
                sentiment_stats["avg_sent"] += float(sent)
                sentiment_stats["n_with_sent"] += 1
                boost = notes_p.get("sent_boost")
                if boost and int(float(boost)) != 0:
                    sentiment_stats["boosted_trades"] += 1
            except (ValueError, TypeError):
                pass
    if sentiment_stats["n_with_sent"] > 0:
        sentiment_stats["avg_sent"] = round(sentiment_stats["avg_sent"] / sentiment_stats["n_with_sent"], 3)

    # Post-exit analysis
    continued_count = sum(1 for t in trades if t.get("would_have_continued", "").startswith("YES"))
    missed_avg = 0
    missed_n = 0
    for t in trades:
        m30 = t.get("missed_pct_30m")
        if m30 is not None:
            missed_avg += m30
            missed_n += 1
    missed_avg = round(missed_avg / missed_n, 3) if missed_n > 0 else 0

    # Suggested tuner changes
    suggestions = []
    if win_rate < 50:
        suggestions.append("Win rate below 50% — consider tightening entry filters or raising ML_MIN_WIN_PROB")
    if win_rate > 70:
        suggestions.append("Win rate above 70% — engine performing well, consider increasing position sizes")
    if avg_pnl < 0:
        suggestions.append("Avg PnL negative — review TP/SL ratios, may need wider TP or tighter SL")
    if max_dd < -5:
        suggestions.append(f"Max drawdown {max_dd:.1f}% — consider reducing leverage or position sizes")
    if continued_count > n * 0.3:
        suggestions.append("30%+ trades continued after exit — TP may be too tight, consider widening")
    if ml_accuracy and ml_accuracy < 55:
        suggestions.append("ML accuracy below 55% — model may need more features or more training data")
    if ml_accuracy and ml_accuracy > 70:
        suggestions.append("ML accuracy above 70% — consider enabling ML_MIN_WIN_PROB filter (e.g. 0.5)")

    # Build report
    report = {
        "status": "complete",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "week_start": start_str,
        "week_end": end_str,
        "summary": {
            "n_trades": n,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_pnl": round(total_pnl, 3),
            "avg_pnl": round(avg_pnl, 3),
            "max_drawdown": round(max_dd, 3),
        },
        "top_performers": [
            {"symbol": t["symbol"], "side": t["side"], "pnl_pct": round(t["pnl_pct"] or 0, 3)}
            for t in top_3
        ],
        "worst_performers": [
            {"symbol": t["symbol"], "side": t["side"], "pnl_pct": round(t["pnl_pct"] or 0, 3)}
            for t in bottom_3
        ],
        "equity_curve": equity_points,
        "mode_breakdown": mode_stats,
        "ml_stats": {
            "accuracy": ml_accuracy,
            "correct": ml_stats["correct"],
            "incorrect": ml_stats["incorrect"],
            "no_prediction": ml_stats["no_prediction"],
        },
        "sentiment": sentiment_stats,
        "post_exit": {
            "continued_count": continued_count,
            "avg_missed_30m": missed_avg,
        },
        "suggestions": suggestions,
        "all_time": {
            "total_trades": dict(all_trades)["total"] if all_trades else 0,
            "total_wins": dict(all_trades)["wins"] if all_trades else 0,
            "avg_pnl": round(float(dict(all_trades)["avg_pnl"] or 0), 3) if all_trades else 0,
        },
    }

    # Save to file
    filename = f"weekly_{week_start.strftime('%Y%m%d')}_{week_end.strftime('%Y%m%d')}.json"
    filepath = os.path.join(_REPORTS_DIR, filename)
    with open(filepath, "w") as f:
        json.dump(report, f, indent=2, default=str)
    log.info("[REPORT] Weekly report saved: %s (%d trades, %.1f%% WR)", filename, n, win_rate)

    return report


def get_latest_report() -> dict | None:
    """Get the most recent weekly report."""
    _ensure_dirs()
    reports = sorted(Path(_REPORTS_DIR).glob("weekly_*.json"), reverse=True)
    if not reports:
        return None
    try:
        with open(reports[0]) as f:
            return json.load(f)
    except Exception:
        return None


def get_all_reports() -> list[dict]:
    """Get metadata for all weekly reports."""
    _ensure_dirs()
    reports = sorted(Path(_REPORTS_DIR).glob("weekly_*.json"), reverse=True)
    result = []
    for p in reports[:12]:  # Last 12 weeks
        try:
            with open(p) as f:
                r = json.load(f)
                result.append({
                    "filename": p.name,
                    "week_start": r.get("week_start"),
                    "week_end": r.get("week_end"),
                    "n_trades": r.get("summary", {}).get("n_trades", 0),
                    "win_rate": r.get("summary", {}).get("win_rate", 0),
                    "total_pnl": r.get("summary", {}).get("total_pnl", 0),
                })
        except Exception:
            continue
    return result
'''

MODULE_PATH = "/root/memecoin_engine/utils/weekly_report.py"
with open(MODULE_PATH, "w") as f:
    f.write(REPORT_CODE)
print(f"✅ Written {MODULE_PATH}")


# ── Part 2: Add API endpoints and background task to main.py ──

MAIN_PY = "/root/memecoin_engine/dashboard/backend/main.py"
with open(MAIN_PY, "r") as f:
    mcode = f.read()

REPORT_ENDPOINTS = '''

# ── Weekly Report Endpoints ────────────────────────────────────────────────────

@app.get("/api/reports/weekly")
async def reports_weekly_latest():
    """Get the latest weekly report."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from utils.weekly_report import get_latest_report, generate_weekly_report
        report = get_latest_report()
        if not report:
            # Generate on-demand
            report = generate_weekly_report()
        return report
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/reports/weekly/generate")
async def reports_weekly_generate():
    """Force generate a new weekly report."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from utils.weekly_report import generate_weekly_report
        return generate_weekly_report()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/reports/weekly/history")
async def reports_weekly_history():
    """Get list of all weekly reports."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from utils.weekly_report import get_all_reports
        return {"reports": get_all_reports()}
    except Exception as e:
        return {"error": str(e)}

'''

if '/api/reports/weekly' not in mcode:
    if '/api/sentiment/' in mcode:
        idx = mcode.find('/api/sentiment/')
        at_idx = mcode.rfind('\n@app.', 0, idx)
        if at_idx > 0:
            mcode = mcode[:at_idx] + REPORT_ENDPOINTS + mcode[at_idx:]
        else:
            mcode += REPORT_ENDPOINTS
    else:
        mcode += REPORT_ENDPOINTS
    print("✓ Added weekly report API endpoints")
else:
    print("⚠ Report endpoints already exist")

# ── Part 3: Add weekly report background task ──
REPORT_TASK = '''
async def _weekly_report_loop():
    """Generate weekly report every Sunday at 00:00 UTC."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    await asyncio.sleep(30)  # startup delay
    logger.info("[REPORT] Weekly report loop started")
    while True:
        try:
            now = datetime.now(timezone.utc) if 'timezone' in dir() else __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
            # Check if it's Sunday 00:xx UTC
            if now.weekday() == 6 and now.hour == 0 and now.minute < 5:
                from utils.weekly_report import generate_weekly_report
                report = generate_weekly_report()
                logger.info("[REPORT] Weekly report generated: %d trades, %.1f%% WR",
                            report.get("summary", {}).get("n_trades", 0),
                            report.get("summary", {}).get("win_rate", 0))
        except Exception as e:
            logger.debug("Weekly report error: %s", e)
        await asyncio.sleep(300)  # Check every 5 minutes

'''

if '_weekly_report_loop' not in mcode:
    # Insert before the lifespan function
    lifespan_idx = mcode.find('@asynccontextmanager')
    if lifespan_idx == -1:
        lifespan_idx = mcode.find('async def lifespan')
    if lifespan_idx > 0:
        mcode = mcode[:lifespan_idx] + REPORT_TASK + '\n' + mcode[lifespan_idx:]
        print("✓ Added _weekly_report_loop function")
    else:
        print("⚠ Could not find lifespan insertion point")

    # Register the task in lifespan
    if 'task_post_exit' in mcode and 'task_weekly_report' not in mcode:
        old_pe = 'task_post_exit = asyncio.create_task(_post_exit_monitor_loop())'
        new_pe = old_pe + '\n        task_weekly_report = asyncio.create_task(_weekly_report_loop())'
        if old_pe in mcode:
            mcode = mcode.replace(old_pe, new_pe, 1)
            print("✓ Registered task_weekly_report in lifespan")

            # Add to all_tasks
            old_tasks = 'task_post_exit)'
            new_tasks = 'task_post_exit, task_weekly_report)'
            mcode = mcode.replace(old_tasks, new_tasks, 1)
            print("✓ Added task_weekly_report to all_tasks")
        else:
            print("⚠ Could not find task_post_exit line")
else:
    print("⚠ Weekly report loop already exists")

with open(MAIN_PY, "w") as f:
    f.write(mcode)

print("\n✅ Weekly report module + endpoints + background task deployed")
