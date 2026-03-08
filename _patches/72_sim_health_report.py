"""
Patch 72 — 24h Simulation Health Report (Full)

Bug fixes:
  - DD monitor loop: realized_pnl_pct → pnl_pct, entry_ts_utc → opened_ts_utc
  - live-checklist: 3× realized_pnl_pct → pnl_pct
  - simulate-finalize: realized_pnl_pct → pnl_pct, entry_ts_utc → opened_ts_utc
  - brain/sim-review: skipped_signals_log ts → ts_utc

New additions:
  - /api/brain/sim-review: add bull_readiness_score, discipline_score,
    dynamic_exit_lift, trades_today, criteria_detail fields
  - 24h auto-trigger metadata: include new metric fields
  - next_action: improved guidance text
"""
import subprocess, sys
from pathlib import Path

ROOT = Path("/root/memecoin_engine")
MAIN = ROOT / "dashboard/backend/main.py"

text = MAIN.read_text()

# ──────────────────────────────────────────────────────────────────────────────
# FIX 1 — DD monitor loop: realized_pnl_pct / entry_ts_utc
# ──────────────────────────────────────────────────────────────────────────────
OLD1 = (
    '                    "SELECT realized_pnl_pct, exit_reason FROM perp_positions "\n'
    '                    "WHERE dry_run=1 AND entry_ts_utc >= ? AND status=\'CLOSED\' "\n'
    '                    "AND realized_pnl_pct IS NOT NULL ORDER BY entry_ts_utc",'
)
NEW1 = (
    '                    "SELECT pnl_pct, exit_reason FROM perp_positions "\n'
    '                    "WHERE dry_run=1 AND opened_ts_utc >= ? AND status=\'CLOSED\' "\n'
    '                    "AND pnl_pct IS NOT NULL ORDER BY opened_ts_utc",'
)
assert OLD1 in text, "FIX1 anchor not found"
text = text.replace(OLD1, NEW1, 1)
print("✓ FIX1: DD monitor loop column names")

# ──────────────────────────────────────────────────────────────────────────────
# FIX 2a — live-checklist: avg_pnl for bull_score (avg_pnl_row variable)
# ──────────────────────────────────────────────────────────────────────────────
OLD2a = (
    '                avg_pnl_row = c.execute(\n'
    '                    "SELECT AVG(realized_pnl_pct) FROM perp_positions "\n'
    '                    "WHERE status=\'CLOSED\' AND closed_ts_utc >= ?", (since_7d,)).fetchone()\n'
    '                avg_pnl = float(avg_pnl_row[0] or 0)'
)
NEW2a = (
    '                avg_pnl_row = c.execute(\n'
    '                    "SELECT AVG(pnl_pct) FROM perp_positions "\n'
    '                    "WHERE status=\'CLOSED\' AND closed_ts_utc >= ?", (since_7d,)).fetchone()\n'
    '                avg_pnl = float(avg_pnl_row[0] or 0)'
)
assert OLD2a in text, "FIX2a anchor not found"
text = text.replace(OLD2a, NEW2a, 1)
print("✓ FIX2a: live-checklist avg_pnl_row")

# ──────────────────────────────────────────────────────────────────────────────
# FIX 2b — live-checklist: tl_pnl for lift computation
# ──────────────────────────────────────────────────────────────────────────────
OLD2b = (
    '                tl_pnl_row = c.execute(\n'
    '                    "SELECT AVG(realized_pnl_pct) FROM perp_positions "\n'
    '                    "WHERE status=\'CLOSED\' AND exit_reason=\'TIME_LIMIT\' AND closed_ts_utc >= ?",'
)
NEW2b = (
    '                tl_pnl_row = c.execute(\n'
    '                    "SELECT AVG(pnl_pct) FROM perp_positions "\n'
    '                    "WHERE status=\'CLOSED\' AND exit_reason=\'TIME_LIMIT\' AND closed_ts_utc >= ?",'
)
assert OLD2b in text, "FIX2b anchor not found"
text = text.replace(OLD2b, NEW2b, 1)
print("✓ FIX2b: live-checklist tl_pnl_row")

