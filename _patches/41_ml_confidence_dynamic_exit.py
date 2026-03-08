#!/usr/bin/env python3
"""Patch 41 — ML confidence tier sizing + dynamic exit strategy.

Changes to perp_executor.py:
1. Add ML confidence tier sizing after ML gating block (HIGH→1.3x, LOW→0.7x)
2. Add ml_conf to notes strings for all 3 modes
3. Add _evaluate_dynamic_exit() function
4. Add dynamic_exit_log table creation
5. Modify all 3 monitor steps (swing/scalp/mid) with dynamic exit evaluation
6. Set ML_MIN_WIN_PROB=0.45 in .env
"""
import re, os

EXECUTOR = "/root/memecoin_engine/utils/perp_executor.py"
ENV_FILE = "/root/memecoin_engine/.env"

with open(EXECUTOR, "r") as f:
    code = f.read()

# ─────────────────────────────────────────────────────────────
# 1. Add ML confidence-tier sizing after ML gating block
# ─────────────────────────────────────────────────────────────
# Find the ML gating block end: "    except Exception as _mle:\n        logger.debug("ML predict error: %s", _mle)"
old_ml_end = '''    except Exception as _mle:
        logger.debug("ML predict error: %s", _mle)


    # ── Volatility filter'''

new_ml_end = '''    except Exception as _mle:
        logger.debug("ML predict error: %s", _mle)

    # ── ML Confidence Tier Sizing ──
    ml_conf_tier = "MEDIUM"
    if ml_prediction:
        ml_conf_tier = ml_prediction.get("confidence", "MEDIUM")
        if ml_conf_tier == "HIGH":
            combined_mult = round(combined_mult * 1.3, 2)
            logger.info("[ML-SIZE] %s %s conf=HIGH → 1.3x sizing (mult=%.2f)", symbol, side, combined_mult)
        elif ml_conf_tier == "LOW":
            combined_mult = round(combined_mult * 0.7, 2)
            logger.info("[ML-SIZE] %s %s conf=LOW → 0.7x sizing (mult=%.2f)", symbol, side, combined_mult)

    # ── Volatility filter'''

assert old_ml_end in code, "Cannot find ML gating block end"
code = code.replace(old_ml_end, new_ml_end, 1)
print("[OK] Added ML confidence tier sizing")

# ─────────────────────────────────────────────────────────────
# 2. Add ml_conf to notes for SCALP mode
# ─────────────────────────────────────────────────────────────
old_scalp_notes = (
    "f\"mode=SCALP|source={signal.get('source','scalp')}|regime={regime}|size_mult={combined_mult}|vol_mult={vol_mult}|pd_flag={pump_dump_flag}|sent={sent_score}|sent_boost={sent_boost}|ml_wp={ml_prediction['win_prob'] if ml_prediction else 'N/A'}|ml_ret={ml_prediction['pred_return'] if ml_prediction else 'N/A'}\""
)
new_scalp_notes = (
    "f\"mode=SCALP|source={signal.get('source','scalp')}|regime={regime}|size_mult={combined_mult}|vol_mult={vol_mult}|pd_flag={pump_dump_flag}|sent={sent_score}|sent_boost={sent_boost}|ml_wp={ml_prediction['win_prob'] if ml_prediction else 'N/A'}|ml_ret={ml_prediction['pred_return'] if ml_prediction else 'N/A'}|ml_conf={ml_conf_tier}\""
)
assert old_scalp_notes in code, "Cannot find SCALP notes string"
code = code.replace(old_scalp_notes, new_scalp_notes, 1)
print("[OK] Added ml_conf to SCALP notes")

# ─────────────────────────────────────────────────────────────
# 3. Add ml_conf to notes for MID mode
# ─────────────────────────────────────────────────────────────
old_mid_notes = (
    "f\"mode=MID|source={signal.get('source','mid')}|regime={regime}|size_mult={combined_mult}|vol_mult={vol_mult}|pd_flag={pump_dump_flag}|sent={sent_score}|sent_boost={sent_boost}|ml_wp={ml_prediction['win_prob'] if ml_prediction else 'N/A'}|ml_ret={ml_prediction['pred_return'] if ml_prediction else 'N/A'}\""
)
new_mid_notes = (
    "f\"mode=MID|source={signal.get('source','mid')}|regime={regime}|size_mult={combined_mult}|vol_mult={vol_mult}|pd_flag={pump_dump_flag}|sent={sent_score}|sent_boost={sent_boost}|ml_wp={ml_prediction['win_prob'] if ml_prediction else 'N/A'}|ml_ret={ml_prediction['pred_return'] if ml_prediction else 'N/A'}|ml_conf={ml_conf_tier}\""
)
assert old_mid_notes in code, "Cannot find MID notes string"
code = code.replace(old_mid_notes, new_mid_notes, 1)
print("[OK] Added ml_conf to MID notes")

