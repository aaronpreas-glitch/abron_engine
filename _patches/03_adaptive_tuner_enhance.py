#!/usr/bin/env python3
"""
Patch auto_tune.py to:
1. Add RSI/momentum pattern analysis to _process_perp_learnings
2. Add post-exit data integration into _adaptive_perp_tune
3. Add _analyze_indicator_patterns() helper
"""

TUNE_PY = "/root/memecoin_engine/auto_tune.py"

with open(TUNE_PY, "r") as f:
    code = f.read()

# ── 1. Add _analyze_indicator_patterns() function ──
INDICATOR_ANALYSIS = '''
def _analyze_indicator_patterns() -> dict:
    """
    Read closed perp trades and correlate RSI / momentum at entry with outcomes.

    Returns a dict of insights per mode:
    {
      "SCALP": {
        "rsi_win_range": [30, 55],   # RSI range where wins cluster
        "rsi_loss_range": [65, 85],  # RSI range where losses cluster
        "momentum_win_avg": 0.45,    # avg momentum on winning trades
        "momentum_loss_avg": -0.12,
        "n_with_indicators": 15,
      },
      ...
    }
    """
    import sqlite3 as _sq
    import re
    db_path = str(BASE_DIR / "data_storage" / "engine.db")

    try:
        with _sq.connect(db_path) as conn:
            conn.row_factory = _sq.Row
            rows = conn.execute("""
                SELECT notes, pnl_pct, leverage, exit_reason,
                    CASE
                      WHEN notes LIKE '%mode=SCALP%' THEN 'SCALP'
                      WHEN notes LIKE '%mode=MID%'   THEN 'MID'
                      ELSE 'SWING'
                    END as mode
                FROM perp_positions
                WHERE status='CLOSED' AND notes IS NOT NULL AND pnl_pct IS NOT NULL
                ORDER BY closed_ts_utc DESC
                LIMIT 300
            """).fetchall()
    except Exception as exc:
        log.warning("indicator_patterns DB read failed: %s", exc)
        return {}

    from collections import defaultdict
    patterns = defaultdict(lambda: {
        "rsi_wins": [], "rsi_losses": [],
        "mom_wins": [], "mom_losses": [],
        "n_with_rsi": 0, "n_with_mom": 0,
    })

    for r in rows:
        notes = r["notes"] or ""
        mode = r["mode"]
        lev = float(r["leverage"] or 1)
        raw_pnl = float(r["pnl_pct"] or 0) / lev
        is_win = raw_pnl > 0

        # Extract RSI from notes: rsi_14=XX.XX
        rsi_match = re.search(r'rsi_14=([\d.]+)', notes)
        if rsi_match:
            rsi_val = float(rsi_match.group(1))
            patterns[mode]["n_with_rsi"] += 1
            if is_win:
                patterns[mode]["rsi_wins"].append(rsi_val)
            else:
                patterns[mode]["rsi_losses"].append(rsi_val)

        # Extract momentum from notes: momentum_5m=X.XXX or momentum_15m=X.XXX
        mom_match = re.search(r'momentum_(?:5m|15m)=([-\d.]+)', notes)
        if mom_match:
            mom_val = float(mom_match.group(1))
            patterns[mode]["n_with_mom"] += 1
            if is_win:
                patterns[mode]["mom_wins"].append(mom_val)
            else:
                patterns[mode]["mom_losses"].append(mom_val)

    results = {}
    for mode, data in patterns.items():
        result = {"n_with_rsi": data["n_with_rsi"], "n_with_mom": data["n_with_mom"]}

        if data["rsi_wins"]:
            rw = sorted(data["rsi_wins"])
            result["rsi_win_avg"] = round(sum(rw) / len(rw), 1)
            result["rsi_win_range"] = [round(rw[int(len(rw)*0.25)], 1), round(rw[int(len(rw)*0.75)], 1)]
        if data["rsi_losses"]:
            rl = sorted(data["rsi_losses"])
            result["rsi_loss_avg"] = round(sum(rl) / len(rl), 1)
            result["rsi_loss_range"] = [round(rl[int(len(rl)*0.25)], 1), round(rl[int(len(rl)*0.75)], 1)]
        if data["mom_wins"]:
            result["momentum_win_avg"] = round(sum(data["mom_wins"]) / len(data["mom_wins"]), 4)
        if data["mom_losses"]:
            result["momentum_loss_avg"] = round(sum(data["mom_losses"]) / len(data["mom_losses"]), 4)

        results[mode] = result
        log.info(
            "[INDICATORS] %s: rsi_data=%d mom_data=%d %s",
            mode, data["n_with_rsi"], data["n_with_mom"],
            {k: v for k, v in result.items() if k not in ("n_with_rsi", "n_with_mom")},
        )

    return results


def _analyze_post_exit_data() -> dict:
    """
    Read post-exit tracking to understand if we're leaving money on the table.

    Returns per-mode insights:
    {
      "SCALP": {
        "avg_missed_5m": 0.15,
        "avg_missed_30m": 0.45,
        "pct_continued": 60.0,  # % of trades where price kept going after exit
        "tp_too_tight_evidence": True/False,
        "n": 20,
      }
    }
    """
    import sqlite3 as _sq
    db_path = str(BASE_DIR / "data_storage" / "engine.db")

    try:
        with _sq.connect(db_path) as conn:
            conn.row_factory = _sq.Row
            rows = conn.execute("""
                SELECT mode, exit_reason, missed_pct_5m, missed_pct_15m,
                       missed_pct_30m, would_have_continued
                FROM post_exit_tracking
                WHERE price_30m IS NOT NULL
                ORDER BY exit_ts DESC LIMIT 200
            """).fetchall()
    except Exception:
        return {}

    from collections import defaultdict
    by_mode = defaultdict(lambda: {
        "missed_5m": [], "missed_15m": [], "missed_30m": [],
        "continued": 0, "n": 0, "tp_exits": 0, "tp_missed_30m": [],
    })

    for r in rows:
        mode = r["mode"] or "SWING"
        by_mode[mode]["n"] += 1
        by_mode[mode]["missed_5m"].append(float(r["missed_pct_5m"] or 0))
        by_mode[mode]["missed_15m"].append(float(r["missed_pct_15m"] or 0))
        by_mode[mode]["missed_30m"].append(float(r["missed_pct_30m"] or 0))
        if str(r["would_have_continued"] or "").startswith("YES"):
            by_mode[mode]["continued"] += 1
        if str(r["exit_reason"] or "").startswith("TP"):
            by_mode[mode]["tp_exits"] += 1
            by_mode[mode]["tp_missed_30m"].append(float(r["missed_pct_30m"] or 0))

    results = {}
    for mode, data in by_mode.items():
        n = data["n"]
        if n == 0:
            continue
        avg_5 = sum(data["missed_5m"]) / n
        avg_15 = sum(data["missed_15m"]) / n
        avg_30 = sum(data["missed_30m"]) / n
        pct_cont = data["continued"] / n * 100

        # If TP exits consistently show price continuing, TP is too tight
        tp_too_tight = False
        if data["tp_exits"] >= 3 and data["tp_missed_30m"]:
            avg_tp_missed = sum(data["tp_missed_30m"]) / len(data["tp_missed_30m"])
            tp_too_tight = avg_tp_missed > 0.5  # price went 0.5%+ higher after TP exit

        results[mode] = {
            "n": n,
            "avg_missed_5m": round(avg_5, 3),
            "avg_missed_15m": round(avg_15, 3),
            "avg_missed_30m": round(avg_30, 3),
            "pct_continued": round(pct_cont, 1),
            "tp_too_tight_evidence": tp_too_tight,
        }

        log.info(
            "[POST-EXIT ANALYSIS] %s: n=%d missed_30m=%.3f%% continued=%.0f%% tp_tight=%s",
            mode, n, avg_30, pct_cont, tp_too_tight,
        )

    return results

'''

