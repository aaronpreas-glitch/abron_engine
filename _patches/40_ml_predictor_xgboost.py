#!/usr/bin/env python3
"""Patch 40 — Replace ml_predictor.py with XGBoost + Platt calibration rewrite."""
import os, sys

TARGET = "/root/memecoin_engine/utils/ml_predictor.py"

NEW_CONTENT = r'''"""
ML Predictor v2 — XGBoost + Platt Calibration.

Win probability & predicted return for perp signals.
- XGBoost base classifier + CalibratedClassifierCV (Platt scaling)
- 12 high-signal derived features (dropped 9 zero-importance)
- StandardScaler for feature normalization
- GridSearchCV with StratifiedKFold for hyperparameter tuning
- Rolling 60-trade window for regime adaptation
- Confidence tiers (HIGH/MEDIUM/LOW) for gating + sizing
- Feature importance history tracking

Retrains automatically when new trades close.
"""
import os, re, json, logging, sqlite3, pickle, time, warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning)
log = logging.getLogger("ml_predictor")

# ── Paths ──
_ENGINE_ROOT = os.environ.get("ENGINE_ROOT", "/root/memecoin_engine")
_DB_PATH     = os.path.join(_ENGINE_ROOT, "data_storage", "engine.db")
_MODEL_DIR   = os.path.join(_ENGINE_ROOT, "data_storage", "ml_models")
_CLF_PATH    = os.path.join(_MODEL_DIR, "win_classifier.pkl")
_REG_PATH    = os.path.join(_MODEL_DIR, "return_regressor.pkl")
_SCALER_PATH = os.path.join(_MODEL_DIR, "feature_scaler.pkl")
_META_PATH   = os.path.join(_MODEL_DIR, "model_meta.json")
_IMP_HISTORY = os.path.join(_MODEL_DIR, "importance_history.json")

MIN_TRADES = 15
ROLLING_WINDOW = 60  # train on last N trades

# ── Feature names (12 high-signal features) ──
FEATURE_NAMES = [
    "side_long",           # binary: 1=LONG, 0=SHORT
    "rsi_14",              # raw RSI value
    "rsi_zone",            # derived: 0=oversold(<35), 1=neutral, 2=overbought(>65)
    "momentum_5m",         # raw momentum
    "macd_hist",           # raw MACD histogram
    "macd_signal",         # derived: 1=bullish cross, -1=bearish, 0=none
    "atr_pct",             # raw ATR percentage
    "vol_regime",          # derived: 0=low(<0.15), 1=normal, 2=high(>0.4)
    "size_mult",           # raw size multiplier
    "momentum_alignment",  # derived: sign(mom_5m)==sign(side) ? 1 : 0
    "rsi_mom_interact",    # derived: rsi_14 * momentum_5m
    "missed_pct_30m",      # from post-exit (0.0 if not available)
]


# ── Feature extraction ──
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


def _safe_float(val, default=0.0):
    """Safely convert to float."""
    if val is None or val == "None" or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _extract_features(row: dict, notes_parsed: dict = None) -> dict | None:
    """Extract 12 ML features from a perp_positions row + parsed notes."""
    if notes_parsed is None:
        notes_parsed = _parse_notes(row.get("notes", "") or "")

    f = {}

    # 1. Side
    f["side_long"] = 1.0 if (row.get("side") or "").upper() == "LONG" else 0.0

    # 2. RSI raw
    f["rsi_14"] = _safe_float(notes_parsed.get("rsi_14"), 50.0)

    # 3. RSI zone (derived)
    if f["rsi_14"] < 35:
        f["rsi_zone"] = 0.0  # oversold
    elif f["rsi_14"] > 65:
        f["rsi_zone"] = 2.0  # overbought
    else:
        f["rsi_zone"] = 1.0  # neutral

    # 4. Momentum 5m
    f["momentum_5m"] = _safe_float(notes_parsed.get("momentum_5m"), 0.0)

    # 5. MACD histogram
    f["macd_hist"] = _safe_float(notes_parsed.get("macd_hist"), 0.0)

    # 6. MACD signal (derived from cross)
    macd_cross = (notes_parsed.get("macd_cross") or "").upper()
    if macd_cross == "BULLISH":
        f["macd_signal"] = 1.0
    elif macd_cross == "BEARISH":
        f["macd_signal"] = -1.0
    else:
        f["macd_signal"] = 0.0

    # 7. ATR %
    f["atr_pct"] = _safe_float(notes_parsed.get("atr_pct"), 0.5)

    # 8. Volatility regime (derived from ATR)
    if f["atr_pct"] < 0.15:
        f["vol_regime"] = 0.0  # low vol
    elif f["atr_pct"] > 0.4:
        f["vol_regime"] = 2.0  # high vol
    else:
        f["vol_regime"] = 1.0  # normal

    # 9. Size multiplier
    f["size_mult"] = _safe_float(notes_parsed.get("size_mult"), 1.0)

    # 10. Momentum alignment (derived: does momentum agree with trade direction?)
    side_sign = 1.0 if f["side_long"] == 1.0 else -1.0
    mom_sign = 1.0 if f["momentum_5m"] > 0 else (-1.0 if f["momentum_5m"] < 0 else 0.0)
    f["momentum_alignment"] = 1.0 if (side_sign * mom_sign > 0) else 0.0

    # 11. RSI × Momentum interaction
    f["rsi_mom_interact"] = f["rsi_14"] * f["momentum_5m"]

    # 12. Missed pct 30m (filled later from post-exit data)
    f["missed_pct_30m"] = 0.0

    return f


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


# ── Importance history ──
def _append_importance_history(entry: dict):
    """Append a training run's importances to history file."""
    history = []
    if os.path.exists(_IMP_HISTORY):
        try:
            with open(_IMP_HISTORY) as f:
                history = json.load(f)
        except Exception:
            history = []
    history.append(entry)
    # Keep last 50 entries
    history = history[-50:]
    with open(_IMP_HISTORY, "w") as f:
        json.dump(history, f, indent=2)


# ── Training ──
def train_model(force: bool = False) -> dict:
    """Train XGBoost classifier + regressor with Platt calibration.
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

    # Rolling window: use only last ROLLING_WINDOW trades
    trades_window = trades[:ROLLING_WINDOW]  # already DESC by closed_ts

    post_exit = _get_post_exit_data()

    # Build feature matrix
    X_raw = []
    y_win = []
    y_pnl = []
    trade_ids = []

    for trade in trades_window:
        notes_p = _parse_notes(trade.get("notes", "") or "")
        feats = _extract_features(trade, notes_p)
        if feats is None:
            continue

        # Add post-exit feature
        pe = post_exit.get(trade["id"])
        if pe:
            feats["missed_pct_30m"] = _safe_float(pe.get("missed_pct_30m"), 0.0)

        row = _features_to_array(feats)
        X_raw.append(row)
        pnl = float(trade["pnl_pct"] or 0)
        y_win.append(1 if pnl > 0 else 0)
        y_pnl.append(pnl)
        trade_ids.append(trade["id"])

    if len(X_raw) < MIN_TRADES:
        return {"status": "insufficient_features", "usable_trades": len(X_raw)}

    X = np.array(X_raw, dtype=np.float64)
    y_win = np.array(y_win)
    y_pnl = np.array(y_pnl)

    # Handle NaN/Inf
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # ── StandardScaler ──
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    n_pos = int(np.sum(y_win))
    n_neg = int(len(y_win) - n_pos)
    spw = n_neg / max(n_pos, 1)  # scale_pos_weight for class imbalance

    # ── XGBoost + CalibratedClassifierCV ──
    from xgboost import XGBClassifier, XGBRegressor
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.model_selection import cross_val_score, StratifiedKFold

    n_folds = min(3, min(n_pos, n_neg))  # can't have more folds than minority class
    if n_folds < 2:
        n_folds = 2

    use_grid_search = len(X_scaled) >= 40

    if use_grid_search:
        log.info("[ML] Dataset >= 40 trades, running GridSearchCV...")
        from sklearn.model_selection import GridSearchCV
        import signal as _signal

        base = XGBClassifier(
            subsample=0.8, colsample_bytree=0.8,
            scale_pos_weight=spw, eval_metric="logloss",
            use_label_encoder=False, random_state=42,
            verbosity=0,
        )

        param_grid = {
            "max_depth": [2, 3, 4],
            "n_estimators": [100, 200],
            "learning_rate": [0.03, 0.05, 0.1],
        }

        skf = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42)

        try:
            grid = GridSearchCV(
                base, param_grid, cv=skf, scoring="accuracy",
                n_jobs=1, refit=True,
            )
            grid.fit(X_scaled, y_win)
            best_xgb = grid.best_estimator_
            log.info("[ML] GridSearch best params: %s (score=%.3f)",
                     grid.best_params_, grid.best_score_)
        except Exception as e:
            log.warning("[ML] GridSearch failed (%s), using defaults", e)
            best_xgb = XGBClassifier(
                n_estimators=200, max_depth=3, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8,
                scale_pos_weight=spw, eval_metric="logloss",
                use_label_encoder=False, random_state=42, verbosity=0,
            )
            best_xgb.fit(X_scaled, y_win)
    else:
        best_xgb = XGBClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            scale_pos_weight=spw, eval_metric="logloss",
            use_label_encoder=False, random_state=42, verbosity=0,
        )
        best_xgb.fit(X_scaled, y_win)

    # Platt scaling for proper probability calibration
    try:
        clf = CalibratedClassifierCV(best_xgb, cv=n_folds, method="sigmoid")
        clf.fit(X_scaled, y_win)
    except Exception as e:
        log.warning("[ML] Calibration failed (%s), using raw XGBoost", e)
        clf = best_xgb

    # Cross-validation accuracy (on calibrated model if possible, else raw)
    try:
        skf_eval = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42)
        # CV on the full pipeline: XGBoost + Calibration
        cv_clf = CalibratedClassifierCV(
            XGBClassifier(
                n_estimators=best_xgb.get_params().get("n_estimators", 200),
                max_depth=best_xgb.get_params().get("max_depth", 3),
                learning_rate=best_xgb.get_params().get("learning_rate", 0.05),
                subsample=0.8, colsample_bytree=0.8,
                scale_pos_weight=spw, eval_metric="logloss",
                use_label_encoder=False, random_state=42, verbosity=0,
            ),
            cv=max(2, n_folds - 1) if n_folds > 2 else 2,
            method="sigmoid",
        )
        cv_scores = cross_val_score(cv_clf, X_scaled, y_win, cv=skf_eval, scoring="accuracy")
        cv_accuracy = float(np.mean(cv_scores))
    except Exception as e:
        log.warning("[ML] CV scoring failed (%s), using training accuracy", e)
        preds = clf.predict(X_scaled)
        cv_accuracy = float(np.mean(preds == y_win))

    # ── Regression: XGBoost regressor ──
    reg = XGBRegressor(
        n_estimators=200, max_depth=3, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        random_state=42, verbosity=0,
    )

    try:
        from sklearn.model_selection import cross_val_score as cv_score_reg
        skf_reg = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42)
        # For regression CV, use KFold instead
        from sklearn.model_selection import KFold
        kf = KFold(n_splits=n_folds, shuffle=True, random_state=42)
        reg_cv = cv_score_reg(reg, X_scaled, y_pnl, cv=kf, scoring="r2")
        cv_r2 = float(np.mean(reg_cv))
    except Exception:
        cv_r2 = 0.0

    reg.fit(X_scaled, y_pnl)

    # ── Feature importances (from base XGBoost, not calibrated wrapper) ──
    try:
        if hasattr(best_xgb, "feature_importances_"):
            raw_imp = best_xgb.feature_importances_
        elif hasattr(clf, "estimators_"):
            raw_imp = np.mean([
                e.feature_importances_ for e in clf.estimators_
                if hasattr(e, "feature_importances_")
            ], axis=0)
        else:
            raw_imp = np.zeros(len(FEATURE_NAMES))
    except Exception:
        raw_imp = np.zeros(len(FEATURE_NAMES))

    clf_imp = dict(zip(FEATURE_NAMES, [round(float(x), 4) for x in raw_imp]))

    try:
        reg_imp_raw = reg.feature_importances_
    except Exception:
        reg_imp_raw = np.zeros(len(FEATURE_NAMES))
    reg_imp = dict(zip(FEATURE_NAMES, [round(float(x), 4) for x in reg_imp_raw]))

    # ── Probability range check (on training data) ──
    try:
        train_probs = clf.predict_proba(X_scaled)[:, 1]
        prob_min = float(np.min(train_probs))
        prob_max = float(np.max(train_probs))
        prob_mean = float(np.mean(train_probs))
    except Exception:
        prob_min = prob_max = prob_mean = 0.5

    # ── Save models + scaler ──
    os.makedirs(_MODEL_DIR, exist_ok=True)
    with open(_CLF_PATH, "wb") as f:
        pickle.dump(clf, f)
    with open(_REG_PATH, "wb") as f:
        pickle.dump(reg, f)
    with open(_SCALER_PATH, "wb") as f:
        pickle.dump(scaler, f)

    # ── Save metadata ──
    meta = {
        "status": "trained",
        "model_type": "XGBoost + CalibratedClassifierCV (Platt)",
        "n_trades": len(trades),
        "n_trades_window": len(X_raw),
        "n_features": len(FEATURE_NAMES),
        "feature_names": FEATURE_NAMES,
        "cv_accuracy": round(cv_accuracy, 4),
        "cv_r2": round(cv_r2, 4),
        "win_rate_actual": round(float(np.mean(y_win)), 4),
        "avg_pnl_actual": round(float(np.mean(y_pnl)), 4),
        "clf_importances": clf_imp,
        "reg_importances": reg_imp,
        "prob_range": {"min": round(prob_min, 4), "max": round(prob_max, 4), "mean": round(prob_mean, 4)},
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "class_balance": {"wins": n_pos, "losses": n_neg},
        "grid_search_used": use_grid_search,
        "xgb_params": {
            k: v for k, v in best_xgb.get_params().items()
            if k in ("n_estimators", "max_depth", "learning_rate", "subsample", "colsample_bytree")
        },
    }
    with open(_META_PATH, "w") as f:
        json.dump(meta, f, indent=2)

    # ── Append to importance history ──
    _append_importance_history({
        "ts": datetime.now(timezone.utc).isoformat(),
        "n_trades": len(X_raw),
        "cv_acc": round(cv_accuracy, 4),
        "cv_r2": round(cv_r2, 4),
        "importances": clf_imp,
    })

    log.info(
        "[ML] XGBoost model trained: %d trades (window %d), CV_acc=%.1f%%, CV_R2=%.3f, prob_range=%.2f-%.2f",
        len(trades), len(X_raw), cv_accuracy * 100, cv_r2, prob_min, prob_max,
    )
    return meta


# ── Prediction ──
_clf_cache = None
_reg_cache = None
_scaler_cache = None
_cache_ts = 0


def _load_models():
    """Load cached models + scaler, retrain if needed."""
    global _clf_cache, _reg_cache, _scaler_cache, _cache_ts

    # Refresh every 5 minutes
    if _clf_cache is not None and time.time() - _cache_ts < 300:
        return _clf_cache, _reg_cache, _scaler_cache

    if not os.path.exists(_CLF_PATH) or not os.path.exists(_REG_PATH):
        result = train_model()
        if result.get("status") != "trained":
            return None, None, None

    try:
        with open(_CLF_PATH, "rb") as f:
            _clf_cache = pickle.load(f)
        with open(_REG_PATH, "rb") as f:
            _reg_cache = pickle.load(f)
        if os.path.exists(_SCALER_PATH):
            with open(_SCALER_PATH, "rb") as f:
                _scaler_cache = pickle.load(f)
        else:
            _scaler_cache = None
        _cache_ts = time.time()
        return _clf_cache, _reg_cache, _scaler_cache
    except Exception as e:
        log.warning("[ML] Failed to load models: %s", e)
        return None, None, None


def _confidence_tier(win_prob: float) -> str:
    """Map calibrated win probability to confidence tier."""
    if win_prob >= 0.75:
        return "HIGH"
    if win_prob >= 0.60:
        return "MEDIUM"
    return "LOW"


def predict_signal(signal: dict) -> dict | None:
    """Predict win probability and expected return for a signal dict.

    Returns: {"win_prob": 0.72, "pred_return": 1.45, "confidence": "HIGH"} or None
    """
    import numpy as np

    clf, reg, scaler = _load_models()
    if clf is None:
        return None

    # Build feature dict from signal
    f = {
        "side_long": 1.0 if str(signal.get("side", "")).upper() == "LONG" else 0.0,
        "rsi_14": _safe_float(signal.get("rsi_14"), 50.0),
        "momentum_5m": _safe_float(signal.get("momentum_5m"), 0.0),
        "macd_hist": _safe_float(signal.get("macd_hist"), 0.0),
        "atr_pct": _safe_float(signal.get("atr_pct"), 0.5),
        "size_mult": _safe_float(signal.get("size_mult"), 1.0),
        "missed_pct_30m": 0.0,  # unknown for new signals
    }

    # Derived features
    f["rsi_zone"] = 0.0 if f["rsi_14"] < 35 else (2.0 if f["rsi_14"] > 65 else 1.0)

    macd_cross = str(signal.get("macd_cross", "")).upper()
    f["macd_signal"] = 1.0 if macd_cross == "BULLISH" else (-1.0 if macd_cross == "BEARISH" else 0.0)

    f["vol_regime"] = 0.0 if f["atr_pct"] < 0.15 else (2.0 if f["atr_pct"] > 0.4 else 1.0)

    side_sign = 1.0 if f["side_long"] == 1.0 else -1.0
    mom_sign = 1.0 if f["momentum_5m"] > 0 else (-1.0 if f["momentum_5m"] < 0 else 0.0)
    f["momentum_alignment"] = 1.0 if (side_sign * mom_sign > 0) else 0.0

    f["rsi_mom_interact"] = f["rsi_14"] * f["momentum_5m"]

    row = _features_to_array(f)
    X = np.array([row], dtype=np.float64)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # Scale if scaler available
    if scaler is not None:
        X = scaler.transform(X)

    win_prob = float(clf.predict_proba(X)[0][1]) if hasattr(clf, "predict_proba") else 0.5
    pred_return = float(reg.predict(X)[0])

    confidence = _confidence_tier(win_prob)

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


def get_importance_history() -> list:
    """Return feature importance history for trend chart."""
    if os.path.exists(_IMP_HISTORY):
        try:
            with open(_IMP_HISTORY) as f:
                return json.load(f)
        except Exception:
            pass
    return []


# ── Threshold Optimizer ──
def optimize_threshold(lookback_days: int = 30) -> dict:
    """Run A/B simulations to find optimal entry thresholds."""
    import numpy as np

    trades = _get_closed_trades()
    post_exit = _get_post_exit_data()

    if len(trades) < MIN_TRADES:
        return {"status": "insufficient_data", "trades_available": len(trades)}

    clf, reg, scaler = _load_models()
    if clf is None:
        return {"status": "model_not_trained"}

    results = []
    for trade in trades:
        notes_p = _parse_notes(trade.get("notes", "") or "")
        feats = _extract_features(trade, notes_p)
        if feats is None:
            continue

        pe = post_exit.get(trade["id"])
        feats["missed_pct_30m"] = _safe_float(pe.get("missed_pct_30m"), 0.0) if pe else 0.0

        row = _features_to_array(feats)
        X = np.array([row], dtype=np.float64)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        if scaler is not None:
            X = scaler.transform(X)

        win_prob = float(clf.predict_proba(X)[0][1])
        pnl = float(trade["pnl_pct"] or 0)

        results.append({
            "id": trade["id"],
            "symbol": trade["symbol"],
            "side": trade["side"],
            "mode": notes_p.get("mode", "SWING"),
            "pnl_pct": pnl,
            "is_win": pnl > 0,
            "win_prob": round(win_prob, 4),
            "confidence": _confidence_tier(win_prob),
        })

    if not results:
        return {"status": "no_results"}

    thresholds = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
    simulations = []

    for thresh in thresholds:
        above = [r for r in results if r["win_prob"] >= thresh]
        if not above:
            simulations.append({
                "threshold": thresh, "n_trades": 0, "n_filtered": len(results),
                "win_rate": 0, "avg_pnl": 0, "total_pnl": 0,
            })
            continue

        wins = sum(1 for r in above if r["is_win"])
        avg_pnl = sum(r["pnl_pct"] for r in above) / len(above)
        total_pnl = sum(r["pnl_pct"] for r in above)

        simulations.append({
            "threshold": thresh,
            "n_trades": len(above),
            "n_filtered": len(results) - len(above),
            "win_rate": round(wins / len(above) * 100, 1),
            "avg_pnl": round(avg_pnl, 3),
            "total_pnl": round(total_pnl, 3),
        })

    best = max(
        [s for s in simulations if s["n_trades"] >= 3],
        key=lambda s: s["avg_pnl"] * (s["n_trades"] ** 0.5),
        default=simulations[0] if simulations else None,
    )

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
    """Check if model is stale and retrain if needed."""
    trades = _get_closed_trades()
    meta = get_model_status()

    if meta.get("status") == "not_trained" and len(trades) >= MIN_TRADES:
        log.info("[ML] No model found, training initial XGBoost model with %d trades", len(trades))
        return train_model(force=True)

    if meta.get("n_trades", 0) < len(trades):
        log.info("[ML] %d new trades since last training, retraining XGBoost",
                 len(trades) - meta.get("n_trades", 0))
        return train_model(force=True)

    return meta
'''