# ─────────────────────────────────────────────────────────────
# 4. Add ml_conf to notes for SWING mode
# ─────────────────────────────────────────────────────────────
old_swing_notes = (
    "f\"mode=SWING|source={signal.get('source','auto')}|regime={regime}|size_mult={combined_mult}|vol_mult={vol_mult}|pd_flag={pump_dump_flag}|sent={sent_score}|sent_boost={sent_boost}|ml_wp={ml_prediction['win_prob'] if ml_prediction else 'N/A'}|ml_ret={ml_prediction['pred_return'] if ml_prediction else 'N/A'}\""
)
new_swing_notes = (
    "f\"mode=SWING|source={signal.get('source','auto')}|regime={regime}|size_mult={combined_mult}|vol_mult={vol_mult}|pd_flag={pump_dump_flag}|sent={sent_score}|sent_boost={sent_boost}|ml_wp={ml_prediction['win_prob'] if ml_prediction else 'N/A'}|ml_ret={ml_prediction['pred_return'] if ml_prediction else 'N/A'}|ml_conf={ml_conf_tier}\""
)
assert old_swing_notes in code, "Cannot find SWING notes string"
code = code.replace(old_swing_notes, new_swing_notes, 1)
print("[OK] Added ml_conf to SWING notes")

# ─────────────────────────────────────────────────────────────
# 5. Add _evaluate_dynamic_exit() + _log_dynamic_exit() + table creation
#    Insert before perp_monitor_step()
# ─────────────────────────────────────────────────────────────
DYNAMIC_EXIT_CODE = '''

# ═══════════════════════════════════════════════════════════════
# DYNAMIC EXIT STRATEGY
# ═══════════════════════════════════════════════════════════════

def _ensure_dynamic_exit_table():
    """Create dynamic_exit_log table if it doesn't exist."""
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS dynamic_exit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER,
                ts_utc TEXT,
                action TEXT,
                ml_wp REAL,
                ml_conf TEXT,
                pnl_at_decision REAL,
                age_h REAL,
                reason TEXT,
                outcome TEXT
            )
        """)
        c.commit()

_ensure_dynamic_exit_table()


def _log_dynamic_exit(pos_id, action, ml_wp, ml_conf, pnl_pct, age_h, reason):
    """Record a dynamic exit decision for learning."""
    try:
        with _conn() as c:
            c.execute("""
                INSERT INTO dynamic_exit_log
                    (position_id, ts_utc, action, ml_wp, ml_conf, pnl_at_decision, age_h, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (pos_id, _now_iso(), action, ml_wp, ml_conf, pnl_pct, age_h, reason))
            c.commit()
    except Exception as e:
        logger.debug("Failed to log dynamic exit: %s", e)


def _parse_notes_dict(notes_str):
    """Parse pipe-delimited key=value notes into dict."""
    result = {}
    if not notes_str:
        return result
    for part in notes_str.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _evaluate_dynamic_exit(pos, current_price, age_h, mode):
    """Evaluate whether to modify exit behavior based on ML + trend.

    Returns: None (no action), or dict with:
      {"action": "EXTEND"|"EARLY_EXIT"|"TRAIL_ATR", "reason": "...", "trail_pct": ...}
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

    # Max hold for this mode
    if mode == "SCALP":
        max_hold_h = SCALP_MAX_HOLD_MIN() / 60.0
    elif mode == "MID":
        max_hold_h = MID_MAX_HOLD_H()
    else:
        max_hold_h = PERP_MAX_HOLD_H()

    near_expiry = age_h >= max_hold_h * 0.8

    # ── Smart Expiry (replaces dumb TIME_LIMIT for near-expiry trades) ──
    if near_expiry:
        if pnl_pct > 0.3 and ml_wp >= 0.55:
            # Profitable + ML favorable → switch to ATR trailing stop
            trail = max(0.003, atr_pct / 100 * 1.5)
            _log_dynamic_exit(pos["id"], "TRAIL_ATR", ml_wp, ml_conf, pnl_pct, age_h,
                              f"near_expiry+profitable({pnl_pct:.2f}%)+ml_favorable({ml_wp:.2f})")
            return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "TRAILING_ATR_EXTEND"}

        if pnl_pct < -0.5 and ml_wp < 0.45:
            # Losing + ML bearish → cut early before full TIME_LIMIT
            _log_dynamic_exit(pos["id"], "EARLY_EXIT", ml_wp, ml_conf, pnl_pct, age_h,
                              f"near_expiry+losing({pnl_pct:.2f}%)+ml_bearish({ml_wp:.2f})")
            return {"action": "EARLY_EXIT", "reason": "ML_EARLY_EXIT"}

    # ── Mid-hold dynamic adjustments ──
    if pnl_pct > 0.5 and ml_conf == "HIGH":
        # Winner with high ML confidence → trail instead of waiting for TP
        trail = max(0.003, atr_pct / 100 * 0.8)
        _log_dynamic_exit(pos["id"], "TRAIL_ATR", ml_wp, ml_conf, pnl_pct, age_h,
                          f"mid_hold+winning({pnl_pct:.2f}%)+HIGH_conf")
        return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "TRAILING_ATR_WINNER"}

    if pnl_pct < -1.0 and ml_wp < 0.40:
        # Significantly losing + ML very bearish → cut losses early
        _log_dynamic_exit(pos["id"], "EARLY_EXIT", ml_wp, ml_conf, pnl_pct, age_h,
                          f"mid_hold+deep_loss({pnl_pct:.2f}%)+ml_very_bearish({ml_wp:.2f})")
        return {"action": "EARLY_EXIT", "reason": "ML_EARLY_EXIT"}

    return None  # no dynamic action, continue normal monitoring


'''

