#!/usr/bin/env python3
"""
Patch 53 — Dynamic Exit Improvements

Applies to: /root/memecoin_engine/utils/perp_executor.py

Changes:
  1. Lower DYNAMIC_TRAIL threshold: ml_wp 0.68→0.65, pnl_pct 0.4%→0.25%.
  2. Add PROTECT_PROFIT: exit when mfe >= 0.8% and current pnl drops below 0.4%.
  3. Add TIME_TRAIL: trail after 20% of hold window if position is green.
  4. Register new exit reasons (PROTECT_PROFIT, TRAIL_EARLY) in _dyn_reasons
     for outcome classification.
"""

import pathlib

TARGET = pathlib.Path("/root/memecoin_engine/utils/perp_executor.py")
assert TARGET.exists(), f"Target not found: {TARGET}"
content = TARGET.read_text()

# ── Change 1: Add PROTECT_PROFIT + TRAIL_EARLY to _dyn_reasons ───────────────
OLD_DYN_REASONS = (
    '        _dyn_reasons = ("ML_EARLY_EXIT", "ML_PROB_DROP", "DYNAMIC_TRAIL",\n'
    '                        "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER", "PARTIAL_PROFIT",\n'
    '                        "PROFIT_LOCK", "SENTIMENT_TRAIL")'
)
NEW_DYN_REASONS = (
    '        _dyn_reasons = ("ML_EARLY_EXIT", "ML_PROB_DROP", "DYNAMIC_TRAIL",\n'
    '                        "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER", "PARTIAL_PROFIT",\n'
    '                        "PROFIT_LOCK", "SENTIMENT_TRAIL",\n'
    '                        "PROTECT_PROFIT", "TRAIL_EARLY")'
)
assert OLD_DYN_REASONS in content, "FAIL [1/4]: _dyn_reasons anchor not found"
content = content.replace(OLD_DYN_REASONS, NEW_DYN_REASONS, 1)
print("OK [1/4] PROTECT_PROFIT + TRAIL_EARLY added to _dyn_reasons")

# ── Change 2: Add outcome classification for new exit reasons ─────────────────
# PROTECT_PROFIT acts like a good early exit when profitable (like ML_PROB_DROP good_call)
# TRAIL_EARLY acts like any trail (positive = good, negative = bad)
OLD_OUTCOME_TRAIL = (
    '            elif exit_reason in ("DYNAMIC_TRAIL", "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER", "PROFIT_LOCK", "SENTIMENT_TRAIL") and leveraged_pct > 0:\n'
    '                _outcome = "good_call"  # trailed and caught profit\n'
    '            elif exit_reason in ("DYNAMIC_TRAIL", "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER", "PROFIT_LOCK", "SENTIMENT_TRAIL") and leveraged_pct < -0.5:\n'
    '                _outcome = "bad_call"  # trail didn\'t help'
)
NEW_OUTCOME_TRAIL = (
    '            elif exit_reason in ("DYNAMIC_TRAIL", "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER",\n'
    '                                 "PROFIT_LOCK", "SENTIMENT_TRAIL", "TRAIL_EARLY") and leveraged_pct > 0:\n'
    '                _outcome = "good_call"  # trailed and caught profit\n'
    '            elif exit_reason in ("DYNAMIC_TRAIL", "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER",\n'
    '                                 "PROFIT_LOCK", "SENTIMENT_TRAIL", "TRAIL_EARLY") and leveraged_pct < -0.5:\n'
    '                _outcome = "bad_call"  # trail didn\'t help\n'
    '            elif exit_reason == "PROTECT_PROFIT" and leveraged_pct > 0:\n'
    '                _outcome = "good_call"  # locked in profit before giveback'
)
assert OLD_OUTCOME_TRAIL in content, "FAIL [2/4]: outcome trail anchor not found"
content = content.replace(OLD_OUTCOME_TRAIL, NEW_OUTCOME_TRAIL, 1)
print("OK [2/4] outcome classification updated for PROTECT_PROFIT + TRAIL_EARLY")

# ── Change 3: Rewrite _evaluate_dynamic_exit with improved logic ──────────────
# Use slice-replace: find function boundaries
idx_eval_start = content.find('\ndef _evaluate_dynamic_exit(')
assert idx_eval_start != -1, "FAIL [3/4]: _evaluate_dynamic_exit not found"
idx_eval_end = content.find('\nasync def perp_monitor_step(', idx_eval_start + 1)
assert idx_eval_end != -1, "FAIL [3/4]: end of _evaluate_dynamic_exit not found"

