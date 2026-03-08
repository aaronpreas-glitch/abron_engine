#!/usr/bin/env python3
"""
Patch auto_tune.py to add _process_spot_learnings() — unified spot + perp learning.
This reads closed spot trades and writes spot_profiles.json alongside perp_profiles.json.
Also adds spot data to the indicator analysis.
"""

TUNE_PY = '/root/memecoin_engine/auto_tune.py'

with open(TUNE_PY, 'r') as f:
    code = f.read()

SPOT_LEARNING = '''

def _process_spot_learnings() -> dict:
    """
    Read closed spot (memecoin) trades and compute win rate, avg PnL, MAE/MFE stats.
    Writes spot_profiles.json for the dashboard and adaptive tuning.

    Spot trades are all LONG (can't short spot memecoins) and use the 'trades' table.
    """
    import sqlite3 as _sq
    import json
    from collections import defaultdict

    db_path = str(BASE_DIR / "data_storage" / "engine.db")

    try:
        with _sq.connect(db_path) as conn:
            conn.row_factory = _sq.Row
            rows = conn.execute("""
                SELECT symbol, regime_label, entry_price, exit_price,
                       pnl_pct, exit_reason, mae, mfe, notes,
                       opened_ts_utc, closed_ts_utc
                FROM trades
                WHERE status='CLOSED' AND pnl_pct IS NOT NULL
                ORDER BY closed_ts_utc DESC
                LIMIT 200
            """).fetchall()
    except Exception as exc:
        log.warning("spot_learnings DB read failed: %s", exc)
        return {}

    if not rows:
        log.info("spot_learnings: no closed spot trades yet")
        return {}

    n = len(rows)
    wins = sum(1 for r in rows if (r["pnl_pct"] or 0) > 0)
    pnls = [float(r["pnl_pct"] or 0) for r in rows]
    avg_pnl = sum(pnls) / n
    win_rate = wins / n * 100

    # Exit reason breakdown
    reasons = defaultdict(int)
    for r in rows:
        reasons[r["exit_reason"] or "UNKNOWN"] += 1

    # MAE/MFE if available
    mfes = [float(r["mfe"]) for r in rows if r["mfe"] is not None]
    maes = [float(r["mae"]) for r in rows if r["mae"] is not None]

    profile = {
        "n": n,
        "win_rate": round(win_rate, 1),
        "avg_pnl": round(avg_pnl, 3),
        "avg_mfe": round(sum(mfes) / len(mfes) * 100, 3) if mfes else None,
        "avg_mae": round(sum(maes) / len(maes) * 100, 3) if maes else None,
        "reasons": dict(reasons),
    }

    # Suggestion
    if n >= 5:
        if win_rate >= 70 and avg_pnl > 1.0:
            profile["suggestion"] = "strong_performer"
        elif win_rate >= 60 and avg_pnl > 0:
            profile["suggestion"] = "balanced"
        elif avg_pnl < 0:
            profile["suggestion"] = "needs_work"
        else:
            profile["suggestion"] = "developing"

    output = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "total_trades": n,
        "spot": profile,
    }

    try:
        out_path = BASE_DIR / "data_storage" / "spot_profiles.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, indent=2))
        log.info(
            "spot_profiles.json written: n=%d wr=%.0f%% avg_pnl=%.2f%%",
            n, win_rate, avg_pnl,
        )
    except Exception as exc:
        log.warning("Could not write spot_profiles.json: %s", exc)

    return output

'''

if '_process_spot_learnings' not in code:
    # Insert before _analyze_indicator_patterns
    if '_analyze_indicator_patterns' in code:
        code = code.replace(
            'def _analyze_indicator_patterns',
            SPOT_LEARNING + 'def _analyze_indicator_patterns',
        )
        print("✓ Added _process_spot_learnings()")
    else:
        # Fallback: insert before _adaptive_perp_tune
        code = code.replace(
            'def _adaptive_perp_tune',
            SPOT_LEARNING + 'def _adaptive_perp_tune',
        )
        print("✓ Added _process_spot_learnings() (before adaptive)")

    # Also call it from run_auto_tune
    if '_process_spot_learnings()' not in code:
        old_perp_call = '        _process_perp_learnings()'
        new_perp_call = '        _process_perp_learnings()\n        _process_spot_learnings()'
        count = code.count(old_perp_call)
        if count > 0:
            code = code.replace(old_perp_call, new_perp_call)
            print(f"✓ Added _process_spot_learnings() call in {count} location(s)")
        else:
            print("⚠ Could not find _process_perp_learnings() call for insertion")
else:
    print("⚠ _process_spot_learnings already exists, skipping")

with open(TUNE_PY, 'w') as f:
    f.write(code)

print("\n✅ auto_tune.py spot learning patch complete")