# Insert before perp_monitor_step
assert "async def perp_monitor_step():" in code, "Cannot find perp_monitor_step"
code = code.replace(
    "async def perp_monitor_step():",
    DYNAMIC_EXIT_CODE + "async def perp_monitor_step():",
    1,
)
print("[OK] Added _evaluate_dynamic_exit() and helpers")

# ─────────────────────────────────────────────────────────────
# 6. Modify perp_monitor_step (SWING) — add dynamic exit before TIME_LIMIT
# ─────────────────────────────────────────────────────────────
old_swing_exit = '''        if side == "LONG":
            if price <= stop:
                exit_reason = "STOP_LOSS"
            elif tp2 and price >= tp2:
                exit_reason = "TP2"
            elif tp1 and price >= tp1:
                exit_reason = "TP1"
            elif age_h >= max_hold:
                exit_reason = "TIME_LIMIT"
        else:  # SHORT
            if price >= stop:
                exit_reason = "STOP_LOSS"
            elif tp2 and price <= tp2:
                exit_reason = "TP2"
            elif tp1 and price <= tp1:
                exit_reason = "TP1"
            elif age_h >= max_hold:
                exit_reason = "TIME_LIMIT"

        if exit_reason:
            result = _close_perp_position(pos_id, price, exit_reason)
            mode   = "PAPER" if pos["dry_run"] else "LIVE"
            if result:
                logger.info(
                    "[PERP %s] Closed %s %s @ $%.4f  reason=%s  pnl=%.2f%%",
                    mode, side, symbol, price, exit_reason, result.get("pnl_pct", 0),
                )'''

new_swing_exit = '''        if side == "LONG":
            if price <= stop:
                exit_reason = "STOP_LOSS"
            elif tp2 and price >= tp2:
                exit_reason = "TP2"
            elif tp1 and price >= tp1:
                exit_reason = "TP1"
        else:  # SHORT
            if price >= stop:
                exit_reason = "STOP_LOSS"
            elif tp2 and price <= tp2:
                exit_reason = "TP2"
            elif tp1 and price <= tp1:
                exit_reason = "TP1"

        # Dynamic exit evaluation (before TIME_LIMIT fallback)
        if exit_reason is None:
            dyn = _evaluate_dynamic_exit(pos, price, age_h, "SWING")
            if dyn:
                if dyn["action"] == "EARLY_EXIT":
                    exit_reason = dyn["reason"]
                elif dyn["action"] == "TRAIL_ATR":
                    entry_p = pos["entry_price"]
                    mfe_price = pos.get("mfe_price") or price
                    # Use MFE as peak; fallback to current price
                    try:
                        peak = float(mfe_price) if float(mfe_price) > 0 else price
                    except (ValueError, TypeError):
                        peak = price
                    if side == "LONG":
                        trail_stop = peak * (1 - dyn["trail_pct"])
                        if price <= trail_stop:
                            exit_reason = dyn["reason"]
                    else:
                        trail_stop = peak * (1 + dyn["trail_pct"])
                        if price >= trail_stop:
                            exit_reason = dyn["reason"]

        # TIME_LIMIT fallback
        if exit_reason is None and age_h >= max_hold:
            exit_reason = "TIME_LIMIT"

        if exit_reason:
            result = _close_perp_position(pos_id, price, exit_reason)
            mode   = "PAPER" if pos["dry_run"] else "LIVE"
            if result:
                logger.info(
                    "[PERP %s] Closed %s %s @ $%.4f  reason=%s  pnl=%.2f%%",
                    mode, side, symbol, price, exit_reason, result.get("pnl_pct", 0),
                )'''