# ── Write the file ──
os.makedirs(os.path.dirname(TARGET), exist_ok=True)
with open(TARGET, "w") as f:
    f.write(NEW_CONTENT)

print(f"[OK] Wrote {TARGET} ({len(NEW_CONTENT)} bytes)")

# ── Verify it compiles ──
import subprocess
result = subprocess.run(
    ["python3", "-c", f"import py_compile; py_compile.compile('{TARGET}', doraise=True)"],
    capture_output=True, text=True
)
if result.returncode == 0:
    print("[OK] ml_predictor.py compiles successfully")
else:
    print(f"[ERROR] Compilation failed:\n{result.stderr}")
    sys.exit(1)

# ── Force retrain with new model ──
print("[INFO] Forcing model retrain with XGBoost...")
sys.path.insert(0, "/root/memecoin_engine")
try:
    # Clear old cached models to force fresh load
    import importlib
    from utils import ml_predictor
    importlib.reload(ml_predictor)
    result = ml_predictor.train_model(force=True)
    print(f"[OK] Training result: status={result.get('status')}")
    if result.get("status") == "trained":
        print(f"  CV accuracy: {result.get('cv_accuracy', 0):.1%}")
        print(f"  CV R2:       {result.get('cv_r2', 0):.3f}")
        print(f"  Prob range:  {result.get('prob_range', {}).get('min', '?'):.4f} - {result.get('prob_range', {}).get('max', '?'):.4f}")
        print(f"  Trades used: {result.get('n_trades_window', '?')} (window) / {result.get('n_trades', '?')} (total)")
        print(f"  GridSearch:  {result.get('grid_search_used', False)}")
        print(f"  XGB params:  {result.get('xgb_params', {})}")
        imp = result.get("clf_importances", {})
        sorted_imp = sorted(imp.items(), key=lambda x: x[1], reverse=True)
        print("  Top features:")
        for name, val in sorted_imp[:6]:
            print(f"    {name}: {val:.4f}")
except Exception as e:
    print(f"[WARNING] Auto-retrain failed: {e}")
    import traceback
    traceback.print_exc()
    print("  (Model will retrain on next API call)")
