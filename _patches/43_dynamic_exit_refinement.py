#!/usr/bin/env python3
"""Patch 43 — Dynamic Exit Refinement: fix peak_price bug, enhanced exit logic,
partial close support, safety rails, auto-label outcomes.

Changes to perp_executor.py:
1. Fix mfe_price → peak_price/trough_price in all 3 monitor steps
2. Add partial_closes column to perp_positions
3. Rewrite _evaluate_dynamic_exit() with DYNAMIC_TRAIL, ML_PROB_DROP, PARTIAL_PROFIT
4. Add _execute_partial_close() and _has_partial_close()
5. Add PARTIAL_CLOSE handler in all 3 monitor steps
6. Add auto-labeling in _close_perp_position()
7. Add safety rails: hard max hold (2.5x), circuit breaker (4 consecutive losses)
"""
import re, os

EXECUTOR = "/root/memecoin_engine/utils/perp_executor.py"

with open(EXECUTOR, "r") as f:
    code = f.read()

# ═══════════════════════════════════════════════════════════════
# 1. Replace _ensure_dynamic_exit_table to also add partial_closes column
# ═══════════════════════════════════════════════════════════════
old_ensure = '''def _ensure_dynamic_exit_table():
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

_ensure_dynamic_exit_table()'''

new_ensure = '''def _ensure_dynamic_exit_table():
    """Create dynamic_exit_log table + add partial_closes column."""
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
        # Add partial_closes column to perp_positions
        try:
            c.execute("ALTER TABLE perp_positions ADD COLUMN partial_closes TEXT")
        except Exception:
            pass  # already exists
        c.commit()

_ensure_dynamic_exit_table()


# ── Circuit breaker state ──
_dynamic_exit_disabled_until = None


def _check_dynamic_exit_circuit_breaker():
    """Disable dynamic exits for 24h if 4 consecutive EARLY_EXIT losses."""
    global _dynamic_exit_disabled_until
    if _dynamic_exit_disabled_until and datetime.now(timezone.utc) < _dynamic_exit_disabled_until:
        return True  # still disabled
    if _dynamic_exit_disabled_until and datetime.now(timezone.utc) >= _dynamic_exit_disabled_until:
        _dynamic_exit_disabled_until = None
        logger.info("[CIRCUIT BREAKER] Dynamic exit re-enabled after 24h cooldown")
        return False
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute("""
                SELECT del.action, pp.pnl_pct
                FROM dynamic_exit_log del
                JOIN perp_positions pp ON del.position_id = pp.id
                WHERE del.action IN ('EARLY_EXIT', 'ML_PROB_DROP') AND pp.status = 'CLOSED'
                ORDER BY del.ts_utc DESC LIMIT 4
            """)
            rows = cur.fetchall()
        if len(rows) >= 4 and all(r[1] is not None and float(r[1]) < 0 for r in rows):
            _dynamic_exit_disabled_until = datetime.now(timezone.utc) + timedelta(hours=24)
            logger.warning("[CIRCUIT BREAKER] 4 consecutive early-exit losses — disabling dynamic exits for 24h")
            return True
    except Exception:
        pass
    return False


def _has_partial_close(pos_id):
    """Check if this position already had a partial close."""
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute("SELECT partial_closes FROM perp_positions WHERE id=?", (pos_id,))
            row = cur.fetchone()
            if row and row[0]:
                import json as _json
                closes = _json.loads(row[0])
                return len(closes) > 0
    except Exception:
        pass
    return False


def _execute_partial_close(pos_id, close_pct, current_price, reason):
    """Close a fraction of the position and record it."""
    import json as _json
    with _conn() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM perp_positions WHERE id=?", (pos_id,))
        row = cur.fetchone()
        if not row:
            return
        pos = dict(row)

    entry = pos["entry_price"]
    side = pos["side"].upper()
    size = pos["size_usd"]
    lev = pos["leverage"]

    if side == "LONG":
        raw_pct = (current_price - entry) / entry * 100
    else:
        raw_pct = (entry - current_price) / entry * 100
    leveraged_pct = raw_pct * lev

    closed_size = size * close_pct
    partial_pnl = closed_size * (leveraged_pct / 100)
    new_size = round(size - closed_size, 2)

    partial_record = {
        "ts": _now_iso(),
        "pct": close_pct,
        "price": current_price,
        "pnl_usd": round(partial_pnl, 4),
        "pnl_pct": round(leveraged_pct, 4),
        "reason": reason,
    }

    existing_json = pos.get("partial_closes") or "[]"
    try:
        existing = _json.loads(existing_json)
    except Exception:
        existing = []
    existing.append(partial_record)

    with _conn() as c:
        cur = c.cursor()
        cur.execute("""
            UPDATE perp_positions SET size_usd = ?, partial_closes = ? WHERE id = ?
        """, (new_size, _json.dumps(existing), pos_id))
        c.commit()

    logger.info(
        "[PARTIAL CLOSE] pos=%d %s closed %.0f%% at $%.4f  pnl=$%.2f (%.2f%%)  remaining=$%.0f",
        pos_id, reason, close_pct * 100, current_price, partial_pnl, leveraged_pct, new_size,
    )'''