assert old_swing_exit in code, "Cannot find swing monitor exit block"
code = code.replace(old_swing_exit, new_swing_exit, 1)
print("[OK] Modified perp_monitor_step (SWING) with dynamic exit")

# ─────────────────────────────────────────────────────────────
# 7. Modify scalp_monitor_step — add dynamic exit before TIME_LIMIT
# ─────────────────────────────────────────────────────────────
old_scalp_exit = '''        if side == "LONG":
            if price <= stop:
                exit_reason = "STOP_LOSS"
            elif tp1 and price >= tp1:
                exit_reason = "TP1"
            elif age_h >= max_hold_h:
                exit_reason = "TIME_LIMIT"
        else:  # SHORT
            if price >= stop:
                exit_reason = "STOP_LOSS"
            elif tp1 and price <= tp1:
                exit_reason = "TP1"
            elif age_h >= max_hold_h:
                exit_reason = "TIME_LIMIT"

        if exit_reason:
            result = _close_perp_position(pos_id, price, exit_reason)
            paper_label = "PAPER" if pos["dry_run"] else "LIVE"
            if result:
                logger.info(
                    "[SCALP %s] Closed %s %s @ $%.4f  reason=%s  pnl=%.2f%%",
                    paper_label, side, symbol, price, exit_reason, result.get("pnl_pct", 0),
                )'''

new_scalp_exit = '''        if side == "LONG":
            if price <= stop:
                exit_reason = "STOP_LOSS"
            elif tp1 and price >= tp1:
                exit_reason = "TP1"
        else:  # SHORT
            if price >= stop:
                exit_reason = "STOP_LOSS"
            elif tp1 and price <= tp1:
                exit_reason = "TP1"

        # Dynamic exit evaluation (before TIME_LIMIT fallback)
        if exit_reason is None:
            dyn = _evaluate_dynamic_exit(pos, price, age_h, "SCALP")
            if dyn:
                if dyn["action"] == "EARLY_EXIT":
                    exit_reason = dyn["reason"]
                elif dyn["action"] == "TRAIL_ATR":
                    entry_p = pos["entry_price"]
                    mfe_price = pos.get("mfe_price") or price
                    try:
                        peak = float(mfe_price) if float(mfe_price) > 0 else price
                    except (ValueError, TypeError):
                        peak = price
                    if side == "LONG":
                        trail_stop = peak * (1 - dyn["trail_pct"])
                        if price <= trail_stop:
                            exit_reason = dyn["reason"]
                    else:
                        trail_stop = peak * (1 + dyn["trail_pct"])
                        if price >= trail_stop:
                            exit_reason = dyn["reason"]

        # TIME_LIMIT fallback
        if exit_reason is None and age_h >= max_hold_h:
            exit_reason = "TIME_LIMIT"

        if exit_reason:
            result = _close_perp_position(pos_id, price, exit_reason)
            paper_label = "PAPER" if pos["dry_run"] else "LIVE"
            if result:
                logger.info(
                    "[SCALP %s] Closed %s %s @ $%.4f  reason=%s  pnl=%.2f%%",
                    paper_label, side, symbol, price, exit_reason, result.get("pnl_pct", 0),
                )'''

assert old_scalp_exit in code, "Cannot find scalp monitor exit block"
code = code.replace(old_scalp_exit, new_scalp_exit, 1)
print("[OK] Modified scalp_monitor_step with dynamic exit")

