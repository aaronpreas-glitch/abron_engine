#!/usr/bin/env python3
"""
Patch perp_executor.py and ml_predictor.py to:
1. Add sentiment_score to trade notes
2. Add sentiment_boost to position sizing
3. Add sentiment as ML feature
4. Upgrade pump-dump guardrail with sentiment spike detection
"""

# ── Part 1: perp_executor.py ──

PERP_PY = "/root/memecoin_engine/utils/perp_executor.py"

with open(PERP_PY, "r") as f:
    code = f.read()

changes = 0

# Add sentiment-based sizing
SENTIMENT_SIZING = '''
    # ── Sentiment-based adjustment ──
    sent_score = float(signal.get("sentiment_score", 0) or 0)
    sent_boost = int(signal.get("sentiment_boost", 0) or 0)
    sent_mult = 1.0

    # Positive sentiment with volume spike: size up
    if sent_boost >= 10 and side == "LONG":
        sent_mult = 1.3
    elif sent_boost >= 5 and side == "LONG":
        sent_mult = 1.15
    # Negative sentiment: reduce size
    elif sent_boost <= -5 and side == "LONG":
        sent_mult = 0.7
    elif sent_boost >= 10 and side == "SHORT":
        sent_mult = 0.7  # Don't short into positive sentiment
    elif sent_boost <= -5 and side == "SHORT":
        sent_mult = 1.2  # Negative sentiment supports shorts

    # Pump-dump + sentiment spike guardrail
    if pump_dump_flag and sent_score > 0.4:
        sent_mult = min(sent_mult, 0.3)
        logger.info("[PUMP-DUMP+SENT] %s flagged — pump-dump + high sentiment → 0.3x", symbol)

    combined_mult = round(combined_mult * sent_mult, 2)

'''

if 'Sentiment-based adjustment' not in code:
    # Insert after volatility filter block, before "# Compute exit levels"
    marker = '    # Compute exit levels'
    if marker in code:
        code = code.replace(marker, SENTIMENT_SIZING + marker, 1)
        print("✓ Added sentiment-based sizing to perp_executor")
        changes += 1
    else:
        print("✗ Could not find '# Compute exit levels' marker")
else:
    print("⚠ Sentiment sizing already exists")


# Add sentiment to trade notes
if 'sent_score=' not in code and 'sent=' not in code:
    # Add to all 3 mode notes — append before vol_mult or ml_wp
    for mode_tag in ["SCALP", "MID", "SWING"]:
        old = f"|vol_mult={{vol_mult}}|pd_flag={{pump_dump_flag}}|ml_wp="
        new = f"|vol_mult={{vol_mult}}|pd_flag={{pump_dump_flag}}|sent={{sent_score}}|sent_boost={{sent_boost}}|ml_wp="
        if old in code:
            code = code.replace(old, new, 1)
            print(f"✓ Added sentiment to {mode_tag} notes")

    # Also add sentiment to ML prediction signal
    old_ml = "_ml_sig = dict(signal)"
    new_ml = '_ml_sig = dict(signal)\n        _ml_sig["sentiment_score"] = sent_score'
    if old_ml in code:
        code = code.replace(old_ml, new_ml, 1)
        print("✓ Added sentiment to ML prediction signal")

    changes += 1
else:
    print("⚠ Sentiment already in notes")


with open(PERP_PY, "w") as f:
    f.write(code)

print(f"\n✅ perp_executor sentiment ({changes} changes)")


# ── Part 2: ml_predictor.py ──

ML_PY = "/root/memecoin_engine/utils/ml_predictor.py"

with open(ML_PY, "r") as f:
    ml_code = f.read()

ml_changes = 0

# Add sentiment_score to FEATURE_NAMES
old_features = '''FEATURE_NAMES = [
    "side_long", "mode_scalp", "mode_mid", "mode_swing",
    "leverage", "regime_code", "rsi_14",
    "momentum_5m", "momentum_15m",
    "macd_hist", "macd_bullish", "macd_bearish",
    "atr_pct", "size_mult",
]'''

new_features = '''FEATURE_NAMES = [
    "side_long", "mode_scalp", "mode_mid", "mode_swing",
    "leverage", "regime_code", "rsi_14",
    "momentum_5m", "momentum_15m",
    "macd_hist", "macd_bullish", "macd_bearish",
    "atr_pct", "size_mult", "sentiment_score",
]'''

if 'sentiment_score' not in ml_code:
    if old_features in ml_code:
        ml_code = ml_code.replace(old_features, new_features)
        print("✓ Added sentiment_score to ML FEATURE_NAMES")
        ml_changes += 1
    else:
        print("⚠ Could not find FEATURE_NAMES block")

    # Also add sentiment extraction in _extract_features
    old_size_mult = '    # Size multiplier (risk/indicator combined)\n    sm = notes_parsed.get("size_mult")\n    features["size_mult"] = float(sm) if sm else 1.0\n\n    return features'

    new_size_mult = '''    # Size multiplier (risk/indicator combined)
    sm = notes_parsed.get("size_mult")
    features["size_mult"] = float(sm) if sm else 1.0

    # Sentiment
    sent = notes_parsed.get("sent") or notes_parsed.get("sentiment_score")
    features["sentiment_score"] = float(sent) if sent and sent != "None" else 0.0

    return features'''

    if old_size_mult in ml_code:
        ml_code = ml_code.replace(old_size_mult, new_size_mult)
        print("✓ Added sentiment extraction in _extract_features")
        ml_changes += 1
    else:
        # Try more flexible match
        if 'sentiment_score' not in ml_code.split('_extract_features')[1][:500] if '_extract_features' in ml_code else True:
            print("⚠ Could not find size_mult block in _extract_features")

    # Add sentiment to predict_signal's feature builder
    old_predict = '        "size_mult": float(signal.get("size_mult", 1.0)),'
    new_predict = '''        "size_mult": float(signal.get("size_mult", 1.0)),
        "sentiment_score": float(signal.get("sentiment_score", 0) or 0),'''

    if old_predict in ml_code and 'sentiment_score' not in ml_code.split('predict_signal')[1][:800]:
        ml_code = ml_code.replace(old_predict, new_predict, 1)
        print("✓ Added sentiment to predict_signal feature builder")
        ml_changes += 1
    else:
        pass

    with open(ML_PY, "w") as f:
        f.write(ml_code)
    print(f"\n✅ ml_predictor sentiment ({ml_changes} changes)")
else:
    print("⚠ Sentiment already in ml_predictor")