# ──────────────────────────────────────────────────────────────────────────────
# FIX 2c — live-checklist: avg_pnl_val for 7d avg PNL check
# ──────────────────────────────────────────────────────────────────────────────
OLD2c = (
    '                row = c.execute(\n'
    '                    "SELECT AVG(realized_pnl_pct) FROM perp_positions "\n'
    '                    "WHERE status=\'CLOSED\' AND closed_ts_utc >= ?", (since_7d,)).fetchone()\n'
    '                avg_pnl_val = round(float(row[0] or 0), 3)'
)
NEW2c = (
    '                row = c.execute(\n'
    '                    "SELECT AVG(pnl_pct) FROM perp_positions "\n'
    '                    "WHERE status=\'CLOSED\' AND closed_ts_utc >= ?", (since_7d,)).fetchone()\n'
    '                avg_pnl_val = round(float(row[0] or 0), 3)'
)
assert OLD2c in text, "FIX2c anchor not found"
text = text.replace(OLD2c, NEW2c, 1)
print("✓ FIX2c: live-checklist avg_pnl_val")

# ──────────────────────────────────────────────────────────────────────────────
# FIX 3 — simulate-finalize: realized_pnl_pct / entry_ts_utc (3 occurrences)
# ──────────────────────────────────────────────────────────────────────────────
OLD3 = (
    '                    trades = c.execute(\n'
    '                        "SELECT symbol, side, status, realized_pnl_pct, exit_reason "\n'
    '                        "FROM perp_positions WHERE dry_run=1 AND entry_ts_utc >= ? ORDER BY entry_ts_utc",\n'
    '                        (start_ts,)\n'
    '                    ).fetchall()\n'
    '                    closed = [t for t in trades if t["status"] == "CLOSED" and t["realized_pnl_pct"] is not None]\n'
    '                    n_closed = len(closed)\n'
    '                    pnls = [float(t["realized_pnl_pct"]) for t in closed] if closed else []'
)
NEW3 = (
    '                    trades = c.execute(\n'
    '                        "SELECT symbol, side, status, pnl_pct, exit_reason "\n'
    '                        "FROM perp_positions WHERE dry_run=1 AND opened_ts_utc >= ? ORDER BY opened_ts_utc",\n'
    '                        (start_ts,)\n'
    '                    ).fetchall()\n'
    '                    closed = [t for t in trades if t["status"] == "CLOSED" and t["pnl_pct"] is not None]\n'
    '                    n_closed = len(closed)\n'
    '                    pnls = [float(t["pnl_pct"]) for t in closed] if closed else []'
)
assert OLD3 in text, "FIX3 anchor not found"
text = text.replace(OLD3, NEW3, 1)
print("✓ FIX3: simulate-finalize column names")