# ─────────────────────────────────────────────────────────────
# 8. Modify mid_monitor_step — add dynamic exit before TIME_LIMIT
# ─────────────────────────────────────────────────────────────
old_mid_exit = '''        reason = None
        if side == 'LONG':
            if price >= tp_price:   reason = 'TP1'
            elif price <= sl_price: reason = 'STOP_LOSS'
        else:
            if price <= tp_price:   reason = 'TP1'
            elif price >= sl_price: reason = 'STOP_LOSS'

        if reason is None and age_h >= max_hold_h:
            reason = 'TIME_LIMIT'

        if reason:
            result = _close_perp_position(pos_id, price, reason)
            pnl = result.get('pnl_pct', 0) if result else 0
            logger.info(
                '[MID PAPER] Closed %s %s @ %.4g  reason=%s  pnl=%.2f%%  held=%.1fh',
                side, symbol, price, reason, pnl, age_h
            )'''

new_mid_exit = '''        reason = None
        if side == 'LONG':
            if price >= tp_price:   reason = 'TP1'
            elif price <= sl_price: reason = 'STOP_LOSS'
        else:
            if price <= tp_price:   reason = 'TP1'
            elif price >= sl_price: reason = 'STOP_LOSS'

        # Dynamic exit evaluation (before TIME_LIMIT fallback)
        if reason is None:
            dyn = _evaluate_dynamic_exit(pos, price, age_h, "MID")
            if dyn:
                if dyn["action"] == "EARLY_EXIT":
                    reason = dyn["reason"]
                elif dyn["action"] == "TRAIL_ATR":
                    mfe_price = pos.get("mfe_price") or price
                    try:
                        peak = float(mfe_price) if float(mfe_price) > 0 else price
                    except (ValueError, TypeError):
                        peak = price
                    if side == "LONG":
                        trail_stop = peak * (1 - dyn["trail_pct"])
                        if price <= trail_stop:
                            reason = dyn["reason"]
                    else:
                        trail_stop = peak * (1 + dyn["trail_pct"])
                        if price >= trail_stop:
                            reason = dyn["reason"]

        if reason is None and age_h >= max_hold_h:
            reason = 'TIME_LIMIT'

        if reason:
            result = _close_perp_position(pos_id, price, reason)
            pnl = result.get('pnl_pct', 0) if result else 0
            logger.info(
                '[MID PAPER] Closed %s %s @ %.4g  reason=%s  pnl=%.2f%%  held=%.1fh',
                side, symbol, price, reason, pnl, age_h
            )'''

assert old_mid_exit in code, "Cannot find mid monitor exit block"
code = code.replace(old_mid_exit, new_mid_exit, 1)
print("[OK] Modified mid_monitor_step with dynamic exit")

# ─────────────────────────────────────────────────────────────
# Write the file
# ─────────────────────────────────────────────────────────────
with open(EXECUTOR, "w") as f:
    f.write(code)
print(f"[OK] Wrote {EXECUTOR} ({len(code)} bytes)")

# ─────────────────────────────────────────────────────────────
# 9. Update ML_MIN_WIN_PROB in .env
# ─────────────────────────────────────────────────────────────
with open(ENV_FILE, "r") as f:
    env = f.read()

if "ML_MIN_WIN_PROB=" in env:
    env = re.sub(r"ML_MIN_WIN_PROB=[\d.]+", "ML_MIN_WIN_PROB=0.45", env)
else:
    env += "\nML_MIN_WIN_PROB=0.45\n"

with open(ENV_FILE, "w") as f:
    f.write(env)
print("[OK] Set ML_MIN_WIN_PROB=0.45 in .env")

# ─────────────────────────────────────────────────────────────
# Verify compilation
# ─────────────────────────────────────────────────────────────
import subprocess
result = subprocess.run(
    ["python3", "-c", f"import py_compile; py_compile.compile('{EXECUTOR}', doraise=True)"],
    capture_output=True, text=True
)
if result.returncode == 0:
    print("[OK] perp_executor.py compiles successfully")
else:
    print(f"[ERROR] Compilation failed:\n{result.stderr}")
    import sys
    sys.exit(1)

print("\n=== Patch 41 complete ===")
print("  - ML confidence tier sizing (HIGH→1.3x, MEDIUM→1.0x, LOW→0.7x)")
print("  - ml_conf added to notes for SCALP, MID, SWING")
print("  - _evaluate_dynamic_exit() with smart expiry + mid-hold logic")
print("  - dynamic_exit_log table created")
print("  - All 3 monitor steps (swing/scalp/mid) updated with dynamic exit")
print("  - ML_MIN_WIN_PROB=0.45")
