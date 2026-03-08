#!/usr/bin/env python3
"""
Patch main.py to:
1. Add MACD(12,26,9) and ATR(14) helper functions
2. Add MACD + ATR to scalp and MID scan signal dicts
3. Update log lines to include MACD/ATR
"""

MAIN_PY = "/root/memecoin_engine/dashboard/backend/main.py"

with open(MAIN_PY, "r") as f:
    code = f.read()

# ── 1. Add MACD and ATR helper functions after _compute_rsi ──

MACD_ATR_HELPERS = '''

def _compute_macd(candles: list, fast: int = 12, slow: int = 26, sig: int = 9) -> dict | None:
    """Compute MACD(12,26,9) from OHLC candle list.
    Returns {macd_line, signal_line, histogram, crossover} or None.
    crossover: 'BULLISH' if MACD just crossed above signal, 'BEARISH' if below, else None.
    """
    needed = slow + sig + 2
    if len(candles) < needed:
        return None
    closes = [float(c[4]) for c in candles[-(needed):-1]]
    if len(closes) < needed - 1:
        return None

    def _ema(data: list, period: int) -> list:
        mult = 2.0 / (period + 1)
        ema = [data[0]]
        for i in range(1, len(data)):
            ema.append(data[i] * mult + ema[-1] * (1 - mult))
        return ema

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
    signal_line = _ema(macd_line[slow-1:], sig)

    if len(signal_line) < 2:
        return None

    ml = macd_line[-1]
    sl = signal_line[-1]
    hist = ml - sl
    prev_ml = macd_line[-2]
    prev_sl = signal_line[-2]

    crossover = None
    if prev_ml <= prev_sl and ml > sl:
        crossover = "BULLISH"
    elif prev_ml >= prev_sl and ml < sl:
        crossover = "BEARISH"

    return {
        "macd_line": round(ml, 6),
        "signal_line": round(sl, 6),
        "histogram": round(hist, 6),
        "crossover": crossover,
    }


def _compute_atr(candles: list, period: int = 14) -> float | None:
    """Compute ATR(14) from OHLC candle list.
    Each candle: [time, open, high, low, close, vwap, volume, count].
    Returns ATR as a percentage of current price, or None.
    """
    needed = period + 2
    if len(candles) < needed:
        return None
    # Use completed candles (skip last forming one)
    use = candles[-(needed):-1]
    trs = []
    for i in range(1, len(use)):
        high = float(use[i][2])
        low = float(use[i][3])
        prev_close = float(use[i-1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if not trs:
        return None
    # Wilder smoothing
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    # Return as % of current price
    curr_price = float(use[-1][4])
    if curr_price <= 0:
        return None
    return round(atr / curr_price * 100, 4)

'''

if '_compute_macd' not in code:
    # Insert after _compute_rsi function (find the end of it)
    # The pattern: end of _compute_rsi is "    return round(100 - (100 / (1 + rs)), 2)\n\n"
    marker = "    return round(100 - (100 / (1 + rs)), 2)\n\n"
    if marker in code:
        code = code.replace(marker, marker + MACD_ATR_HELPERS, 1)
        print("✓ Added _compute_macd() and _compute_atr() helpers")
    else:
        print("✗ Could not find RSI function end marker")
else:
    print("⚠ _compute_macd already exists, skipping")


# ── 2. Add MACD/ATR to scalp scan ──
if 'macd' not in code.split('_scalp_signal_scan_loop')[1].split('_mid_monitor_loop')[0]:
    # After "rsi = _compute_rsi(candles)" in scalp, add MACD and ATR
    old_scalp_rsi = '''                    rsi = _compute_rsi(candles)
                    log.info(
                        "[SCALP SCAN] %s  5m=%.3f%%  threshold=±%.2f%%  price=$%.2f  RSI=%s",
                        symbol, chg_5m, threshold, price_now, rsi,
                    )'''
    new_scalp_rsi = '''                    rsi = _compute_rsi(candles)
                    macd = _compute_macd(candles)
                    atr = _compute_atr(candles)
                    macd_hist = macd["histogram"] if macd else None
                    macd_cross = macd["crossover"] if macd else None
                    log.info(
                        "[SCALP SCAN] %s  5m=%.3f%%  RSI=%s  MACD_H=%s  ATR=%s%%  cross=%s",
                        symbol, chg_5m, rsi, macd_hist, atr, macd_cross,
                    )'''
    if old_scalp_rsi in code:
        code = code.replace(old_scalp_rsi, new_scalp_rsi, 1)
        print("✓ Added MACD/ATR to scalp scan log")
    else:
        print("✗ Could not find scalp RSI log pattern")

    # Add to LONG signal dict
    old_sl = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "LONG",
                            "regime_label": phase or "SCALP", "source": "scalp",
                            "momentum_5m": round(chg_5m, 3),
                            "price_at_signal": price_now,
                            "rsi_14": rsi,
                        })
                        log.info("[SCALP SCAN] -> LONG signal fired for %s  5m=%.3f%%", symbol, chg_5m)'''
    new_sl = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "LONG",
                            "regime_label": phase or "SCALP", "source": "scalp",
                            "momentum_5m": round(chg_5m, 3),
                            "price_at_signal": price_now,
                            "rsi_14": rsi,
                            "macd_hist": macd_hist,
                            "macd_cross": macd_cross,
                            "atr_pct": atr,
                        })
                        log.info("[SCALP SCAN] -> LONG %s  5m=%.3f%%  RSI=%s MACD_cross=%s", symbol, chg_5m, rsi, macd_cross)'''
    if old_sl in code:
        code = code.replace(old_sl, new_sl, 1)

    # Add to SHORT signal dict
    old_ss = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "SHORT",
                            "regime_label": phase or "SCALP", "source": "scalp",
                            "momentum_5m": round(chg_5m, 3),
                            "price_at_signal": price_now,
                            "rsi_14": rsi,
                        })
                        log.info("[SCALP SCAN] -> SHORT signal fired for %s  5m=%.3f%%", symbol, chg_5m)'''
    new_ss = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "SHORT",
                            "regime_label": phase or "SCALP", "source": "scalp",
                            "momentum_5m": round(chg_5m, 3),
                            "price_at_signal": price_now,
                            "rsi_14": rsi,
                            "macd_hist": macd_hist,
                            "macd_cross": macd_cross,
                            "atr_pct": atr,
                        })
                        log.info("[SCALP SCAN] -> SHORT %s  5m=%.3f%%  RSI=%s MACD_cross=%s", symbol, chg_5m, rsi, macd_cross)'''
    if old_ss in code:
        code = code.replace(old_ss, new_ss, 1)

    print("✓ Added MACD/ATR to scalp signal dicts")