assert old_ensure in code, "Cannot find _ensure_dynamic_exit_table"
code = code.replace(old_ensure, new_ensure, 1)
print("[OK] Replaced _ensure_dynamic_exit_table + added circuit breaker + partial close helpers")

# ═══════════════════════════════════════════════════════════════
# 2. Rewrite _evaluate_dynamic_exit() with enhanced conditions
# ═══════════════════════════════════════════════════════════════
old_evaluate = '''def _evaluate_dynamic_exit(pos, current_price, age_h, mode):
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

    return None  # no dynamic action, continue normal monitoring'''

new_evaluate = '''def _evaluate_dynamic_exit(pos, current_price, age_h, mode):
    """Evaluate whether to modify exit behavior based on ML + trend.

    Returns: None (no action), or dict with:
      {"action": "EARLY_EXIT"|"TRAIL_ATR"|"PARTIAL_CLOSE", "reason": "...", ...}

    Priority order:
      1. DYNAMIC_TRAIL — high ML conviction + profitable → immediate trailing
      2. PARTIAL_PROFIT — medium conf + winning ≥0.8% → close 50%
      3. ML_PROB_DROP — medium conf + losing → cut early
      4. Near-expiry smart exits
      5. Mid-hold adjustments
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

    # ── 1. DYNAMIC_TRAIL: High ML conviction + profitable → immediate trailing ──
    if ml_wp >= 0.72 and pnl_pct > 0:
        trail = max(0.003, atr_pct / 100 * 1.2)
        _log_dynamic_exit(pos["id"], "DYNAMIC_TRAIL", ml_wp, ml_conf, pnl_pct, age_h,
                          f"high_ml_wp({ml_wp:.2f})+profitable({pnl_pct:.2f}%)")
        return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "DYNAMIC_TRAIL"}

    # ── 2. PARTIAL_PROFIT: Medium conf + winning ≥0.8% → close 50% ──
    if pnl_pct >= 0.8 and ml_conf == "MEDIUM" and not _has_partial_close(pos["id"]):
        _log_dynamic_exit(pos["id"], "PARTIAL_PROFIT", ml_wp, ml_conf, pnl_pct, age_h,
                          f"partial_profit({pnl_pct:.2f}%)+medium_conf({ml_wp:.2f})")
        return {"action": "PARTIAL_CLOSE", "close_pct": 0.50, "reason": "PARTIAL_PROFIT"}

    # ── 3. ML_PROB_DROP: Medium conf + losing → entry prediction wrong ──
    if ml_conf == "MEDIUM" and pnl_pct < -0.3:
        _log_dynamic_exit(pos["id"], "ML_PROB_DROP", ml_wp, ml_conf, pnl_pct, age_h,
                          f"medium_conf({ml_wp:.2f})+losing({pnl_pct:.2f}%)")
        return {"action": "EARLY_EXIT", "reason": "ML_PROB_DROP"}

    # ── 4. Near-expiry smart exits ──
    if near_expiry:
        if pnl_pct > 0.3 and ml_wp >= 0.55:
            trail = max(0.003, atr_pct / 100 * 1.5)
            _log_dynamic_exit(pos["id"], "TRAIL_ATR", ml_wp, ml_conf, pnl_pct, age_h,
                              f"near_expiry+profitable({pnl_pct:.2f}%)+ml_favorable({ml_wp:.2f})")
            return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "TRAILING_ATR_EXTEND"}

        if pnl_pct < -0.5 and ml_wp < 0.45:
            _log_dynamic_exit(pos["id"], "EARLY_EXIT", ml_wp, ml_conf, pnl_pct, age_h,
                              f"near_expiry+losing({pnl_pct:.2f}%)+ml_bearish({ml_wp:.2f})")
            return {"action": "EARLY_EXIT", "reason": "ML_EARLY_EXIT"}

    # ── 5. Mid-hold dynamic adjustments ──
    if pnl_pct > 0.5 and ml_conf == "HIGH":
        trail = max(0.003, atr_pct / 100 * 0.8)
        _log_dynamic_exit(pos["id"], "TRAIL_ATR", ml_wp, ml_conf, pnl_pct, age_h,
                          f"mid_hold+winning({pnl_pct:.2f}%)+HIGH_conf")
        return {"action": "TRAIL_ATR", "trail_pct": trail, "reason": "TRAILING_ATR_WINNER"}

    if pnl_pct < -1.0 and ml_wp < 0.40:
        _log_dynamic_exit(pos["id"], "EARLY_EXIT", ml_wp, ml_conf, pnl_pct, age_h,
                          f"mid_hold+deep_loss({pnl_pct:.2f}%)+ml_very_bearish({ml_wp:.2f})")
        return {"action": "EARLY_EXIT", "reason": "ML_EARLY_EXIT"}

    return None  # no dynamic action, continue normal monitoring'''

