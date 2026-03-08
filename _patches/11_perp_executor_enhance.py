#!/usr/bin/env python3
"""
Patch perp_executor.py to:
1. Add MACD/ATR to trade notes (all 3 modes)
2. Add indicator-based position sizing (RSI sweet spot + MACD confirmation = 1.5x)
3. Add risk escalation (3-loss streak auto-reduces size to 0.5x)
4. Add ATR-based volatility TP/SL adjustment
"""

PERP_PY = "/root/memecoin_engine/utils/perp_executor.py"

with open(PERP_PY, "r") as f:
    code = f.read()

changes = 0

# ── 1. Add MACD/ATR keys to momentum capture loop ──
old_mom = '''for mk in ("momentum_5m", "momentum_15m", "momentum_1h", "rsi_14"):
            if mk in signal:
                mom_str += f"|{mk}={signal[mk]}"'''
new_mom = '''for mk in ("momentum_5m", "momentum_15m", "momentum_1h", "rsi_14", "macd_hist", "macd_cross", "atr_pct"):
            if mk in signal and signal[mk] is not None:
                mom_str += f"|{mk}={signal[mk]}"'''

count = code.count(old_mom)
if count > 0:
    code = code.replace(old_mom, new_mom)
    print(f"✓ Added MACD/ATR to momentum keys in {count} mode blocks")
    changes += 1
else:
    if "macd_hist" in code and "macd_cross" in code:
        print("⚠ MACD keys may already be in code")
    else:
        print("✗ Could not find momentum keys pattern")


# ── 2. Add risk escalation + indicator sizing BEFORE position open ──
# Insert after "paper_tag = ..." and before "# Compute exit levels"
RISK_AND_SIZING = '''
    # ── Risk escalation: check recent losing streak ──
    size_mult = 1.0
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute("""
                SELECT pnl_pct FROM perp_positions
                WHERE status='CLOSED' AND pnl_pct IS NOT NULL
                ORDER BY closed_ts_utc DESC LIMIT 5
            """)
            recent = [dict(r)["pnl_pct"] for r in cur.fetchall()]
        # Count consecutive losses from most recent
        streak = 0
        for pnl in recent:
            if pnl < 0:
                streak += 1
            else:
                break
        if streak >= 3:
            size_mult = 0.5
            logger.info("[RISK] %d consecutive losses — reducing size to 50%%", streak)
        elif streak >= 5:
            size_mult = 0.3
            logger.info("[RISK] %d consecutive losses — reducing size to 30%%", streak)
        # Check daily drawdown
        cur2 = _conn().cursor()
        today_start = _now_iso()[:10] + "T00:00:00"
        cur2.execute("""
            SELECT COALESCE(SUM(pnl_usd), 0) FROM perp_positions
            WHERE status='CLOSED' AND closed_ts_utc >= ?
        """, (today_start,))
        daily_pnl = cur2.fetchone()[0] or 0
        total_capital = sum(p.get("size_usd", 0) for p in (_get_open_scalp_positions() + _get_open_swing_positions()))
        if total_capital > 0 and daily_pnl < 0 and abs(daily_pnl) / max(total_capital, 100) > 0.02:
            size_mult = min(size_mult, 0.5)
            logger.info("[RISK] Daily DD >2%% ($%.2f) — capping size to 50%%", daily_pnl)
    except Exception as _re:
        logger.debug("Risk escalation check error: %s", _re)

    # ── Indicator-based position sizing ──
    rsi_val = signal.get("rsi_14")
    macd_cross_val = signal.get("macd_cross")
    atr_val = signal.get("atr_pct")

    indicator_mult = 1.0
    if rsi_val is not None:
        # RSI sweet spot: 40-60 (neutral zone with momentum) = ideal entry
        if 35 <= rsi_val <= 65:
            indicator_mult = 1.2  # slight boost for balanced RSI
        # MACD confirmation on top of RSI sweet spot
        if macd_cross_val:
            if side == "LONG" and macd_cross_val == "BULLISH" and rsi_val < 55:
                indicator_mult = 1.5  # strong confirmation: MACD bullish + RSI not overbought
                logger.info("[SIZING] LONG + MACD bullish cross + RSI=%.1f → 1.5x size", rsi_val)
            elif side == "SHORT" and macd_cross_val == "BEARISH" and rsi_val > 45:
                indicator_mult = 1.5
                logger.info("[SIZING] SHORT + MACD bearish cross + RSI=%.1f → 1.5x size", rsi_val)
        # Overbought/oversold contraindication
        if side == "LONG" and rsi_val > 75:
            indicator_mult = 0.7  # overbought — reduce size for longs
        elif side == "SHORT" and rsi_val < 25:
            indicator_mult = 0.7  # oversold — reduce size for shorts

    combined_mult = round(size_mult * indicator_mult, 2)
'''

if '[RISK]' not in code:
    # Insert after paper_tag line, before "# Compute exit levels"
    old_before_compute = '''    paper_tag = "PAPER" if dry_run else "LIVE"

    # Compute exit levels'''
    new_before_compute = '''    paper_tag = "PAPER" if dry_run else "LIVE"
''' + RISK_AND_SIZING + '''
    # Compute exit levels'''

    if old_before_compute in code:
        code = code.replace(old_before_compute, new_before_compute, 1)
        print("✓ Added risk escalation + indicator sizing")
        changes += 1
    else:
        print("✗ Could not find insertion point for risk/sizing")
else:
    print("⚠ Risk escalation already in code, skipping")


