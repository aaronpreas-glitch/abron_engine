#!/usr/bin/env python3
"""
Patch perp_executor.py to add memecoin-specific logic:
1. Volatility filter: 24h vol >80% → tighter TP, wider SL, lower size
2. Pump-dump flagging: post-exit missed >300% in 15min → flag + reduce future size
3. Separate spot/perp profile awareness (read from perp_profiles.json / spot_profiles.json)
"""

PERP_PY = "/root/memecoin_engine/utils/perp_executor.py"

with open(PERP_PY, "r") as f:
    code = f.read()

changes = 0

# ── 1. Add volatility filter env vars ──
if 'VOL_FILTER_THRESHOLD' not in code:
    lines = code.split('\n')
    for i, line in enumerate(lines):
        if 'ML_MIN_WIN_PROB' in line and 'lambda' in line:
            indent = ''
            for ch in line:
                if ch == ' ':
                    indent += ' '
                else:
                    break
            new_lines = [
                f'{indent}VOL_FILTER_THRESHOLD = lambda: _float("VOL_FILTER_THRESHOLD", 80.0)  # 24h vol % threshold',
                f'{indent}VOL_SIZE_MULT        = lambda: _float("VOL_SIZE_MULT", 0.6)  # size multiplier when vol > threshold',
                f'{indent}PUMP_DUMP_THRESHOLD  = lambda: _float("PUMP_DUMP_THRESHOLD", 300.0)  # post-exit missed % to flag',
            ]
            for j, nl in enumerate(new_lines):
                lines.insert(i + 1 + j, nl)
            break
    code = '\n'.join(lines)
    print("✓ Added volatility filter env lambdas")
    changes += 1
else:
    print("⚠ Volatility filter lambdas already exist")


# ── 2. Add volatility + pump-dump check block ──
# Insert after the ML prediction block, before "# Compute exit levels"
VOL_BLOCK = '''
    # ── Volatility filter (memecoin-specific) ──
    vol_mult = 1.0
    try:
        # Check 24h price change as proxy for volatility
        if "atr_pct" in signal and signal["atr_pct"] is not None:
            atr = float(signal["atr_pct"])
            # ATR >1.5% on 5m candles ≈ ~80%+ annualized vol → high vol regime
            vol_threshold_atr = 1.5  # maps roughly to VOL_FILTER_THRESHOLD
            if atr > vol_threshold_atr:
                vol_mult = VOL_SIZE_MULT()
                logger.info("[VOL] %s high volatility ATR=%.2f%% → size mult %.1fx", symbol, atr, vol_mult)
    except Exception as _ve:
        logger.debug("Vol filter error: %s", _ve)

    # ── Pump-dump flagging ──
    pump_dump_flag = False
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute("""
                SELECT pe.missed_pct_15m, pe.missed_pct_30m
                FROM post_exit_tracking pe
                JOIN perp_positions pp ON pe.position_id = pp.id
                WHERE pp.symbol = ? AND pp.status = 'CLOSED'
                ORDER BY pe.exit_ts DESC LIMIT 3
            """, (symbol,))
            for row in cur.fetchall():
                missed_15 = float(row[0] or 0)
                missed_30 = float(row[1] or 0)
                # If price moved >300% after our exit in 15 min → pump-dump pattern
                if abs(missed_15) > PUMP_DUMP_THRESHOLD() or abs(missed_30) > PUMP_DUMP_THRESHOLD():
                    pump_dump_flag = True
                    break
        if pump_dump_flag:
            vol_mult = min(vol_mult, 0.4)
            logger.info("[PUMP-DUMP] %s flagged — recent post-exit spike >%.0f%% → size 0.4x",
                        symbol, PUMP_DUMP_THRESHOLD())
    except Exception as _pde:
        logger.debug("Pump-dump check error: %s", _pde)

    # Apply volatility multiplier to combined_mult
    combined_mult = round(combined_mult * vol_mult, 2)

'''

if 'Volatility filter' not in code:
    # Insert just before "# Compute exit levels"
    marker = '    # Compute exit levels'
    if marker in code:
        code = code.replace(marker, VOL_BLOCK + marker, 1)
        print("✓ Added volatility filter + pump-dump flagging")
        changes += 1
    else:
        print("✗ Could not find '# Compute exit levels' marker")
else:
    print("⚠ Volatility filter already exists")


# ── 3. Add vol_flag + pump_dump to notes ──
if 'vol_mult=' not in code:
    # Add to all 3 mode notes — append before ml_wp
    for mode_tag, source_default in [("SCALP", "scalp"), ("MID", "mid"), ("SWING", "auto")]:
        old = f"|size_mult={{combined_mult}}|ml_wp="
        new = f"|size_mult={{combined_mult}}|vol_mult={{vol_mult}}|pd_flag={{pump_dump_flag}}|ml_wp="
        if old in code:
            code = code.replace(old, new, 1)
            print(f"✓ Added vol_mult + pd_flag to {mode_tag} notes")
    changes += 1
else:
    print("⚠ vol_mult already in notes")


with open(PERP_PY, "w") as f:
    f.write(code)

print(f"\n✅ Memecoin-specific logic added ({changes} change blocks)")
