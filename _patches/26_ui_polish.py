#!/usr/bin/env python3
"""
Patch main.py to add:
1. /api/perps/trade/{id} — detailed single trade view with all data
2. /api/perps/mini-chart/{symbol} — mini 1h OHLC candle data for sparklines
"""

MAIN_PY = "/root/memecoin_engine/dashboard/backend/main.py"

with open(MAIN_PY, "r") as f:
    code = f.read()

UI_ENDPOINTS = '''

# ── UI Polish Endpoints ────────────────────────────────────────────────────────

@app.get("/api/perps/trade/{trade_id}")
async def perps_trade_detail(trade_id: int):
    """Get detailed view of a single trade with all data including post-exit."""
    import sqlite3
    db_path = os.path.join(_engine_root(), "data_storage", "engine.db")
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            trade = conn.execute("SELECT * FROM perp_positions WHERE id=?", (trade_id,)).fetchone()
            if not trade:
                return {"error": "Trade not found"}
            trade = dict(trade)

            # Get post-exit data
            pe = conn.execute("SELECT * FROM post_exit_tracking WHERE position_id=?", (trade_id,)).fetchone()
            post_exit = dict(pe) if pe else None

        # Parse notes
        notes_parsed = {}
        notes = trade.get("notes", "") or ""
        for part in notes.split("|"):
            if "=" in part:
                k, v = part.split("=", 1)
                notes_parsed[k.strip()] = v.strip()

        # Get ML prediction
        ml_pred = None
        try:
            import sys
            root = _engine_root()
            if root not in sys.path:
                sys.path.insert(0, root)
            from utils.ml_predictor import predict_signal
            signal = {
                "side": trade["side"],
                "source": notes_parsed.get("source", "auto"),
                "rsi_14": float(notes_parsed.get("rsi_14", 50)),
                "macd_hist": float(notes_parsed.get("macd_hist", 0)) if notes_parsed.get("macd_hist") and notes_parsed.get("macd_hist") != "None" else None,
                "macd_cross": notes_parsed.get("macd_cross") if notes_parsed.get("macd_cross") != "None" else None,
                "atr_pct": float(notes_parsed.get("atr_pct", 0.5)) if notes_parsed.get("atr_pct") and notes_parsed.get("atr_pct") != "None" else None,
                "momentum_5m": float(notes_parsed.get("momentum_5m", 0)),
                "momentum_15m": float(notes_parsed.get("momentum_15m", 0)),
                "regime_label": notes_parsed.get("regime", trade.get("regime_label", "TRANSITION")),
            }
            ml_pred = predict_signal(signal)
        except Exception:
            pass

        return {
            "trade": trade,
            "notes_parsed": notes_parsed,
            "post_exit": post_exit,
            "ml_prediction": ml_pred,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/perps/mini-chart/{symbol}")
async def perps_mini_chart(symbol: str, interval: int = 5, count: int = 60):
    """Get mini OHLC candle data for sparkline display.
    Fetches from Kraken. interval=5 (min), count=60 → 5 hours of data.
    """
    import httpx
    kraken_map = {
        "SOL": "SOLUSD", "BTC": "XXBTZUSD", "ETH": "XETHZUSD",
        "DOGE": "XDGUSD", "XRP": "XXRPZUSD", "ADA": "ADAUSD",
        "DOT": "DOTUSD", "AVAX": "AVAXUSD", "LINK": "LINKUSD",
    }
    pair = kraken_map.get(symbol.upper())
    if not pair:
        return {"error": f"Unknown symbol {symbol}", "candles": []}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.kraken.com/0/public/OHLC",
                params={"pair": pair, "interval": interval}
            )
            data = resp.json()
            if data.get("error"):
                return {"error": str(data["error"]), "candles": []}

            result = data.get("result", {})
            key = [k for k in result if k != "last"]
            if not key:
                return {"candles": []}

            raw = result[key[0]]
            # Take last 'count' candles
            candles = raw[-count:] if len(raw) > count else raw

            # Format: [timestamp, open, high, low, close, volume]
            formatted = []
            for c in candles:
                formatted.append({
                    "t": int(c[0]),
                    "o": float(c[1]),
                    "h": float(c[2]),
                    "l": float(c[3]),
                    "c": float(c[4]),
                    "v": float(c[6]),
                })

            return {
                "symbol": symbol.upper(),
                "interval_min": interval,
                "candles": formatted,
            }
    except Exception as e:
        return {"error": str(e), "candles": []}

'''

if '/api/perps/trade/' not in code:
    # Insert before backtest endpoints
    if '/api/backtest/run' in code:
        idx = code.find('/api/backtest/run')
        at_idx = code.rfind('\n@app.', 0, idx)
        if at_idx > 0:
            code = code[:at_idx] + UI_ENDPOINTS + code[at_idx:]
        else:
            code += UI_ENDPOINTS
    elif '/api/brain/ml-status' in code:
        idx = code.find('/api/brain/ml-status')
        at_idx = code.rfind('\n@app.', 0, idx)
        if at_idx > 0:
            code = code[:at_idx] + UI_ENDPOINTS + code[at_idx:]
        else:
            code += UI_ENDPOINTS
    else:
        code += UI_ENDPOINTS
    print("✓ Added trade detail + mini-chart endpoints")
else:
    print("⚠ UI endpoints already exist")

with open(MAIN_PY, "w") as f:
    f.write(code)

print("\n✅ UI polish endpoints added")
