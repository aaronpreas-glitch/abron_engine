#!/usr/bin/env python3
"""
Patch auto_tune.py to:
1. Add outlier filtering (ignore 100x+ extremes)
2. Enhance indicator pattern analysis with MACD/ATR correlations
3. Add MACD crossover win rate analysis
"""

TUNE_PY = "/root/memecoin_engine/auto_tune.py"

with open(TUNE_PY, "r") as f:
    code = f.read()

changes = 0

# ── 1. Add outlier filtering to _adaptive_perp_tune ──
# After the DB read, filter out extreme outliers before analysis
if 'outlier' not in code.split('_adaptive_perp_tune')[1].split('env_updates')[0] if '_adaptive_perp_tune' in code else True:
    old_too_few = '''    if len(rows) < 10:
        log.info("adaptive_perp_tune: only %d trades with MAE/MFE — need 10+ to tune", len(rows))
        return []

    # Fetch supplementary analyses'''

    new_too_few = '''    if len(rows) < 10:
        log.info("adaptive_perp_tune: only %d trades with MAE/MFE — need 10+ to tune", len(rows))
        return []

    # Filter outliers: ignore trades with >100% PnL (data anomalies, flash crashes, etc.)
    filtered = []
    outlier_count = 0
    for r in rows:
        pnl = abs(float(r["pnl_pct"] or 0))
        if pnl > 100:  # >100% leveraged PnL = likely anomaly
            outlier_count += 1
            continue
        filtered.append(r)
    if outlier_count:
        log.info("[ADAPTIVE] Filtered %d outlier trades (>100%% PnL)", outlier_count)
    rows = filtered

    if len(rows) < 10:
        log.info("adaptive_perp_tune: only %d trades after outlier filter — need 10+", len(rows))
        return []

    # Fetch supplementary analyses'''

    if old_too_few in code:
        code = code.replace(old_too_few, new_too_few, 1)
        print("✓ Added outlier filtering to _adaptive_perp_tune")
        changes += 1
    else:
        print("⚠ Could not find outlier insertion point — may need manual check")
else:
    print("⚠ Outlier filtering already present")


# ── 2. Enhance _analyze_indicator_patterns with MACD/ATR ──
# Find the existing patterns function and add MACD analysis
if 'macd_bull_wins' not in code:
    # Add MACD analysis to the existing loop
    old_mom_match = '''        # Extract momentum from notes: momentum_5m=X.XXX or momentum_15m=X.XXX
        mom_match = re.search(r'momentum_(?:5m|15m)=([-\\d.]+)', notes)'''

    new_mom_match = '''        # Extract MACD crossover from notes
        macd_cross_match = re.search(r'macd_cross=(\\w+)', notes)
        if macd_cross_match:
            cross = macd_cross_match.group(1)
            patterns[mode]["n_with_macd"] = patterns[mode].get("n_with_macd", 0) + 1
            key = f"macd_{cross.lower()}_{'wins' if is_win else 'losses'}"
            patterns[mode][key] = patterns[mode].get(key, 0) + 1

        # Extract ATR from notes
        atr_match = re.search(r'atr_pct=([-\\d.]+)', notes)
        if atr_match:
            atr_v = float(atr_match.group(1))
            patterns[mode]["n_with_atr"] = patterns[mode].get("n_with_atr", 0) + 1
            if is_win:
                patterns[mode].setdefault("atr_wins", []).append(atr_v)
            else:
                patterns[mode].setdefault("atr_losses", []).append(atr_v)

        # Extract momentum from notes: momentum_5m=X.XXX or momentum_15m=X.XXX
        mom_match = re.search(r'momentum_(?:5m|15m)=([-\\d.]+)', notes)'''

    if old_mom_match in code:
        code = code.replace(old_mom_match, new_mom_match, 1)
        print("✓ Added MACD/ATR pattern extraction")
        changes += 1
    else:
        print("⚠ Could not find momentum_match pattern for MACD insertion")

    # Add MACD results to output
    old_results_log = '''        results[mode] = result
        log.info(
            "[INDICATORS] %s: rsi_data=%d mom_data=%d %s",
            mode, data["n_with_rsi"], data["n_with_mom"],
            {k: v for k, v in result.items() if k not in ("n_with_rsi", "n_with_mom")},
        )'''

    new_results_log = '''        # MACD crossover win rate
        n_macd = data.get("n_with_macd", 0)
        if n_macd >= 5:
            bull_wins = data.get("macd_bullish_wins", 0)
            bull_losses = data.get("macd_bullish_losses", 0)
            bear_wins = data.get("macd_bearish_wins", 0)
            bear_losses = data.get("macd_bearish_losses", 0)
            if bull_wins + bull_losses > 0:
                result["macd_bull_wr"] = round(bull_wins / (bull_wins + bull_losses) * 100, 1)
            if bear_wins + bear_losses > 0:
                result["macd_bear_wr"] = round(bear_wins / (bear_wins + bear_losses) * 100, 1)
            result["n_with_macd"] = n_macd

        # ATR-based vol analysis
        atr_wins = data.get("atr_wins", [])
        atr_losses = data.get("atr_losses", [])
        if atr_wins:
            result["avg_atr_win"] = round(sum(atr_wins) / len(atr_wins), 4)
        if atr_losses:
            result["avg_atr_loss"] = round(sum(atr_losses) / len(atr_losses), 4)
        n_atr = data.get("n_with_atr", 0)
        if n_atr > 0:
            result["n_with_atr"] = n_atr

        results[mode] = result
        log.info(
            "[INDICATORS] %s: rsi=%d mom=%d macd=%d atr=%d | %s",
            mode, data["n_with_rsi"], data["n_with_mom"], n_macd, n_atr,
            {k: v for k, v in result.items() if k.startswith(("rsi_", "macd_", "avg_atr"))},
        )'''

    if old_results_log in code:
        code = code.replace(old_results_log, new_results_log, 1)
        print("✓ Added MACD/ATR to indicator analysis output")
        changes += 1
    else:
        print("⚠ Could not find results_log pattern")
else:
    print("⚠ MACD analysis already in indicator patterns")


with open(TUNE_PY, "w") as f:
    f.write(code)

print(f"\n✅ auto_tune.py enhanced ({changes} change blocks)")
