#!/usr/bin/env python3
"""
Patch main.py to add ML Brain API endpoints:
1. /api/brain/ml-status — model training status, accuracy, feature importances
2. /api/brain/ml-predict — predict win prob + return for a hypothetical signal
3. /api/brain/ml-train — force retrain the model
4. /api/brain/ml-optimize — threshold optimizer A/B simulation
5. /api/brain/ml-recent-predictions — recent signals with predictions
6. Add ML retrain check to existing periodic loops
"""

MAIN_PY = "/root/memecoin_engine/dashboard/backend/main.py"

with open(MAIN_PY, "r") as f:
    code = f.read()

ML_ENDPOINTS = '''

# ── ML Brain Endpoints ─────────────────────────────────────────────────────────

@app.get("/api/brain/ml-status")
async def brain_ml_status():
    """Return ML model training status, accuracy, feature importances."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from utils.ml_predictor import get_model_status, maybe_retrain
        # Auto-train if needed
        status = maybe_retrain()
        return status
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/brain/ml-predict")
async def brain_ml_predict(
    side: str = "LONG",
    source: str = "scalp",
    rsi: float = 50.0,
    macd_hist: float = 0.0,
    macd_cross: str = "",
    atr_pct: float = 0.5,
    momentum_5m: float = 0.0,
    momentum_15m: float = 0.0,
    regime: str = "TRANSITION",
):
    """Predict win probability and expected return for a hypothetical signal."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from utils.ml_predictor import predict_signal
        signal = {
            "side": side,
            "source": source,
            "rsi_14": rsi,
            "macd_hist": macd_hist,
            "macd_cross": macd_cross or None,
            "atr_pct": atr_pct,
            "momentum_5m": momentum_5m,
            "momentum_15m": momentum_15m,
            "regime_label": regime,
        }
        result = predict_signal(signal)
        if result is None:
            return {"error": "Model not trained yet — need 15+ closed trades"}
        return {"signal": signal, "prediction": result}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/brain/ml-train")
async def brain_ml_train():
    """Force retrain the ML model."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from utils.ml_predictor import train_model
        result = train_model(force=True)
        return result
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/brain/ml-optimize")
async def brain_ml_optimize(lookback_days: int = 30):
    """Run threshold optimizer A/B simulations."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from utils.ml_predictor import optimize_threshold
        return optimize_threshold(lookback_days=lookback_days)
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/brain/ml-recent-predictions")
async def brain_ml_recent_predictions():
    """Get recent closed trades with retroactive ML predictions."""
    import sys
    root = _engine_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from utils.ml_predictor import predict_signal, _get_closed_trades, _parse_notes
        trades = _get_closed_trades()
        results = []
        for trade in trades[:30]:  # Last 30 trades
            notes_p = _parse_notes(trade.get("notes", "") or "")
            # Reconstruct signal from trade data
            signal = {
                "side": trade["side"],
                "source": notes_p.get("source", "auto"),
                "rsi_14": float(notes_p.get("rsi_14", 50)),
                "macd_hist": float(notes_p.get("macd_hist", 0)) if notes_p.get("macd_hist") and notes_p.get("macd_hist") != "None" else None,
                "macd_cross": notes_p.get("macd_cross") if notes_p.get("macd_cross") != "None" else None,
                "atr_pct": float(notes_p.get("atr_pct", 0.5)) if notes_p.get("atr_pct") and notes_p.get("atr_pct") != "None" else None,
                "momentum_5m": float(notes_p.get("momentum_5m", 0)),
                "momentum_15m": float(notes_p.get("momentum_15m", 0)),
                "regime_label": notes_p.get("regime", trade.get("regime_label", "TRANSITION")),
            }
            pred = predict_signal(signal)
            results.append({
                "id": trade["id"],
                "symbol": trade["symbol"],
                "side": trade["side"],
                "mode": notes_p.get("mode", "SWING"),
                "pnl_pct": trade["pnl_pct"],
                "exit_reason": trade["exit_reason"],
                "prediction": pred,
                "closed_at": trade["closed_ts_utc"],
            })
        return {"trades": results, "n": len(results)}
    except Exception as e:
        return {"error": str(e)}

'''

