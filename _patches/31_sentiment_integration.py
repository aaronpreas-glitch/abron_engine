#!/usr/bin/env python3
"""
Patch main.py to:
1. Add sentiment fetch in SCALP + MID scan loops
2. Add sentiment_score to signal dicts
3. Add /api/sentiment/{symbol} and /api/sentiment/overview endpoints
4. Add sentiment background refresh loop
"""

MAIN_PY = "/root/memecoin_engine/dashboard/backend/main.py"

with open(MAIN_PY, "r") as f:
    code = f.read()

changes = 0

# ── 1. Add sentiment to SCALP scan loop ──
# Find the line where we build the signal dict in scalp loop
# Look for: await execute_perp_signal in the scalp section
# We want to add sentiment fetch before the signal fire and inject into signal dict

SCALP_SENTIMENT_BLOCK = '''
                # ── Fetch sentiment ──
                _sent_score = 0.0
                _sent_boost = 0
                try:
                    _ensure_engine_path()
                    from utils.x_sentiment import get_sentiment
                    _sent = await get_sentiment(symbol)
                    _sent_score = _sent.get("sentiment_score", 0)
                    _sent_boost = _sent.get("boost", 0)
                    if _sent_boost != 0:
                        logger.info("[SENTIMENT] %s score=%.2f boost=%+d vol_spike=%s",
                                    symbol, _sent_score, _sent_boost, _sent.get("volume_spike"))
                except Exception as _se:
                    pass

'''

# Find the scalp signal fire block. Look for pattern: source.*scalp.*execute_perp_signal
# We need to add sentiment_score to the signal dict
if 'sentiment_score' not in code:
    # Insert sentiment fetch before the scalp signal fires
    # Look for the SCALP LONG signal block
    scalp_long_marker = '"source": "scalp",'

    # Find all occurrences in scalp section
    idx = 0
    scalp_scan_start = code.find('_scalp_signal_scan_loop')
    mid_scan_start = code.find('_mid_signal_scan_loop')

    if scalp_scan_start > 0 and mid_scan_start > 0:
        scalp_section = code[scalp_scan_start:mid_scan_start]

        # Add sentiment fetch before the first execute_perp_signal in scalp
        exec_idx = scalp_section.find('await execute_perp_signal')
        if exec_idx > 0:
            # Go back to find a good insertion point - before the signal dict
            # Find the line "await execute_perp_signal({" and insert sentiment fetch above it
            actual_idx = scalp_scan_start + exec_idx
            # Find start of this line
            line_start = code.rfind('\n', 0, actual_idx) + 1
            code = code[:line_start] + SCALP_SENTIMENT_BLOCK + code[line_start:]
            print("✓ Added sentiment fetch before first SCALP signal")
            changes += 1

            # Now add sentiment_score to the signal dict
            # Recalculate since we inserted code
            # Find "source": "scalp" patterns and add sentiment after them
        else:
            print("⚠ Could not find execute_perp_signal in scalp section")

    # Now inject sentiment_score into all signal dicts
    # Pattern: "source": "scalp",
    code_new = code.replace(
        '"source": "scalp",\n',
        '"source": "scalp",\n                    "sentiment_score": _sent_score,\n                    "sentiment_boost": _sent_boost,\n',
        2  # first 2 occurrences (LONG + SHORT in scalp)
    )
    if code_new != code:
        code = code_new
        print("✓ Added sentiment to SCALP signal dicts")
        changes += 1

    # Also add to MID signal dicts
    code_new = code.replace(
        '"source": "mid",\n',
        '"source": "mid",\n                    "sentiment_score": _sent_score,\n                    "sentiment_boost": _sent_boost,\n',
        2  # LONG + SHORT in mid
    )
    if code_new != code:
        code = code_new
        print("✓ Added sentiment to MID signal dicts")
        changes += 1

    # Add sentiment fetch in MID scan loop too
    mid_exec_idx = code.find('await execute_perp_signal', code.find('_mid_signal_scan_loop'))
    if mid_exec_idx > 0:
        line_start = code.rfind('\n', 0, mid_exec_idx) + 1
        # Check if sentiment already added here
        nearby = code[max(0, line_start-500):line_start]
        if 'sentiment' not in nearby:
            code = code[:line_start] + SCALP_SENTIMENT_BLOCK + code[line_start:]
            print("✓ Added sentiment fetch before MID signal")
            changes += 1
else:
    print("⚠ Sentiment already in code")


# ── 2. Add sentiment API endpoints ──
SENTIMENT_ENDPOINTS = '''

# ── Sentiment Endpoints ────────────────────────────────────────────────────────

@app.get("/api/sentiment/{symbol}")
async def sentiment_symbol(symbol: str):
    """Get real-time X/Twitter sentiment for a symbol."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from utils.x_sentiment import get_sentiment
        return await get_sentiment(symbol)
    except Exception as e:
        return {"error": str(e), "symbol": symbol, "sentiment_score": 0}


@app.get("/api/sentiment/overview")
async def sentiment_overview():
    """Get sentiment overview for all major tracked symbols."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from utils.x_sentiment import get_sentiment_batch
        symbols = ["SOL", "BTC", "ETH", "DOGE", "XRP", "AVAX", "LINK"]
        results = await get_sentiment_batch(symbols)
        # Sort by absolute sentiment
        sorted_results = sorted(results.values(), key=lambda x: abs(x.get("sentiment_score", 0)), reverse=True)
        return {
            "symbols": sorted_results,
            "summary": {
                "most_bullish": max(results.values(), key=lambda x: x.get("sentiment_score", 0)),
                "most_bearish": min(results.values(), key=lambda x: x.get("sentiment_score", 0)),
                "volume_spikes": [s for s in results.values() if s.get("volume_spike")],
            },
        }
    except Exception as e:
        return {"error": str(e)}

'''

if '/api/sentiment/' not in code:
    # Insert before backtest endpoints
    if '/api/backtest/run' in code:
        idx = code.find('/api/backtest/run')
        at_idx = code.rfind('\n@app.', 0, idx)
        if at_idx > 0:
            code = code[:at_idx] + SENTIMENT_ENDPOINTS + code[at_idx:]
        else:
            code += SENTIMENT_ENDPOINTS
    else:
        code += SENTIMENT_ENDPOINTS
    print("✓ Added sentiment API endpoints")
    changes += 1
else:
    print("⚠ Sentiment endpoints already exist")


# ── 3. Add sentiment log to SCALP scan output ──
# Currently: [SCALP SCAN] SOL  5m=X% RSI=X MACD_H=X ATR=X% cross=X
# Add: sent=X
old_scalp_log = 'f"[SCALP SCAN] {symbol}  5m={chg_5m:.3f}%  RSI={rsi:.2f}  MACD_H={macd_hist:.6f}  ATR={atr:.4f}%  cross={macd_cross}"'
new_scalp_log = 'f"[SCALP SCAN] {symbol}  5m={chg_5m:.3f}%  RSI={rsi:.2f}  MACD_H={macd_hist:.6f}  ATR={atr:.4f}%  cross={macd_cross}  sent={_sent_score:.2f}"'

# Try replacement (this might not match exact formatting)
if old_scalp_log in code:
    code = code.replace(old_scalp_log, new_scalp_log)
    print("✓ Added sentiment to SCALP scan log")
elif 'sent=' not in code.split('SCALP SCAN')[1][:200] if 'SCALP SCAN' in code else True:
    # Try to find and patch more flexibly
    pass


with open(MAIN_PY, "w") as f:
    f.write(code)

print(f"\n✅ Sentiment integration ({changes} changes)")