else:
    print("⚠ MACD already in scalp scan, skipping")


# ── 3. Add MACD/ATR to MID scan ──
if 'macd' not in code.split('_mid_signal_scan_loop')[1].split('_spot_monitor_loop')[0]:
    old_mid_rsi = '''                    rsi = _compute_rsi(candles)
                    log.info(
                        "[MID SCAN] %s  15m=%.3f%%  threshold=+-%.2f%%  price=$%.2f  RSI=%s",
                        symbol, chg_15m, threshold, price_now, rsi,
                    )'''
    new_mid_rsi = '''                    rsi = _compute_rsi(candles)
                    macd = _compute_macd(candles)
                    atr = _compute_atr(candles)
                    macd_hist = macd["histogram"] if macd else None
                    macd_cross = macd["crossover"] if macd else None
                    log.info(
                        "[MID SCAN] %s  15m=%.3f%%  RSI=%s  MACD_H=%s  ATR=%s%%  cross=%s",
                        symbol, chg_15m, rsi, macd_hist, atr, macd_cross,
                    )'''
    if old_mid_rsi in code:
        code = code.replace(old_mid_rsi, new_mid_rsi, 1)

    # MID LONG
    old_ml = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "LONG",
                            "regime_label": phase or "MID", "source": "mid",
                            "momentum_15m": round(chg_15m, 3),
                            "price_at_signal": price_now,
                            "rsi_14": rsi,
                        })
                        log.info("[MID SCAN] -> LONG signal fired for %s  15m=%.3f%%", symbol, chg_15m)'''
    new_ml = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "LONG",
                            "regime_label": phase or "MID", "source": "mid",
                            "momentum_15m": round(chg_15m, 3),
                            "price_at_signal": price_now,
                            "rsi_14": rsi,
                            "macd_hist": macd_hist,
                            "macd_cross": macd_cross,
                            "atr_pct": atr,
                        })
                        log.info("[MID SCAN] -> LONG %s  15m=%.3f%%  RSI=%s MACD_cross=%s", symbol, chg_15m, rsi, macd_cross)'''
    if old_ml in code:
        code = code.replace(old_ml, new_ml, 1)

    # MID SHORT
    old_ms = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "SHORT",
                            "regime_label": phase or "MID", "source": "mid",
                            "momentum_15m": round(chg_15m, 3),
                            "price_at_signal": price_now,
                            "rsi_14": rsi,
                        })
                        log.info("[MID SCAN] -> SHORT signal fired for %s  15m=%.3f%%", symbol, chg_15m)'''
    new_ms = '''                        await execute_perp_signal({
                            "symbol": symbol, "side": "SHORT",
                            "regime_label": phase or "MID", "source": "mid",
                            "momentum_15m": round(chg_15m, 3),
                            "price_at_signal": price_now,
                            "rsi_14": rsi,
                            "macd_hist": macd_hist,
                            "macd_cross": macd_cross,
                            "atr_pct": atr,
                        })
                        log.info("[MID SCAN] -> SHORT %s  15m=%.3f%%  RSI=%s MACD_cross=%s", symbol, chg_15m, rsi, macd_cross)'''
    if old_ms in code:
        code = code.replace(old_ms, new_ms, 1)

    print("✓ Added MACD/ATR to MID scan signals")
else:
    print("⚠ MACD already in MID scan, skipping")


with open(MAIN_PY, "w") as f:
    f.write(code)

print("\n✅ main.py MACD/ATR patch complete")