if '/api/brain/ml-status' not in code:
    # Insert before the WebSocket handler or at end of endpoints
    if '/api/perps/indicator-insights' in code:
        idx = code.find('/api/perps/indicator-insights')
        # Find the end of that endpoint function
        next_at = code.find('\n@app.', idx + 30)
        if next_at == -1:
            next_at = code.find('\n@app.websocket', idx + 30)
        if next_at > 0:
            code = code[:next_at] + ML_ENDPOINTS + code[next_at:]
        else:
            code += ML_ENDPOINTS
    elif '/api/perps/risk' in code:
        idx = code.find('/api/perps/risk')
        next_at = code.find('\n@app.', idx + 15)
        if next_at > 0:
            code = code[:next_at] + ML_ENDPOINTS + code[next_at:]
        else:
            code += ML_ENDPOINTS
    else:
        code += ML_ENDPOINTS
    print("✓ Added ML Brain API endpoints")
else:
    print("⚠ ML Brain endpoints already exist")


# ── Add ML retrain to the post-exit monitor (runs every 60s, retrain check is cheap) ──
ML_RETRAIN_SNIPPET = '''
            # Auto-retrain ML model if new trades closed
            try:
                import sys as _sys
                _r = _engine_root()
                if _r not in _sys.path:
                    _sys.path.insert(0, _r)
                from utils.ml_predictor import maybe_retrain
                maybe_retrain()
            except Exception:
                pass
'''

if 'maybe_retrain' not in code:
    # Insert in the post-exit monitor loop, right after "await asyncio.sleep(60)"
    # Actually, better to insert right before the sleep in post_exit_monitor
    marker = "        await asyncio.sleep(60)  # check every minute"
    if marker not in code:
        # Try alternative marker
        marker = "        await asyncio.sleep(60)"
        # Find in post_exit context
        pe_idx = code.find('_post_exit_monitor_loop')
        if pe_idx > 0:
            sleep_idx = code.find(marker, pe_idx)
            if sleep_idx > 0:
                code = code[:sleep_idx] + ML_RETRAIN_SNIPPET + "\n" + code[sleep_idx:]
                print("✓ Added ML retrain check to post-exit loop")
            else:
                print("⚠ Could not find sleep marker in post-exit loop")
        else:
            print("⚠ Could not find post_exit_monitor_loop")
    else:
        code = code.replace(marker, ML_RETRAIN_SNIPPET + "\n" + marker, 1)
        print("✓ Added ML retrain check to post-exit loop")
else:
    print("⚠ ML retrain already in code")


# ── Add ML prediction to execute_perp_signal notes ──
# We want to call predict_signal before opening and log the prediction
ML_PREDICT_INJECT = '''
    # ── ML prediction ──
    ml_prediction = None
    try:
        import sys as _sys2
        _r2 = _engine_root() if '_engine_root' in dir() else os.environ.get("ENGINE_ROOT", "/root/memecoin_engine")
        if _r2 not in _sys2.path:
            _sys2.path.insert(0, _r2)
        from utils.ml_predictor import predict_signal as _ml_predict
        ml_prediction = _ml_predict(signal)
        if ml_prediction:
            logger.info("[ML] Prediction for %s %s: win_prob=%.1f%% pred_ret=%.2f%% conf=%s",
                        symbol, side, ml_prediction["win_prob"] * 100,
                        ml_prediction["pred_return"], ml_prediction["confidence"])
    except Exception as _mle:
        logger.debug("ML prediction error: %s", _mle)

'''

# This is tricky — we need to find the right place in perp_executor.py, not main.py
# Actually, let's do this in a separate patch for perp_executor
# For now, just save main.py

with open(MAIN_PY, "w") as f:
    f.write(code)

print("\n✅ Brain ML API endpoints added to main.py")