assert old_evaluate in code, "Cannot find _evaluate_dynamic_exit"
code = code.replace(old_evaluate, new_evaluate, 1)
print("[OK] Rewrote _evaluate_dynamic_exit() with DYNAMIC_TRAIL, PARTIAL_PROFIT, ML_PROB_DROP")

# ═══════════════════════════════════════════════════════════════
# 3. Fix SWING monitor: mfe_price → peak_price, add PARTIAL_CLOSE, hard max, circuit breaker
# ═══════════════════════════════════════════════════════════════
old_swing_dyn = '''        # Dynamic exit evaluation (before TIME_LIMIT fallback)
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
            exit_reason = "TIME_LIMIT"'''

new_swing_dyn = '''        # Hard safety rail: never exceed 2.5x max hold time
        hard_max_h = max_hold * 2.5
        if age_h >= hard_max_h:
            exit_reason = "HARD_TIME_LIMIT"

        # Dynamic exit evaluation (before TIME_LIMIT fallback)
        if exit_reason is None and not _check_dynamic_exit_circuit_breaker():
            dyn = _evaluate_dynamic_exit(pos, price, age_h, "SWING")
            if dyn:
                if dyn["action"] == "EARLY_EXIT":
                    exit_reason = dyn["reason"]
                elif dyn["action"] == "PARTIAL_CLOSE":
                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                elif dyn["action"] == "TRAIL_ATR":
                    if side == "LONG":
                        peak = float(pos.get("peak_price") or price)
                    else:
                        peak = float(pos.get("trough_price") or price)
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
            exit_reason = "TIME_LIMIT"'''

assert old_swing_dyn in code, "Cannot find SWING dynamic exit block"
code = code.replace(old_swing_dyn, new_swing_dyn, 1)
print("[OK] Fixed SWING monitor: peak_price, PARTIAL_CLOSE, hard max, circuit breaker")

# ═══════════════════════════════════════════════════════════════
# 4. Fix SCALP monitor: same fixes
# ═══════════════════════════════════════════════════════════════
old_scalp_dyn = '''        # Dynamic exit evaluation (before TIME_LIMIT fallback)
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
            exit_reason = "TIME_LIMIT"'''

new_scalp_dyn = '''        # Hard safety rail: never exceed 2.5x max hold time
        hard_max_h = max_hold_h * 2.5
        if age_h >= hard_max_h:
            exit_reason = "HARD_TIME_LIMIT"

        # Dynamic exit evaluation (before TIME_LIMIT fallback)
        if exit_reason is None and not _check_dynamic_exit_circuit_breaker():
            dyn = _evaluate_dynamic_exit(pos, price, age_h, "SCALP")
            if dyn:
                if dyn["action"] == "EARLY_EXIT":
                    exit_reason = dyn["reason"]
                elif dyn["action"] == "PARTIAL_CLOSE":
                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                elif dyn["action"] == "TRAIL_ATR":
                    if side == "LONG":
                        peak = float(pos.get("peak_price") or price)
                    else:
                        peak = float(pos.get("trough_price") or price)
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
            exit_reason = "TIME_LIMIT"'''

assert old_scalp_dyn in code, "Cannot find SCALP dynamic exit block"
code = code.replace(old_scalp_dyn, new_scalp_dyn, 1)
print("[OK] Fixed SCALP monitor: peak_price, PARTIAL_CLOSE, hard max, circuit breaker")

# ═══════════════════════════════════════════════════════════════
# 5. Fix MID monitor: same fixes
# ═══════════════════════════════════════════════════════════════
old_mid_dyn = '''        # Dynamic exit evaluation (before TIME_LIMIT fallback)
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
            reason = 'TIME_LIMIT\''''