# ──────────────────────────────────────────────────────────────────────────────
# FIX 4 — brain/sim-review: skipped_signals_log ts → ts_utc
# ──────────────────────────────────────────────────────────────────────────────
OLD4 = '"WHERE ts >= datetime(\'now\', \'-24 hours\')"'
NEW4 = '"WHERE ts_utc >= datetime(\'now\', \'-24 hours\')"'
assert OLD4 in text, "FIX4 anchor not found"
text = text.replace(OLD4, NEW4, 1)
print("✓ FIX4: skipped_signals_log ts → ts_utc")

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 1 — brain/sim-review: add new fields to result init dict
# ──────────────────────────────────────────────────────────────────────────────
OLD_INIT = (
    '        "skipped_signals_count": 0,\n'
    '        "good_call_rate": 0.0,\n'
    '        "next_action": "",\n'
    '        "ts": datetime.utcnow().isoformat() + "Z",\n'
    '    }'
)
NEW_INIT = (
    '        "skipped_signals_count": 0,\n'
    '        "good_call_rate": 0.0,\n'
    '        "bull_readiness_score": 0.0,\n'
    '        "discipline_score": 0.0,\n'
    '        "dynamic_exit_lift": 0.0,\n'
    '        "trades_today": 0,\n'
    '        "criteria_detail": [],\n'
    '        "next_action": "",\n'
    '        "ts": datetime.utcnow().isoformat() + "Z",\n'
    '    }'
)
assert OLD_INIT in text, "FEATURE1 init dict anchor not found"
text = text.replace(OLD_INIT, NEW_INIT, 1)
print("✓ FEATURE1: sim-review result init dict extended")

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 2 — brain/sim-review: add fresh metric computation block before next_action
# ──────────────────────────────────────────────────────────────────────────────
OLD_NEXT = (
    '    except Exception as exc:\n'
    '        log.warning("brain_sim_review error: %s", exc)\n'
    '        result["error"] = str(exc)\n'
    '    # Next-action suggestion'
)
NEW_NEXT = (
    '    except Exception as exc:\n'
    '        log.warning("brain_sim_review error: %s", exc)\n'
    '        result["error"] = str(exc)\n'
    '    # ── Fresh live metrics: bull readiness, discipline, dynamic exit lift ──\n'
    '    try:\n'
    '        from datetime import timedelta as _td72\n'
    '        _since7 = (datetime.utcnow() - _td72(days=7)).isoformat()\n'
    '        _today  = datetime.utcnow().strftime("%Y-%m-%d")\n'
    '        with _sq.connect(db_path) as _cc:\n'
    '            _cc.row_factory = _sq.Row\n'
    '            # Trades closed today (sim)\n'
    '            result["trades_today"] = int(_cc.execute(\n'
    '                "SELECT COUNT(*) FROM perp_positions WHERE status=\'CLOSED\' AND dry_run=1 "\n'
    '                "AND closed_ts_utc >= ?", (_today,)).fetchone()[0] or 0)\n'
    '            # Dynamic exit stats\n'
    '            _tot_ex = int(_cc.execute("SELECT COUNT(*) FROM dynamic_exit_log WHERE ts_utc >= ?",\n'
    '                                      (_since7,)).fetchone()[0] or 0)\n'
    '            _gd_ex  = int(_cc.execute(\n'
    '                "SELECT COUNT(*) FROM dynamic_exit_log WHERE ts_utc >= ? AND outcome=\'good_call\'",\n'
    '                (_since7,)).fetchone()[0] or 0)\n'
    '            _gc_r = (_gd_ex / _tot_ex * 100) if _tot_ex >= 5 else 50.0\n'
    '            result["discipline_score"] = round((_gd_ex / _tot_ex * 100) if _tot_ex >= 5 else 0.0, 1)\n'
    '            # 7d avg PNL and TIME_LIMIT PNL for lift\n'
    '            _ap7 = float(_cc.execute(\n'
    '                "SELECT AVG(pnl_pct) FROM perp_positions WHERE status=\'CLOSED\' AND closed_ts_utc >= ?",\n'
    '                (_since7,)).fetchone()[0] or 0)\n'
    '            _tl7 = float(_cc.execute(\n'
    '                "SELECT AVG(pnl_pct) FROM perp_positions WHERE status=\'CLOSED\' "\n'
    '                "AND exit_reason=\'TIME_LIMIT\' AND closed_ts_utc >= ?",\n'
    '                (_since7,)).fetchone()[0] or 0)\n'
    '            result["dynamic_exit_lift"] = round(_ap7 - _tl7, 2)\n'
    '            # Bull readiness score\n'
    '            _disc_pts = min(30.0, _gc_r / 100 * 30)\n'
    '            _ml_pts   = min(20.0, _gc_r / 100 * 20)\n'
    '            _pnl_pts  = min(20.0, max(0.0, _ap7 / 1.0) * 20)\n'
    '            _lift_pts = min(20.0, max(0.0, result["dynamic_exit_lift"] / 0.5) * 20)\n'
    '            _tc7 = int(_cc.execute("SELECT COUNT(*) FROM perp_positions "\n'
    '                                   "WHERE status=\'CLOSED\' AND closed_ts_utc >= ?",\n'
    '                                   (_since7,)).fetchone()[0] or 0)\n'
    '            _tl_c7 = int(_cc.execute(\n'
    '                "SELECT COUNT(*) FROM perp_positions WHERE status=\'CLOSED\' "\n'
    '                "AND exit_reason=\'TIME_LIMIT\' AND closed_ts_utc >= ?",\n'
    '                (_since7,)).fetchone()[0] or 0)\n'
    '            _tlp7 = (_tl_c7 / _tc7 * 100) if _tc7 >= 5 else 100.0\n'
    '            _tl_pts = min(10.0, max(0.0, (100 - _tlp7) / 30.0) * 10)\n'
    '            result["bull_readiness_score"] = round(_disc_pts + _ml_pts + _pnl_pts + _lift_pts + _tl_pts, 1)\n'
    '            # Criteria detail for modal checklist\n'
    '            result["criteria_detail"] = [\n'
    '                {"label": "Avg PNL/trade > 0.5%",    "pass": _ap7 > 0.5,\n'
    '                 "value": round(_ap7, 2), "target": 0.5},\n'
    '                {"label": "Max DD < 3%",              "pass": result["max_dd_pct"] < 3.0,\n'
    '                 "value": result["max_dd_pct"], "target": 3.0},\n'
    '                {"label": "Win rate \\u2265 45%",       "pass": result["win_rate_pct"] >= 45.0,\n'
    '                 "value": result["win_rate_pct"], "target": 45.0},\n'
    '                {"label": "TIME_LIMIT% < 70%",        "pass": result["time_limit_pct"] < 70.0,\n'
    '                 "value": result["time_limit_pct"], "target": 70.0},\n'
    '                {"label": "Ran \\u2265 24h",             "pass": result["hours_active"] >= 24.0,\n'
    '                 "value": round(result["hours_active"], 1), "target": 24.0},\n'
    '                {"label": "\\u2265 3 closed trades",    "pass": result["closed_count"] >= 3,\n'
    '                 "value": result["closed_count"], "target": 3},\n'
    '                {"label": "Discipline score > 75",   "pass": result["discipline_score"] > 75.0,\n'
    '                 "value": result["discipline_score"], "target": 75.0},\n'
    '                {"label": "Bull Readiness \\u2265 75",  "pass": result["bull_readiness_score"] >= 75.0,\n'
    '                 "value": result["bull_readiness_score"], "target": 75.0},\n'
    '            ]\n'
    '    except Exception as _fx:\n'
    '        log.debug("sim_review live_metrics error: %s", _fx)\n'
    '    # Next-action suggestion'
)
assert OLD_NEXT in text, "FEATURE2 next_action anchor not found"
text = text.replace(OLD_NEXT, NEW_NEXT, 1)
print("✓ FEATURE2: sim-review live metrics computation block")

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 3 — 24h milestone metadata: enrich stats dict with new fields
# ──────────────────────────────────────────────────────────────────────────────
OLD_STATS = (
    '                        stats = {\n'
    '                            "hours_active": round(hours_active, 1),\n'
    '                            "closed_count": n_closed, "avg_pnl_pct": avg_pnl,\n'
    '                            "total_pnl_pct": total_pnl, "win_rate_pct": win_rate,\n'
    '                            "time_limit_pct": tl_pct, "max_dd_pct": max_dd_f,\n'
    '                            "recommendation": {\n'
    '                                "level": rec_level, "message": rec_msg,\n'
    '                                "passed": passed_c, "total": total_c,\n'
    '                            },\n'
    '                        }'
)
NEW_STATS = (
    '                        # Compute supplemental metrics for rich metadata\n'
    '                        try:\n'
    '                            _m_disc = round(\n'
    '                                (sum(1 for t in closed if t[1] != "TIME_LIMIT") / len(closed) * 100)\n'
    '                                if closed else 0.0, 1)\n'
    '                            _m_lift = round(\n'
    '                                (avg_pnl - (sum(float(t[0]) for t in closed if t[1] == "TIME_LIMIT") /\n'
    '                                 max(1, sum(1 for t in closed if t[1] == "TIME_LIMIT"))))\n'
    '                                if any(t[1] == "TIME_LIMIT" for t in closed) else 0.0, 2)\n'
    '                        except Exception:\n'
    '                            _m_disc = 0.0; _m_lift = 0.0\n'
    '                        stats = {\n'
    '                            "hours_active": round(hours_active, 1),\n'
    '                            "closed_count": n_closed, "avg_pnl_pct": avg_pnl,\n'
    '                            "total_pnl_pct": total_pnl, "win_rate_pct": win_rate,\n'
    '                            "time_limit_pct": tl_pct, "max_dd_pct": max_dd_f,\n'
    '                            "discipline_score": _m_disc,\n'
    '                            "dynamic_exit_lift": _m_lift,\n'
    '                            "recommendation": {\n'
    '                                "level": rec_level, "message": rec_msg,\n'
    '                                "passed": passed_c, "total": total_c,\n'
    '                            },\n'
    '                        }'
)
assert OLD_STATS in text, "FEATURE3 stats dict anchor not found"
text = text.replace(OLD_STATS, NEW_STATS, 1)
print("✓ FEATURE3: 24h milestone metadata enriched")

# ──────────────────────────────────────────────────────────────────────────────
# Write + compile
# ──────────────────────────────────────────────────────────────────────────────
MAIN.write_text(text)
r = subprocess.run([sys.executable, "-m", "py_compile", str(MAIN)], capture_output=True, text=True)
if r.returncode != 0:
    print("✗ compile error:", r.stderr)
    sys.exit(1)
print("✓ main.py compiles OK — all backend changes applied")