if '_analyze_indicator_patterns' not in code:
    # Insert before _adaptive_perp_tune
    code = code.replace(
        'def _adaptive_perp_tune() -> list[str]:',
        INDICATOR_ANALYSIS + '\ndef _adaptive_perp_tune() -> list[str]:',
    )
    print("✓ Added _analyze_indicator_patterns() and _analyze_post_exit_data()")
else:
    print("⚠ _analyze_indicator_patterns already exists, skipping")

# ── 2. Integrate post-exit data into _adaptive_perp_tune ──
# Add post-exit analysis call at the start of _adaptive_perp_tune and use it in the tuning logic

if 'post_exit_insights' not in code:
    # After the initial DB read in _adaptive_perp_tune, add post-exit + indicator analysis calls
    old_too_few = '''    if len(rows) < 10:
        log.info("adaptive_perp_tune: only %d trades with MAE/MFE — need 10+ to tune", len(rows))
        return []'''

    new_too_few = '''    if len(rows) < 10:
        log.info("adaptive_perp_tune: only %d trades with MAE/MFE — need 10+ to tune", len(rows))
        return []

    # Fetch supplementary analyses
    indicator_insights = _analyze_indicator_patterns()
    post_exit_insights = _analyze_post_exit_data()'''

    if old_too_few in code:
        code = code.replace(old_too_few, new_too_few, 1)
        print("✓ Added indicator + post-exit analysis calls to _adaptive_perp_tune")
    else:
        print("⚠ Could not find insertion point for analysis calls")

    # Add post-exit TP adjustment after the existing TP raise logic for each mode
    # For SCALP: after "SCALP TP raised" block, add post-exit check
    POSTEX_SCALP = '''
            # Post-exit evidence: if price keeps going after TP exits, raise TP
            pe = post_exit_insights.get("SCALP", {})
            if pe.get("tp_too_tight_evidence") and pe.get("n", 0) >= 5:
                avg_missed = pe.get("avg_missed_30m", 0)
                if avg_missed > 0.3:
                    delta = min(avg_missed * 0.15, 0.5)  # raise by 15% of missed, capped 0.5%
                    new_tp_pe = round(min(float(env_updates.get("SCALP_TP_PCT", str(current_tp))) + delta, 5.0), 2)
                    if new_tp_pe > current_tp:
                        env_updates["SCALP_TP_PCT"] = str(new_tp_pe)
                        changes.append(f"SCALP TP (post-exit): -> {new_tp_pe} (missed_30m={avg_missed:.2f}%)")'''

    old_elif_mid = '''        elif mode == "MID":'''
    new_elif_mid = POSTEX_SCALP + '''

        elif mode == "MID":'''

    if 'post-exit' not in code.split('_adaptive_perp_tune')[1] if '_adaptive_perp_tune' in code else True:
        if old_elif_mid in code:
            code = code.replace(old_elif_mid, new_elif_mid, 1)
            print("✓ Added post-exit TP adjustment for SCALP")
        else:
            print("⚠ Could not find SCALP→MID transition for post-exit injection")

    # Similarly for MID
    POSTEX_MID = '''
            # Post-exit evidence for MID
            pe = post_exit_insights.get("MID", {})
            if pe.get("tp_too_tight_evidence") and pe.get("n", 0) >= 5:
                avg_missed = pe.get("avg_missed_30m", 0)
                if avg_missed > 0.5:
                    delta = min(avg_missed * 0.12, 1.0)
                    new_tp_pe = round(min(float(env_updates.get("MID_TP_PCT", str(current_tp))) + delta, 15.0), 1)
                    if new_tp_pe > current_tp:
                        env_updates["MID_TP_PCT"] = str(new_tp_pe)
                        changes.append(f"MID TP (post-exit): -> {new_tp_pe}")'''

    old_elif_swing = '''        elif mode == "SWING":'''
    new_elif_swing = POSTEX_MID + '''

        elif mode == "SWING":'''

    if old_elif_swing in code:
        code = code.replace(old_elif_swing, new_elif_swing, 1)
        print("✓ Added post-exit TP adjustment for MID")

else:
    print("⚠ post_exit_insights already in code, skipping")

# ── 3. Add indicator patterns to perp_profiles.json output ──
if 'indicator_patterns' not in code:
    old_profiles_write = '''        out_path = BASE_DIR / "data_storage" / "perp_profiles.json"'''
    new_profiles_write = '''        # Add indicator pattern analysis to profiles
        try:
            ind_patterns = _analyze_indicator_patterns()
            for mode_key in profiles:
                if mode_key in ind_patterns:
                    profiles[mode_key]["indicators"] = ind_patterns[mode_key]
        except Exception:
            pass

        out_path = BASE_DIR / "data_storage" / "perp_profiles.json"'''

    if old_profiles_write in code:
        code = code.replace(old_profiles_write, new_profiles_write, 1)
        print("✓ Added indicator patterns to perp_profiles.json")
    else:
        print("⚠ Could not find profiles write point")
else:
    print("⚠ indicator_patterns already in profiles output, skipping")

with open(TUNE_PY, "w") as f:
    f.write(code)

print("\n✅ auto_tune.py patched successfully")