# ── 3. Apply combined_mult to all mode size calculations ──
# For SCALP
old_scalp_size = '        size_usd   = float(signal.get("size_usd", SCALP_SIZE_USD()))'
new_scalp_size = '        size_usd   = round(float(signal.get("size_usd", SCALP_SIZE_USD())) * combined_mult, 2)'
# Only replace in the scalp block (first occurrence after "if is_scalp:")
if old_scalp_size in code and 'combined_mult' in code:
    # Replace all 3 occurrences (scalp, mid, swing)
    lines = code.split('\n')
    replaced = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == 'size_usd   = float(signal.get("size_usd", SCALP_SIZE_USD()))':
            lines[i] = line.replace(
                'float(signal.get("size_usd", SCALP_SIZE_USD()))',
                'round(float(signal.get("size_usd", SCALP_SIZE_USD())) * combined_mult, 2)'
            )
            replaced += 1
        elif stripped == 'size_usd   = float(signal.get("size_usd", MID_SIZE_USD()))':
            lines[i] = line.replace(
                'float(signal.get("size_usd", MID_SIZE_USD()))',
                'round(float(signal.get("size_usd", MID_SIZE_USD())) * combined_mult, 2)'
            )
            replaced += 1
        elif stripped == 'size_usd   = float(signal.get("size_usd", PERP_SIZE_USD()))':
            lines[i] = line.replace(
                'float(signal.get("size_usd", PERP_SIZE_USD()))',
                'round(float(signal.get("size_usd", PERP_SIZE_USD())) * combined_mult, 2)'
            )
            replaced += 1
    code = '\n'.join(lines)
    print(f"✓ Applied combined_mult to {replaced} mode size calculations")
    changes += 1


# ── 4. Add ATR-based TP/SL adjustment for scalp ──
# After scalp tp1_pct line, add ATR adjustment
ATR_ADJUST = '''
        # ATR-based TP/SL: high volatility = wider SL + TP; low vol = tighter
        if atr_val and atr_val > 0:
            if atr_val > 1.5:    # high volatility (>1.5% ATR)
                stop_pct *= 1.3  # widen SL by 30%
                tp1_pct  *= 1.2  # widen TP by 20%
                logger.debug("[ATR] %s high vol ATR=%.2f%% → wider SL/TP", symbol, atr_val)
            elif atr_val < 0.5:  # low volatility (<0.5% ATR)
                stop_pct *= 0.8  # tighten SL by 20%
                tp1_pct  *= 0.85 # tighten TP by 15%
                logger.debug("[ATR] %s low vol ATR=%.2f%% → tighter SL/TP", symbol, atr_val)
'''

if '[ATR]' not in code:
    # Insert after "tp1_pct    = SCALP_TP_PCT() / 100" in the scalp block
    old_scalp_tp = "        tp1_pct    = SCALP_TP_PCT() / 100\n        if side == \"LONG\":"
    new_scalp_tp = "        tp1_pct    = SCALP_TP_PCT() / 100" + ATR_ADJUST + "        if side == \"LONG\":"

    if old_scalp_tp in code:
        code = code.replace(old_scalp_tp, new_scalp_tp, 1)
        print("✓ Added ATR-based TP/SL adjustment for scalp")
        changes += 1

        # Also for MID
        old_mid_tp = "        tp1_pct    = MID_TP_PCT() / 100\n        if side == \"LONG\":"
        new_mid_tp = "        tp1_pct    = MID_TP_PCT() / 100" + ATR_ADJUST.replace("symbol, atr_val", "symbol, atr_val") + "        if side == \"LONG\":"
        if old_mid_tp in code:
            code = code.replace(old_mid_tp, new_mid_tp, 1)
            print("✓ Added ATR-based TP/SL adjustment for MID")

        # Also for SWING
        old_swing_tp = "        tp2_pct    = PERP_TP2_PCT() / 100\n        if side == \"LONG\":"
        new_swing_tp = "        tp2_pct    = PERP_TP2_PCT() / 100" + ATR_ADJUST + "        if side == \"LONG\":"
        if old_swing_tp in code:
            code = code.replace(old_swing_tp, new_swing_tp, 1)
            print("✓ Added ATR-based TP/SL adjustment for SWING")
    else:
        print("✗ Could not find scalp tp1_pct insertion point")
else:
    print("⚠ ATR adjustments already in code, skipping")


# ── 5. Add size_mult info to notes ──
if 'size_mult=' not in code:
    # In all 3 mode note strings, append size_mult
    old_scalp_notes = "f\"mode=SCALP|source={signal.get('source','scalp')}|regime={regime}\""
    new_scalp_notes = "f\"mode=SCALP|source={signal.get('source','scalp')}|regime={regime}|size_mult={combined_mult}\""
    code = code.replace(old_scalp_notes, new_scalp_notes, 1)

    old_mid_notes = "f\"mode=MID|source={signal.get('source','mid')}|regime={regime}\""
    new_mid_notes = "f\"mode=MID|source={signal.get('source','mid')}|regime={regime}|size_mult={combined_mult}\""
    code = code.replace(old_mid_notes, new_mid_notes, 1)

    old_swing_notes = "f\"mode=SWING|source={signal.get('source','auto')}|regime={regime}\""
    new_swing_notes = "f\"mode=SWING|source={signal.get('source','auto')}|regime={regime}|size_mult={combined_mult}\""
    code = code.replace(old_swing_notes, new_swing_notes, 1)
    print("✓ Added size_mult to trade notes")
    changes += 1


with open(PERP_PY, "w") as f:
    f.write(code)

print(f"\n✅ perp_executor.py enhanced ({changes} change blocks)")
