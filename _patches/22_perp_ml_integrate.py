#!/usr/bin/env python3
"""
Patch perp_executor.py to:
1. Add ML prediction before position open
2. Log ML win_prob + pred_return in trade notes
3. Optionally skip low-confidence signals (configurable)
"""

PERP_PY = "/root/memecoin_engine/utils/perp_executor.py"

with open(PERP_PY, "r") as f:
    code = f.read()

changes = 0

# ── 1. Add ML_MIN_WIN_PROB env lambda ──
if 'ML_MIN_WIN_PROB' not in code:
    old_scalp_enabled = 'SCALP_ENABLED      = lambda: _bool("SCALP_ENABLED", False)'
    new_scalp_enabled = '''ML_MIN_WIN_PROB    = lambda: _float("ML_MIN_WIN_PROB", 0.0)  # 0 = no filter; 0.5 = skip <50% win_prob
SCALP_ENABLED      = lambda: _bool("SCALP_ENABLED", False)'''
    if old_scalp_enabled in code:
        code = code.replace(old_scalp_enabled, new_scalp_enabled, 1)
        print("✓ Added ML_MIN_WIN_PROB env lambda")
        changes += 1
    else:
        print("⚠ Could not find SCALP_ENABLED insertion point for ML lambda")


# ── 2. Add ML prediction + gating right after combined_mult, before exit levels ──
ML_PREDICT_BLOCK = '''
    # ── ML Prediction ──
    ml_prediction = None
    try:
        from utils.ml_predictor import predict_signal as _ml_predict
        _ml_sig = dict(signal)
        _ml_sig["size_mult"] = combined_mult
        ml_prediction = _ml_predict(_ml_sig)
        if ml_prediction:
            logger.info("[ML] %s %s %s: win_prob=%.1f%% pred_ret=%.2f%% conf=%s",
                        mode_tag, symbol, side,
                        ml_prediction["win_prob"] * 100,
                        ml_prediction["pred_return"],
                        ml_prediction["confidence"])
            # Gate: skip if win_prob below threshold
            min_wp = ML_MIN_WIN_PROB()
            if min_wp > 0 and ml_prediction["win_prob"] < min_wp:
                logger.info("[ML] SKIP %s %s — win_prob %.1f%% < threshold %.1f%%",
                            symbol, side, ml_prediction["win_prob"] * 100, min_wp * 100)
                return False
    except Exception as _mle:
        logger.debug("ML predict error: %s", _mle)

'''

if 'ml_prediction' not in code:
    # Insert after combined_mult line, before "# Compute exit levels"
    old_before_exit = '    combined_mult = round(size_mult * indicator_mult, 2)\n\n    # Compute exit levels'
    new_before_exit = '    combined_mult = round(size_mult * indicator_mult, 2)\n' + ML_PREDICT_BLOCK + '    # Compute exit levels'

    if old_before_exit in code:
        code = code.replace(old_before_exit, new_before_exit, 1)
        print("✓ Added ML prediction + gating block")
        changes += 1
    else:
        # Try with single newline
        old2 = '    combined_mult = round(size_mult * indicator_mult, 2)\n    # Compute exit levels'
        new2 = '    combined_mult = round(size_mult * indicator_mult, 2)\n' + ML_PREDICT_BLOCK + '    # Compute exit levels'
        if old2 in code:
            code = code.replace(old2, new2, 1)
            print("✓ Added ML prediction + gating block (alt pattern)")
            changes += 1
        else:
            print("✗ Could not find combined_mult -> exit levels insertion point")
else:
    print("⚠ ML prediction already in code")


# ── 3. Add ml_wp and ml_ret to trade notes for all 3 modes ──
# We need to append |ml_wp={win_prob}|ml_ret={pred_ret} to notes in each mode block

def add_ml_to_notes(code, old_notes, mode_name):
    """Add ML prediction data to notes string for a given mode."""
    if old_notes in code:
        # Insert ML info before the closing quote of the f-string
        # The notes strings end with |size_mult={combined_mult}"
        # We append |ml_wp=X|ml_ret=Y
        new_notes = old_notes.replace(
            "|size_mult={combined_mult}\"",
            "|size_mult={combined_mult}|ml_wp={ml_prediction['win_prob'] if ml_prediction else 'N/A'}|ml_ret={ml_prediction['pred_return'] if ml_prediction else 'N/A'}\""
        )
        if new_notes != old_notes:
            code = code.replace(old_notes, new_notes, 1)
            print(f"✓ Added ML to {mode_name} notes")
        return code
    else:
        print(f"⚠ Could not find {mode_name} notes pattern")
        return code

if 'ml_wp=' not in code:
    # SCALP notes
    code = add_ml_to_notes(code,
        "f\"mode=SCALP|source={signal.get('source','scalp')}|regime={regime}|size_mult={combined_mult}\"",
        "SCALP")

    # MID notes
    code = add_ml_to_notes(code,
        "f\"mode=MID|source={signal.get('source','mid')}|regime={regime}|size_mult={combined_mult}\"",
        "MID")

    # SWING notes
    code = add_ml_to_notes(code,
        "f\"mode=SWING|source={signal.get('source','auto')}|regime={regime}|size_mult={combined_mult}\"",
        "SWING")

    changes += 1
else:
    print("⚠ ML data already in notes")


with open(PERP_PY, "w") as f:
    f.write(code)

print(f"\n✅ perp_executor.py ML integration ({changes} change blocks)")