new_mid_dyn = '''        # Hard safety rail: never exceed 2.5x max hold time
        hard_max_h = max_hold_h * 2.5
        if age_h >= hard_max_h:
            reason = "HARD_TIME_LIMIT"

        # Dynamic exit evaluation (before TIME_LIMIT fallback)
        if reason is None and not _check_dynamic_exit_circuit_breaker():
            dyn = _evaluate_dynamic_exit(pos, price, age_h, "MID")
            if dyn:
                if dyn["action"] == "EARLY_EXIT":
                    reason = dyn["reason"]
                elif dyn["action"] == "PARTIAL_CLOSE":
                    _execute_partial_close(pos_id, dyn["close_pct"], price, dyn["reason"])
                elif dyn["action"] == "TRAIL_ATR":
                    if side == "LONG":
                        peak = float(pos.get("peak_price") or price)
                    else:
                        peak = float(pos.get("trough_price") or price)
                    if side == "LONG":
                        trail_stop = peak * (1 - dyn["trail_pct"])
                        if price <= trail_stop:
                            reason = dyn["reason"]
                    else:
                        trail_stop = peak * (1 + dyn["trail_pct"])
                        if price >= trail_stop:
                            reason = dyn["reason"]

        if reason is None and age_h >= max_hold_h:
            reason = 'TIME_LIMIT\''''

assert old_mid_dyn in code, "Cannot find MID dynamic exit block"
code = code.replace(old_mid_dyn, new_mid_dyn, 1)
print("[OK] Fixed MID monitor: peak_price, PARTIAL_CLOSE, hard max, circuit breaker")

# ═══════════════════════════════════════════════════════════════
# 6. Add auto-labeling in _close_perp_position()
# ═══════════════════════════════════════════════════════════════
# Insert after the broadcast_trade_event block but before post_exit_tracking
old_post_exit = '''    # Create post-exit tracking record for price monitoring after close'''

new_post_exit = '''    # Auto-label dynamic exit decisions with outcome
    try:
        _dyn_reasons = ("ML_EARLY_EXIT", "ML_PROB_DROP", "DYNAMIC_TRAIL",
                        "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER", "PARTIAL_PROFIT")
        if exit_reason in _dyn_reasons:
            if exit_reason in ("ML_EARLY_EXIT", "ML_PROB_DROP") and leveraged_pct < 0:
                _outcome = "good_call"  # correctly cut a loser
            elif exit_reason in ("ML_EARLY_EXIT", "ML_PROB_DROP") and leveraged_pct > 0.5:
                _outcome = "bad_call"  # cut a winner
            elif exit_reason in ("DYNAMIC_TRAIL", "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER") and leveraged_pct > 0:
                _outcome = "good_call"  # trailed and caught profit
            elif exit_reason in ("DYNAMIC_TRAIL", "TRAILING_ATR_EXTEND", "TRAILING_ATR_WINNER") and leveraged_pct < -0.5:
                _outcome = "bad_call"  # trail didn't help
            else:
                _outcome = "neutral"
            with _conn() as c:
                c.execute("UPDATE dynamic_exit_log SET outcome = ? WHERE position_id = ?",
                          (_outcome, position_id))
                c.commit()
            logger.info("[DYN-EXIT OUTCOME] pos=%d reason=%s pnl=%.2f%% → %s",
                        position_id, exit_reason, leveraged_pct, _outcome)
    except Exception:
        pass

    # Create post-exit tracking record for price monitoring after close'''

assert old_post_exit in code, "Cannot find post-exit tracking comment"
code = code.replace(old_post_exit, new_post_exit, 1)
print("[OK] Added auto-labeling in _close_perp_position()")

# ═══════════════════════════════════════════════════════════════
# Write the file
# ═══════════════════════════════════════════════════════════════
with open(EXECUTOR, "w") as f:
    f.write(code)
print(f"[OK] Wrote {EXECUTOR} ({len(code)} bytes)")

# Verify compilation
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

print("\n=== Patch 43 complete ===")
print("  - Fixed peak_price bug in all 3 monitors (was using mfe_price which doesn't exist)")
print("  - DYNAMIC_TRAIL: ml_wp >= 0.72 + profitable → immediate ATR trail")
print("  - PARTIAL_PROFIT: pnl >= 0.8% + MEDIUM conf → close 50%")
print("  - ML_PROB_DROP: MEDIUM conf + losing -0.3% → cut early")
print("  - Hard max hold: 2.5x original TIME_LIMIT ceiling")
print("  - Circuit breaker: 4 consecutive early-exit losses → 24h cooldown")
print("  - Auto-label: good_call/bad_call/neutral in dynamic_exit_log")
print("  - partial_closes column added to perp_positions")
