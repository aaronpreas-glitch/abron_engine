#!/usr/bin/env python3
"""
ML Predictor module: utils/ml_predictor.py

Provides:
1. train_model() — trains a Random Forest on closed perp trades
2. predict_signal() — returns win probability + predicted return for a new signal
3. get_model_status() — returns model accuracy, feature importances, training stats
4. optimize_threshold() — A/B simulations to find optimal score threshold
"""

ML_PREDICTOR_CODE = r'''"""
ML Predictor — Win probability & predicted return for perp signals.

Uses scikit-learn Random Forest trained on closed perp_positions.
Features: RSI, MACD histogram, ATR%, momentum, regime, side, mode, leverage, size_mult.
Labels: is_win (binary), pnl_pct (regression).

Retrains automatically when new trades close (checks staleness).
"""
import os, re, json, logging, sqlite3, pickle, time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("ml_predictor")

# ── Paths ──
_ENGINE_ROOT = os.environ.get("ENGINE_ROOT", "/root/memecoin_engine")
_DB_PATH     = os.path.join(_ENGINE_ROOT, "data_storage", "engine.db")
_MODEL_DIR   = os.path.join(_ENGINE_ROOT, "data_storage", "ml_models")
_CLF_PATH    = os.path.join(_MODEL_DIR, "win_classifier.pkl")
_REG_PATH    = os.path.join(_MODEL_DIR, "return_regressor.pkl")
_META_PATH   = os.path.join(_MODEL_DIR, "model_meta.json")

# Minimum trades needed to train
MIN_TRADES = 15

# ── Feature extraction from notes ──
def _parse_notes(notes: str) -> dict:
    """Parse pipe-delimited key=value notes string into dict."""
    result = {}
    if not notes:
        return result
    for part in notes.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _extract_features(row: dict, notes_parsed: dict = None) -> dict | None:
    """Extract ML features from a perp_positions row + parsed notes.
    Returns dict of numeric features or None if insufficient data."""
    if notes_parsed is None:
        notes_parsed = _parse_notes(row.get("notes", "") or "")

    features = {}

    # Side: 1=LONG, 0=SHORT
    features["side_long"] = 1.0 if (row.get("side") or "").upper() == "LONG" else 0.0

    # Mode: one-hot (SCALP, MID, SWING)
    mode = notes_parsed.get("mode", "SWING").upper()
    features["mode_scalp"] = 1.0 if mode == "SCALP" else 0.0
    features["mode_mid"]   = 1.0 if mode == "MID" else 0.0
    features["mode_swing"] = 1.0 if mode == "SWING" else 0.0

    # Leverage
    try:
        features["leverage"] = float(notes_parsed.get("leverage", row.get("leverage", 2.0)))
    except (ValueError, TypeError):
        features["leverage"] = 2.0

    # Regime: encode common regimes
    regime = (notes_parsed.get("regime") or row.get("regime_label") or "UNKNOWN").upper()
    regime_map = {"ACCUMULATION": 0, "MARKUP": 1, "DISTRIBUTION": 2, "MARKDOWN": 3, "TRANSITION": 4}
    features["regime_code"] = float(regime_map.get(regime, 4))

    # RSI
    rsi_str = notes_parsed.get("rsi_14")
    if rsi_str:
        try:
            features["rsi_14"] = float(rsi_str)
        except (ValueError, TypeError):
            features["rsi_14"] = 50.0
    else:
        features["rsi_14"] = 50.0  # default neutral

    # Momentum 5m/15m
    mom5 = notes_parsed.get("momentum_5m")
    mom15 = notes_parsed.get("momentum_15m")
    features["momentum_5m"] = float(mom5) if mom5 else 0.0
    features["momentum_15m"] = float(mom15) if mom15 else 0.0

    # MACD histogram
    macd_h = notes_parsed.get("macd_hist")
    features["macd_hist"] = float(macd_h) if macd_h and macd_h != "None" else 0.0

    # MACD crossover
    macd_cross = notes_parsed.get("macd_cross", "").upper()
    features["macd_bullish"] = 1.0 if macd_cross == "BULLISH" else 0.0
    features["macd_bearish"] = 1.0 if macd_cross == "BEARISH" else 0.0

    # ATR %
    atr = notes_parsed.get("atr_pct")
    features["atr_pct"] = float(atr) if atr and atr != "None" else 0.5  # default mid-vol

    # Size multiplier (risk/indicator combined)
    sm = notes_parsed.get("size_mult")
    features["size_mult"] = float(sm) if sm else 1.0

    return features


# ── Feature names (ordered) ──
FEATURE_NAMES = [
    "side_long", "mode_scalp", "mode_mid", "mode_swing",
    "leverage", "regime_code", "rsi_14",
    "momentum_5m", "momentum_15m",
    "macd_hist", "macd_bullish", "macd_bearish",
    "atr_pct", "size_mult",
]


def _features_to_array(feat_dict: dict) -> list:
    """Convert feature dict to ordered list for model input."""
    return [feat_dict.get(name, 0.0) for name in FEATURE_NAMES]


# ── Database access ──
def _get_closed_trades() -> list[dict]:
    """Fetch all closed perp_positions with PnL."""
    if not os.path.exists(_DB_PATH):
        return []
    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, symbol, side, entry_price, exit_price,
                   stop_price, tp1_price, tp2_price,
                   size_usd, leverage, pnl_pct, pnl_usd,
                   regime_label, exit_reason, notes,
                   mae, mfe, opened_ts_utc, closed_ts_utc
            FROM perp_positions
            WHERE status='CLOSED' AND pnl_pct IS NOT NULL
            ORDER BY closed_ts_utc DESC
        """).fetchall()
    return [dict(r) for r in rows]


def _get_post_exit_data() -> dict:
    """Get post-exit missed_pct by position_id."""
    if not os.path.exists(_DB_PATH):
        return {}
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT position_id, missed_pct_5m, missed_pct_15m, missed_pct_30m,
                       would_have_continued
                FROM post_exit_tracking
            """).fetchall()
        return {r["position_id"]: dict(r) for r in rows}
    except Exception:
        return {}


# ── Training ──
def train_model(force: bool = False) -> dict:
    """Train classification (win/loss) and regression (pnl_pct) models.
    Returns status dict with accuracy, feature importances, etc."""
    import numpy as np

    trades = _get_closed_trades()
    if len(trades) < MIN_TRADES:
        return {
            "status": "insufficient_data",
            "trades_available": len(trades),
            "trades_needed": MIN_TRADES,
        }

    # Check staleness
    if not force and os.path.exists(_META_PATH):
        try:
            with open(_META_PATH) as f:
                meta = json.load(f)
            if meta.get("n_trades") == len(trades):
                return {"status": "up_to_date", **meta}
        except Exception:
            pass

    post_exit = _get_post_exit_data()

    # Build feature matrix
    X = []
    y_win = []
    y_pnl = []
    trade_ids = []

    for trade in trades:
        notes_p = _parse_notes(trade.get("notes", "") or "")
        feats = _extract_features(trade, notes_p)
        if feats is None:
            continue

        # Add post-exit features if available
        pe = post_exit.get(trade["id"])
        if pe:
            feats["missed_pct_30m"] = float(pe.get("missed_pct_30m") or 0)
        else:
            feats["missed_pct_30m"] = 0.0

        row = _features_to_array(feats)
        row.append(feats.get("missed_pct_30m", 0.0))  # extra feature

        X.append(row)
        pnl = float(trade["pnl_pct"] or 0)
        y_win.append(1 if pnl > 0 else 0)
        y_pnl.append(pnl)
        trade_ids.append(trade["id"])

    if len(X) < MIN_TRADES:
        return {"status": "insufficient_features", "usable_trades": len(X)}

    X = np.array(X, dtype=np.float64)
    y_win = np.array(y_win)
    y_pnl = np.array(y_pnl)

    # Handle NaN/Inf
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    feature_names_ext = FEATURE_NAMES + ["missed_pct_30m"]

    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.model_selection import cross_val_score

    # ── Classification: win probability ──
    clf = RandomForestClassifier(
        n_estimators=100,
        max_depth=5,
        min_samples_leaf=max(2, len(X) // 10),
        random_state=42,
        class_weight="balanced",
    )

    # Cross-validation (leave-one-out if tiny dataset, otherwise 5-fold)
    n_folds = min(5, len(X))
    if n_folds >= 2:
        cv_scores = cross_val_score(clf, X, y_win, cv=n_folds, scoring="accuracy")
        cv_accuracy = float(np.mean(cv_scores))
    else:
        cv_accuracy = 0.0

    clf.fit(X, y_win)

    # ── Regression: predicted return ──
    reg = RandomForestRegressor(
        n_estimators=100,
        max_depth=5,
        min_samples_leaf=max(2, len(X) // 10),
        random_state=42,
    )
    if n_folds >= 2:
        reg_cv = cross_val_score(reg, X, y_pnl, cv=n_folds, scoring="r2")
        cv_r2 = float(np.mean(reg_cv))
    else:
        cv_r2 = 0.0

    reg.fit(X, y_pnl)

    # Feature importances
    clf_imp = dict(zip(feature_names_ext, [round(float(x), 4) for x in clf.feature_importances_]))
    reg_imp = dict(zip(feature_names_ext, [round(float(x), 4) for x in reg.feature_importances_]))

    # Save models
    os.makedirs(_MODEL_DIR, exist_ok=True)
    with open(_CLF_PATH, "wb") as f:
        pickle.dump(clf, f)
    with open(_REG_PATH, "wb") as f:
        pickle.dump(reg, f)

    # Save metadata
    meta = {
        "status": "trained",
        "n_trades": len(trades),
        "n_features": len(feature_names_ext),
        "feature_names": feature_names_ext,
        "cv_accuracy": round(cv_accuracy, 4),
        "cv_r2": round(cv_r2, 4),
        "win_rate_actual": round(float(np.mean(y_win)), 4),
        "avg_pnl_actual": round(float(np.mean(y_pnl)), 4),
        "clf_importances": clf_imp,
        "reg_importances": reg_imp,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "class_balance": {"wins": int(np.sum(y_win)), "losses": int(len(y_win) - np.sum(y_win))},
    }
    with open(_META_PATH, "w") as f:
        json.dump(meta, f, indent=2)

    log.info("[ML] Model trained: %d trades, CV_acc=%.1f%%, CV_R2=%.3f", len(X), cv_accuracy * 100, cv_r2)
    return meta


# ── Prediction ──
_clf_cache = None
_reg_cache = None
_cache_ts = 0

def _load_models():
    """Load cached models, retrain if needed."""
    global _clf_cache, _reg_cache, _cache_ts

    # Refresh every 5 minutes
    if _clf_cache is not None and time.time() - _cache_ts < 300:
        return _clf_cache, _reg_cache

    if not os.path.exists(_CLF_PATH) or not os.path.exists(_REG_PATH):
        # Try to train
        result = train_model()
        if result.get("status") != "trained":
            return None, None

    try:
        with open(_CLF_PATH, "rb") as f:
            _clf_cache = pickle.load(f)
        with open(_REG_PATH, "rb") as f:
            _reg_cache = pickle.load(f)
        _cache_ts = time.time()
        return _clf_cache, _reg_cache
    except Exception as e:
        log.warning("[ML] Failed to load models: %s", e)
        return None, None


def predict_signal(signal: dict) -> dict | None:
    """Predict win probability and expected return for a signal dict.

    signal should contain keys like: side, source, regime_label,
    rsi_14, macd_hist, macd_cross, atr_pct, momentum_5m, momentum_15m, etc.

    Returns: {"win_prob": 0.72, "pred_return": 1.45, "confidence": "HIGH"} or None
    """
    import numpy as np

    clf, reg = _load_models()
    if clf is None:
        return None

    # Build feature dict from signal
    source = str(signal.get("source", "auto")).lower()
    mode = "SCALP" if source == "scalp" else ("MID" if source == "mid" else "SWING")

    feat = {
        "side_long": 1.0 if str(signal.get("side", "")).upper() == "LONG" else 0.0,
        "mode_scalp": 1.0 if mode == "SCALP" else 0.0,
        "mode_mid": 1.0 if mode == "MID" else 0.0,
        "mode_swing": 1.0 if mode == "SWING" else 0.0,
        "leverage": float(signal.get("leverage", 3.0)),
        "regime_code": float({"ACCUMULATION": 0, "MARKUP": 1, "DISTRIBUTION": 2, "MARKDOWN": 3, "TRANSITION": 4}.get(
            str(signal.get("regime_label", "TRANSITION")).upper(), 4
        )),
        "rsi_14": float(signal.get("rsi_14") or 50),
        "momentum_5m": float(signal.get("momentum_5m") or 0),
        "momentum_15m": float(signal.get("momentum_15m") or 0),
        "macd_hist": float(signal.get("macd_hist") or 0) if signal.get("macd_hist") is not None else 0.0,
        "macd_bullish": 1.0 if str(signal.get("macd_cross", "")).upper() == "BULLISH" else 0.0,
        "macd_bearish": 1.0 if str(signal.get("macd_cross", "")).upper() == "BEARISH" else 0.0,
        "atr_pct": float(signal.get("atr_pct") or 0.5) if signal.get("atr_pct") is not None else 0.5,
        "size_mult": float(signal.get("size_mult", 1.0)),
    }

    row = _features_to_array(feat)
    row.append(0.0)  # missed_pct_30m (unknown for new signal)

    X = np.array([row], dtype=np.float64)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    win_prob = float(clf.predict_proba(X)[0][1]) if hasattr(clf, "predict_proba") else 0.5
    pred_return = float(reg.predict(X)[0])

    # Confidence tier
    if win_prob >= 0.70:
        confidence = "HIGH"
    elif win_prob >= 0.55:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "win_prob": round(win_prob, 4),
        "pred_return": round(pred_return, 4),
        "confidence": confidence,
    }


def get_model_status() -> dict:
    """Return current model metadata and status."""
    if os.path.exists(_META_PATH):
        try:
            with open(_META_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"status": "not_trained", "n_trades": 0}


# ── Threshold Optimizer ──
def optimize_threshold(lookback_days: int = 30) -> dict:
    """Run A/B simulations to find optimal entry thresholds.

    Simulates different win_prob thresholds and compares outcomes.
    Returns current vs optimized performance comparison.
    """
    import numpy as np

    trades = _get_closed_trades()
    post_exit = _get_post_exit_data()

    if len(trades) < MIN_TRADES:
        return {"status": "insufficient_data", "trades_available": len(trades)}

    # Filter by lookback
    if lookback_days:
        cutoff = datetime.now(timezone.utc).isoformat()[:10]
        # Simple: use all trades for now (we have limited data)

    # Re-predict each historical trade
    clf, reg = _load_models()
    if clf is None:
        return {"status": "model_not_trained"}

    results = []
    for trade in trades:
        notes_p = _parse_notes(trade.get("notes", "") or "")
        feats = _extract_features(trade, notes_p)
        if feats is None:
            continue

        pe = post_exit.get(trade["id"])
        feats["missed_pct_30m"] = float(pe.get("missed_pct_30m") or 0) if pe else 0.0

        row = _features_to_array(feats) + [feats.get("missed_pct_30m", 0.0)]
        X = np.array([row], dtype=np.float64)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        win_prob = float(clf.predict_proba(X)[0][1])
        pnl = float(trade["pnl_pct"] or 0)
        is_win = pnl > 0

        results.append({
            "id": trade["id"],
            "symbol": trade["symbol"],
            "side": trade["side"],
            "mode": notes_p.get("mode", "SWING"),
            "pnl_pct": pnl,
            "is_win": is_win,
            "win_prob": win_prob,
        })

    if not results:
        return {"status": "no_results"}

    # Simulate different thresholds
    thresholds = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
    simulations = []

    for thresh in thresholds:
        above = [r for r in results if r["win_prob"] >= thresh]
        below = [r for r in results if r["win_prob"] < thresh]

        if not above:
            simulations.append({
                "threshold": thresh,
                "n_trades": 0,
                "n_filtered": len(results),
                "win_rate": 0,
                "avg_pnl": 0,
                "total_pnl": 0,
            })
            continue

        wins = sum(1 for r in above if r["is_win"])
        avg_pnl = sum(r["pnl_pct"] for r in above) / len(above)
        total_pnl = sum(r["pnl_pct"] for r in above)

        simulations.append({
            "threshold": thresh,
            "n_trades": len(above),
            "n_filtered": len(below),
            "win_rate": round(wins / len(above) * 100, 1),
            "avg_pnl": round(avg_pnl, 3),
            "total_pnl": round(total_pnl, 3),
        })

    # Find best threshold (maximize Sharpe-like: avg_pnl * sqrt(n_trades))
    best = max(
        [s for s in simulations if s["n_trades"] >= 3],
        key=lambda s: s["avg_pnl"] * (s["n_trades"] ** 0.5),
        default=simulations[0] if simulations else None,
    )

    # Current (no filter) = threshold 0.0
    all_wins = sum(1 for r in results if r["is_win"])
    current = {
        "threshold": 0.0,
        "n_trades": len(results),
        "n_filtered": 0,
        "win_rate": round(all_wins / len(results) * 100, 1) if results else 0,
        "avg_pnl": round(sum(r["pnl_pct"] for r in results) / len(results), 3) if results else 0,
        "total_pnl": round(sum(r["pnl_pct"] for r in results), 3),
    }

    return {
        "status": "complete",
        "n_trades_analyzed": len(results),
        "current": current,
        "best": best,
        "improvement": {
            "win_rate_delta": round((best["win_rate"] - current["win_rate"]) if best else 0, 1),
            "avg_pnl_delta": round((best["avg_pnl"] - current["avg_pnl"]) if best else 0, 3),
        },
        "simulations": simulations,
        "trade_predictions": results,
    }


# ── Auto-retrain check ──
def maybe_retrain() -> dict:
    """Check if model is stale and retrain if needed. Called periodically."""
    trades = _get_closed_trades()
    meta = get_model_status()

    if meta.get("status") == "not_trained" and len(trades) >= MIN_TRADES:
        log.info("[ML] No model found, training initial model with %d trades", len(trades))
        return train_model(force=True)

    if meta.get("n_trades", 0) < len(trades):
        log.info("[ML] %d new trades since last training, retraining", len(trades) - meta.get("n_trades", 0))
        return train_model(force=True)

    return meta
'''

# Write the module to VPS
MODULE_PATH = "/root/memecoin_engine/utils/ml_predictor.py"

with open(MODULE_PATH, "w") as f:
    f.write(ML_PREDICTOR_CODE)

print(f"✅ Written {MODULE_PATH}")