NEW_EVAL = '''
def _evaluate_dynamic_exit(pos, current_price, age_h, mode):
    """Evaluate whether to modify exit behavior based on ML + trend + sentiment + MFE.

    Returns: None (no action), or dict with:
      {"action": "EARLY_EXIT"|"TRAIL_ATR"|"PARTIAL_CLOSE"|"PROFIT_LOCK", "reason": "...", ...}

    Priority order:
      0. PROTECT_PROFIT — had >=0.8% MFE gain but gave back to <0.4% -> lock now
      1. PROFIT_LOCK    — high conf + big winner -> lock 30% + trail rest
      2. DYNAMIC_TRAIL  — ML conviction >=0.65 + profitable >=0.25% -> trail  (lowered)
      2b. SENTIMENT_TRAIL — ML >=0.65 + profitable >=0.25% + high sentiment -> trail
      2c. TIME_TRAIL    — green after 20% of hold window -> trail (time-based)
      3. PARTIAL_PROFIT — medium/high conf + winning >=0.7% -> close 50%
      4. ML_PROB_DROP   — medium conf + losing -> cut early
      5. Near-expiry smart exits
      6. Mid-hold adjustments
    """
    notes_str = pos.get("notes") or ""
    np = _parse_notes_dict(notes_str)
    ml_wp_str = np.get("ml_wp", "0.5")
    ml_conf = np.get("ml_conf", "MEDIUM")

    try:
        ml_wp = float(ml_wp_str) if ml_wp_str != "N/A" else 0.5
    except (ValueError, TypeError):
        ml_wp = 0.5

    entry = float(pos.get("entry_price") or 0)
    side = (pos.get("side") or "").upper()
    if entry <= 0:
        return None

    # Calculate current unrealized PnL %
    if side == "LONG":
        pnl_pct = (current_price - entry) / entry * 100
    else:
        pnl_pct = (entry - current_price) / entry * 100

    # Get ATR for trailing calculation
    atr_pct_str = np.get("atr_pct", "0.5")
    try:
        atr_pct = float(atr_pct_str) if atr_pct_str != "None" else 0.5
    except (ValueError, TypeError):
        atr_pct = 0.5

    # Parse sent_boost for sentiment trail
    try:
        sent_boost = int(np.get("sent_boost", "0"))
    except (ValueError, TypeError):
        sent_boost = 0

    # Trail tighten factor (auto-learned)
    tighten = _get_trail_tighten_factor()

    # Max hold for this mode
    if mode == "SCALP":
        max_hold_h = SCALP_MAX_HOLD_MIN() / 60.0
    elif mode == "MID":
        max_hold_h = MID_MAX_HOLD_H()
    else:
        max_hold_h = PERP_MAX_HOLD_H()

    near_expiry = age_h >= max_hold_h * 0.8

    # ── 0. PROTECT_PROFIT: MFE giveback protection ────────────────────────
    # If we had >= 0.8% unrealized gain at any point but are now below 0.4%,
    # exit immediately to lock the remaining profit.
    mfe_frac = float(pos.get("mfe") or 0)   # stored as fraction: 0.008 = 0.8%
    if mfe_frac >= 0.008 and 0 <= pnl_pct < 0.4:
        _log_dynamic_exit(pos["id"], "MFE_GIVEBACK", ml_wp, ml_conf, pnl_pct, age_h,
                          f"mfe={mfe_frac*100:.2f}%_now={pnl_pct:.2f}%")
        return {"action": "EARLY_EXIT", "reason": "PROTECT_PROFIT"}

    # ── 1. PROFIT_LOCK: High conf + big winner -> close 30% + trail rest ──
    if pnl_pct >= 1.2 and ml_conf == "HIGH" and not _has_partial_close(pos["id"]):
        trail = max(0.003, atr_pct / 100 * 1.0 * tighten)
        _log_dynamic_exit(pos["id"], "PROFIT_LOCK", ml_wp, ml_conf, pnl_pct, age_h,
                          f"profit_lock({pnl_pct:.2f}%)+HIGH_conf+trail({trail:.4f})")
        return {"action": "PROFIT_LOCK", "close_pct": 0.30, "trail_pct": trail, "reason": "PROFIT_LOCK"}

    # ── 2. DYNAMIC_TRAIL: ML conviction >=0.65 + profitable >=0.25% -> trail ─
    if ml_wp >= 0.65 and pnl_pct > 0.25:
        trail = max(0.003, atr_pct / 100 * 1.2 * tighten)
        _log_dynamic_exit(pos["id"], "DYNAMIC_TRAIL", ml_wp, ml_conf, pnl_pct, age_h,
                          f"ml_wp({ml_wp:.2f})+profitable({pnl_pct:.2f}%)+tighten({tighten:.2f})")
        return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "DYNAMIC_TRAIL"}

    # ── 2b. SENTIMENT_TRAIL: ML >=0.65 + profitable >=0.25% + high sentiment ─
    if ml_wp >= 0.65 and pnl_pct > 0.25 and sent_boost >= 8:
        trail = max(0.003, atr_pct / 100 * 1.2 * tighten)
        _log_dynamic_exit(pos["id"], "SENTIMENT_TRAIL", ml_wp, ml_conf, pnl_pct, age_h,
                          f"sentiment_trail({pnl_pct:.2f}%)+sent_boost({sent_boost})+tighten({tighten:.2f})")
        return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "SENTIMENT_TRAIL"}

    # ── 2c. TIME_TRAIL: green after 20% of hold window → trail (time-based) ──
    # Ensures we protect any open profit even when ML conviction is low.
    time_trail_eligible = age_h >= max_hold_h * 0.20 and pnl_pct > 0.15
    if time_trail_eligible and ml_wp < 0.65:  # only when DYNAMIC_TRAIL didn't fire
        trail = max(0.003, atr_pct / 100 * 1.5 * tighten)
        _log_dynamic_exit(pos["id"], "TIME_TRAIL", ml_wp, ml_conf, pnl_pct, age_h,
                          f"time_trail(age={age_h:.2f}h/{max_hold_h:.2f}h)+pnl({pnl_pct:.2f}%)")
        return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "TRAIL_EARLY"}

    # ── 3. PARTIAL_PROFIT: Medium/High conf + winning >=0.7% -> close 50% ──
    if pnl_pct >= 0.7 and ml_conf in ("MEDIUM", "HIGH") and not _has_partial_close(pos["id"]):
        _log_dynamic_exit(pos["id"], "PARTIAL_PROFIT", ml_wp, ml_conf, pnl_pct, age_h,
                          f"partial_profit({pnl_pct:.2f}%)+{ml_conf}_conf({ml_wp:.2f})")
        return {"action": "PARTIAL_CLOSE", "close_pct": 0.50, "reason": "PARTIAL_PROFIT"}

    # ── 4. ML_PROB_DROP: Medium conf + losing -> cut early ──
    if ml_conf == "MEDIUM" and pnl_pct < -0.3:
        _log_dynamic_exit(pos["id"], "ML_PROB_DROP", ml_wp, ml_conf, pnl_pct, age_h,
                          f"medium_conf({ml_wp:.2f})+losing({pnl_pct:.2f}%)")
        return {"action": "EARLY_EXIT", "reason": "ML_PROB_DROP"}

    # ── 5. Near-expiry smart exits ──
    if near_expiry:
        if pnl_pct > 0.3 and ml_wp >= 0.55:
            trail = max(0.003, atr_pct / 100 * 1.5 * tighten)
            _log_dynamic_exit(pos["id"], "TRAIL_ATR", ml_wp, ml_conf, pnl_pct, age_h,
                              f"near_expiry+profitable({pnl_pct:.2f}%)+ml_favorable({ml_wp:.2f})")
            return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "TRAILING_ATR_EXTEND"}

        if pnl_pct < -0.5 and ml_wp < 0.45:
            _log_dynamic_exit(pos["id"], "EARLY_EXIT", ml_wp, ml_conf, pnl_pct, age_h,
                              f"near_expiry+losing({pnl_pct:.2f}%)+ml_bearish({ml_wp:.2f})")
            return {"action": "EARLY_EXIT", "reason": "ML_EARLY_EXIT"}

    # ── 6. Mid-hold dynamic adjustments ──
    if pnl_pct > 0.5 and ml_conf == "HIGH":
        trail = max(0.003, atr_pct / 100 * 0.8 * tighten)
        _log_dynamic_exit(pos["id"], "TRAIL_ATR", ml_wp, ml_conf, pnl_pct, age_h,
                          f"mid_hold+winning({pnl_pct:.2f}%)+HIGH_conf")
        return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "TRAILING_ATR_WINNER"}

    if pnl_pct < -1.0 and ml_wp < 0.40:
        _log_dynamic_exit(pos["id"], "EARLY_EXIT", ml_wp, ml_conf, pnl_pct, age_h,
                          f"mid_hold+deep_loss({pnl_pct:.2f}%)+ml_very_bearish({ml_wp:.2f})")
        return {"action": "EARLY_EXIT", "reason": "ML_EARLY_EXIT"}

    return None  # no dynamic action, continue normal monitoring
'''

content = content[:idx_eval_start] + NEW_EVAL + content[idx_eval_end:]
print("OK [3/4] _evaluate_dynamic_exit rewritten (PROTECT_PROFIT + TIME_TRAIL + lower thresholds)")

# ── Change 4: Register PROTECT_PROFIT in perp_monitor_step TRAIL_ATR handler ─
# TRAIL_EARLY has action=TRAIL_ATR, so the existing monitor step handles it correctly.
# PROTECT_PROFIT has action=EARLY_EXIT, so it is also handled correctly already.
# We just need to verify the file compiles.
print("OK [4/4] No monitor_step changes needed (new reasons use existing action types)")

TARGET.write_text(content)
print("\nPatch 53 applied successfully.")
